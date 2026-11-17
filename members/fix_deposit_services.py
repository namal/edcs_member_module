"""Fix Deposit eligibility and atomic, dated financial postings."""

from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import (
    FixDepositAccount,
    FixDepositLoan,
    FixDepositPolicy,
    FixDepositWithdrawal,
    FixLoanEntry,
    Member,
    PaymentTransaction,
)
from .permissions import district_records, require_member_access, require_permission
from .transaction_balances import account_statement

ZERO = Decimal("0.00")


def add_months(day, months):
    index = day.year * 12 + day.month - 1 + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def money(value, *, allow_zero=False):
    try:
        amount = Decimal(str(value))
        if (
            not amount.is_finite()
            or amount < 0
            or (not allow_zero and amount == 0)
            or amount > Decimal("9999999999.99")
            or amount != amount.quantize(Decimal("0.01"))
        ):
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(
            "Enter a valid LKR amount with no more than two decimal places."
        )
    return amount


def loan_balances(loan, as_of):
    principal_paid = interest_paid = charged = ZERO
    for entry in loan.entries.all():
        if entry.entry_date > as_of:
            continue
        if entry.kind == FixLoanEntry.Kind.INTEREST:
            charged += entry.interest_amount
        else:
            principal_paid += entry.principal_amount
            interest_paid += entry.interest_amount
    return {
        "principal_due": loan.principal - principal_paid,
        "interest_due": charged - interest_paid,
        "principal_paid": principal_paid,
        "interest_paid": interest_paid,
        "interest_charged": charged,
    }


def account_status(account, as_of):
    policy = account.policy
    balance = account_statement(account.member, policy.payment_type, None, as_of)[
        "final_balance"
    ]
    loans = [loan for loan in account.loans.all() if loan.issued_on <= as_of]
    loan_rows = []
    principal_due = interest_due = ZERO
    open_count = 0
    for loan in loans:
        amounts = loan_balances(loan, as_of)
        unsettled = loan.settled_on is None or loan.settled_on > as_of
        if unsettled:
            open_count += 1
        principal_due += amounts["principal_due"]
        interest_due += amounts["interest_due"]
        loan_rows.append({"loan": loan, **amounts, "unsettled": unsettled})
    withdrawals = list(account.withdrawals.all())
    released = [
        item
        for item in withdrawals
        if item.status == FixDepositWithdrawal.Status.RELEASED
        and item.released_on <= as_of
    ]
    cycle_start = max(
        (item.released_on for item in released), default=account.cycle_started_on
    )
    maturity_date = add_months(cycle_start, policy.maturity_months)
    last_loan = max((loan.issued_on for loan in loans), default=None)
    next_loan_date = (
        add_months(last_loan, policy.loan_interval_months)
        if last_loan
        else account.cycle_started_on
    )
    collateral_cap = (max(balance, ZERO) * policy.loan_percent / 100).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    loan_limit = max(collateral_cap - principal_due, ZERO)
    withdrawal_limit = (max(balance, ZERO) * policy.withdrawal_percent / 100).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    common = []
    if not (account.is_active and policy.is_active and policy.payment_type.is_active):
        common.append("This account, rule or payment type is inactive.")
    if as_of < account.cycle_started_on:
        common.append("The selected date is before the registered cycle start.")
    loan_reasons = list(common)
    if policy.loan_interest_rate is None:
        loan_reasons.append("The bank must configure the loan interest rate first.")
    if as_of < next_loan_date:
        loan_reasons.append(
            f"The next loan can be issued from {next_loan_date.isoformat()}."
        )
    if loan_limit <= 0:
        loan_reasons.append(
            "No loan capacity remains within the deposit security limit."
        )
    withdrawal_reasons = list(common)
    if as_of < maturity_date:
        withdrawal_reasons.append(
            f"The withdrawal cycle matures on {maturity_date.isoformat()}."
        )
    if open_count:
        withdrawal_reasons.append(
            "All Fix Deposit Loans must be fully paid and marked settled, including interest."
        )
    if withdrawal_limit <= 0:
        withdrawal_reasons.append(
            "There is no positive deposit amount available for withdrawal."
        )
    return {
        "balance_date": as_of,
        "deposit_balance": balance,
        "principal_due": principal_due,
        "interest_due": interest_due,
        "open_loan_count": open_count,
        "loan_rows": loan_rows,
        "cycle_start": cycle_start,
        "maturity_date": maturity_date,
        "next_loan_date": next_loan_date,
        "loan_limit": loan_limit,
        "collateral_cap": collateral_cap,
        "withdrawal_limit": withdrawal_limit,
        "minimum_held": balance - withdrawal_limit,
        "loan_reasons": loan_reasons,
        "withdrawal_reasons": withdrawal_reasons,
        "can_borrow": not loan_reasons,
        "can_withdraw": not withdrawal_reasons,
        "pending_withdrawal": next(
            (
                item
                for item in withdrawals
                if item.status == FixDepositWithdrawal.Status.PENDING
            ),
            None,
        ),
    }


