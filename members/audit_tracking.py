"""Record business and access changes without storing credentials or request bodies."""

import json
from contextvars import ContextVar

from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from .ledger_tracking import ledger_actor, officer_identity

audit_request = ContextVar("audit_request", default={})
SKIP_MODELS = {"auditlog", "memberauditevent", "ledgerevent", "ledgerrecord"}
SENSITIVE = {
    "password",
    "payload",
    "query",
    "deduplication_key",
    "target_fingerprint",
    "operation_key",
}
VOLATILE = {"updated_at", "last_login"}
MODULES = {
    "financialyearclosing": ("financialyearclosing", "Financial year-end closing"),
    "user": ("users", "Users & access"),
    "group": ("roles", "Roles & permissions"),
    "ledgerofficerprofile": ("users", "Users & access"),
    "approvalhierarchy": ("approvals", "Approvals"),
    "approvallevel": ("approvals", "Approvals"),
    "approvalrequest": ("approvals", "Approvals"),
    "approvalauditevent": ("approvals", "Approvals"),
    "paymentplanamount": ("paymentplan", "Payment plans"),
    "paymentplantemplateamount": ("paymentplantemplate", "Payment plan templates"),
    "paymentreceipt": ("paymenttransaction", "Debit / Credit & contributions"),
    "paymentreceiptallocation": (
        "paymenttransaction",
        "Debit / Credit & contributions",
    ),
    "paymenttransaction": ("paymenttransaction", "Debit / Credit & contributions"),
    "childdeposittransaction": ("childdeposit", "Child deposits"),
    "childdepositinterestyear": ("depositinterestyear", "Quarterly interest"),
    "openingbalanceamount": ("openingbalance", "Opening balances"),
    "fixdepositloan": ("fixdepositaccount", "Fix Deposit loans & withdrawals"),
    "fixloanentry": ("fixdepositaccount", "Fix Deposit loans & withdrawals"),
    "fixdepositwithdrawal": ("fixdepositaccount", "Fix Deposit loans & withdrawals"),
    "ledgerofficerassignment": ("memberledger", "Member ledgers"),
}


def module_for(model):
    return MODULES.get(
        model._meta.model_name,
        (model._meta.model_name, str(model._meta.verbose_name_plural).capitalize()),
    )


def safe_value(value):
    if isinstance(value, dict):
        return {
            key: safe_value(item)
            for key, item in value.items()
            if not any(
                word in key.lower()
                for word in ("password", "secret", "token", "credential")
            )
        }
    if isinstance(value, (list, tuple)):
        return [safe_value(item) for item in value]
    return value


def snapshot(obj):
    result = {}
    for field in obj._meta.concrete_fields:
        if field.name in SENSITIVE or field.name in VOLATILE:
            continue
        value = safe_value(getattr(obj, field.attname))
        result[field.name] = {"label": str(field.verbose_name), "value": value}
    return json.loads(json.dumps(result, cls=DjangoJSONEncoder))


def scope_for(obj):
    from .permissions import MEMBER_LOOKUPS

    member = None
    path = MEMBER_LOOKUPS.get(obj._meta.model_name)
    if path:
        member_pk = obj
        for key in path.split("__"):
            member_pk = getattr(member_pk, key, None)
            if member_pk is None:
                break
        from .models import Member

        member = Member.objects.filter(pk=member_pk).first() if member_pk else None
    elif obj._meta.model_name == "ledgerofficerassignment":
        member = obj.ledger.member
    elif obj._meta.model_name == "membertransfer":
        member = obj.member
    elif obj._meta.model_name == "memberledger":
        member = obj.member
    if member:
        return {
            "member_pk": member.pk,
            "district_pk": member.district_id,
            "lager_pk": member.lager_id,
            "zonal_office_pk": member.office_location_id,
        }
    return {
        "district_pk": (
            obj.pk
            if obj._meta.model_name == "district"
            else getattr(obj, "district_id", None)
        ),
        "zonal_office_pk": (
            obj.pk
            if obj._meta.model_name == "officelocation"
            else getattr(obj, "zonal_office_id", None)
        ),
    }


def write_event(
    obj, action, before, after, *, using=None, changes=None, actor=None, scope=None
):
    from .models import AuditLog

    changes = (
        changes
        if changes is not None
        else [
            {
                "field": key,
                "label": (after.get(key) or before[key])["label"],
                "before": before.get(key, {}).get("value"),
                "after": after.get(key, {}).get("value"),
            }
            for key in dict.fromkeys([*before, *after])
            if before.get(key) != after.get(key)
        ]
    )
    if action == "updated" and not changes:
        return
    actor = actor or ledger_actor.get()
    if actor is None and action == "created":
        for field in (
            "created_by",
            "recorded_by",
            "performed_by",
            "changed_by",
            "actor",
        ):
            if getattr(obj, f"{field}_id", None):
                actor = getattr(obj, field)
                break
    name, epf = officer_identity(actor)
    module, label = module_for(obj._meta.model)
    AuditLog.objects.using(using).create(
        module=module,
        module_label=label,
        source_model=obj._meta.label_lower,
        source_id=obj.pk,
        record_label=str(obj)[:600],
        action=action,
        before=before,
        after=after,
        changes=changes,
        actor_id=actor.pk if actor else None,
        actor_username=actor.username if actor else "",
        actor_name=name,
        actor_epf=epf,
        **(scope if scope is not None else scope_for(obj)),
        **audit_request.get(),
    )


