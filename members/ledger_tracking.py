"""Attach saved records to permanent ledger pages without changing account IDs."""

import json
from contextvars import ContextVar
from datetime import datetime

from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

ledger_actor = ContextVar("ledger_actor", default=None)

# Model: member path, effective date, permission module, human-readable category.
TRACKED = {
    "memberstatusevent": (
        "member",
        "effective_at",
        "memberstatusrequest",
        "Membership status change",
    ),
    "paymenttransaction": (
        "member",
        "transaction_date",
        "paymenttransaction",
        "Member debit / credit",
    ),
    "openingbalance": ("member", "balance_date", "openingbalance", "Opening balances"),
    "openingbalanceamount": (
        "opening_balance.member",
        "opening_balance.balance_date",
        "openingbalance",
        "Opening amount",
    ),
    "paymentplan": ("member", "agreement_date", "paymentplan", "Payment plan"),
    "paymentplanamount": (
        "plan.member",
        "plan.agreement_date",
        "paymentplan",
        "Monthly contribution",
    ),
    "child": ("member", "created_at", "child", "Child registration"),
    "childdeposit": ("child.member", "opened_on", "childdeposit", "Child account"),
    "childdeposittransaction": (
        "deposit.child.member",
        "transaction_date",
        "childdeposit",
        "Child debit / credit",
    ),
    "depositinterestyear": (
        "member",
        "calculated_through",
        "depositinterestyear",
        "Member interest calculation",
    ),
    "childdepositinterestyear": (
        "deposit.child.member",
        "calculated_through",
        "depositinterestyear",
        "Child interest calculation",
    ),
    "fixdepositaccount": (
        "member",
        "cycle_started_on",
        "fixdepositaccount",
        "Fix Deposit account",
    ),
    "fixdepositloan": (
        "account.member",
        "issued_on",
        "fixdepositaccount",
        "Loan issued",
    ),
    "fixloanentry": (
        "loan.account.member",
        "entry_date",
        "fixdepositaccount",
        "Loan repayment / assessed interest",
    ),
    "fixdepositwithdrawal": (
        "account.member",
        "requested_on",
        "fixdepositaccount",
        "Withdrawal request / review",
    ),
}
FROZEN = {
    "paymenttransaction",
    "childdeposittransaction",
    "openingbalance",
    "openingbalanceamount",
    "fixloanentry",
}
DATED_POSTINGS = {
    "paymenttransaction",
    "childdeposittransaction",
    "fixdepositloan",
    "fixloanentry",
    "fixdepositwithdrawal",
    "childdeposit",
}


def related_value(obj, path):
    for name in path.split("."):
        obj = getattr(obj, name)
    return obj


def effective_date(obj):
    value = related_value(obj, TRACKED[obj._meta.model_name][1])
    if obj._meta.model_name == "fixdepositloan" and obj.settled_on:
        value = obj.settled_on
    if obj._meta.model_name == "fixdepositwithdrawal" and obj.released_on:
        value = obj.released_on
    if value is None:
        return timezone.localdate()
    if isinstance(value, str):
        from datetime import date

        return date.fromisoformat(value)
    if isinstance(value, datetime):
        return (
            timezone.localtime(value).date()
            if timezone.is_aware(value)
            else value.date()
        )
    return value


def officer_identity(user):
    if user is None:
        return "", ""
    from .models import LedgerOfficerProfile

    profile = LedgerOfficerProfile.objects.filter(user_id=user.pk).first()
    return user.get_full_name() or user.username, (
        (profile.epf_number or "") if profile else ""
    )


