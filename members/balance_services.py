"""Read-only member balance inquiry using the posted payment ledger."""

from datetime import date
from decimal import Decimal

from .deposit_interest import (
    current_financial_year,
    financial_year_start,
    interest_report,
)
from .models import Member, OpeningBalanceAmount, PaymentPlan, PaymentTransaction
from .transaction_balances import balance_report

ZERO = Decimal("0.00")


def member_balance_inquiry(member, month, as_of, *, history="all"):
    """Callers must authorize the member before requesting their financial data.

    Receipt allocations and posted interest are already ledger credits. Classify
    those credits without adding their source amounts to the balance again.
    Opening amounts replace earlier transactions for their payment type only.
    """
    members = Member.objects.filter(pk=member.pk)
    accounts = balance_report(members, month, as_of)
    by_type = {row["payment_type_id"]: row for row in accounts}
    for row in accounts:
        row.update(contribution=ZERO, interest=ZERO, other_credit=ZERO)

    openings = list(
        OpeningBalanceAmount.objects.filter(
            opening_balance__member=member,
            opening_balance__balance_date__lte=as_of,
        ).select_related("opening_balance", "payment_type")
    )
    entries = PaymentTransaction.objects.filter(
        member=member, transaction_date__lte=as_of
    )
    if openings:
        entries = entries.exclude(
            payment_type_id__in=[item.payment_type_id for item in openings],
            transaction_date__lt=openings[0].opening_balance.balance_date,
        )
    entries = entries.select_related(
        "payment_type", "receipt_allocation__receipt", "interest_posting"
    ).order_by("transaction_date", "pk")

    events = []
    for item in openings:
        events.append(
            {
                "date": item.opening_balance.balance_date,
                "order": (0, item.pk),
                "payment_type": item.payment_type.name,
                "kind": "Opening balance",
                "reference": "",
                "description": "Saved opening amount",
                "debit": ZERO,
                "credit": item.amount,
            }
        )
    for entry in entries:
        receipt = getattr(entry, "receipt_allocation", None)
        posting = getattr(entry, "interest_posting", None)
        category = (
            "contribution" if receipt else "interest" if posting else "other_credit"
        )
        if entry.transaction_date >= month:
            by_type[entry.payment_type_id][category] += entry.credit
        events.append(
            {
                "date": entry.transaction_date,
                "order": (1, entry.pk),
                "payment_type": entry.payment_type.name,
                "kind": (
                    "Paid contribution"
                    if receipt
                    else "Posted quarterly interest" if posting else "Debit / Credit"
                ),
                "reference": entry.reference
                or (receipt.receipt.receipt_number if receipt else ""),
                "description": entry.description,
                "debit": entry.debit,
                "credit": entry.credit,
            }
        )
    events.sort(key=lambda item: (item["date"], item["order"]))
    running = ZERO
    history_opening = ZERO
    visible_events = []
    for event in events:
        running += event["credit"] - event["debit"]
        event["balance"] = running
        if history == "month" and event["date"] < month:
            history_opening = running
        else:
            visible_events.append(event)

    totals = {
        name: sum((row[name] for row in accounts), ZERO)
        for name in (
            "brought_forward",
            "contribution",
            "interest",
            "other_credit",
            "total_debit",
            "final_balance",
        )
    }

    # Use the established quarterly minimum-month-end calculation, including
    # pension-quarter exclusions, configured financial year and saved rates.
    year_start = financial_year_start(current_financial_year(as_of))
    quarters = []
    accrued = ZERO
    for row in interest_report(members, year_start, as_of=as_of):
        record = row["record"]
        posted = bool(record.posted_at and record.year_end <= as_of)
        for quarter in record.quarters:
            if (
                not quarter["complete"]
                or date.fromisoformat(quarter["months"][-1]["date"]) > as_of
            ):
                continue
            amount = Decimal(quarter["interest"])
            if not posted:
                accrued += amount
            quarters.append(
                {
                    "payment_type": row["payment_type_name"],
                    "number": quarter["number"],
                    "end": quarter["months"][-1]["date"],
                    "minimum": quarter["minimum"],
                    "rate": record.annual_rate,
                    "amount": amount,
                    "status": "Posted" if posted else "Unposted",
                    "exclusion_reason": quarter.get("exclusion_reason", ""),
                }
            )

    plan = (
        PaymentPlan.objects.filter(member=member)
        .prefetch_related("amounts__payment_type")
        .first()
    )
    return {
        "accounts": accounts,
        "totals": totals,
        "events": visible_events,
        "history_opening": history_opening,
        "history": history,
        "quarter_rows": quarters,
        "interest_year_start": year_start,
        "accrued_interest": accrued,
        "balance_with_accrued_interest": totals["final_balance"] + accrued,
        "plan": plan,
        "as_of": as_of,
        "month_start": month,
    }
