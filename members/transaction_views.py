from decimal import Decimal
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from .approval_services import form_checkpoint, checkpoint
from .approval_scope import PAYMENT_EXCEPTIONS
from .contribution_services import below_plan_amount
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import (
    ChildDeposit,
    ChildDepositTransaction,
    Member,
    PaymentTransaction,
    PaymentType,
)
from .permissions import district_records, has_module_permission, require_permission
from .transaction_balances import (
    account_statement,
    balance_report,
    child_account_summary,
    member_balances,
)
from .account_transaction_forms import (
    AccountStatementFilterForm,
    AccountTransactionForm,
)
from .child_services import child_statement
from .transaction_forms import PaymentTransactionForm, TransactionFilterForm
from .views import paginated


def ledger_url(entry):
    return (
        reverse("payment_transaction_list")
        + "?"
        + urlencode({"member": entry.member_id, "payment_type": entry.payment_type_id})
    )


@login_required
@require_GET
def payment_transaction_balances(request):
    if not any(
        has_module_permission(request.user, PaymentTransaction, action)
        for action in ("view", "add", "change")
    ):
        raise PermissionDenied
    member_id = request.GET.get("member", "")
    if not member_id.isdigit():
        return JsonResponse({"error": "Select a valid member."}, status=400)
    member = get_object_or_404(district_records(request.user, Member), pk=member_id)
    try:
        as_of = forms.DateField().clean(request.GET.get("date", timezone.localdate()))
    except ValidationError:
        return JsonResponse({"error": "Choose a valid transaction date."}, status=400)
    entry = None
    if "entry" in request.GET:
        require_permission(request.user, PaymentTransaction, "change")
        entry_id = request.GET["entry"]
        if not entry_id.isdigit():
            return JsonResponse({"error": "Select a valid entry."}, status=400)
        entry = get_object_or_404(
            district_records(request.user, PaymentTransaction),
            pk=entry_id,
            member=member,
        )
    selected = request.GET.get("child_deposit")
    if selected:
        if not any(
            has_module_permission(request.user, ChildDeposit, action)
            for action in ("view", "add", "change")
        ):
            raise PermissionDenied
        if not selected.isdigit():
            return JsonResponse(
                {"error": "Select a valid child deposit account."}, status=400
            )
        get_object_or_404(
            district_records(request.user, ChildDeposit),
            pk=selected,
            child__member=member,
        )
    return JsonResponse(
        {
            **member_balances(member, as_of, exclude_entry=entry),
            **child_account_summary(request.user, member, as_of, selected=selected),
        }
    )


@login_required
def payment_transaction_list(request):
    require_permission(request.user, PaymentTransaction, "view")
    form = AccountStatementFilterForm(request.GET or None, user=request.user)
    entries = PaymentTransaction.objects.none()
    context = {
        "section": "payment_transactions",
        "title": "Debit / Credit",
        "form": form,
    }
    if form.is_bound and form.is_valid():
        data = form.cleaned_data
        start = data["date"] or data["date_from"]
        end = data["date"] or data["date_to"] or timezone.localdate()
        child_deposit = data["child_deposit"]
        if child_deposit:
            require_permission(request.user, ChildDeposit, "view")
            statement = child_statement(child_deposit, start, end)
            statement.update(
                opening_amount=(
                    child_deposit.opening_amount
                    if child_deposit.opened_on <= end
                    else Decimal("0.00")
                ),
                opening_date=child_deposit.opened_on,
                effective_date_from=(
                    max(start, child_deposit.opened_on)
                    if start
                    else child_deposit.opened_on
                ),
            )
        else:
            statement = account_statement(
                data["member"], data["payment_type"], start, end
            )
        entries = statement.pop("entries")
        context.update(
            selected_member=data["member"],
            selected_type=(
                child_deposit.deposit_type if child_deposit else data["payment_type"]
            ),
            selected_child=child_deposit,
            **statement,
            selection_query=urlencode(
                {
                    "member": data["member"].pk,
                    "payment_type": (
                        data["payment_type"]
                        if child_deposit
                        else data["payment_type"].pk
                    ),
                    **({"child_deposit": child_deposit.pk} if child_deposit else {}),
                }
            ),
        )
    context.update(paginated(request, entries.order_by("transaction_date", "pk")))
    from .ledger_tracking import attach_ledger_references

    context["page_obj"].object_list = attach_ledger_references(
        context["page_obj"].object_list,
        (
            ChildDepositTransaction
            if context.get("selected_child")
            else PaymentTransaction
        ),
        request.user,
    )
    return render(request, "transactions/ledger.html", context)