def before_save(sender, instance, using, raw=False, **kwargs):
    if raw:
        return
    old = (
        sender._base_manager.using(using).filter(pk=instance.pk).first()
        if instance.pk
        else None
    )
    instance._audit_before = snapshot(old) if old else {}
    instance._audit_password_changed = bool(
        old
        and hasattr(instance, "password")
        and old.password != instance.password
        and (
            kwargs.get("update_fields") is None or "password" in kwargs["update_fields"]
        )
    )


def after_save(sender, instance, created, using, raw=False, **kwargs):
    if raw:
        return
    saved = sender._base_manager.using(using).get(pk=instance.pk)
    before = getattr(instance, "_audit_before", {})
    after = snapshot(saved)
    if getattr(instance, "_audit_password_changed", False):
        before["password"] = {"label": "Password", "value": "[redacted]"}
        after["password"] = {"label": "Password", "value": "[changed]"}
    action = (
        saved.action
        if sender._meta.model_name == "approvalauditevent"
        else "created" if created else "updated"
    )
    write_event(saved, action, before, after, using=using)


def before_delete(sender, instance, using, **kwargs):
    write_event(instance, "deleted", snapshot(instance), {}, using=using)


def before_m2m(sender, instance, action, reverse, model, pk_set, using, **kwargs):
    if not action.startswith("pre_"):
        return
    owner_field = next(
        field
        for field in sender._meta.fields
        if field.is_relation and field.related_model is type(instance)
    )
    other_field = next(
        field
        for field in sender._meta.fields
        if field.is_relation
        and field.related_model is model
        and field is not owner_field
    )
    old = set(
        sender._base_manager.using(using)
        .filter(**{owner_field.attname: instance.pk})
        .values_list(other_field.attname, flat=True)
    )
    selected = set(pk_set or [])
    new = (
        old | selected
        if action == "pre_add"
        else old - selected if action == "pre_remove" else set()
    )
    if new == old:
        return
    field = other_field.name
    write_event(
        instance,
        "access_changed",
        {field: {"label": str(sender._meta.verbose_name), "value": sorted(old)}},
        {field: {"label": str(sender._meta.verbose_name), "value": sorted(new)}},
        using=using,
    )


def authentication_event(sender, request, user=None, credentials=None, **kwargs):
    from django.contrib.auth import get_user_model

    if user is None:
        username = str((credentials or {}).get("username", ""))[:150]
        user = get_user_model()(username=username)
    write_event(
        user,
        kwargs.get("audit_action", "login"),
        {},
        {},
        actor=user if user.pk else None,
    )


def connect_audit_signals():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.contrib.auth.signals import (
        user_logged_in,
        user_logged_out,
        user_login_failed,
    )
    from django.db.models.signals import pre_save, post_save, pre_delete, m2m_changed

    models_to_track = [
        model
        for model in apps.get_app_config("members").get_models()
        if model._meta.model_name not in SKIP_MODELS
    ]
    models_to_track.extend([get_user_model(), Group])
    for model in models_to_track:
        pre_save.connect(
            before_save,
            sender=model,
            dispatch_uid=f"central-audit-before-{model._meta.label_lower}",
        )
        post_save.connect(
            after_save,
            sender=model,
            dispatch_uid=f"central-audit-after-{model._meta.label_lower}",
        )
        pre_delete.connect(
            before_delete,
            sender=model,
            dispatch_uid=f"central-audit-delete-{model._meta.label_lower}",
        )
        for field in model._meta.many_to_many:
            m2m_changed.connect(
                before_m2m,
                sender=field.remote_field.through,
                dispatch_uid=f"central-audit-m2m-{field.remote_field.through._meta.label_lower}",
            )
    user_logged_in.connect(authentication_event, dispatch_uid="central-audit-login")

    def logout(sender, **kwargs):
        authentication_event(sender, audit_action="logout", **kwargs)

    def failed(sender, **kwargs):
        authentication_event(sender, audit_action="login_failed", **kwargs)

    user_logged_out.connect(logout, weak=False, dispatch_uid="central-audit-logout")
    user_login_failed.connect(
        failed, weak=False, dispatch_uid="central-audit-login-failed"
    )


class AuditTransactionMiddleware:
    """Commit business writes and their audit events together; keep safe reads lightweight."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            return self.get_response(request)
        from django.db import transaction

        with transaction.atomic():
            response = self.get_response(request)
            if response.status_code >= 400:
                transaction.set_rollback(True)
            return response