def _locked_account(actor, pk, action):
    require_permission(actor, FixDepositAccount, action)
    member_id = get_object_or_404(
        district_records(actor, FixDepositAccount), pk=pk
    ).member_id
    Member.objects.select_for_update().get(pk=member_id)
    require_member_access(actor, member_id, lock=True)
    account = get_object_or_404(
        district_records(actor, FixDepositAccount)
        .select_for_update(of=("self",))
        .select_related("member", "policy__payment_type"),
        pk=pk,
    )
    account.policy = (
        FixDepositPolicy.objects.select_for_update()
        .select_related("payment_type")
        .get(pk=account.policy_id)
    )
    return account


def _action_date(account, day):
    if day > timezone.localdate() or day < account.cycle_started_on:
        raise ValidationError("Use a date from the cycle start through today.")
    dates = [account.cycle_started_on]
    for loan in account.loans.prefetch_related("entries"):
        dates.append(loan.issued_on)
        if loan.settled_on:
            dates.append(loan.settled_on)
        dates.extend(entry.entry_date for entry in loan.entries.all())
    dates.extend(
        account.withdrawals.filter(
            status=FixDepositWithdrawal.Status.RELEASED
        ).values_list("released_on", flat=True)
    )
    if day < max(dates):
        raise ValidationError(
            "The date cannot be before the latest posted loan, repayment, settlement or withdrawal on this account."
        )


@transaction.atomic
def issue_loan(actor, account_pk, day, principal, notes=""):
    account = _locked_account(actor, account_pk, "add")
    _action_date(account, day)
    status = account_status(account, day)
    principal = money(principal)
    if not status["can_borrow"]:
        raise ValidationError(status["loan_reasons"])
    if principal > status["loan_limit"]:
        raise ValidationError(
            f"The maximum additional loan is LKR {status['loan_limit']:.2f}."
        )
    loan = FixDepositLoan(
        account=account,
        issued_on=day,
        principal=principal,
        interest_rate=account.policy.loan_interest_rate,
        deposit_balance_at_issue=status["deposit_balance"],
        maximum_loan_at_issue=status["loan_limit"],
        created_by=actor,
        notes=notes,
    )
    loan.full_clean()
    loan.save()
    return loan


@transaction.atomic
def record_loan_entry(
    actor,
    loan_pk,
    day,
    kind,
    principal=ZERO,
    interest=ZERO,
    reference="",
    notes="",
    operation_key=None,
):
    loan = get_object_or_404(district_records(actor, FixDepositLoan), pk=loan_pk)
    account = _locked_account(actor, loan.account_id, "change")
    loan = FixDepositLoan.objects.select_for_update().get(pk=loan.pk)
    principal, interest = money(principal, allow_zero=True), money(
        interest, allow_zero=True
    )
    if operation_key:
        existing = FixLoanEntry.objects.filter(operation_key=operation_key).first()
        if existing:
            if (
                existing.loan_id == loan.pk
                and existing.kind == kind
                and existing.entry_date == day
                and existing.principal_amount == principal
                and existing.interest_amount == interest
            ):
                return existing
            raise ValidationError(
                "This entry reference has already been used for another posting."
            )
    _action_date(account, day)
    if loan.settled_on or day < loan.issued_on:
        raise ValidationError(
            "Entries require an unsettled loan and a date on or after its issue date."
        )
    amounts = loan_balances(loan, day)
    if kind == FixLoanEntry.Kind.REPAYMENT and (
        principal > amounts["principal_due"] or interest > amounts["interest_due"]
    ):
        raise ValidationError(
            "A repayment cannot exceed the principal or assessed interest still due."
        )
    entry = FixLoanEntry(
        loan=loan,
        kind=kind,
        entry_date=day,
        principal_amount=principal,
        interest_amount=interest,
        reference=reference,
        notes=notes,
        recorded_by=actor,
    )
    if operation_key:
        entry.operation_key = operation_key
    entry.full_clean()
    entry.save()
    return entry


@transaction.atomic
def settle_loan(actor, loan_pk, day, interest_confirmed):
    loan = get_object_or_404(district_records(actor, FixDepositLoan), pk=loan_pk)
    account = _locked_account(actor, loan.account_id, "change")
    loan = FixDepositLoan.objects.select_for_update().get(pk=loan.pk)
    _action_date(account, day)
    if loan.settled_on:
        raise ValidationError("This loan has already been settled.")
    amounts = loan_balances(loan, day)
    if amounts["principal_due"] != 0 or amounts["interest_due"] != 0:
        raise ValidationError(
            "All principal and assessed interest must be paid before settlement."
        )
    if not interest_confirmed:
        raise ValidationError(
            "Confirm that all interest through the settlement date has been assessed and paid."
        )
    loan.settled_on, loan.settled_by = day, actor
    loan.full_clean()
    loan.save(update_fields=["settled_on", "settled_by"])
    return loan


