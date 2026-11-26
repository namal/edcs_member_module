"""Read-only Lager reporting through members' current assignments."""

from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import (
    Case,
    CharField,
    DateField,
    DecimalField,
    F,
    IntegerField,
    Q,
    Sum,
    TextField,
    Value,
    When,
)
from django.db.models.functions import Cast, Concat, TruncDate
from django.urls import reverse

from . import models as m
from .child_services import deposits_with_balances
from .permissions import has_module_permission
from .transaction_balances import balance_report

ZERO = Decimal("0.00")
GROUPS = {
    "membership_status": (
        m.MemberStatusRequest,
        "Membership activation / inactivation",
    ),
    "members": (m.Member, "Member registration & retirement"),
    "openings": (m.OpeningBalance, "Opening balances"),
    "plans": (m.PaymentPlan, "Payment plans"),
    "payments": (m.PaymentTransaction, "Member deposits / debit / credit"),
    "loans": (m.FixDepositAccount, "Fix Deposit accounts, loans & withdrawals"),
    "children": (m.Child, "Children"),
    "child_deposits": (m.ChildDeposit, "Child accounts / debit / credit"),
    "interest": (m.DepositInterestYear, "Quarterly interest"),
}


def activity_groups(user):
    return {
        key: label
        for key, (model, label) in GROUPS.items()
        if has_module_permission(user, model, "view")
    }


