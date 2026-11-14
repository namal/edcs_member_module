"""Quarterly child-account interest using the same minimum month-end rule."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from .deposit_interest import (
    ZERO,
    PENNY,
    ROUND_HALF_UP,
    add_months,
    calculation_fingerprint,
)
from .models import (
    Child,
    ChildDeposit,
    ChildDepositInterestYear,
    ChildDepositTransaction,
    Member,
)


def child_rate_rules(deposit):
    return {
        "birth_date": deposit.child.date_of_birth.isoformat(),
        "age_threshold": deposit.deposit_type.age_threshold,
        "below": str(deposit.deposit_type.rate_below_threshold),
        "from": str(deposit.deposit_type.rate_from_threshold),
        "method": "exclude_transition_quarter",
    }


def threshold_birthday(rules):
    born = date.fromisoformat(rules["birth_date"])
    year = born.year + rules["age_threshold"]
    try:
        return born.replace(year=year)
    except ValueError:
        # Child.age_on changes a leap-day child's age on 1 March in non-leap years.
        return date(year, 3, 1)


def quarter_rate(rules, start, end):
    birthday = threshold_birthday(rules)
    below, above = Decimal(rules["below"]), Decimal(rules["from"])
    if start <= birthday <= end:
        return None, []
    rate = above if birthday < start else below
    return rate, [
        {"start": start.isoformat(), "end": end.isoformat(), "annual_rate": str(rate)}
    ]


def child_interest_report(deposits, start, *, as_of=None):
    """Read-only report. The caller must scope deposits to authorized districts."""
    as_of = as_of or timezone.localdate()
    end = add_months(start, 12) - timedelta(days=1)
    through = min(end, as_of)
    if through < start:
        return []
    deposits = deposits.filter(opened_on__lte=through).select_related(
        "child__member", "deposit_type"
    )
    saved = {
        item.deposit_id: item
        for item in ChildDepositInterestYear.objects.filter(
            deposit__in=deposits, year_start=start
        )
    }
    events = {}
    entries = (
        ChildDepositTransaction.objects.filter(
            deposit__in=deposits,
            transaction_date__lte=through,
            transaction_date__gte=F("deposit__opened_on"),
        )
        .values("deposit_id", "transaction_date")
        .annotate(net=Sum(F("credit") - F("debit")))
        .order_by("transaction_date")
    )
    for item in entries:
        events.setdefault(item["deposit_id"], []).append(
            (item["transaction_date"], item["net"])
        )
    ends = [
        add_months(start, i).replace(
            day=monthrange(add_months(start, i).year, add_months(start, i).month)[1]
        )
        for i in range(12)
    ]
    rows = []
    for deposit in deposits:
        previous = saved.get(deposit.pk)
        if previous and previous.posted_at:
            record = previous
        else:
            rules = (
                dict(previous.rate_rules)
                if previous and end < as_of
                else child_rate_rules(deposit)
            )
            rules["method"] = "exclude_transition_quarter"
            rules["birth_date"] = deposit.child.date_of_birth.isoformat()
            movement = events.get(deposit.pk, [])
            index = 0
            balance = Decimal(deposit.opening_amount)
            monthly = []
            for day in ends:
                if day > through:
                    monthly.append(None)
                    continue
                while index < len(movement) and movement[index][0] <= day:
                    balance += movement[index][1]
                    index += 1
                monthly.append(balance if day >= deposit.opened_on else ZERO)
            while index < len(movement):
                balance += movement[index][1]
                index += 1
            total = ZERO
            quarters = []
            for number in range(4):
                first = number * 3
                complete = ends[first + 2] <= through
                rate, segments = quarter_rate(
                    rules, add_months(start, first), ends[first + 2]
                )
                excluded = (
                    add_months(start, first)
                    <= threshold_birthday(rules)
                    <= ends[first + 2]
                )
                minimum = min(monthly[first : first + 3]) if complete else None
                amount = (
                    (max(minimum, ZERO) * rate / Decimal("400")).quantize(
                        PENNY, rounding=ROUND_HALF_UP
                    )
                    if complete and rate is not None
                    else None
                )
                if complete and minimum <= ZERO:
                    amount = ZERO
                if complete and excluded:
                    amount = ZERO
                if amount is not None:
                    total += amount
                quarters.append(
                    {
                        "number": number + 1,
                        "months": [
                            {
                                "date": ends[i].isoformat(),
                                "balance": (
                                    str(monthly[i]) if monthly[i] is not None else None
                                ),
                            }
                            for i in range(first, first + 3)
                        ],
                        "complete": complete,
                        "minimum": str(minimum) if complete else None,
                        "interest": str(amount) if amount is not None else None,
                        "annual_rate": str(rate) if rate is not None else None,
                        "rate_segments": segments,
                        "rate_pending": False,
                        "excluded": excluded,
                        "exclusion_reason": (
                            f"Age-threshold birthday on {threshold_birthday(rules).isoformat()}"
                            if excluded
                            else ""
                        ),
                    }
                )
            annual_rate = Decimal(
                rules["from"] if threshold_birthday(rules) <= end else rules["below"]
            )
            record = previous or ChildDepositInterestYear(
                deposit=deposit, year_start=start
            )
            for name, value in {
                "year_end": end,
                "annual_rate": annual_rate,
                "rate_rules": rules,
                "quarters": quarters,
                "calculated_through": through,
                "annual_interest": total,
                "closing_balance": balance,
                "next_opening_balance": balance + total,
            }.items():
                setattr(record, name, value)
        record.deposit = deposit
        rows.append(
            {
                "account_group": "children",
                "member_id": deposit.child.member_id,
                "member_no": deposit.child.member.member_no,
                "full_name": deposit.child.member.full_name,
                "child_name": deposit.child.name,
                "account_no": deposit.account_no,
                "deposit_id": deposit.pk,
                "payment_type_name": deposit.deposit_type.name,
                "record": record,
                "can_post": end < as_of and not record.posted_at,
                "next_year_start": end + timedelta(days=1),
                "detail_url_name": "child_interest_detail",
                "post_url_name": "child_interest_edit",
                "delete_url_name": "child_interest_delete",
            }
        )
    return sorted(
        rows, key=lambda row: (row["member_no"], row["child_name"], row["account_no"])
    )


def child_calculation_fingerprint(record):
    return hashlib.sha256(
        json.dumps(
            {
                "deposit_id": record.deposit_id,
                "calculation": calculation_fingerprint(record),
                "rate_rules": record.rate_rules,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


@transaction.atomic
def save_child_interest_year(deposit, start, *, today=None):
    Member.objects.select_for_update().get(pk=deposit.child.member_id)
    deposit = ChildDeposit.objects.select_related("child__member", "deposit_type").get(
        pk=deposit.pk
    )
    existing = ChildDepositInterestYear.objects.filter(
        deposit=deposit, year_start=start
    ).first()
    if existing and existing.posted_at:
        return existing
    rows = child_interest_report(
        ChildDeposit.objects.filter(pk=deposit.pk), start, as_of=today
    )
    if not rows:
        raise ValidationError(
            "This child deposit has not opened in the selected financial year."
        )
    record = rows[0]["record"]
    if ChildDepositInterestYear.objects.filter(
        deposit=deposit,
        posted_at__isnull=False,
        year_end__gte=start,
        year_start__lte=record.year_end,
    ).exists():
        raise ValidationError("This period overlaps a posted child interest year.")
    record.full_clean()
    record.save()
    return record


@transaction.atomic
def post_child_interest_year(pk, *, actor=None, today=None, expected_fingerprint=None):
    record = ChildDepositInterestYear.objects.select_related("deposit__child").get(
        pk=pk
    )
    Member.objects.select_for_update().get(pk=record.deposit.child.member_id)
    record = (
        ChildDepositInterestYear.objects.select_for_update()
        .select_related("deposit__child", "deposit__deposit_type")
        .get(pk=pk)
    )
    if record.posted_at:
        return record
    today = today or timezone.localdate()
    if today <= record.year_end:
        raise ValidationError(
            "The financial year must end before child deposit interest can be posted."
        )
    others = ChildDepositInterestYear.objects.filter(
        deposit_id=record.deposit_id
    ).exclude(pk=record.pk)
    if others.filter(
        year_start__gt=record.year_start, posted_at__isnull=False
    ).exists():
        raise ValidationError("A later child interest year is already posted.")
    if others.filter(year_start__lt=record.year_start, posted_at__isnull=True).exists():
        raise ValidationError(
            "Post or remove earlier child interest drafts before closing this year."
        )
    record = save_child_interest_year(record.deposit, record.year_start, today=today)
    if (
        expected_fingerprint is not None
        and child_calculation_fingerprint(record) != expected_fingerprint
    ):
        raise ValidationError(
            "Balances or child rate rules changed after review. Review the calculation again before posting."
        )
    if record.annual_interest > ZERO:
        if record.annual_interest > Decimal("9999999999.99"):
            raise ValidationError(
                "Annual interest exceeds the child ledger entry limit."
            )
        entry = ChildDepositTransaction(
            deposit=record.deposit,
            transaction_date=record.year_end,
            credit=record.annual_interest,
            interest_rate=record.annual_rate,
            created_by=actor,
            reference=f"INTEREST-{record.year_start.isoformat()}",
            description="Annual child deposit interest from quarterly minimum month-end balances.",
        )
        entry._annual_interest_posting = True
        entry.full_clean()
        entry.save()
        record.ledger_transaction = entry
    record.posted_at = timezone.now()
    record.posted_by = actor
    record.save()
    return record


def guard_child_identity(child):
    if not child.pk:
        return
    previous = Child.objects.filter(pk=child.pk).first()
    if (
        previous
        and ChildDepositInterestYear.objects.filter(
            deposit__child_id=child.pk, posted_at__isnull=False
        ).exists()
        and (
            previous.member_id != child.member_id
            or previous.date_of_birth != child.date_of_birth
        )
    ):
        raise ValidationError(
            "A posted child interest year protects the child's member and date of birth."
        )


def guard_child_deposit(deposit):
    if (
        not deposit.pk
        or not ChildDepositInterestYear.objects.filter(
            deposit_id=deposit.pk, posted_at__isnull=False
        ).exists()
    ):
        return
    previous = ChildDeposit.objects.get(pk=deposit.pk)
    if any(
        getattr(previous, name) != getattr(deposit, name)
        for name in ("child_id", "deposit_type_id", "opened_on", "opening_amount")
    ):
        raise ValidationError(
            "A posted interest year protects the child deposit identity and opening amount/date."
        )


def guard_child_transaction(entry, *, deleting=False):
    if not entry.deposit_id or not entry.transaction_date:
        return
    if (
        entry.pk
        and ChildDepositInterestYear.objects.filter(
            ledger_transaction_id=entry.pk, posted_at__isnull=False
        ).exists()
    ):
        raise ValidationError(
            "Annual child interest postings cannot be edited or deleted."
        )
    original = (
        ChildDepositTransaction.objects.filter(pk=entry.pk).first()
        if entry.pk
        else None
    )
    for candidate in (entry, original):
        if (
            candidate
            and ChildDepositInterestYear.objects.filter(
                deposit_id=candidate.deposit_id,
                posted_at__isnull=False,
                year_end__gte=candidate.transaction_date,
            ).exists()
        ):
            if (
                not deleting
                and original
                and original.deposit_id == entry.deposit_id
                and all(
                    ChildDepositTransaction._meta.get_field(name).to_python(
                        getattr(original, name)
                    )
                    == ChildDepositTransaction._meta.get_field(name).to_python(
                        getattr(entry, name)
                    )
                    for name in (
                        "transaction_date",
                        "debit",
                        "credit",
                        "interest_rate",
                    )
                )
            ):
                return
            raise ValidationError(
                "This child deposit's interest year is closed. Earlier amounts and dates cannot be changed."
            )
