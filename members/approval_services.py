"""Approval checkpoints run after form validation and before business writes."""

import hashlib
import json
from contextvars import ContextVar

from django.apps import apps
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, QueryDict
from django.shortcuts import redirect
from django.urls import resolve, reverse
from django.utils import timezone

from .approval_models import ApprovalHierarchy, ApprovalRequest, ApprovalAuditEvent
from .ledger_tracking import ledger_actor, officer_identity
from .approval_scope import ALL_MODULES, requires_approval_scope

execution = ContextVar("approval_execution", default=None)


def encode(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, cls=DjangoJSONEncoder).encode()
    ).hexdigest()


def save_request(record, **kwargs):
    record._approval_service_write = True
    try:
        record.save(**kwargs)
    finally:
        record._approval_service_write = False


def audit(
    actor,
    hierarchy,
    action,
    *,
    record=None,
    reason="",
    override=False,
    level="",
    details=None,
):
    name, epf = officer_identity(actor)
    return ApprovalAuditEvent.objects.create(
        hierarchy=hierarchy,
        request=record,
        action=action,
        actor=actor,
        actor_name=name,
        actor_epf=epf,
        reason=reason,
        is_override=override,
        level_name=level,
        details=encode(details or {}),
    )


def hierarchy_snapshot(hierarchy):
    return {
        "name": hierarchy.name,
        "module": hierarchy.module,
        "action": hierarchy.action,
        "is_active": hierarchy.is_active,
        "version": hierarchy.version,
        "levels": [
            {
                "position": level.position,
                "name": level.name,
                "allow_rejection": level.allow_rejection,
                "approvers": [
                    {
                        "id": user.pk,
                        "name": officer_identity(user)[0],
                        "epf": officer_identity(user)[1],
                    }
                    for user in level.approvers.order_by("pk")
                ],
            }
            for level in hierarchy.levels.order_by("position", "pk")
        ],
    }


def target_fingerprint(target):
    if target is None or target.pk is None:
        return ""
    fresh = type(target).objects.get(pk=target.pk)
    state = {
        "record": {
            field.attname: getattr(fresh, field.attname)
            for field in fresh._meta.concrete_fields
        }
    }
    for name in ("amounts", "ledgers", "entries", "loans", "withdrawals"):
        manager = getattr(fresh, name, None)
        if hasattr(manager, "values"):
            state[name] = list(manager.order_by("pk").values())
    for name in ("policy", "deposit_type", "payment_type", "child"):
        if getattr(fresh, f"{name}_id", None):
            related = getattr(fresh, name)
            state[name] = {
                field.attname: getattr(related, field.attname)
                for field in related._meta.concrete_fields
            }
    return digest(state)


def _summary(form, target):
    if form is None:
        return [{"label": "Record", "value": str(target)}] if target else []
    rows = []
    for name, value in form.cleaned_data.items():
        if name in ("operation_key", "calculation_hash", "source_ledger"):
            continue
        if hasattr(value, "all"):
            value = ", ".join(str(item) for item in value)
        elif isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        elif value is None:
            value = "—"
        else:
            value = str(value)
        rows.append(
            {
                "label": form.fields[name].label or name.replace("_", " ").capitalize(),
                "value": value,
            }
        )
    return rows


def _parameters(data):
    return {
        key: [str(value) for value in values]
        for key, values in data.lists()
        if key != "csrfmiddlewaretoken"
    }


def form_checkpoint(
    request,
    form,
    *,
    model=None,
    action=None,
    target=None,
    description=None,
    extra_summary=None,
):
    instance = getattr(form, "instance", None)
    model = model or type(instance)
    action = action or ("change" if instance is not None and instance.pk else "add")
    target = target or (instance if instance is not None and instance.pk else None)
    label = {"add": "Create", "change": "Update", "delete": "Delete"}[action]
    return checkpoint(
        request,
        model,
        action,
        description or f"{label} {model._meta.verbose_name}",
        target=target,
        form=form,
        extra_summary=extra_summary,
    )


