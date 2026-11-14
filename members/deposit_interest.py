"""Quarterly minimum month-end interest, capitalized once at financial year-end."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from .models import (
    DepositInterestYear,
    Member,
    OpeningBalanceAmount,
    PaymentTransaction,
    PaymentType,
)
from .transaction_balances import balance_report

ZERO = Decimal("0.00")
PENNY = Decimal("0.01")


def add_months(day, months):
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def financial_year_start(year):
    return date(year, settings.FINANCIAL_YEAR_START_MONTH, 1)


def current_financial_year(today=None):
    today = today or timezone.localdate()
    return today.year - (today.month < settings.FINANCIAL_YEAR_START_MONTH)


def interest_report(members, start, *, payment_type=None, as_of=None):
    """Read-only bulk calculation. Callers supply a district-scoped member queryset.

    A missing month-end before the account exists is zero. Negative balances earn
    zero interest. Only fully completed quarters contribute to annual interest.
    Posted years always display their immutable snapshot, never a live revaluation.
    """
    as_of = as_of or timezone.localdate()
    end = add_months(start, 12) - timedelta(days=1)
    through = min(as_of, end)
    if through < start:
        return []
    month_ends = [
        add_months(start, index).replace(
            day=monthrange(
                add_months(start, index).year, add_months(start, index).month
            )[1]
        )
        for index in range(12)
    ]
    monthly = []
    identities = {}
    for day in month_ends:
        values = {}
        if day <= through:
            for item in balance_report(members, None, day, payment_type=payment_type):
                key = (item["member_id"], item["payment_type_id"])
                values[key] = item["final_balance"]
                identities[key] = item
        monthly.append(values)
    closing = {}
    for item in balance_report(members, None, through, payment_type=payment_type):
        key = (item["member_id"], item["payment_type_id"])
        identities[key] = item
        closing[key] = item["final_balance"]
    saved = DepositInterestYear.objects.filter(
        member__in=members, year_start=start
    ).select_related("member", "payment_type")
    if payment_type:
        saved = saved.filter(payment_type=payment_type)
    snapshots = {(item.member_id, item.payment_type_id): item for item in saved}
    for key, item in snapshots.items():
        identities.setdefault(
            key,
            {
                "member_id": item.member_id,
                "member_no": item.member.member_no,
                "full_name": item.member.full_name,
                "payment_type_id": item.payment_type_id,
                "payment_type_name": item.payment_type.name,
                "is_active": item.payment_type.is_active,
            },
        )
    types = PaymentType.objects.in_bulk({key[1] for key in identities})
    pension_dates = dict(members.values_list("pk", "pension_date"))
    rows = []
    for key, identity in identities.items():
        previous = snapshots.get(key)
        if previous and previous.posted_at:
            record = previous
        else:
            # Once the year ends, retain its last saved annual rate. A new year's
            # bank rate must not revalue the previous year's accrued interest.
            rate = (
                previous.annual_rate
                if previous and end < as_of
                else types[key[1]].interest_rate
            )
            quarters = []
            total = ZERO
            for quarter in range(4):
                first = quarter * 3
                complete = month_ends[first + 2] <= through
                months = [
                    {
                        "date": month_ends[index].isoformat(),
                        "balance": (
                            str(monthly[index].get(key, ZERO))
                            if month_ends[index] <= through
                            else None
                        ),
                    }
                    for index in range(first, first + 3)
                ]
                minimum = (
                    min(Decimal(item["balance"]) for item in months)
                    if complete
                    else None
                )
                pension_date = pension_dates.get(key[0])
                excluded = bool(
                    pension_date
                    and add_months(start, first)
                    <= pension_date
                    <= month_ends[first + 2]
                )
                amount = (
                    (max(minimum, ZERO) * rate / Decimal("400")).quantize(
                        PENNY, rounding=ROUND_HALF_UP
                    )
                    if complete
                    else ZERO
                )
                if complete and excluded:
                    amount = ZERO
                total += amount
                quarters.append(
                    {
                        "number": quarter + 1,
                        "months": months,
                        "complete": complete,
                        "minimum": str(minimum) if complete else None,
                        "interest": str(amount) if complete else None,
                        "excluded": excluded,
                        "exclusion_reason": (
                            f"Member pension date on {pension_date.isoformat()}"
                            if excluded
                            else ""
                        ),
                    }
                )
            principal = closing.get(key, ZERO)
            record = previous or DepositInterestYear(
                member_id=key[0], payment_type_id=key[1], year_start=start
            )
            for name, value in {
                "year_end": end,
                "annual_rate": rate,
                "quarters": quarters,
                "calculated_through": through,
                "annual_interest": total,
                "closing_balance": principal,
                "next_opening_balance": principal + total,
            }.items():
                setattr(record, name, value)
        rows.append(
            {
                **identity,
                "record": record,
                "quarterly_rate": record.annual_rate / Decimal("4"),
                "can_post": end < as_of and not record.posted_at,
                "next_year_start": end + timedelta(days=1),
            }
        )
    return sorted(
        rows,
        key=lambda row: (row["member_no"], row["member_id"], row["payment_type_name"]),
    )


@transaction.atomic
def save_interest_year(member, payment_type, start, *, today=None):
    Member.objects.select_for_update().get(pk=member.pk)
    existing = DepositInterestYear.objects.filter(
        member=member, payment_type=payment_type, year_start=start
    ).first()
    if existing and existing.posted_at:
        return existing
    rows = interest_report(
        Member.objects.filter(pk=member.pk),
        start,
        payment_type=payment_type,
        as_of=today,
    )
    if not rows:
        raise ValidationError(
            "This account has no opening balance or transactions in this financial year."
        )
    record = rows[0]["record"]
    if DepositInterestYear.objects.filter(
        member=member,
        payment_type=payment_type,
        posted_at__isnull=False,
        year_end__gte=start,
        year_start__lte=record.year_end,
    ).exists():
        raise ValidationError(
            "This financial year overlaps an already posted interest year."
        )
    record.full_clean()
    record.save()
    return record


def calculation_fingerprint(record):
    values = {
        name: str(getattr(record, name))
        for name in (
            "member_id",
            "payment_type_id",
            "year_start",
            "year_end",
            "annual_rate",
            "annual_interest",
            "closing_balance",
            "next_opening_balance",
        )
    }
    values["quarters"] = record.quarters
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


@transaction.atomic
def post_interest_year(pk, *, actor=None, today=None, expected_fingerprint=None):
    record = DepositInterestYear.objects.get(pk=pk)
    Member.objects.select_for_update().get(pk=record.member_id)
    record = (
        DepositInterestYear.objects.select_for_update()
        .select_related("member", "payment_type")
        .get(pk=pk)
    )
    if record.posted_at:
        return record
    today = today or timezone.localdate()
    if today <= record.year_end:
        raise ValidationError(
            "Annual interest can be posted only after the financial year has ended."
        )
    others = DepositInterestYear.objects.filter(
        member_id=record.member_id, payment_type_id=record.payment_type_id
    ).exclude(pk=record.pk)
    if others.filter(
        year_start__gt=record.year_start, posted_at__isnull=False
    ).exists():
        raise ValidationError(
            "A later year is already posted. Earlier interest cannot be inserted retrospectively."
        )
    if others.filter(year_start__lt=record.year_start, posted_at__isnull=True).exists():
        raise ValidationError(
            "Post or remove earlier draft interest years before closing this year."
        )
    record = save_interest_year(
        record.member, record.payment_type, record.year_start, today=today
    )
    if (
        expected_fingerprint is not None
        and calculation_fingerprint(record) != expected_fingerprint
    ):
        raise ValidationError(
            "Balances or the annual rate changed after your review. Review the year-end calculation again before posting."
        )
    if record.annual_interest > ZERO:
        if record.annual_interest > Decimal("9999999999.99"):
            raise ValidationError(
                "Annual interest exceeds the ledger entry limit. Contact an administrator."
            )
        entry = PaymentTransaction(
            member=record.member,
            payment_type=record.payment_type,
            transaction_date=record.year_end,
            credit=record.annual_interest,
            created_by=actor,
            reference=f"INTEREST-{record.year_start.isoformat()}",
            description=f"Annual deposit interest: four quarterly minimum month-end balances, {record.annual_rate}% annual rate.",
        )
        entry.full_clean()
        entry.save()
        record.ledger_transaction = entry
    record.posted_at = timezone.now()
    record.posted_by = actor
    record.save()
    return record


def _closed_years(member_id, payment_type_id=None):
    records = DepositInterestYear.objects.filter(
        member_id=member_id, posted_at__isnull=False
    )
    return (
        records.filter(payment_type_id=payment_type_id) if payment_type_id else records
    )


def guard_interest_transaction(entry, *, deleting=False):
    if not entry.member_id or not entry.payment_type_id or not entry.transaction_date:
        return
    if (
        entry.pk
        and DepositInterestYear.objects.filter(
            ledger_transaction_id=entry.pk, posted_at__isnull=False
        ).exists()
    ):
        raise ValidationError(
            "Annual interest postings cannot be edited or deleted through Debit / Credit."
        )
    original = (
        PaymentTransaction.objects.filter(pk=entry.pk).first() if entry.pk else None
    )
    for candidate in (entry, original):
        if (
            candidate
            and _closed_years(candidate.member_id, candidate.payment_type_id)
            .filter(year_end__gte=candidate.transaction_date)
            .exists()
        ):
            if (
                not deleting
                and original
                and all(
                    getattr(original, name) == getattr(entry, name)
                    for name in (
                        "member_id",
                        "payment_type_id",
                        "transaction_date",
                        "debit",
                        "credit",
                    )
                )
            ):
                return
            raise ValidationError(
                "This account's interest year is closed. Its earlier amounts and dates cannot be changed."
            )


def guard_interest_opening(balance):
    if not balance.member_id:
        return
    original = (
        type(balance).objects.filter(pk=balance.pk).first() if balance.pk else None
    )
    protected = _closed_years(balance.member_id).exists() or (
        original and _closed_years(original.member_id).exists()
    )
    if protected and (
        original is None
        or original.balance_date != balance.balance_date
        or original.member_id != balance.member_id
    ):
        raise ValidationError(
            "The opening date is protected by a posted interest year."
        )


def guard_interest_opening_amount(amount, *, deleting=False):
    original = (
        OpeningBalanceAmount.objects.filter(pk=amount.pk).first() if amount.pk else None
    )
    protected = _closed_years(
        amount.opening_balance.member_id, amount.payment_type_id
    ).exists() or (
        original
        and _closed_years(
            original.opening_balance.member_id, original.payment_type_id
        ).exists()
    )
    if not protected:
        return
    if (
        deleting
        or original is None
        or original.amount != amount.amount
        or original.opening_balance_id != amount.opening_balance_id
        or original.payment_type_id != amount.payment_type_id
    ):
        raise ValidationError(
            "This opening amount is protected by a posted interest year."
        )


def protect_interest_opening_delete(balance):
    if _closed_years(balance.member_id).exists():
        raise ProtectedError(
            "Opening balances are protected by a posted interest year.", [balance]
        )


def guard_member_pension(member):
    """Pension dates may not rewrite an already posted year's eligibility."""
    if not member.pk:
        return
    previous = Member.objects.filter(pk=member.pk).values("pension_date").first()
    if previous is None:
        return
    field = Member._meta.get_field("pension_date")
    new_date = field.to_python(member.pension_date)
    old_date = previous["pension_date"]
    if new_date == old_date:
        return
    for day in (old_date, new_date):
        if (
            day
            and DepositInterestYear.objects.filter(
                member_id=member.pk,
                posted_at__isnull=False,
                year_start__lte=day,
                year_end__gte=day,
            ).exists()
        ):
            raise ValidationError(
                {
                    "pension_date": "The pension date would change a posted interest year and cannot be changed."
                }
            )
