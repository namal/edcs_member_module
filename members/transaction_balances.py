"""Balances derived from saved opening amounts and dated member transactions."""

from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value, Window

from .models import (
    OpeningBalance,
    OpeningBalanceAmount,
    PaymentTransaction,
    PaymentType,
)

ZERO = Decimal("0.00")


def child_account_summary(user, member, as_of, *, selected=None):
    """Authorized child balances and recent entries; member totals stay separate."""
    from django.urls import reverse
    from .child_services import child_statement, deposits_with_balances
    from .models import ChildDeposit
    from .permissions import has_module_permission

    if not any(
        has_module_permission(user, ChildDeposit, action)
        for action in ("view", "add", "change")
    ):
        return {}
    can_view = has_module_permission(user, ChildDeposit, "view")
    accounts = []
    selected_account = None
    deposits = deposits_with_balances(user, as_of).filter(child__member=member)
    for deposit in deposits:
        rate = deposit.rate_on(as_of)
        row = {
            "id": deposit.pk,
            "deposit_type_id": deposit.deposit_type_id,
            "name": str(deposit),
            "child_name": deposit.child.name,
            "account_no": deposit.account_no,
            "type_name": deposit.deposit_type.name,
            "balance": format(deposit.as_of_balance, ".2f"),
            "is_active": bool(
                deposit.is_active
                and deposit.child.is_active
                and deposit.deposit_type.is_active
            ),
            "opened_on": deposit.opened_on.isoformat(),
            "rate": format(rate, ".2f") if rate is not None else None,
            "statement_url": (
                reverse("child_deposit_detail", args=[deposit.pk])
                + f"?date_to={as_of.isoformat()}"
                if can_view
                else None
            ),
        }
        accounts.append(row)
        if str(deposit.pk) == str(selected):
            selected_account = row
            if can_view:
                statement = child_statement(deposit, None, as_of)
                row["total_credit"] = format(statement["total_credit"], ".2f")
                row["total_debit"] = format(statement["total_debit"], ".2f")
                row["opening_amount"] = format(statement["brought_forward"], ".2f")
                row["transactions"] = [
                    {
                        "date": entry.transaction_date.isoformat(),
                        "reference": entry.reference,
                        "description": entry.description,
                        "debit": format(entry.debit, ".2f"),
                        "credit": format(entry.credit, ".2f"),
                        "balance": format(entry.running_balance, ".2f"),
                    }
                    for entry in statement["entries"].order_by(
                        "-transaction_date", "-pk"
                    )[:10]
                ]
    return {
        "child_accounts": accounts,
        "child_total": format(
            sum((Decimal(row["balance"]) for row in accounts), ZERO), ".2f"
        ),
        "selected_child_account": selected_account,
        "can_view_child_transactions": can_view,
    }


def account_statement(member, payment_type, start, end):
    """Return a statement starting at the saved opening amount, when effective."""
    opening = (
        OpeningBalanceAmount.objects.filter(
            opening_balance__member=member,
            payment_type=payment_type,
            opening_balance__balance_date__lte=end,
        )
        .select_related("opening_balance")
        .first()
    )
    opening_amount = opening.amount if opening else ZERO
    opening_date = opening.opening_balance.balance_date if opening else None
    effective_start = (
        max(start, opening_date) if start and opening_date else start or opening_date
    )
    entries = PaymentTransaction.objects.filter(
        member=member, payment_type=payment_type, transaction_date__lte=end
    )
    if opening_date:
        entries = entries.filter(transaction_date__gte=opening_date)
    brought_forward = opening_amount
    if effective_start:
        brought_forward += entries.filter(
            transaction_date__lt=effective_start
        ).aggregate(amount=Sum(F("credit") - F("debit"), default=ZERO))["amount"]
        entries = entries.filter(transaction_date__gte=effective_start)
    totals = entries.aggregate(
        debit=Sum("debit", default=ZERO), credit=Sum("credit", default=ZERO)
    )
    entries = entries.annotate(
        running_net=Window(
            expression=Sum(F("credit") - F("debit")),
            order_by=[F("transaction_date").asc(), F("pk").asc()],
        ),
    ).annotate(
        running_balance=F("running_net")
        + Value(
            brought_forward, output_field=DecimalField(max_digits=24, decimal_places=2)
        ),
    )
    return {
        "entries": entries,
        "opening_amount": opening_amount,
        "opening_date": opening_date,
        "effective_date_from": effective_start,
        "balance_date": end,
        "brought_forward": brought_forward,
        "total_debit": totals["debit"],
        "total_credit": totals["credit"],
        "net_movement": totals["credit"] - totals["debit"],
        "final_balance": brought_forward + totals["credit"] - totals["debit"],
    }