def snapshot_record(obj):
    data = {
        field.attname: getattr(obj, field.attname)
        for field in obj._meta.concrete_fields
    }
    account = child = ""
    if hasattr(obj, "payment_type_id") and obj.payment_type_id:
        account = obj.payment_type.name
    if obj._meta.model_name.startswith("childdeposit"):
        deposit = obj if obj._meta.model_name == "childdeposit" else obj.deposit
        account = f"{deposit.account_no} · {deposit.deposit_type.name}"
        child = deposit.child.name
    if obj._meta.model_name == "child":
        child = obj.name
    if obj._meta.model_name.startswith("fix"):
        account_obj = (
            obj
            if obj._meta.model_name == "fixdepositaccount"
            else (
                obj.loan.account
                if obj._meta.model_name == "fixloanentry"
                else obj.account
            )
        )
        account = account_obj.policy.payment_type.name
    if obj._meta.model_name == "fixloanentry":
        label = obj.get_kind_display()
    else:
        label = TRACKED[obj._meta.model_name][3]
    if obj._meta.model_name == "fixdepositwithdrawal":
        label += f" · {obj.get_status_display()}"
    result = {
        "data": data,
        "label": label,
        "permission": TRACKED[obj._meta.model_name][2],
        "account": account,
        "child": child,
        "reference": getattr(obj, "reference", "") or getattr(obj, "account_no", ""),
    }
    return json.loads(json.dumps(result, cls=DjangoJSONEncoder))


def ledger_context(obj, *, deleting=False, locking=True):
    from .models import Member, MemberLedger, LedgerRecord
    from .transfer_services import initialize_member_ledger

    member_id = related_value(obj, TRACKED[obj._meta.model_name][0]).pk
    member = (Member.objects.select_for_update() if locking else Member.objects).get(
        pk=member_id
    )
    if member.status == "pending" and obj._meta.model_name in DATED_POSTINGS | {
        "openingbalance",
        "openingbalanceamount",
    }:
        raise ValidationError(
            "This member is awaiting activation approval. Financial postings require an active membership."
        )
    if ledger_actor.get() is not None:
        from .permissions import require_member_access

        require_member_access(ledger_actor.get(), member, lock=locking)
    ledger = MemberLedger.objects.filter(member=member, status="active").first()
    if ledger is None:
        if not locking:
            return member, None, None
        ledger = initialize_member_ledger(member)
    record = (
        LedgerRecord.objects.select_related("original_ledger")
        .filter(source_model=obj._meta.label_lower, source_id=obj.pk)
        .first()
        if obj.pk
        else None
    )
    if record and record.member_id != member.pk:
        raise ValidationError(
            "A saved ledger record cannot be moved to another member."
        )
    historical = record and record.original_ledger.status == "transferred"
    if historical and deleting:
        raise ValidationError(
            "Historical ledger records cannot be deleted. Record corrections in the current ledger."
        )
    if historical and obj._meta.model_name in FROZEN:
        previous = type(obj).objects.get(pk=obj.pk)
        if any(
            getattr(previous, field.attname) != getattr(obj, field.attname)
            for field in obj._meta.concrete_fields
            if field.name not in ("updated_at",)
        ):
            raise ValidationError(
                "Historical ledger records are read-only. Record corrections in the current ledger."
            )
    if historical and obj._meta.model_name == "childdeposit" and not deleting:
        previous = type(obj).objects.get(pk=obj.pk)
        if any(
            getattr(previous, field) != getattr(obj, field)
            for field in ("child_id", "deposit_type_id", "opened_on", "opening_amount")
        ):
            raise ValidationError(
                "The original child account opening is retained on its historical ledger."
            )
    if historical and obj._meta.model_name == "fixdepositloan" and not deleting:
        previous = type(obj).objects.get(pk=obj.pk)
        if any(
            getattr(previous, field.attname) != getattr(obj, field.attname)
            for field in obj._meta.concrete_fields
            if field.name not in ("settled_on", "settled_by")
        ):
            raise ValidationError(
                "Original loan details are retained on the historical ledger."
            )
    if (
        (not record or record.original_ledger_id == ledger.pk)
        and ledger.previous_ledger_id
        and obj._meta.model_name in DATED_POSTINGS
        and effective_date(obj) < ledger.opened_on
        and not getattr(obj, "_annual_interest_posting", False)
    ):
        raise ValidationError(
            "New entries must be dated on or after the current ledger's transfer date."
        )
    return member, ledger, record