def activity_query(members, groups, start=None, end=None):
    """UNION ALL permits database pagination without loading every entry in Python.

    Amounts on plans, loans and snapshots are descriptive. Cash totals come only
    from the original member/child transaction tables, never these combined rows.
    """
    queries = []
    money = DecimalField(max_digits=24, decimal_places=2)

    def source(
        model,
        group,
        key,
        date_field,
        member_path,
        label,
        *,
        account="",
        child="",
        reference="",
        debit=None,
        credit=None,
        amount=None,
        details="",
        related=None,
        type_id=None,
        timestamp=False,
        condition=None,
        principal=None,
        interest=None,
    ):
        if group not in groups:
            return
        queryset = model.objects.filter(**{f"{member_path}__in": members})
        if condition is not None:
            queryset = queryset.filter(condition)
        member_prefix = "" if member_path == "pk" else f"{member_path}__"
        annotations = {
            "event_date": (
                TruncDate(date_field)
                if timestamp
                else F(date_field) if isinstance(date_field, str) else date_field
            ),
            "event_source": Value(key, output_field=CharField()),
            "event_id": F("pk"),
            "event_member_id": F(f"{member_prefix}pk"),
            "event_member_no": F(f"{member_prefix}member_no"),
            "event_member_name": F(f"{member_prefix}full_name"),
            "event_label": (
                Value(label, output_field=CharField())
                if isinstance(label, str)
                else label
            ),
            "event_account": (
                (F(account) if isinstance(account, str) else account)
                if account
                else Value("", output_field=CharField())
            ),
            "event_child": F(child) if child else Value("", output_field=CharField()),
            "event_reference": (
                F(reference) if reference else Value("", output_field=CharField())
            ),
            "event_debit": F(debit) if debit else Cast(Value(None), money),
            "event_credit": F(credit) if credit else Cast(Value(None), money),
            "event_amount": (
                (F(amount) if isinstance(amount, str) else amount)
                if amount is not None
                else Cast(Value(None), money)
            ),
            "event_details": (
                F(details) if details else Value("", output_field=TextField())
            ),
            "event_related_id": F(related) if related else F("pk"),
            "event_type_id": (
                F(type_id) if type_id else Value(0, output_field=IntegerField())
            ),
            "event_principal": (
                F(principal) if principal else Cast(Value(None), money)
            ),
            "event_interest": (F(interest) if interest else Cast(Value(None), money)),
        }
        queryset = queryset.annotate(**annotations)
        if start:
            queryset = queryset.filter(event_date__gte=start)
        if end:
            queryset = queryset.filter(event_date__lte=end)
        queries.append(queryset.order_by().values(*annotations))

    source(m.Member, "members", "member", "admit_date", "pk", "Member registration")
    source(
        m.MemberStatusEvent,
        "membership_status",
        "membership_status",
        "effective_at",
        "member",
        "Membership status change",
        reference="request__reference",
        details="description",
        related="request_id",
        timestamp=True,
    )
    source(
        m.Member,
        "members",
        "retirement",
        "pension_date",
        "pk",
        "Retirement / pension date",
        condition=Q(pension_date__isnull=False),
    )
    source(
        m.OpeningBalanceAmount,
        "openings",
        "opening",
        "opening_balance__balance_date",
        "opening_balance__member",
        "Opening balance",
        account="payment_type__name",
        amount="amount",
        related="opening_balance_id",
    )
    source(
        m.PaymentPlanAmount,
        "plans",
        "plan",
        "plan__agreement_date",
        "plan__member",
        "Monthly payment plan",
        account="payment_type__name",
        amount="amount",
        related="plan_id",
    )
    source(
        m.PaymentTransaction,
        "payments",
        "payment",
        "transaction_date",
        "member",
        Case(
            When(interest_posting__isnull=False, then=Value("Annual interest credit")),
            When(
                fix_deposit_withdrawal__isnull=False,
                then=Value("Released deposit withdrawal"),
            ),
            default=Value("Member debit / credit"),
            output_field=CharField(),
        ),
        account="payment_type__name",
        reference="reference",
        debit="debit",
        credit="credit",
        details="description",
        type_id="payment_type_id",
    )
    source(
        m.FixDepositAccount,
        "loans",
        "fix_account",
        "created_at",
        "member",
        "Fix Deposit account created",
        account="policy__payment_type__name",
        details="notes",
        timestamp=True,
    )
    source(
        m.FixDepositLoan,
        "loans",
        "loan",
        "issued_on",
        "account__member",
        "Loan issued",
        account="account__policy__payment_type__name",
        reference="reference",
        amount="principal",
        details="notes",
    )
    source(
        m.FixDepositLoan,
        "loans",
        "settlement",
        "settled_on",
        "account__member",
        "Loan settled",
        account="account__policy__payment_type__name",
        reference="reference",
        condition=Q(settled_on__isnull=False),
    )
    source(
        m.FixLoanEntry,
        "loans",
        "loan_entry",
        "entry_date",
        "loan__account__member",
        Case(
            When(kind="repayment", then=Value("Loan repayment")),
            default=Value("Loan interest assessed"),
            output_field=CharField(),
        ),
        account="loan__account__policy__payment_type__name",
        reference="loan__reference",
        amount=F("principal_amount") + F("interest_amount"),
        details="notes",
        related="loan_id",
        principal="principal_amount",
        interest="interest_amount",
    )
    source(
        m.FixDepositWithdrawal,
        "loans",
        "withdrawal",
        "requested_on",
        "account__member",
        "Withdrawal requested",
        account="account__policy__payment_type__name",
        amount="amount",
        details="notes",
        related="account_id",
    )
    source(
        m.FixDepositWithdrawal,
        "loans",
        "withdrawal_review",
        Case(
            When(status="released", then=F("released_on")),
            default=TruncDate("reviewed_at"),
            output_field=DateField(),
        ),
        "account__member",
        Case(
            When(status="released", then=Value("Withdrawal released")),
            default=Value("Withdrawal rejected"),
            output_field=CharField(),
        ),
        account="account__policy__payment_type__name",
        amount="amount",
        details="notes",
        related="account_id",
        condition=Q(reviewed_at__isnull=False),
    )
    source(
        m.Child,
        "children",
        "child",
        "created_at",
        "member",
        "Child registered",
        child="name",
        timestamp=True,
    )
    source(
        m.ChildDeposit,
        "child_deposits",
        "child_account",
        "opened_on",
        "child__member",
        "Child account opening",
        account="deposit_type__name",
        child="child__name",
        reference="account_no",
        amount="opening_amount",
    )
    source(
        m.ChildDepositTransaction,
        "child_deposits",
        "child_payment",
        "transaction_date",
        "deposit__child__member",
        Case(
            When(
                interest_posting__isnull=False,
                then=Value("Child annual interest credit"),
            ),
            default=Value("Child debit / credit"),
            output_field=CharField(),
        ),
        account=Concat(
            "deposit__account_no",
            Value(" · "),
            "deposit__deposit_type__name",
            output_field=CharField(),
        ),
        child="deposit__child__name",
        reference="reference",
        debit="debit",
        credit="credit",
        details="description",
        related="deposit_id",
    )
    source(
        m.DepositInterestYear,
        "interest",
        "interest",
        "calculated_through",
        "member",
        Case(
            When(
                posted_at__isnull=True,
                then=Value("Member interest calculated (unposted)"),
            ),
            default=Value("Member interest calculated (posted)"),
            output_field=CharField(),
        ),
        account="payment_type__name",
        amount="annual_interest",
    )
    source(
        m.ChildDepositInterestYear,
        "interest",
        "child_interest",
        "calculated_through",
        "deposit__child__member",
        Case(
            When(
                posted_at__isnull=True,
                then=Value("Child interest calculated (unposted)"),
            ),
            default=Value("Child interest calculated (posted)"),
            output_field=CharField(),
        ),
        account="deposit__deposit_type__name",
        child="deposit__child__name",
        reference="deposit__account_no",
        amount="annual_interest",
    )
    if not queries:
        return []
    return (
        queries[0]
        .union(*queries[1:], all=True)
        .order_by("-event_date", "event_member_no", "event_source", "-event_id")
    )