def balance_report(members, start, end, *, payment_type=None):
    """Aggregate member/type balances in bulk. Members must already be scoped."""
    openings = OpeningBalanceAmount.objects.filter(
        opening_balance__member__in=members,
        opening_balance__balance_date__lte=end,
    )
    entries = PaymentTransaction.objects.filter(
        member__in=members, transaction_date__lte=end
    )
    if payment_type is not None:
        openings = openings.filter(payment_type=payment_type)
        entries = entries.filter(payment_type=payment_type)
    baseline = OpeningBalanceAmount.objects.filter(
        opening_balance__member_id=OuterRef("member_id"),
        payment_type_id=OuterRef("payment_type_id"),
        opening_balance__balance_date__lte=end,
    ).values("opening_balance__balance_date")[:1]
    entries = entries.annotate(baseline=Subquery(baseline)).filter(
        Q(baseline__isnull=True) | Q(transaction_date__gte=F("baseline"))
    )
    rows = {}

    def row_for(member_id, member_no, full_name, type_id, type_name, active):
        key = (member_id, type_id)
        if key not in rows:
            rows[key] = {
                "member_id": member_id,
                "member_no": member_no,
                "full_name": full_name,
                "payment_type_id": type_id,
                "payment_type_name": type_name,
                "is_active": active,
                "opening_date": None,
                "brought_forward": ZERO,
                "total_debit": ZERO,
                "total_credit": ZERO,
            }
        return rows[key]

    for item in openings.values(
        "opening_balance__member_id",
        "opening_balance__member__member_no",
        "opening_balance__member__full_name",
        "payment_type_id",
        "payment_type__name",
        "payment_type__is_active",
        "opening_balance__balance_date",
        "amount",
    ):
        row = row_for(
            item["opening_balance__member_id"],
            item["opening_balance__member__member_no"],
            item["opening_balance__member__full_name"],
            item["payment_type_id"],
            item["payment_type__name"],
            item["payment_type__is_active"],
        )
        row["brought_forward"] = item["amount"]
        row["opening_date"] = item["opening_balance__balance_date"]
    period = Q(transaction_date__gte=start) if start else Q()
    earlier = Q(transaction_date__lt=start) if start else Q(pk__in=[])
    for item in (
        entries.order_by()
        .values(
            "member_id",
            "member__member_no",
            "member__full_name",
            "payment_type_id",
            "payment_type__name",
            "payment_type__is_active",
        )
        .annotate(
            period_debit=Sum("debit", filter=period, default=ZERO),
            period_credit=Sum("credit", filter=period, default=ZERO),
            previous=Sum(F("credit") - F("debit"), filter=earlier, default=ZERO),
        )
    ):
        row = row_for(
            item["member_id"],
            item["member__member_no"],
            item["member__full_name"],
            item["payment_type_id"],
            item["payment_type__name"],
            item["payment_type__is_active"],
        )
        row["brought_forward"] += item["previous"]
        row["total_debit"] = item["period_debit"]
        row["total_credit"] = item["period_credit"]
    for row in rows.values():
        row["final_balance"] = (
            row["brought_forward"] + row["total_credit"] - row["total_debit"]
        )
        row["effective_date_from"] = (
            max(start, row["opening_date"])
            if start and row["opening_date"]
            else start or row["opening_date"]
        )
    return sorted(
        rows.values(),
        key=lambda row: (
            row["member_no"],
            row["member_id"],
            row["payment_type_name"],
            row["payment_type_id"],
        ),
    )


def member_balances(member, as_of, *, exclude_entry=None):
    """Opening amounts start on their balance date; earlier entries are historical.

    A payment type without a saved opening amount retains all its transactions.
    Excluding an entry gives the balance before that entry when editing it.
    Callers must first authorize access to the member and any excluded entry.
    """
    opening = (
        OpeningBalance.objects.filter(member=member, balance_date__lte=as_of)
        .prefetch_related("amounts")
        .first()
    )
    opening_amounts = (
        {item.payment_type_id: item.amount for item in opening.amounts.all()}
        if opening
        else {}
    )
    entries = PaymentTransaction.objects.filter(
        member=member, transaction_date__lte=as_of
    )
    if opening_amounts:
        entries = entries.exclude(
            payment_type_id__in=opening_amounts,
            transaction_date__lt=opening.balance_date,
        )
    if exclude_entry is not None:
        entries = entries.exclude(pk=exclude_entry.pk)
    movements = {
        row["payment_type_id"]: row
        for row in entries.values("payment_type_id").annotate(
            debit=Sum("debit"), credit=Sum("credit")
        )
    }
    types = PaymentType.objects.filter(
        Q(is_active=True) | Q(pk__in=opening_amounts) | Q(pk__in=movements)
    )
    rows = []
    total = Decimal("0.00")
    for payment_type in types:
        movement = movements.get(payment_type.pk, {})
        amount = opening_amounts.get(payment_type.pk, Decimal("0.00"))
        amount += movement.get("credit", Decimal("0.00")) - movement.get(
            "debit", Decimal("0.00")
        )
        total += amount
        rows.append(
            {
                "payment_type_id": payment_type.pk,
                "name": payment_type.name,
                "is_active": payment_type.is_active,
                "balance": format(amount, ".2f"),
            }
        )
    remaining_credits = []
    for receipt in member.payment_receipts.filter(received_on__lte=as_of):
        saved = receipt.plan_snapshot
        if saved.get("remaining_allocations"):
            remaining_credits.extend(
                {
                    "receipt": receipt.receipt_number,
                    "received_on": receipt.received_on.isoformat(),
                    "amount": row["amount"],
                    "payment_type": row["name"],
                }
                for row in saved["remaining_allocations"]
                if not row.get("child_deposit_id")
            )
            continue
        remaining = Decimal(saved.get("remaining_amount", "0"))
        if remaining > ZERO and saved.get("remaining_payment_type_id"):
            remaining_credits.append(
                {
                    "receipt": receipt.receipt_number,
                    "received_on": receipt.received_on.isoformat(),
                    "amount": format(remaining, ".2f"),
                    "payment_type": saved["remaining_payment_type_name"],
                }
            )
    return {
        "member_id": member.pk,
        "as_of": as_of.isoformat(),
        "opening_date": opening.balance_date.isoformat() if opening else None,
        "total": format(total, ".2f"),
        "has_records": bool(opening_amounts or movements),
        "balances": rows,
        "remaining_contribution_credits": remaining_credits,
        "remaining_contribution_total": format(
            sum((Decimal(row["amount"]) for row in remaining_credits), ZERO), ".2f"
        ),
    }
