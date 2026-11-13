from decimal import Decimal

from django.db.models import Case, DecimalField, F, Q, Sum, Value, When, Window

from .models import ChildDeposit
from .permissions import district_records

ZERO = Decimal("0.00")


def deposits_with_balances(user, as_of):
    period = Q(
        transactions__transaction_date__lte=as_of,
        transactions__transaction_date__gte=F("opened_on"),
    )
    return (
        district_records(user, ChildDeposit)
        .select_related("child__member", "deposit_type")
        .annotate(
            total_credit=Sum("transactions__credit", filter=period, default=ZERO),
            total_debit=Sum("transactions__debit", filter=period, default=ZERO),
        )
        .annotate(
            as_of_balance=Case(
                When(
                    opened_on__lte=as_of,
                    then=F("opening_amount") + F("total_credit") - F("total_debit"),
                ),
                default=Value(ZERO),
                output_field=DecimalField(max_digits=24, decimal_places=2),
            )
        )
        .order_by("child__member__member_no", "child__name", "account_no", "pk")
    )


def display_balance(deposit, as_of):
    deposit.balance = deposit.as_of_balance
    deposit.configured_rate = deposit.rate_on(as_of)
    deposit.child_age = deposit.child.age_on(as_of)
    return deposit


def deposit_balance(deposit, as_of, *, exclude_entry=None):
    if as_of < deposit.opened_on:
        return ZERO
    entries = deposit.transactions.filter(
        transaction_date__lte=as_of, transaction_date__gte=deposit.opened_on
    )
    if exclude_entry is not None:
        entries = entries.exclude(pk=exclude_entry.pk)
    return (
        Decimal(deposit.opening_amount)
        + entries.aggregate(amount=Sum(F("credit") - F("debit"), default=ZERO))[
            "amount"
        ]
    )


def child_statement(deposit, start, end):
    effective_start = max(start, deposit.opened_on) if start else deposit.opened_on
    entries = deposit.transactions.filter(
        transaction_date__gte=deposit.opened_on, transaction_date__lte=end
    )
    brought_forward = (
        Decimal(deposit.opening_amount) if end >= deposit.opened_on else ZERO
    )
    brought_forward += entries.filter(transaction_date__lt=effective_start).aggregate(
        amount=Sum(F("credit") - F("debit"), default=ZERO)
    )["amount"]
    entries = entries.filter(transaction_date__gte=effective_start)
    totals = entries.aggregate(
        credit=Sum("credit", default=ZERO), debit=Sum("debit", default=ZERO)
    )
    entries = entries.annotate(
        running_balance=Window(
            expression=Sum(F("credit") - F("debit")),
            order_by=[F("transaction_date").asc(), F("pk").asc()],
        )
        + Value(
            brought_forward, output_field=DecimalField(max_digits=24, decimal_places=2)
        )
    )
    return {
        "entries": entries,
        "brought_forward": brought_forward,
        "total_credit": totals["credit"],
        "total_debit": totals["debit"],
        "final_balance": brought_forward + totals["credit"] - totals["debit"],
        "balance_date": end,
        "configured_rate": deposit.rate_on(end),
        "child_age": deposit.child.age_on(end),
    }
