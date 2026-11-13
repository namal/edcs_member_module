from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .fix_deposit_forms import (
    AmountActionForm,
    FixAccountForm,
    FixFilterForm,
    FixPolicyForm,
    LoanEntryForm,
    SettlementForm,
    WithdrawalReviewForm,
)
from .fix_deposit_services import (
    account_status,
    issue_loan,
    loan_balances,
    record_loan_entry,
    request_withdrawal,
    review_withdrawal,
    settle_loan,
)
from .models import (
    FixDepositAccount,
    FixDepositLoan,
    FixDepositPolicy,
    FixDepositWithdrawal,
    Member,
)
from .permissions import district_records, has_module_permission, require_permission
from .views import delete_record, master_form, paginated


def account_for(user, pk):
    return get_object_or_404(
        district_records(user, FixDepositAccount)
        .select_related("member", "policy__payment_type")
        .prefetch_related("loans__entries", "withdrawals"),
        pk=pk,
    )


@login_required
def fix_deposit_list(request):
    require_permission(request.user, FixDepositAccount, "view")
    form = FixFilterForm(request.GET, user=request.user)
    accounts = FixDepositAccount.objects.none()
    as_of = timezone.localdate()
    if form.is_valid():
        as_of = form.cleaned_data["as_of"]
        accounts = (
            district_records(request.user, FixDepositAccount)
            .select_related("member", "policy__payment_type")
            .prefetch_related("loans__entries", "withdrawals")
        )
        if form.cleaned_data["member"]:
            accounts = accounts.filter(member=form.cleaned_data["member"])
    context = paginated(request, accounts)
    for account in context["page_obj"]:
        account.summary = account_status(account, as_of)
    return render(
        request,
        "fix_deposits/account_list.html",
        {
            "section": "fix_deposits",
            "title": "Fix Deposit Loans & Withdrawals",
            "form": form,
            "as_of": as_of,
            **context,
        },
    )