def checkpoint(
    request, model, action, description, *, target=None, form=None, extra_summary=None
):
    """Return a queue redirect, or None to continue the already validated operation."""
    module = model._meta.model_name
    current = execution.get()
    if not requires_approval_scope(module, action, request.resolver_match.view_name):
        if current is not None:
            raise ValidationError(
                "This older request is outside the current approval scope. Cancel it and use the normal form."
            )
        return None
    if current is not None:
        record = current["record"]
        if (
            record.module,
            record.action,
            record.view_name,
            record.view_kwargs,
            record.payload,
            record.query,
        ) != (
            module,
            action,
            request.resolver_match.view_name,
            request.resolver_match.kwargs,
            _parameters(request.POST),
            _parameters(request.GET),
        ):
            raise ValidationError(
                "The approved operation no longer matches this request. Submit it again."
            )
        if form is not None or extra_summary:
            current_summary = _summary(form, target) + (extra_summary or [])
            if current_summary != record.summary:
                raise ValidationError(
                    "The form's accounts, rates or configured values changed while approval was pending. Review and submit a new request."
                )
        current["seen"] = True
        return None
    from django.db.models import Case, When, Value, IntegerField

    policy = (
        ApprovalHierarchy.objects.filter(
            module__in=[module, ALL_MODULES], is_active=True, action__in=[action, "*"]
        )
        .annotate(
            module_priority=Case(
                When(module=module, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            action_priority=Case(
                When(action=action, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by("-module_priority", "-action_priority", "-pk")
        .first()
    )
    if policy is None:
        return None
    from .permissions import require_permission

    require_permission(request.user, model, action)
    # Lock the policy while copying its levels so concurrent configuration edits
    # cannot produce a mixture of old and new approver assignments.
    with transaction.atomic():
        policy = ApprovalHierarchy.objects.select_for_update().get(pk=policy.pk)
        if not policy.is_active:
            raise PermissionDenied("This approval hierarchy changed. Reload the form.")
        steps = hierarchy_snapshot(policy)["levels"]
        active_users = set(
            get_user_model().objects.filter(is_active=True).values_list("pk", flat=True)
        )
        for level in steps:
            level["approvers"] = [
                user for user in level["approvers"] if user["id"] in active_users
            ]
        if not steps or any(not level["approvers"] for level in steps):
            raise PermissionDenied(
                "An administrator must finish configuring this approval hierarchy before requests can be submitted."
            )
        payload, query = _parameters(
            form.data if form is not None else request.POST
        ), _parameters(request.GET)
        key = digest(
            [
                request.user.pk,
                request.resolver_match.view_name,
                request.resolver_match.kwargs,
                payload,
                query,
            ]
        )
        # Serializing submissions by requester also makes double-clicks idempotent.
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        record = ApprovalRequest.objects.filter(
            deduplication_key=key, status="pending"
        ).first()
        if record is None:
            name, epf = officer_identity(request.user)
            record = ApprovalRequest(
                hierarchy=policy,
                hierarchy_name=policy.name,
                hierarchy_version=policy.version,
                module=module,
                action=action,
                description=description[:250],
                requested_by=request.user,
                requester_name=name,
                requester_epf=epf,
                steps=steps,
                view_name=request.resolver_match.view_name,
                view_kwargs=request.resolver_match.kwargs,
                payload=payload,
                query=query,
                summary=_summary(form, target) + (extra_summary or []),
                target_model=target._meta.label_lower if target and target.pk else "",
                target_id=target.pk if target and target.pk else None,
                target_fingerprint=target_fingerprint(target),
                deduplication_key=key,
            )
            save_request(record)
            audit(request.user, policy, "submitted", record=record)
    messages.info(
        request,
        f"{record.reference} submitted for approval. The requested change will take effect after final approval.",
    )
    return redirect("approval_request_detail", pk=record.pk)


def visible_requests(user):
    rows = ApprovalRequest.objects.all()
    if user.is_superuser:
        return rows
    # Assignment snapshots continue to govern pending requests after policy edits.
    from django.db.models import Q

    return rows.filter(
        Q(requested_by=user) | Q(steps__contains=[{"approvers": [{"id": user.pk}]}])
    )


def decision_rights(user, record):
    pending = record.status == "pending" and user.is_active
    step = (
        record.steps[record.current_step]
        if record.steps and record.current_step < len(record.steps)
        else {}
    )
    assigned = (
        any(item["id"] == user.pk for item in step.get("approvers", []))
        and record.requested_by_id != user.pk
    )
    return {
        "approve": pending and (user.is_superuser or assigned),
        "reject": pending
        and (user.is_superuser or (assigned and step.get("allow_rejection", True))),
        "override": pending and user.is_superuser,
        "cancel": pending and (user.is_superuser or record.requested_by_id == user.pk),
    }


def _querydict(values):
    result = QueryDict(mutable=True)
    for name, items in values.items():
        result.setlist(name, items)
    return result


def _apply(record):
    requester = get_user_model().objects.get(pk=record.requested_by_id)
    if not requester.is_active:
        raise ValidationError(
            "The requester is inactive. Submit a new request from an authorized account."
        )
    if record.target_id:
        model = apps.get_model(record.target_model)
        candidate = model.objects.filter(pk=record.target_id).first()
        if candidate is not None:
            from .ledger_tracking import TRACKED, related_value
            from .models import Member

            member_id = (
                candidate.pk
                if model is Member
                else getattr(candidate, "member_id", None)
            )
            if member_id is None and model._meta.model_name in TRACKED:
                member_id = related_value(
                    candidate, TRACKED[model._meta.model_name][0]
                ).pk
            if member_id:
                # Follow the same member-before-account lock order as financial services.
                Member.objects.select_for_update().get(pk=member_id)
        target = model.objects.select_for_update().filter(pk=record.target_id).first()
        if target is None or target_fingerprint(target) != record.target_fingerprint:
            raise ValidationError(
                "The underlying record changed while approval was pending. Review the latest data and submit a new request."
            )
    path = reverse(record.view_name, kwargs=record.view_kwargs)
    match = resolve(path)
    if not match.func.__module__.startswith("members.") or match.view_name.startswith(
        "approval_"
    ):
        raise ValidationError(
            "This operation is not supported by the approval workflow."
        )
    replay = HttpRequest()
    replay.method = "POST"
    replay.path = replay.path_info = path
    replay.META = {"SERVER_NAME": "localhost", "SERVER_PORT": "80"}
    replay.POST = _querydict(record.payload)
    replay.GET = _querydict(record.query)
    replay.user = requester
    replay.session = {}
    replay._messages = FallbackStorage(replay)
    replay.resolver_match = match
    state = {"record": record, "seen": False}
    token, actor_token = execution.set(state), ledger_actor.set(requester)
    try:
        response = match.func(replay, **match.kwargs)
        notices = list(messages.get_messages(replay))
        errors = [
            notice.message for notice in notices if notice.level >= messages.ERROR
        ]
        if not state["seen"] or response.status_code not in (301, 302, 303) or errors:
            raise ValidationError(
                errors
                or [
                    "The request no longer passes validation. Review the current form and submit a new request."
                ]
            )
        return response.get("Location", "")
    finally:
        execution.reset(token)
        ledger_actor.reset(actor_token)


@transaction.atomic
def decide(actor, request_pk, decision, reason=""):
    record = (
        ApprovalRequest.objects.select_for_update()
        .select_related("hierarchy")
        .get(pk=request_pk)
    )
    rights = decision_rights(actor, record)
    required = {
        "approve": "approve",
        "reject": "reject",
        "override_approve": "override",
        "cancel": "cancel",
    }.get(decision)
    if required is None or not rights[required]:
        raise PermissionDenied(
            "This request is not awaiting your approval, or you do not have this decision permission."
        )
    reason = reason.strip()
    override = actor.is_superuser and decision != "cancel"
    if (
        decision in ("reject", "override_approve", "cancel") or override
    ) and not reason:
        raise ValidationError(
            "Enter a reason for this decision. Administrator overrides always require a reason."
        )
    level = record.steps[record.current_step]["name"]
    if decision in ("reject", "cancel"):
        record.status = "rejected" if decision == "reject" else "cancelled"
        record.completed_at = timezone.now()
        record.outcome = reason
        audit(
            actor,
            record.hierarchy,
            decision,
            record=record,
            reason=reason,
            override=override,
            level=level,
        )
        save_request(record)
        return record
    audit(
        actor,
        record.hierarchy,
        "override_approve" if decision == "override_approve" else "approve",
        record=record,
        reason=reason,
        override=override,
        level=level,
        details={
            "position": record.steps[record.current_step]["position"],
            "remaining_levels": (
                record.steps[record.current_step :]
                if decision == "override_approve"
                else []
            ),
        },
    )
    if decision == "approve" and record.current_step + 1 < len(record.steps):
        record.current_step += 1
        save_request(record, update_fields=["current_step"])
        return record
    try:
        with transaction.atomic():
            result_url = _apply(record)
    except (
        ValidationError,
        PermissionDenied,
        Http404,
        ObjectDoesNotExist,
        IntegrityError,
    ) as error:
        record.status = "failed"
        record.outcome = (
            " ".join(error.messages)
            if isinstance(error, ValidationError)
            else "The requester's access or the underlying records changed. Review the current data and submit a new request."
        )
        audit(
            actor,
            record.hierarchy,
            "application_failed",
            record=record,
            reason=record.outcome,
        )
    else:
        record.status = "approved"
        record.result_url = result_url[:500]
        record.outcome = (
            "All required approvals completed and the requested change was applied."
            if decision == "approve"
            else "Administrator approved all remaining levels and applied the requested change."
        )
        audit(
            actor,
            record.hierarchy,
            "applied",
            record=record,
            override=override,
            reason=reason,
            details={"result_url": result_url},
        )
    record.completed_at = timezone.now()
    save_request(record)
    return record
