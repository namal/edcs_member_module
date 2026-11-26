"""Mandatory workflow approval for membership status changes."""

import hashlib
from datetime import datetime, time
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError, ObjectDoesNotExist
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from . import models as m
from .approval_services import audit, hierarchy_snapshot, save_request
from .ledger_tracking import ledger_actor, officer_identity
from .status_models import status_write

MODULE = "memberstatusrequest"
REASONS = {
    "activate": [("new_registration", "New member registration")],
    "inactivate": [
        ("resignation", "Member resignation"),
        ("death", "Death of a member"),
        ("termination", "Membership termination"),
        ("disciplinary", "Disciplinary action"),
        ("duplicate", "Duplicate membership"),
        ("eligibility", "Membership eligibility issues"),
        ("administrative", "Administrative decision"),
        ("other", "Other approved reasons"),
    ],
    "reactivate": [
        ("eligibility_restored", "Membership eligibility restored"),
        ("disciplinary_clearance", "Disciplinary clearance"),
        ("administrative_review", "Approved administrative review"),
        ("other", "Other approved reactivation reason"),
    ],
}
TRANSITIONS = {
    "activate": ("pending", "active"),
    "inactivate": ("active", "inactive"),
    "reactivate": ("inactive", "active"),
}
MAX_DOCUMENT_SIZE = 5 * 1024 * 1024


def guard_status_change(member):
    if status_write.get():
        return
    actor = ledger_actor.get()
    # Existing status changes always need approval; trusted legacy imports can
    # still create an initial member outside the HTTP registration workflow.
    if member.pk:
        previous = (
            m.Member.objects.filter(pk=member.pk)
            .values_list("status", flat=True)
            .first()
        )
        if previous != member.status and (
            previous == "pending"
            or member.status
            in (
                "active",
                "inactive",
                "pending",
            )
        ):
            raise ValidationError(
                {
                    "status": "Use Membership Status and higher-level approval to activate, inactivate or reactivate a member."
                }
            )
    elif actor is not None and member.status != "pending":
        raise ValidationError({"status": "New members must await activation approval."})


def visible_status_requests(user):
    from .permissions import accessible_members

    if not user.is_authenticated or not user.is_active:
        return m.MemberStatusRequest.objects.none()
    rows = m.MemberStatusRequest.objects.all()
    if user.is_superuser:
        return rows
    owned = Q(member_id__in=accessible_members(user).values("pk"))
    if not any(
        user.has_perm("members." + action + "_memberstatusrequest")
        for action in ("view", "add", "change")
    ):
        owned = (
            Q(requested_by=user, member_id__in=accessible_members(user).values("pk"))
            if user.has_perm("members.add_member")
            else Q(pk__in=[])
        )
    reviewer = Q(pk__in=[])
    if user.has_perm("members.change_memberstatusrequest"):
        reviewer = Q(approval__steps__contains=[{"approvers": [{"id": user.pk}]}])
    return rows.filter(owned | reviewer)


def approval_policy(requester):
    policy = (
        m.ApprovalHierarchy.objects.select_for_update()
        .filter(module=MODULE, action="change", is_active=True)
        .first()
    )
    if policy is None:
        raise ValidationError(
            "An administrator must configure an active Membership Status approval hierarchy before this request can be submitted."
        )
    steps = hierarchy_snapshot(policy)["levels"]
    for step in steps:
        allowed = []
        for person in step["approvers"]:
            reviewer = (
                get_user_model().objects.filter(pk=person["id"], is_active=True).first()
            )
            if (
                reviewer
                and (
                    reviewer.pk != requester.pk
                    or step.get("allow_self_approval", False)
                )
                and (
                    reviewer.is_superuser
                    or reviewer.has_perm("members.change_memberstatusrequest")
                )
            ):
                allowed.append(person)
        step["approvers"] = allowed
    if not steps or any(not step["approvers"] for step in steps):
        raise ValidationError(
            "Each Membership Status approval level needs an active, authorized reviewer. Requester approval also requires the level's explicit self-approval setting. Ask an administrator to finish the hierarchy."
        )
    return policy, steps


def validated_documents(files):
    if len(files) > 5:
        raise ValidationError("Attach at most five supporting documents.")
    result = []
    for file in files:
        if file.size > MAX_DOCUMENT_SIZE or file.size == 0:
            raise ValidationError(
                "Each supporting document must contain data and be no larger than 5 MB."
            )
        data = file.read(MAX_DOCUMENT_SIZE + 1)
        file.seek(0)
        suffix = Path(file.name).suffix.lower()
        if suffix == ".pdf" and data.startswith(b"%PDF-"):
            mime = "application/pdf"
        elif suffix in (".jpg", ".jpeg") and data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        else:
            raise ValidationError(
                "Supporting documents must be valid PDF, JPG or PNG files with matching file extensions."
            )
        result.append(
            {
                "name": Path(file.name).name[:200],
                "content_type": mime,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "content": data,
            }
        )
    return result


