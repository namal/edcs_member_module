"""Reconcile and close account-years without replacing manual opening balances."""

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from .approval_services import digest
from .ledger_tracking import ledger_actor

ZERO = Decimal("0.00")


def validate_account_year(record):
    if record.closing_balance + record.annual_interest != record.next_opening_balance:
        raise ValidationError(
            "The final closing balance and next financial year's opening balance do not reconcile."
        )
    if record.year_opening_balance is not None:
        if (
            record.period_credit is None
            or record.period_debit is None
            or not record.opening_effective_on
        ):
            raise ValidationError(
                "The year's opening and movement snapshot is incomplete."
            )
        if (
            record.year_opening_balance + record.period_credit - record.period_debit
            != record.closing_balance
        ):
            raise ValidationError(
                "Opening balance plus credits minus debits does not match the closing balance."
            )


def validate_previous_close(record, others):
    previous = (
        others.filter(posted_at__isnull=False, year_start__lt=record.year_start)
        .order_by("-year_end")
        .first()
    )
    if previous is None:
        return
    if previous.year_end + timedelta(days=1) != record.year_start:
        raise ValidationError(
            "Close the intervening financial years first; a carry-forward cannot skip a year."
        )
    if (
        record.year_opening_balance is not None
        and record.opening_effective_on == record.year_start
        and record.year_opening_balance != previous.next_opening_balance
    ):
        raise ValidationError(
            "This year's opening balance differs from the previous year's final closing balance."
        )


def historical_opening(record):
    """Read old posted records without changing their snapshots or source balances."""
    from .models import OpeningBalanceAmount, PaymentTransaction

    if record.year_opening_balance is not None:
        return record.year_opening_balance, record.opening_effective_on
    if hasattr(record, "deposit_id"):
        deposit = record.deposit
        effective = max(record.year_start, deposit.opened_on)
        entries = deposit.transactions.filter(
            transaction_date__gte=deposit.opened_on, transaction_date__lt=effective
        )
        initial = deposit.opening_amount
    else:
        opening = (
            OpeningBalanceAmount.objects.filter(
                opening_balance__member_id=record.member_id,
                payment_type_id=record.payment_type_id,
                opening_balance__balance_date__lte=record.year_end,
            )
            .select_related("opening_balance")
            .first()
        )
        baseline = opening.opening_balance.balance_date if opening else None
        effective = max(record.year_start, baseline) if baseline else record.year_start
        initial = opening.amount if opening else ZERO
        entries = PaymentTransaction.objects.filter(
            member_id=record.member_id,
            payment_type_id=record.payment_type_id,
            transaction_date__lt=effective,
        )
        if baseline:
            entries = entries.filter(transaction_date__gte=baseline)
    amount = (
        initial
        + entries.aggregate(net=Sum(F("credit") - F("debit"), default=ZERO))["net"]
    )
    return amount, effective


def closing_preview(start, *, today=None):
    from .deposit_interest import add_months, calculation_fingerprint, interest_report
    from .child_interest import child_calculation_fingerprint, child_interest_report
    from .models import (
        ChildDeposit,
        ChildDepositInterestYear,
        DepositInterestYear,
        Member,
    )

    if (
        not isinstance(start, date)
        or start.day != 1
        or start.month != settings.FINANCIAL_YEAR_START_MONTH
        or not 1900 <= start.year <= 9998
    ):
        raise ValidationError("Choose a valid configured financial-year start.")
    today = today or timezone.localdate()
    end = add_months(start, 12) - timedelta(days=1)
    rows = []
    for group, source in (
        ("members", interest_report(Member.objects.all(), start, as_of=today)),
        (
            "children",
            child_interest_report(ChildDeposit.objects.all(), start, as_of=today),
        ),
    ):
        for item in source:
            record = item["record"]
            validate_account_year(record)
            peers = (
                ChildDepositInterestYear.objects.filter(deposit_id=record.deposit_id)
                if group == "children"
                else DepositInterestYear.objects.filter(
                    member_id=record.member_id, payment_type_id=record.payment_type_id
                )
            )
            validate_previous_close(record, peers.exclude(pk=record.pk))
            opening, effective = historical_opening(record)
            rows.append(
                {
                    "group": group,
                    "member_id": record.member_id,
                    "member_no": item["member_no"],
                    "member_name": item["full_name"],
                    "account": item.get("account_no", item["payment_type_name"]),
                    "type": item["payment_type_name"],
                    "record": record,
                    "opening": opening,
                    "opening_effective_on": effective,
                    "closing": record.next_opening_balance,
                    "fingerprint": (
                        child_calculation_fingerprint(record)
                        if group == "children"
                        else calculation_fingerprint(record)
                    ),
                }
            )
    rows.sort(
        key=lambda item: (
            item["member_id"],
            item["group"],
            item["account"],
            item["type"],
        )
    )
    reviewed = [
        {
            "group": row["group"],
            "member_id": row["member_id"],
            "account_id": (
                row["record"].deposit_id
                if row["group"] == "children"
                else row["record"].payment_type_id
            ),
            "fingerprint": row["fingerprint"],
        }
        for row in rows
    ]
    reviewed.sort(
        key=lambda item: (item["member_id"], item["group"], item["account_id"])
    )
    return {
        "start": start,
        "end": end,
        "next_start": end + timedelta(days=1),
        "rows": rows,
        "total_closing": sum((row["closing"] for row in rows), ZERO),
        "total_interest": sum((row["record"].annual_interest for row in rows), ZERO),
        "pending_count": sum(not row["record"].posted_at for row in rows),
        "can_close": end < today and bool(rows),
        "fingerprint": digest({"start": start, "end": end, "accounts": reviewed}),
    }


