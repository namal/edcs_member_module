"""Explicit contribution allocation, exception checks and atomic receipt posting."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .approval_scope import PAYMENT_EXCEPTIONS
from .approval_services import digest, execution
from .ledger_tracking import ledger_actor
from .models import (
    ChildDeposit,
    ChildDepositTransaction,
    ChildDepositType,
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


def allocation_input(values):
    """Read only the category amounts submitted by the contribution form."""
    return {
        key.removeprefix("allocated_"): value
        for key, value in values.items()
        if key.startswith("allocated_")
    } or None


def allocation_money(value):
    try:
        value = Decimal(str(value))
        if (
            not value.is_finite()
            or not ZERO <= value <= MAX_AMOUNT
            or value != value.quantize(CENT)
        ):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(
            "Enter a nonnegative allocated amount with at most two decimals."
        )
    return value.quantize(CENT)


def normalize_remaining_allocations(allocations):
    if allocations is None:
        return []
    if not isinstance(allocations, (list, tuple)) or len(allocations) > 100:
        raise ValidationError("Submit at most 100 remaining balance allocations.")
    result = []
    for row in allocations:
        if not isinstance(row, dict) or set(row) not in (
            {"payment_type_id", "amount"},
            {"child_deposit_id", "child_deposit_type_id", "amount"},
        ):
            raise ValidationError(
                "Each remaining allocation needs an account and amount."
            )
        ids = {key: str(value) for key, value in row.items() if key != "amount"}
        if any(not value.isdigit() or int(value) <= 0 for value in ids.values()):
            raise ValidationError(
                "Select a valid account for each remaining allocation."
            )
        amount = allocation_money(row["amount"])
        if amount <= ZERO:
            raise ValidationError(
                "Each remaining account allocation must be greater than zero."
            )
        result.append(
            {**{key: int(value) for key, value in ids.items()}, "amount": amount}
        )
    return result


def allocation_preview(
    member,
    received_on,
    amount,
    payment_type=None,
    allocations=None,
    remaining_payment_type=None,
    remaining_allocations=None,
):
    """Use entered amounts exactly; plan amounts are the initial defaults."""
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
    configured_amounts = {a.payment_type_id: a.amount for a in amounts}
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
    plan_total = sum((a.amount for a in amounts), ZERO)
    if allocations is None:
        allocated = {
            str(a.payment_type_id): amount if payment_type else a.amount
            for a in amounts
        }
    else:
        allocated = {
            str(key): allocation_money(value) for key, value in allocations.items()
        }
        if len(allocated) != len(allocations) or set(allocated) != {
            str(a.payment_type_id) for a in amounts
        }:
            raise ValidationError(
                "Enter allocations for exactly the payment categories in this member's selected plan."
            )
        if amount == plan_total and any(
            allocated[str(a.payment_type_id)] != a.amount for a in amounts
        ):
            raise ValidationError(
                "When the received amount equals the plan total, use the configured plan amounts."
            )
    allocated_total = sum(allocated.values(), ZERO)
    rows = [
        {
            "payment_type_id": a.payment_type_id,
            "name": a.payment_type.name,
            "configured": str(a.amount),
            "amount": str(allocated[str(a.payment_type_id)]),
            "below": allocated[str(a.payment_type_id)] < a.amount,
        }
        for a in amounts
    ]
    difference = amount - allocated_total
    credit_rows = [dict(row) for row in rows]
    entered_remaining = normalize_remaining_allocations(remaining_allocations)
    if entered_remaining and remaining_payment_type is not None:
        raise ValidationError(
            "Use the account allocation rows or the single remaining account, not both."
        )
    remaining_rows = []
    remaining_total = sum((row["amount"] for row in entered_remaining), ZERO)
    if remaining_total > max(difference, ZERO):
        raise ValidationError(
            "Remaining account allocations exceed the available contribution balance."
        )
    accounts = {
        account.pk: account
        for account in PaymentType.objects.filter(
            pk__in=[
                row["payment_type_id"]
                for row in entered_remaining
                if "payment_type_id" in row
            ],
            is_active=True,
        )
    }
    child_accounts = {
        account.pk: account
        for account in ChildDeposit.objects.filter(
            pk__in=[
                row["child_deposit_id"]
                for row in entered_remaining
                if "child_deposit_id" in row
            ],
            child__member=member,
            child__is_active=True,
            is_active=True,
            deposit_type__is_active=True,
            opened_on__lte=received_on,
        ).select_related("child", "deposit_type")
    }
    category_totals = {row["payment_type_id"]: Decimal(row["amount"]) for row in rows}
    for row in entered_remaining:
        if "child_deposit_id" in row:
            continue
        category_totals[row["payment_type_id"]] = (
            category_totals.get(row["payment_type_id"], ZERO) + row["amount"]
        )
    running_balance = difference
    for position, entered in enumerate(entered_remaining, 1):
        if "child_deposit_id" in entered:
            account = child_accounts.get(entered["child_deposit_id"])
            if (
                account is None
                or account.deposit_type_id != entered["child_deposit_type_id"]
            ):
                raise ValidationError(
                    "Choose an active child deposit account belonging to this member, of the selected type, opened on or before the receipt date."
                )
            running_balance -= entered["amount"]
            row = {
                "kind": "remaining",
                "position": position,
                "child_deposit_id": account.pk,
                "child_deposit_type_id": account.deposit_type_id,
                "child_id": account.child_id,
                "account_no": account.account_no,
                "opened_on": account.opened_on.isoformat(),
                "annual_rate": str(account.rate_on(received_on)),
                "name": f"{account.deposit_type.name} · {account.child.name} · {account.account_no}",
                "configured": "0.00",
                "amount": str(entered["amount"]),
                "remaining_balance": str(running_balance),
                "below": False,
            }
            remaining_rows.append(row)
            credit_rows.append(row)
            continue
        account = accounts.get(entered["payment_type_id"])
        if account is None:
            raise ValidationError(
                "Choose an active account for every remaining balance allocation."
            )
        running_balance -= entered["amount"]
        row = {
            "kind": "remaining",
            "position": position,
            "payment_type_id": account.pk,
            "name": account.name,
            "configured": str(configured_amounts.get(account.pk, ZERO)),
            "amount": str(entered["amount"]),
            "remaining_balance": str(running_balance),
            "below": category_totals[account.pk]
            < configured_amounts.get(account.pk, ZERO),
        }
        remaining_rows.append(row)
        credit_rows.append(row)
    if remaining_payment_type is not None:
        remaining_payment_type = PaymentType.objects.filter(
            pk=remaining_payment_type.pk, is_active=True
        ).first()
        if remaining_payment_type is None:
            raise ValidationError(
                "Choose an active payment type for the remaining balance."
            )
        if difference > ZERO:
            existing = next(
                (
                    row
                    for row in credit_rows
                    if row.get("payment_type_id") == remaining_payment_type.pk
                ),
                None,
            )
            if existing:
                existing["amount"] = str(Decimal(existing["amount"]) + difference)
            else:
                configured = configured_amounts.get(remaining_payment_type.pk, ZERO)
                credit_rows.append(
                    {
                        "payment_type_id": remaining_payment_type.pk,
                        "name": remaining_payment_type.name,
                        "configured": str(configured),
                        "amount": str(difference),
                        "below": ZERO < difference < configured,
                    }
                )
    snapshot = {
        "member_id": member.pk,
        "district_id": member.district_id,
        "ledger_id": ledger.pk,
        "plan_id": plan.pk,
        "agreement_date": plan.agreement_date.isoformat(),
        "received_on": received_on.isoformat(),
        "received_amount": str(amount),
        "payment_type_id": payment_type.pk if payment_type else None,
        "plan_total": str(plan_total),
        "allocation_method": (
            "plan_amounts" if amount == plan_total else "entered_amounts"
        ),
        "rows": credit_rows,
        "plan_allocations": rows,
        "remaining_payment_type_id": (
            remaining_payment_type.pk if remaining_payment_type else None
        ),
        "remaining_payment_type_name": (
            remaining_payment_type.name if remaining_payment_type else ""
        ),
        "remaining_amount": str(max(difference, ZERO)),
        "remaining_allocations": remaining_rows,
    }
    return {
        "plan": plan,
        "rows": rows,
        "total": amount,
        "allocated_total": allocated_total,
        "difference": difference,
        "remaining_payment_type": remaining_payment_type,
        "remaining_rows": remaining_rows,
        "remaining_total": remaining_total,
        "remaining_balance": (
            difference - remaining_total if not remaining_payment_type else ZERO
        ),
        "credit_rows": credit_rows,
        "is_balanced": difference == ZERO
        or (
            difference > ZERO
            and (remaining_payment_type is not None or remaining_total == difference)
        ),
        "manual_allocation": amount != plan_total,
        "plan_total": plan_total,
        "requires_approval": any(row["below"] for row in credit_rows),
        "snapshot": snapshot,
        "fingerprint": digest(snapshot),
    }


def allocation_summary(preview):
    return [
        {
            "label": row["name"],
            "value": (
                f"Remaining balance allocation LKR {row['amount']}; balance left LKR {row['remaining_balance']}"
                if row.get("kind") == "remaining"
                else f"Plan LKR {row['configured']}; allocated LKR {row['amount']}"
            )
            + (" — below plan" if row["below"] else ""),
        }
        for row in preview["credit_rows"]
    ]


def exception_authorized(actor=None):
    if actor is not None and actor.is_active and actor.is_superuser:
        return True
    state = execution.get()
    return bool(
        state and state["seen"] and state["record"].module == PAYMENT_EXCEPTIONS
    )


def below_plan_amount(entry):
    if getattr(entry, "reverses_id", None):
        return None  # Opposite corrections restore an existing movement, rather than receive a contribution.
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
    credit = getattr(entry, "_receipt_category_total", entry.credit)
    return amount if amount is not None and credit < amount else None


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


def guard_child_receipt_transaction(entry):
    if (
        entry.pk
        and PaymentReceiptAllocation.objects.filter(
            child_transaction_id=entry.pk
        ).exists()
    ):
        raise ValidationError(
            "Allocated receipt entries and their audit history cannot be edited or deleted."
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
        and receipt.plan_snapshot.get("remaining_payment_type_id")
        == (
            values["remaining_payment_type"].pk
            if values.get("remaining_payment_type")
            else None
        )
    )
    entered = allocation_input(values)
    match = match and normalize_remaining_allocations(
        values.get("remaining_allocations")
    ) == normalize_remaining_allocations(
        [
            (
                {
                    "child_deposit_id": row["child_deposit_id"],
                    "child_deposit_type_id": row["child_deposit_type_id"],
                    "amount": row["amount"],
                }
                if row.get("child_deposit_id")
                else {
                    "payment_type_id": row["payment_type_id"],
                    "amount": row["amount"],
                }
            )
            for row in receipt.plan_snapshot.get("remaining_allocations", [])
        ]
    )
    if entered is not None:
        match = match and {
            str(key): allocation_money(value) for key, value in entered.items()
        } == {
            str(row["payment_type_id"]): Decimal(row["amount"])
            for row in receipt.plan_snapshot.get(
                "plan_allocations", receipt.plan_snapshot["rows"]
            )
        }
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
    entered_remaining = normalize_remaining_allocations(
        values.get("remaining_allocations")
    )
    child_ids = [
        row["child_deposit_id"]
        for row in entered_remaining
        if "child_deposit_id" in row
    ]
    if child_ids:
        require_permission(actor, ChildDeposit, "add")
        deposits = list(
            ChildDeposit.objects.select_for_update().filter(
                pk__in=child_ids, child__member=member
            )
        )
        list(
            ChildDepositType.objects.select_for_update().filter(
                pk__in=[deposit.deposit_type_id for deposit in deposits]
            )
        )
    prior = existing_receipt(actor, values)
    if prior:
        return prior
    PaymentPlan.objects.select_for_update().filter(member=member).first()
    # Lock rate/category eligibility while validating and writing the allocations.
    type_ids = set(
        PaymentType.objects.filter(plan_amounts__plan__member=member).values_list(
            "pk", flat=True
        )
    )
    if values.get("remaining_payment_type"):
        type_ids.add(values["remaining_payment_type"].pk)
    type_ids.update(
        row["payment_type_id"] for row in entered_remaining if "payment_type_id" in row
    )
    list(PaymentType.objects.select_for_update().filter(pk__in=type_ids))
    preview = allocation_preview(
        member,
        values["received_on"],
        values["amount"],
        values["payment_type"],
        allocation_input(values),
        remaining_payment_type=values.get("remaining_payment_type"),
        remaining_allocations=values.get("remaining_allocations"),
    )
    if preview["fingerprint"] != expected_fingerprint:
        raise ValidationError(
            "The payment plan or allocation changed. Preview the payment again."
        )
    if not preview["is_balanced"]:
        raise ValidationError(
            "The allocated total must equal the received contribution exactly."
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
        for row in preview["credit_rows"]:
            amount = Decimal(row["amount"])
            entry = None
            child_entry = None
            if row.get("child_deposit_id"):
                deposit = ChildDeposit.objects.select_related(
                    "child", "deposit_type"
                ).get(pk=row["child_deposit_id"])
                child_entry = ChildDepositTransaction(
                    deposit=deposit,
                    transaction_date=receipt.received_on,
                    credit=amount,
                    interest_rate=deposit.rate_on(receipt.received_on),
                    reference=receipt.reference or receipt.receipt_number,
                    description=f"{receipt.receipt_number}: Remaining contribution allocation. {receipt.description}".strip(),
                    created_by=actor,
                )
                child_entry.full_clean()
                child_entry.save()
            elif amount > ZERO:
                entry = PaymentTransaction(
                    member=member,
                    payment_type_id=row["payment_type_id"],
                    transaction_date=receipt.received_on,
                    credit=amount,
                    reference=receipt.reference or receipt.receipt_number,
                    description=(
                        f"{receipt.receipt_number}: "
                        + (
                            "Remaining balance allocation. "
                            if row.get("kind") == "remaining"
                            else ""
                        )
                        + receipt.description
                    ).strip(),
                    created_by=actor,
                )
                if row.get("kind") == "remaining":
                    entry._receipt_category_total = sum(
                        (
                            Decimal(item["amount"])
                            for item in preview["credit_rows"]
                            if item.get("payment_type_id") == row["payment_type_id"]
                        ),
                        ZERO,
                    )
                entry.full_clean()
                entry.save()
            allocation = PaymentReceiptAllocation(
                receipt=receipt,
                payment_type_id=row.get("payment_type_id"),
                configured_amount=Decimal(row["configured"]),
                amount=amount,
                transaction=entry,
                child_transaction=child_entry,
                kind=row.get("kind", "standard"),
                position=row.get("position", 0),
            )
            allocation._receipt_service_write = True
            allocation.full_clean()
            allocation.save()
    finally:
        ledger_actor.reset(token)
    return receipt