@login_required
def payment_transaction_report(request):
    require_permission(request.user, PaymentTransaction, "view")
    form = TransactionFilterForm(
        request.GET, user=request.user, require_selection=False
    )
    rows = []
    context = {
        "section": "payment_transactions",
        "title": "Member balances",
        "form": form,
    }
    if form.is_valid():
        data = form.cleaned_data
        members = district_records(request.user, Member)
        if data["member"]:
            members = members.filter(pk=data["member"].pk)
        start = data["date"] or data["date_from"]
        end = data["date"] or data["date_to"] or timezone.localdate()
        rows = balance_report(members, start, end, payment_type=data["payment_type"])
        date_query = {
            key: data[key].isoformat()
            for key in ("date", "date_from", "date_to")
            if data[key]
        }
        for row in rows:
            row["statement_url"] = (
                reverse("payment_transaction_list")
                + "?"
                + urlencode(
                    {
                        "member": row["member_id"],
                        "payment_type": row["payment_type_id"],
                        **date_query,
                    }
                )
            )
        context.update(
            report_ready=True,
            balance_date=end,
            date_from=start,
            total_brought_forward=sum(
                (row["brought_forward"] for row in rows), Decimal("0.00")
            ),
            total_debit=sum((row["total_debit"] for row in rows), Decimal("0.00")),
            total_credit=sum((row["total_credit"] for row in rows), Decimal("0.00")),
            final_balance=sum((row["final_balance"] for row in rows), Decimal("0.00")),
            member_count=len({row["member_id"] for row in rows}),
        )
    context.update(paginated(request, rows))
    return render(request, "transactions/balance_report.html", context)