def decorate_event(row):
    key = row["event_source"]
    routes = {
        "membership_status": "member_status_detail",
        "member": "member_detail",
        "retirement": "member_detail",
        "opening": "opening_balance_detail",
        "plan": "payment_plan_detail",
        "fix_account": "fix_deposit_detail",
        "loan": "fix_loan_detail",
        "settlement": "fix_loan_detail",
        "loan_entry": "fix_loan_detail",
        "withdrawal": "fix_deposit_detail",
        "withdrawal_review": "fix_deposit_detail",
        "child": "child_detail",
        "child_account": "child_deposit_detail",
        "child_payment": "child_deposit_detail",
        "interest": "deposit_interest_detail",
        "child_interest": "child_interest_detail",
    }
    if key == "payment":
        row["url"] = (
            reverse("payment_transaction_list")
            + "?"
            + urlencode(
                {"member": row["event_member_id"], "payment_type": row["event_type_id"]}
            )
        )
    else:
        row["url"] = reverse(routes[key], args=[row["event_related_id"]])
    if key == "loan_entry":
        components = f"Principal LKR {row['event_principal']:,.2f}; interest LKR {row['event_interest']:,.2f}."
        row["event_details"] = f"{components} {row['event_details']}".strip()
    return row


def lager_summaries(user, members, start, end):
    """Use existing account balance rules, keeping deposit and loan totals separate."""
    result = {}
    if has_module_permission(user, m.PaymentTransaction, "view"):
        rows = balance_report(members, start, end)
        result["member_deposits"] = {
            "balance": sum((row["final_balance"] for row in rows), ZERO),
            "credit": sum((row["total_credit"] for row in rows), ZERO),
            "debit": sum((row["total_debit"] for row in rows), ZERO),
        }
    if has_module_permission(user, m.ChildDeposit, "view"):
        accounts = deposits_with_balances(user, end).filter(child__member__in=members)
        balance = sum((account.as_of_balance for account in accounts), ZERO)
        entries = m.ChildDepositTransaction.objects.filter(
            deposit__child__member__in=members,
            transaction_date__gte=F("deposit__opened_on"),
            transaction_date__lte=end,
        )
        if start:
            entries = entries.filter(transaction_date__gte=start)
        result["child_deposits"] = {
            "balance": balance,
            **entries.aggregate(
                credit=Sum("credit", default=ZERO), debit=Sum("debit", default=ZERO)
            ),
        }
    if has_module_permission(user, m.FixDepositAccount, "view"):
        loans = m.FixDepositLoan.objects.filter(
            account__member__in=members, issued_on__lte=end
        )
        issued = loans.aggregate(amount=Sum("principal", default=ZERO))["amount"]
        entries = m.FixLoanEntry.objects.filter(loan__in=loans, entry_date__lte=end)
        totals = entries.aggregate(
            principal_paid=Sum(
                "principal_amount", filter=Q(kind="repayment"), default=ZERO
            ),
            interest_paid=Sum(
                "interest_amount", filter=Q(kind="repayment"), default=ZERO
            ),
            interest_charged=Sum(
                "interest_amount", filter=Q(kind="interest"), default=ZERO
            ),
        )
        result["loans"] = {
            "principal_due": issued - totals["principal_paid"],
            "interest_due": totals["interest_charged"] - totals["interest_paid"],
        }
    return result
