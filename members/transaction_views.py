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

from .models import Member, PaymentTransaction
from .permissions import district_records, has_module_permission, require_permission
from .transaction_balances import account_statement, balance_report, member_balances
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
    return JsonResponse(member_balances(member, as_of, exclude_entry=entry))


@login_required
def payment_transaction_list(request):
    require_permission(request.user, PaymentTransaction, "view")
    form = TransactionFilterForm(request.GET or None, user=request.user)
    entries = PaymentTransaction.objects.none()
    context = {
        "section": "payment_transactions",
        "title": "Debit / Credit",
        "form": form,
    }
    if form.is_bound and form.is_valid():
        data = form.cleaned_data
        statement = account_statement(
            data["member"],
            data["payment_type"],
            data["date"] or data["date_from"],
            data["date"] or data["date_to"] or timezone.localdate(),
        )
        entries = statement.pop("entries")
        context.update(
            selected_member=data["member"],
            selected_type=data["payment_type"],
            **statement,
            selection_query=urlencode(
                {"member": data["member"].pk, "payment_type": data["payment_type"].pk}
            ),
        )
    context.update(paginated(request, entries.order_by("transaction_date", "pk")))
    from .ledger_tracking import attach_ledger_references

    context["page_obj"].object_list = attach_ledger_references(
        context["page_obj"].object_list, PaymentTransaction, request.user
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
    form = PaymentTransactionForm(
        request.POST if request.method == "POST" else None,
        instance=entry,
        user=request.user,
        initial={
            key: request.GET[key]
            for key in ("member", "payment_type")
            if entry is None and request.GET.get(key, "").isdigit()
        },
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        if entry is None:
            saved.created_by = request.user
        try:
            with transaction.atomic():
                Member.objects.select_for_update().get(pk=saved.member_id)
                configured = below_plan_amount(saved)
                if configured is not None:
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
    return render(
        request,
        "transactions/entry_form.html",
        {
            "section": "payment_transactions",
            "title": (
                "Edit debit / credit entry" if entry else "Add debit / credit entry"
            ),
            "form": form,
            "entry": entry,
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
                for item in form.fields["payment_type"].queryset
            },
            "payment_type_frequencies": {
                str(item.pk): item.interest_period_display
                for item in form.fields["payment_type"].queryset
            },
        },
    )


@login_required
def payment_transaction_delete(request, pk):
    require_permission(request.user, PaymentTransaction, "delete")
    entry = get_object_or_404(
        district_records(request.user, PaymentTransaction).select_related(
            "member", "payment_type"
        ),
        pk=pk,
    )
    if request.method == "POST":
        queued = checkpoint(
            request,
            PaymentTransaction,
            "delete",
            f"Delete debit / credit entry: {entry}",
            target=entry,
        )
        if queued is not None:
            return queued
        destination = ledger_url(entry)
        try:
            entry.delete()
        except ProtectedError as error:
            messages.error(request, str(error.args[0]))
        else:
            messages.success(request, "Debit / credit entry deleted.")
        return redirect(
            destination
            if request.user.has_perm("members.view_paymenttransaction")
            else "dashboard"
        )
    return render(
        request,
        "transactions/entry_delete.html",
        {
            "section": "payment_transactions",
            "title": "Delete debit / credit entry",
            "entry": entry,
            "back_url": ledger_url(entry),
        },
    )