@transaction.atomic
def close_financial_year(start, *, actor, expected_fingerprint, reason, today=None):
    from .models import FinancialYearClosing, Member, PaymentType
    from .deposit_interest import post_interest_year, save_interest_year
    from .child_interest import post_child_interest_year, save_child_interest_year

    if not actor or not actor.is_active or not actor.is_superuser:
        raise PermissionDenied(
            "Only an active administrator can approve a whole-system financial year-end closing."
        )
    if not reason or not reason.strip():
        raise ValidationError(
            "Record the administrator's reason for approving the year-end closing."
        )
    # All existing account writers serialize on the same member rows.
    list(Member.objects.select_for_update().order_by("pk"))
    list(PaymentType.objects.select_for_update().order_by("pk"))
    preview = closing_preview(start, today=today)
    if not preview["can_close"]:
        raise ValidationError(
            "Close only an ended financial year with at least one established account."
        )
    if preview["fingerprint"] != expected_fingerprint:
        raise ValidationError(
            "Accounts or calculations changed after review. Preview the year-end closing again."
        )
    prior = FinancialYearClosing.objects.filter(
        year_start=start, calculation_hash=expected_fingerprint
    ).first()
    if prior:
        return prior
    accounts = []
    newly_posted = 0
    token = ledger_actor.set(actor)
    try:
        for row in preview["rows"]:
            record = row["record"]
            if not record.posted_at:
                if row["group"] == "children":
                    record = save_child_interest_year(
                        record.deposit, start, today=today
                    )
                    record = post_child_interest_year(
                        record.pk,
                        actor=actor,
                        today=today,
                        expected_fingerprint=row["fingerprint"],
                    )
                else:
                    record = save_interest_year(
                        record.member, record.payment_type, start, today=today
                    )
                    record = post_interest_year(
                        record.pk,
                        actor=actor,
                        today=today,
                        expected_fingerprint=row["fingerprint"],
                    )
                newly_posted += 1
            validate_account_year(record)
            accounts.append(
                {
                    "group": row["group"],
                    "record_id": record.pk,
                    "member_id": record.member_id,
                    "member_no": row["member_no"],
                    "member_name": row["member_name"],
                    "account": row["account"],
                    "type": row["type"],
                    "opening": str(row["opening"]),
                    "opening_effective_on": row["opening_effective_on"].isoformat(),
                    "closing": str(record.final_closing_balance),
                    "next_opening": str(record.next_opening_balance),
                    "annual_interest": str(record.annual_interest),
                }
            )
        closing = FinancialYearClosing(
            year_start=start,
            year_end=preview["end"],
            calculation_hash=expected_fingerprint,
            account_count=len(accounts),
            newly_posted_count=newly_posted,
            total_closing=preview["total_closing"],
            total_interest=preview["total_interest"],
            accounts=accounts,
            approved_by=actor,
            reason=reason.strip(),
        )
        closing._year_end_service_write = True
        closing.full_clean()
        closing.save()
    finally:
        ledger_actor.reset(token)
    return closing