def record_event(obj, ledger, record, action):
    from .models import LedgerEvent, LedgerRecord
    from .approval_services import execution

    if record is None:
        record, _ = LedgerRecord.objects.get_or_create(
            source_model=obj._meta.label_lower,
            source_id=obj.pk,
            defaults={"member_id": ledger.member_id, "original_ledger": ledger},
        )
    actor = ledger_actor.get()
    if actor is None and action == "created":
        actor = getattr(obj, "created_by", None) or getattr(obj, "recorded_by", None)
    name, epf = officer_identity(actor)
    snapshot = snapshot_record(obj)
    if obj._meta.model_name == "memberstatusevent":
        snapshot["approval_request_id"] = obj.request.approval_id
        snapshot["approval_reference"] = obj.request.approval.reference
    approved = execution.get()
    if approved and approved["seen"]:
        snapshot["approval_request_id"] = approved["record"].pk
        snapshot["approval_reference"] = approved["record"].reference
    LedgerEvent.objects.create(
        record=record,
        ledger=ledger,
        action=action,
        effective_on=effective_date(obj),
        snapshot=snapshot,
        actor=actor,
        actor_name=name,
        actor_epf=epf,
    )


class LedgerTrackedModel(models.Model):
    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        try:
            ledger_context(self, locking=False)
        except (ObjectDoesNotExist, AttributeError, TypeError, ValueError):
            # Missing/invalid relation fields are reported by normal form validation.
            pass

    def save(self, *args, **kwargs):
        with transaction.atomic():
            _, ledger, record = ledger_context(self)
            created = self.pk is None
            result = super().save(*args, **kwargs)
            persisted = type(self).objects.get(pk=self.pk)
            record_event(persisted, ledger, record, "created" if created else "updated")
            return result

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            try:
                ledger_context(self, deleting=True)
            except ValidationError as error:
                raise ProtectedError(" ".join(error.messages), [self])
            return super().delete(*args, **kwargs)


def before_tracked_delete(sender, instance, **kwargs):
    try:
        _, ledger, record = ledger_context(instance, deleting=True)
    except ValidationError as error:
        raise ProtectedError(" ".join(error.messages), [instance])
    instance._deleting_ledger_context = ledger, record


def after_tracked_delete(sender, instance, **kwargs):
    ledger, record = instance._deleting_ledger_context
    record_event(instance, ledger, record, "deleted")


def protect_history_delete(sender, instance, **kwargs):
    raise ProtectedError(
        "Ledger, transfer and audit history cannot be deleted.", [instance]
    )


def attach_ledger_references(objects, model, user):
    from .models import LedgerRecord, MemberLedger, PaymentReceiptAllocation
    from .permissions import district_records

    rows = list(objects)
    allocation_field = (
        "child_transaction_id"
        if model._meta.model_name == "childdeposittransaction"
        else "transaction_id"
    )
    allocated = (
        set(
            PaymentReceiptAllocation.objects.filter(
                **{f"{allocation_field}__in": [obj.pk for obj in rows]}
            ).values_list(allocation_field, flat=True)
        )
        if model._meta.model_name in ("paymenttransaction", "childdeposittransaction")
        else set()
    )
    records = {
        record.source_id: record
        for record in LedgerRecord.objects.filter(
            source_model=model._meta.label_lower, source_id__in=[obj.pk for obj in rows]
        ).select_related("original_ledger")
    }
    visible = (
        set(
            district_records(user, MemberLedger)
            .filter(pk__in=[record.original_ledger_id for record in records.values()])
            .values_list("pk", flat=True)
        )
        if user.has_perm("members.view_memberledger")
        else set()
    )
    for obj in rows:
        record = records.get(obj.pk)
        obj.ledger_page = record.original_ledger if record else None
        obj.ledger_readonly = bool(
            obj.pk in allocated
            or (record and record.original_ledger.status == "transferred")
        )
        obj.can_view_ledger = bool(record and record.original_ledger_id in visible)
    if model._meta.model_name in ("paymenttransaction", "childdeposittransaction"):
        corrected = set(
            model.objects.filter(reverses_id__in=[obj.pk for obj in rows]).values_list(
                "reverses_id", flat=True
            )
        )
        for obj in rows:
            obj.can_correct = obj.pk not in corrected
    return rows


class LedgerActorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .audit_tracking import audit_request

        audit_token = audit_request.set(
            {"request_path": request.path[:500], "request_method": request.method[:10]}
        )
        token = ledger_actor.set(
            request.user if request.user.is_authenticated else None
        )
        try:
            if request.user.is_authenticated:
                from .status_services import apply_due_status_requests

                apply_due_status_requests()
            return self.get_response(request)
        finally:
            ledger_actor.reset(token)
            audit_request.reset(audit_token)