@transaction.atomic
def request_withdrawal(actor, account_pk, day, amount, notes=""):
    account = _locked_account(actor, account_pk, "add")
    _action_date(account, day)
    status = account_status(account, day)
    amount = money(amount)
    if not status["can_withdraw"]:
        raise ValidationError(status["withdrawal_reasons"])
    if status["pending_withdrawal"]:
        raise ValidationError("This account already has a pending withdrawal request.")
    if amount > status["withdrawal_limit"]:
        raise ValidationError(
            f"The maximum withdrawal is LKR {status['withdrawal_limit']:.2f}."
        )
    withdrawal = FixDepositWithdrawal(
        account=account,
        requested_on=day,
        amount=amount,
        requested_by=actor,
        notes=notes,
    )
    withdrawal.full_clean()
    withdrawal.save()
    return withdrawal


@transaction.atomic
def review_withdrawal(actor, withdrawal_pk, day, release, notes=""):
    withdrawal = get_object_or_404(
        district_records(actor, FixDepositWithdrawal), pk=withdrawal_pk
    )
    account = _locked_account(actor, withdrawal.account_id, "change")
    withdrawal = FixDepositWithdrawal.objects.select_for_update().get(pk=withdrawal.pk)
    if withdrawal.status != FixDepositWithdrawal.Status.PENDING:
        raise ValidationError("This request has already been reviewed.")
    if day < withdrawal.requested_on or day > timezone.localdate():
        raise ValidationError("Use a review date from the request date through today.")
    if release:
        _action_date(account, day)
        status = account_status(account, day)
        if not status["can_withdraw"]:
            raise ValidationError(status["withdrawal_reasons"])
        if withdrawal.amount > status["withdrawal_limit"]:
            raise ValidationError(
                "The deposit balance changed; the requested amount exceeds the current withdrawal limit."
            )
        entry = PaymentTransaction(
            member=account.member,
            payment_type=account.policy.payment_type,
            transaction_date=day,
            debit=withdrawal.amount,
            credit=ZERO,
            reference=f"FD-W-{withdrawal.pk}",
            description="Fix Deposit withdrawal released after maturity and loan settlement.",
            created_by=actor,
        )
        entry._fix_withdrawal_posting = True
        entry.full_clean()
        entry.save()
        withdrawal.status = FixDepositWithdrawal.Status.RELEASED
        withdrawal.released_on = day
        withdrawal.ledger_transaction = entry
        withdrawal.balance_before = status["deposit_balance"]
        withdrawal.maximum_at_release = status["withdrawal_limit"]
        withdrawal.balance_after = status["deposit_balance"] - withdrawal.amount
    else:
        withdrawal.status = FixDepositWithdrawal.Status.REJECTED
    withdrawal.reviewed_by, withdrawal.reviewed_at = actor, timezone.now()
    if notes:
        withdrawal.notes = "\n".join(filter(None, [withdrawal.notes, notes]))
    withdrawal.full_clean()
    withdrawal.save()
    return withdrawal


def locked_payment_types(member_id):
    return set(
        FixDepositAccount.objects.filter(member_id=member_id)
        .filter(Q(loans__isnull=False) | Q(withdrawals__isnull=False))
        .values_list("policy__payment_type_id", flat=True)
    )


def guard_transaction(entry, *, deleting=False):
    if getattr(entry, "_fix_withdrawal_posting", False):
        return
    if (
        entry.pk
        and FixDepositWithdrawal.objects.filter(ledger_transaction_id=entry.pk).exists()
    ):
        raise ValidationError(
            "A released Fix Deposit withdrawal is managed by its own module and cannot be edited or deleted here."
        )
    if (
        not entry.payment_type_id
        or not FixDepositPolicy.objects.filter(
            payment_type_id=entry.payment_type_id
        ).exists()
    ):
        return
    original = (
        PaymentTransaction.objects.filter(pk=entry.pk).first() if entry.pk else None
    )
    unchanged = original is not None and all(
        getattr(original, field) == getattr(entry, field)
        for field in (
            "member_id",
            "payment_type_id",
            "transaction_date",
            "debit",
            "credit",
        )
    )
    if deleting:
        if entry.payment_type_id in locked_payment_types(entry.member_id):
            raise ValidationError(
                "Fix Deposit entries are protected after loan or withdrawal activity."
            )
        return
    if entry.debit is not None and entry.debit > 0 and not unchanged:
        raise ValidationError(
            "Use Fix Deposit Loans & Withdrawals to request and release a Fix Deposit withdrawal. Monthly top-ups can still be recorded as credits here."
        )
    if (
        original is not None
        and not unchanged
        and entry.payment_type_id in locked_payment_types(entry.member_id)
    ):
        raise ValidationError(
            "Amounts and dates of Fix Deposit entries stay fixed after loan or withdrawal activity. Notes and references may be corrected."
        )


def guard_opening_amount(amount, *, deleting=False):
    if amount.payment_type_id not in locked_payment_types(
        amount.opening_balance.member_id
    ):
        return
    original = type(amount).objects.filter(pk=amount.pk).first() if amount.pk else None
    if deleting or original is None or original.amount != amount.amount:
        raise ValidationError(
            "The Fix Deposit opening amount is locked after loan or withdrawal activity."
        )


def protect_opening_delete(balance):
    if locked_payment_types(balance.member_id):
        raise ProtectedError(
            "Opening balances are protected by Fix Deposit loan/withdrawal history.",
            [balance],
        )
