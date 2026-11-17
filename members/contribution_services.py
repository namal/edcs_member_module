"""Exact proportional allocation, exception checks and atomic receipt posting."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .approval_scope import PAYMENT_EXCEPTIONS
from .approval_services import digest, execution
from .ledger_tracking import ledger_actor
from .models import (
    Member,
    PaymentPlan,
    PaymentReceipt,
    PaymentReceiptAllocation,
    PaymentTransaction,
    PaymentType,
)
from .permissions import require_member_access, require_permission

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999.99")


def received_money(value):
    try:
        value = Decimal(str(value))
        if (
            not value.is_finite()
            or not ZERO < value <= MAX_AMOUNT
            or value != value.quantize(CENT)
        ):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(
            "Enter a positive received amount with at most two decimals."
        )
    return value


def allocation_preview(member, received_on, amount, payment_type=None):
    """Integer-cent largest remainders; displayed percentages never drive posting."""
    amount = received_money(amount)
    if not isinstance(received_on, date) or received_on > timezone.localdate():
        raise ValidationError("Choose today or an earlier receipt date.")
    plan = PaymentPlan.objects.filter(member=member).first()
    if plan is None or plan.agreement_date > received_on:
        raise ValidationError(
            "Set a payment plan effective on or before the receipt date."
        )
    amounts = list(
        plan.amounts.filter(amount__gt=0)
        .select_related("payment_type")
        .order_by("payment_type__sort_order", "payment_type__name", "payment_type_id")
    )
    if not amounts:
        raise ValidationError(
            "The payment plan needs at least one positive contribution."
        )
    if payment_type is not None:
        amounts = [a for a in amounts if a.payment_type_id == payment_type.pk]
        if not amounts:
            raise ValidationError(
                "This category has no positive amount in the member's plan."
            )
    if any(not a.payment_type.is_active for a in amounts):
        raise ValidationError(
            "The plan includes an inactive payment category. Review the plan before allocating; amounts will not be silently redistributed."
        )
    ledger = member.ledgers.filter(status="active").first()
    if ledger is None:
        raise ValidationError(
            "Assign a current member ledger before receiving a payment."
        )
    if ledger.previous_ledger_id and received_on < ledger.opened_on:
        raise ValidationError(
            "The receipt date cannot precede the current transfer date."
        )
    total_cents = sum(int(a.amount * 100) for a in amounts)
    cents = int(amount * 100)
    parts = [divmod(cents * int(a.amount * 100), total_cents) for a in amounts]
    allocated = [part[0] for part in parts]
    remainder = cents - sum(allocated)
    order = sorted(range(len(amounts)), key=lambda i: (-parts[i][1], i))
    for i in order[:remainder]:
        allocated[i] += 1
    rows = [
        {
            "payment_type_id": a.payment_type_id,
            "name": a.payment_type.name,
            "configured": str(a.amount),
            "ratio": str(
                (a.amount * 100 / (Decimal(total_cents) / 100)).quantize(CENT)
            ),
            "amount": str(Decimal(allocated[i]) / 100),
            "below": Decimal(allocated[i]) / 100 < a.amount,
        }
        for i, a in enumerate(amounts)
    ]
    snapshot = {
        "member_id": member.pk,
        "district_id": member.district_id,
        "ledger_id": ledger.pk,
        "plan_id": plan.pk,
        "agreement_date": plan.agreement_date.isoformat(),
        "received_on": received_on.isoformat(),
        "received_amount": str(amount),
        "payment_type_id": payment_type.pk if payment_type else None,
        "plan_total": str(Decimal(total_cents) / 100),
        "rows": rows,
    }
    return {
        "plan": plan,
        "rows": rows,
        "total": amount,
        "plan_total": Decimal(total_cents) / 100,
        "requires_approval": any(row["below"] for row in rows),
        "snapshot": snapshot,
        "fingerprint": digest(snapshot),
    }


def allocation_summary(preview):
    return [
        {
            "label": row["name"],
            "value": f"Plan LKR {row['configured']}; allocated LKR {row['amount']}; ratio {row['ratio']}%"
            + (" — below plan" if row["below"] else ""),
        }
        for row in preview["rows"]
    ]


def exception_authorized(actor=None):
    if actor is not None and actor.is_active and actor.is_superuser:
        return True
    state = execution.get()
    return bool(
        state and state["seen"] and state["record"].module == PAYMENT_EXCEPTIONS
    )


def below_plan_amount(entry):
    if entry.credit is None or entry.credit <= 0:
        return None
    # Historical rate/interest journal postings are not member contributions.
    if getattr(entry, "_annual_interest_posting", False):
        return None
    configured = (
        PaymentPlan.objects.filter(
            member_id=entry.member_id, agreement_date__lte=entry.transaction_date
        )
        .values_list("pk", flat=True)
        .first()
    )
    if configured is None:
        return None
    from .models import PaymentPlanAmount

    amount = (
        PaymentPlanAmount.objects.filter(
            plan_id=configured, payment_type_id=entry.payment_type_id, amount__gt=0
        )
        .values_list("amount", flat=True)
        .first()
    )
    return amount if amount is not None and entry.credit < amount else None


def guard_receipt_transaction(entry, *, deleting=False):
    if (
        entry.pk
        and PaymentReceiptAllocation.objects.filter(transaction_id=entry.pk).exists()
    ):
        raise ValidationError(
            "Allocated receipt entries and their audit history cannot be edited or deleted."
        )
    if (
        deleting
        or ledger_actor.get() is None
        or exception_authorized(ledger_actor.get())
    ):
        return
    if entry.pk:
        old = PaymentTransaction.objects.get(pk=entry.pk)
        if all(
            getattr(old, f) == getattr(entry, f)
            for f in (
                "credit",
                "debit",
                "transaction_date",
                "member_id",
                "payment_type_id",
            )
        ):
            return
    if below_plan_amount(entry) is not None:
        raise ValidationError(
            "This below-plan payment must be approved through Payment exceptions before posting."
        )


def existing_receipt(actor, values):
    receipt = PaymentReceipt.objects.filter(
        operation_key=values["operation_key"]
    ).first()
    if receipt is None:
        return None
    match = (
        receipt.created_by_id == actor.pk
        and receipt.member_id == values["member"].pk
        and receipt.received_on == values["received_on"]
        and receipt.amount == values["amount"]
        and receipt.reference == values["reference"]
        and receipt.description == values["description"]
        and receipt.plan_snapshot["payment_type_id"]
        == (values["payment_type"].pk if values["payment_type"] else None)
    )
    if not match:
        raise ValidationError(
            "This payment reference was already used for a different receipt. Start a new contribution."
        )
    return receipt


@transaction.atomic
def post_receipt(actor, values, expected_fingerprint):
    require_permission(actor, PaymentTransaction, "add")
    member = Member.objects.select_for_update().get(pk=values["member"].pk)
    require_member_access(actor, member, lock=True)
    prior = existing_receipt(actor, values)
    if prior:
        return prior
    PaymentPlan.objects.select_for_update().filter(member=member).first()
    # Lock rate/category eligibility while validating and writing the allocations.
    list(
        PaymentType.objects.select_for_update().filter(
            plan_amounts__plan__member=member
        )
    )
    preview = allocation_preview(
        member, values["received_on"], values["amount"], values["payment_type"]
    )
    if preview["fingerprint"] != expected_fingerprint:
        raise ValidationError(
            "The payment plan or allocation changed. Preview the payment again."
        )
    if preview["requires_approval"] and not exception_authorized(actor):
        raise ValidationError(
            "A below-plan receipt cannot be posted until final approval."
        )
    state = execution.get()
    receipt = PaymentReceipt(
        operation_key=values["operation_key"],
        member=member,
        plan=preview["plan"],
        received_on=values["received_on"],
        amount=values["amount"],
        reference=values["reference"],
        description=values["description"],
        plan_snapshot=preview["snapshot"],
        created_by=actor,
        approval_request=state["record"] if state and exception_authorized() else None,
    )
    receipt._receipt_service_write = True
    receipt.full_clean()
    receipt.save()
    token = ledger_actor.set(actor)
    try:
        for row in preview["rows"]:
            amount = Decimal(row["amount"])
            entry = None
            if amount > ZERO:
                entry = PaymentTransaction(
                    member=member,
                    payment_type_id=row["payment_type_id"],
                    transaction_date=receipt.received_on,
                    credit=amount,
                    reference=receipt.reference or receipt.receipt_number,
                    description=f"{receipt.receipt_number}: {receipt.description}".strip(),
                    created_by=actor,
                )
                entry.full_clean()
                entry.save()
            allocation = PaymentReceiptAllocation(
                receipt=receipt,
                payment_type_id=row["payment_type_id"],
                configured_amount=Decimal(row["configured"]),
                amount=amount,
                transaction=entry,
            )
            allocation._receipt_service_write = True
            allocation.full_clean()
            allocation.save()
    finally:
        ledger_actor.reset(token)
    return receipt