@login_required
def fix_deposit_form(request, pk=None):
    require_permission(
        request.user, FixDepositAccount, "change" if pk is not None else "add"
    )
    account = account_for(request.user, pk) if pk is not None else None
    initial = {}
    if account is None and request.GET.get("member", "").isdigit():
        initial["member"] = get_object_or_404(
            district_records(request.user, Member), pk=request.GET["member"]
        ).pk
    form = FixAccountForm(
        request.POST if request.method == "POST" else None,
        instance=account,
        user=request.user,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                saved = form.save()
        except IntegrityError:
            form.add_error(
                None,
                "This member already has an account for the selected Fix Deposit rule.",
            )
        except ValidationError as error:
            form.add_error(None, " ".join(error.messages))
        else:
            messages.success(
                request,
                "Fix Deposit account registered. Existing balances and monthly credits remain in the payment ledger.",
            )
            if request.user.has_perm("members.view_fixdepositaccount"):
                return redirect("fix_deposit_detail", pk=saved.pk)
            if account:
                return redirect("fix_deposit_edit", pk=saved.pk)
            return redirect("fix_deposit_create")
    return render(
        request,
        "fix_deposits/account_form.html",
        {
            "section": "fix_deposits",
            "title": (
                "Edit Fix Deposit account"
                if account
                else "Register Fix Deposit account"
            ),
            "form": form,
        },
    )


@login_required
def fix_deposit_detail(request, pk):
    require_permission(request.user, FixDepositAccount, "view")
    account = account_for(request.user, pk)
    form = FixFilterForm(request.GET, user=request.user)
    form.fields.pop("member")
    context = {
        "section": "fix_deposits",
        "title": "Fix Deposit account",
        "account": account,
        "form": form,
    }
    if form.is_valid():
        context.update(account_status(account, form.cleaned_data["as_of"]))
        context["status_ready"] = True
    return render(request, "fix_deposits/account_detail.html", context)


@login_required
def fix_deposit_delete(request, pk):
    require_permission(request.user, FixDepositAccount, "delete")
    return delete_record(
        request, account_for(request.user, pk), "fix_deposit_list", "fix_deposits"
    )


@login_required
def fix_deposit_policy_list(request):
    require_permission(request.user, FixDepositPolicy, "view")
    return render(
        request,
        "fix_deposits/policy_list.html",
        {
            "section": "fix_deposit_policies",
            "title": "Fix Deposit Rules",
            **paginated(
                request, FixDepositPolicy.objects.select_related("payment_type")
            ),
        },
    )


@login_required
def fix_deposit_policy_form(request, pk=None):
    return master_form(
        request,
        FixDepositPolicy,
        FixPolicyForm,
        pk,
        "fix_deposit_policies",
        "Fix Deposit rule",
        "fix_deposit_policy_list",
    )


@login_required
def fix_deposit_policy_delete(request, pk):
    require_permission(request.user, FixDepositPolicy, "delete")
    return delete_record(
        request,
        get_object_or_404(FixDepositPolicy, pk=pk),
        "fix_deposit_policy_list",
        "fix_deposit_policies",
    )


@login_required
def fix_loan_detail(request, pk):
    require_permission(request.user, FixDepositAccount, "view")
    loan = get_object_or_404(
        district_records(request.user, FixDepositLoan).select_related(
            "account__member", "account__policy"
        ),
        pk=pk,
    )
    return render(
        request,
        "fix_deposits/loan_detail.html",
        {
            "section": "fix_deposits",
            "title": loan.reference,
            "loan": loan,
            **loan_balances(loan, timezone.localdate()),
        },
    )


def perform_action(
    request, account, form_class, title, action, *, loan=None, withdrawal=None, kind=""
):
    form = form_class(request.POST if request.method == "POST" else None)
    form.fields["day"].widget.attrs["min"] = account.cycle_started_on.isoformat()
    if request.method == "POST" and form.is_valid():
        try:
            result = action(form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, " ".join(error.messages))
        else:
            messages.success(request, f"{title} completed.")
            if request.user.has_perm("members.view_fixdepositaccount"):
                if loan:
                    return redirect("fix_loan_detail", pk=loan.pk)
                return redirect("fix_deposit_detail", pk=account.pk)
            return redirect("dashboard")
    try:
        day = forms.DateField().clean(form["day"].value())
    except ValidationError:
        day = timezone.localdate()
    return render(
        request,
        "fix_deposits/action_form.html",
        {
            "section": "fix_deposits",
            "title": title,
            "account": account,
            "loan": loan,
            "withdrawal": withdrawal,
            "form": form,
            "action_kind": kind,
            **account_status(account, day),
        },
    )


@login_required
def fix_loan_create(request, pk):
    require_permission(request.user, FixDepositAccount, "add")
    account = account_for(request.user, pk)
    return perform_action(
        request,
        account,
        AmountActionForm,
        "Issue Fix Deposit Loan",
        lambda data: issue_loan(
            request.user, account.pk, data["day"], data["amount"], data["notes"]
        ),
        kind="loan",
    )


@login_required
def fix_loan_entry(request, pk):
    require_permission(request.user, FixDepositAccount, "change")
    loan = get_object_or_404(district_records(request.user, FixDepositLoan), pk=pk)
    account = account_for(request.user, loan.account_id)
    return perform_action(
        request,
        account,
        LoanEntryForm,
        "Record loan repayment or interest charge",
        lambda data: record_loan_entry(
            request.user,
            loan.pk,
            data["day"],
            data["kind"],
            data["principal"],
            data["interest"],
            data["reference"],
            data["notes"],
            data["operation_key"],
        ),
        loan=loan,
    )


@login_required
def fix_loan_settle(request, pk):
    require_permission(request.user, FixDepositAccount, "change")
    loan = get_object_or_404(district_records(request.user, FixDepositLoan), pk=pk)
    account = account_for(request.user, loan.account_id)
    return perform_action(
        request,
        account,
        SettlementForm,
        "Settle Fix Deposit Loan",
        lambda data: settle_loan(
            request.user, loan.pk, data["day"], data["interest_confirmed"]
        ),
        loan=loan,
    )


@login_required
def fix_withdrawal_create(request, pk):
    require_permission(request.user, FixDepositAccount, "add")
    account = account_for(request.user, pk)
    return perform_action(
        request,
        account,
        AmountActionForm,
        "Request Fix Deposit withdrawal",
        lambda data: request_withdrawal(
            request.user, account.pk, data["day"], data["amount"], data["notes"]
        ),
        kind="withdrawal",
    )


@login_required
def fix_withdrawal_review(request, pk):
    require_permission(request.user, FixDepositAccount, "change")
    withdrawal = get_object_or_404(
        district_records(request.user, FixDepositWithdrawal), pk=pk
    )
    account = account_for(request.user, withdrawal.account_id)
    return perform_action(
        request,
        account,
        WithdrawalReviewForm,
        "Review Fix Deposit withdrawal",
        lambda data: review_withdrawal(
            request.user,
            withdrawal.pk,
            data["day"],
            data["decision"] == "release",
            data["notes"],
        ),
        withdrawal=withdrawal,
    )


@login_required
@require_GET
def fix_deposit_status(request, pk):
    if not any(
        has_module_permission(request.user, FixDepositAccount, action)
        for action in ("view", "add", "change")
    ):
        raise PermissionDenied
    account = account_for(request.user, pk)
    try:
        day = forms.DateField().clean(request.GET.get("date", timezone.localdate()))
    except ValidationError:
        return JsonResponse({"error": "Choose a valid date."}, status=400)
    data = account_status(account, day)
    return JsonResponse(
        {
            key: (
                str(data[key])
                if key
                in ("deposit_balance", "loan_limit", "withdrawal_limit", "minimum_held")
                else data[key]
            )
            for key in (
                "deposit_balance",
                "loan_limit",
                "withdrawal_limit",
                "minimum_held",
                "can_borrow",
                "can_withdraw",
                "loan_reasons",
                "withdrawal_reasons",
                "maturity_date",
                "next_loan_date",
            )
        }
    )