@transaction.atomic
def submit_status_request(
    actor,
    member_pk,
    kind,
    reason,
    description,
    effective_date,
    files=(),
    *,
    registration=False,
):
    from .permissions import district_records, require_permission

    if registration and kind != "activate":
        raise ValidationError("Registration can only request initial activation.")

    require_permission(
        actor, m.Member if registration else m.MemberStatusRequest, "add"
    )
    member = (
        district_records(actor, m.Member)
        .select_for_update(of=("self",))
        .select_related("district", "office_location", "lager")
        .get(pk=member_pk)
    )
    if kind not in TRANSITIONS or member.status != TRANSITIONS[kind][0]:
        raise ValidationError(
            "This action does not match the current membership status. Reload the member and try again."
        )
    if reason not in dict(REASONS[kind]) or len(description.strip()) < 10:
        raise ValidationError(
            "Select a valid reason and give a detailed description of at least 10 characters."
        )
    if effective_date < timezone.localdate():
        raise ValidationError(
            "Choose today or a future requested effective date. Status changes cannot be backdated."
        )
    if not member.lager_id:
        raise ValidationError(
            "Assign the member to a Lager before requesting a status change."
        )
    m.Lager.objects.select_for_update().get(pk=member.lager_id)
    # Recheck scope after locking master ownership.
    if not district_records(actor, m.Member).filter(pk=member.pk).exists():
        raise PermissionDenied("This member is outside your assigned Lager access.")
    if member.status_requests.filter(state__in=["pending", "scheduled"]).exists():
        raise ValidationError(
            "This member already has an awaiting-approval or scheduled status request."
        )
    ledger = member.ledgers.filter(status="active").first()
    if ledger is None:
        raise ValidationError(
            "The member must have an active ledger page before submitting a status request."
        )
    policy, steps = approval_policy(actor)
    documents = validated_documents(list(files))
    identity = {
        "member_no": member.member_no,
        "member_name": member.full_name,
        "district": member.district.name,
        "district_id": member.district_id,
        "zonal_office": member.office_location.name,
        "zonal_office_id": member.office_location_id,
        "ledger_code": member.lager.code,
        "lager_id": member.lager_id,
        "ledger_reference": ledger.reference,
        "current_status": member.get_status_display(),
    }
    name, epf = officer_identity(actor)
    token, actor_token = status_write.set(True), ledger_actor.set(actor)
    try:
        request = m.MemberStatusRequest.objects.create(
            member=member,
            lager=member.lager,
            ledger=ledger,
            kind=kind,
            source_status=member.status,
            reason=reason,
            description=description.strip(),
            requested_effective_date=effective_date,
            snapshot=identity,
            requested_by=actor,
            requester_name=name,
            requester_epf=epf,
        )
        for document in documents:
            m.MemberStatusDocument.objects.create(request=request, **document)
        steps[0]["access_lager_id"] = member.lager_id
        steps[0]["access_member_id"] = member.pk
        record = m.ApprovalRequest(
            hierarchy=policy,
            hierarchy_name=policy.name,
            hierarchy_version=policy.version,
            module=MODULE,
            action="change",
            description=str(request),
            requested_by=actor,
            requester_name=name,
            requester_epf=epf,
            steps=steps,
            view_name="member_status_detail",
            view_kwargs={"pk": request.pk},
            target_model=request._meta.label_lower,
            target_id=request.pk,
            deduplication_key=hashlib.sha256(request.reference.encode()).hexdigest(),
            summary=[
                {"label": label, "value": str(value)}
                for label, value in [
                    ("Membership number", member.member_no),
                    ("Member name", member.full_name),
                    ("District / Branch", identity["district"]),
                    ("Zonal office", identity["zonal_office"]),
                    ("Ledger Code", identity["ledger_code"]),
                    ("Current membership status", identity["current_status"]),
                    ("Requested action", request.get_kind_display()),
                    ("Reason", request.reason_label),
                    ("Detailed reason", request.description),
                    ("Requested effective date", effective_date),
                    ("Supporting documents", len(documents)),
                    ("Requested by", name),
                    (
                        "Request date and time",
                        timezone.localtime(request.requested_at).isoformat(),
                    ),
                ]
            ],
        )
        save_request(record)
        request.approval = record
        request.save(update_fields=["approval"])
        audit(
            actor,
            policy,
            "submitted",
            record=record,
            reason=request.description,
            details={"membership_request": request.reference},
        )
    finally:
        ledger_actor.reset(actor_token)
        status_write.reset(token)
    return request


def _current_member(request):
    from .permissions import district_records

    member = m.Member.objects.select_for_update().get(pk=request.member_id)
    if not member.lager_id:
        raise ValidationError(
            "The member no longer has an assigned Lager. Review the request."
        )
    m.Lager.objects.select_for_update().get(pk=member.lager_id)
    requester = get_user_model().objects.get(pk=request.requested_by_id)
    authorized = requester.has_perm("members.add_memberstatusrequest") or (
        request.kind == "activate" and requester.has_perm("members.add_member")
    )
    if (
        not requester.is_active
        or not authorized
        or not district_records(requester, m.Member).filter(pk=member.pk).exists()
    ):
        raise ValidationError(
            "The requester no longer has permission for this member and Lager. Submit a new authorized request."
        )
    ledger = member.ledgers.filter(status="active").first()
    if (
        member.status != request.source_status
        or member.lager_id != request.lager_id
        or ledger is None
        or ledger.pk != request.ledger_id
    ):
        raise ValidationError(
            "The membership status or assigned Lager/ledger changed while this request was pending. Submit a new request."
        )
    return member