@login_required
def payment_transaction_form(request, pk=None):
    require_permission(
        request.user, PaymentTransaction, "change" if pk is not None else "add"
    )
    entry = (
        get_object_or_404(district_records(request.user, PaymentTransaction), pk=pk)
        if pk is not None
        else None
    )
    if entry and hasattr(entry, "receipt_allocation"):
        raise PermissionDenied(
            "Allocated contribution entries are retained with their receipt and cannot be edited."
        )
    correction_id = (
        request.POST.get("reverses", "")
        if request.method == "POST"
        else request.GET.get("reverse", "")
    )
    correction = (
        get_object_or_404(
            district_records(request.user, PaymentTransaction), pk=correction_id
        )
        if entry is None and str(correction_id).isdigit()
        else None
    )
    form_class = (
        PaymentTransactionForm if entry or correction else AccountTransactionForm
    )
    form_kwargs = (
        {"instance": entry, "correction": correction} if entry or correction else {}
    )
    form = form_class(
        request.POST if request.method == "POST" else None,
        user=request.user,
        **form_kwargs,
        initial={
            key: request.GET[key]
            for key in ("member", "payment_type", "child_deposit")
            if entry is None and request.GET.get(key)
        },
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        is_child = isinstance(saved, ChildDepositTransaction)
        if is_child:
            require_permission(request.user, ChildDeposit, "add")
        if entry is None:
            saved.created_by = request.user
        try:
            with transaction.atomic():
                Member.objects.select_for_update().get(
                    pk=saved.deposit.child.member_id if is_child else saved.member_id
                )
                configured = None if is_child else below_plan_amount(saved)
                if is_child:
                    saved.deposit.refresh_from_db()
                    saved.full_clean()
                    saved.interest_rate = saved.deposit.rate_on(saved.transaction_date)
                    queued = form_checkpoint(
                        request,
                        form,
                        model=ChildDeposit,
                        action="add",
                        target=saved.deposit,
                    )
                elif configured is not None:
                    queued = checkpoint(
                        request,
                        PaymentTransaction,
                        "change" if entry else "add",
                        f"Below-plan category payment: {saved.member}",
                        target=entry,
                        form=form,
                        approval_module=PAYMENT_EXCEPTIONS,
                        required=True,
                        extra_summary=[
                            {
                                "label": "Configured category contribution (LKR)",
                                "value": str(configured),
                            }
                        ],
                    )
                else:
                    queued = form_checkpoint(request, form)
                if queued is not None:
                    return queued
                saved.save()
        except ValidationError as error:
            form.add_error(None, " ".join(error.messages))
        else:
            messages.success(request, "Debit / credit entry saved.")
            if is_child:
                if has_module_permission(request.user, ChildDeposit, "view"):
                    return redirect("child_deposit_detail", pk=saved.deposit_id)
                return redirect("payment_transaction_create")
            if request.user.has_perm("members.view_paymenttransaction"):
                return redirect(ledger_url(saved))
            if entry is not None:
                return redirect("payment_transaction_edit", pk=saved.pk)
            return redirect("payment_transaction_create")
    balance_summary = None
    member_id = form["member"].value()
    if str(member_id or "").isdigit():
        member = form.fields["member"].queryset.filter(pk=member_id).first()
        try:
            as_of = forms.DateField().clean(form["transaction_date"].value())
        except ValidationError:
            as_of = None
        if member is not None and as_of is not None:
            balance_summary = member_balances(member, as_of, exclude_entry=entry)
            balance_summary.update(
                child_account_summary(
                    request.user,
                    member,
                    as_of,
                    selected=(
                        form["child_deposit"].value()
                        if "child_deposit" in form.fields
                        else None
                    ),
                )
            )
    return render(
        request,
        "transactions/entry_form.html",
        {
            "section": "payment_transactions",
            "title": (
                "Edit debit / credit entry"
                if entry
                else (
                    "Correct with opposite entry"
                    if correction
                    else "Add debit / credit entry"
                )
            ),
            "form": form,
            "entry": entry,
            "correction": correction,
            "balance_summary": balance_summary,
            "selected_type_balance": (
                next(
                    (
                        row["balance"]
                        for row in balance_summary["balances"]
                        if str(row["payment_type_id"])
                        == str(form["payment_type"].value())
                    ),
                    "0.00",
                )
                if balance_summary
                else None
            ),
            "back_url": (
                ledger_url(entry) if entry else reverse("payment_transaction_list")
            ),
            "payment_type_rates": {
                str(item.pk): str(item.interest_rate)
                for item in (
                    form.fields["payment_type"].queryset
                    if hasattr(form.fields["payment_type"], "queryset")
                    else PaymentType.objects.filter(is_active=True)
                )
            },
            "payment_type_frequencies": {
                str(item.pk): item.interest_period_display
                for item in (
                    form.fields["payment_type"].queryset
                    if hasattr(form.fields["payment_type"], "queryset")
                    else PaymentType.objects.filter(is_active=True)
                )
            },
        },
    )


@login_required
def payment_transaction_delete(request, pk):
    if not any(
        has_module_permission(request.user, PaymentTransaction, action)
        for action in ("view", "add", "change")
    ):
        raise PermissionDenied
    get_object_or_404(district_records(request.user, PaymentTransaction), pk=pk)
    raise PermissionDenied(
        "Debit / credit entries cannot be deleted. Record an opposite correction instead."
    )