def _apply_status(request, actor):
    member = _current_member(request)
    now = timezone.now()
    target = TRANSITIONS[request.kind][1]
    period = (
        member.active_periods.select_for_update().filter(ended_at__isnull=True).first()
    )
    if target == "inactive":
        if period is None:
            # Existing active records predate this module; keep an explicit imported baseline.
            period = m.MemberActivePeriod.objects.create(
                member=member,
                ledger=request.ledger,
                started_at=timezone.make_aware(
                    datetime.combine(member.admit_date, time.min)
                ),
                imported=True,
            )
        period.ended_at = now
        period.closed_by_request = request
        period.save(update_fields=["ended_at", "closed_by_request"])
    else:
        if period is not None:
            raise ValidationError(
                "An active membership period already exists. Review the status history."
            )
        m.MemberActivePeriod.objects.create(
            member=member,
            ledger=request.ledger,
            started_at=now,
            opened_by_request=request,
        )
    member.status = target
    member.save(update_fields=["status", "updated_at"])
    name, epf = officer_identity(actor)
    event = m.MemberStatusEvent.objects.create(
        member=member,
        ledger=request.ledger,
        request=request,
        from_status=request.source_status,
        to_status=target,
        reason=request.reason_label,
        description=request.description,
        effective_at=now,
        actor=actor,
        actor_name=name,
        actor_epf=epf,
    )
    from .ledger_tracking import record_event

    record_event(event, request.ledger, None, "created")
    request.state = "applied"
    request.applied_at = now
    request.outcome = "Membership status updated; the same ledger page, balances and earlier history are retained."
    request.save(update_fields=["state", "applied_at", "outcome"])


@transaction.atomic
def approve_status_request(record, actor):
    from .approval_services import execution, decision_rights

    context = execution.get()
    if (
        not context
        or context["record"].pk != record.pk
        or context.get("deciding_actor") != getattr(actor, "pk", None)
    ):
        raise PermissionDenied(
            "Use the higher-level approval workflow to apply membership status."
        )
    request = m.MemberStatusRequest.objects.select_for_update().get(approval=record)
    if (
        request.state != "pending"
        or actor is None
        or not decision_rights(actor, record)["approve"]
    ):
        raise ValidationError(
            "This pending request must be approved according to its configured workflow levels."
        )
    _current_member(request)
    token, actor_token = status_write.set(True), ledger_actor.set(actor)
    try:
        request.approved_by = actor
        request.approved_at = timezone.now()
        request.save(update_fields=["approved_by", "approved_at"])
        if request.requested_effective_date > timezone.localdate():
            request.state = "scheduled"
            request.outcome = "Approved. The membership status will change on or after the requested date; actual application time is retained."
            request.save(update_fields=["state", "outcome"])
        else:
            _apply_status(request, actor)
    finally:
        ledger_actor.reset(actor_token)
        status_write.reset(token)
    return reverse("member_status_detail", args=[request.pk])


def sync_approval_outcome(record, actor):
    if record.module != MODULE or record.status not in (
        "rejected",
        "cancelled",
        "failed",
    ):
        return
    token, actor_token = status_write.set(True), ledger_actor.set(actor)
    try:
        request = m.MemberStatusRequest.objects.select_for_update().get(approval=record)
        request.state = record.status
        request.outcome = record.outcome
        request.save(update_fields=["state", "outcome"])
    finally:
        ledger_actor.reset(actor_token)
        status_write.reset(token)


def apply_due_status_requests(limit=100):
    """Idempotent scheduled execution; also usable by cron during quiet periods."""
    applied = failed = 0
    ids = list(
        m.MemberStatusRequest.objects.filter(
            state="scheduled", requested_effective_date__lte=timezone.localdate()
        ).values_list("pk", flat=True)[:limit]
    )
    for pk in ids:
        with transaction.atomic():
            request = m.MemberStatusRequest.objects.select_for_update().get(pk=pk)
            if request.state != "scheduled":
                continue
            token, actor_token = status_write.set(True), ledger_actor.set(
                request.approved_by
            )
            try:
                try:
                    with transaction.atomic():
                        _apply_status(request, request.approved_by)
                except (
                    ValidationError,
                    PermissionDenied,
                    ObjectDoesNotExist,
                    IntegrityError,
                ) as error:
                    request.refresh_from_db()
                    request.state = "failed"
                    request.outcome = (
                        " ".join(error.messages)
                        if isinstance(error, ValidationError)
                        else "The membership records or access changed. Review and submit a new authorized request."
                    )
                    request.save(update_fields=["state", "outcome"])
                    failed += 1
                else:
                    applied += 1
            finally:
                ledger_actor.reset(actor_token)
                status_write.reset(token)
    return {"applied": applied, "failed": failed}
