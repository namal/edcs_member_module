from decimal import Decimal

from django import forms
from django.contrib import messages
from .approval_services import form_checkpoint, checkpoint
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .child_forms import (
    ChildDepositFilterForm,
    ChildDepositForm,
    ChildDepositTypeForm,
    ChildEntryForm,
    ChildForm,
    ChildStatementFilterForm,
)
from .child_services import (
    child_statement,
    deposit_balance,
    deposits_with_balances,
    display_balance,
)
from .models import (
    Child,
    ChildDeposit,
    ChildDepositTransaction,
    ChildDepositType,
    Member,
)
from .permissions import district_records, has_module_permission, require_permission
from .views import delete_record, master_form, paginated


def scoped_deposit(user, pk):
    return get_object_or_404(
        district_records(user, ChildDeposit).select_related(
            "child__member", "deposit_type"
        ),
        pk=pk,
    )


def initial_relation(request, model, key, pk):
    value = request.GET.get(key, "")
    if pk is None and value.isdigit():
        return {
            key: get_object_or_404(district_records(request.user, model), pk=value).pk
        }
    return {}


def after_form(request, model, obj, prefix):
    if request.user.has_perm(f"members.view_{model._meta.model_name}"):
        return redirect(f"{prefix}_detail", pk=obj.pk)
    if request.user.has_perm(f"members.change_{model._meta.model_name}"):
        return redirect(f"{prefix}_edit", pk=obj.pk)
    return redirect(f"{prefix}_create")


@login_required
def child_list(request):
    require_permission(request.user, Child, "view")
    form = ChildDepositFilterForm(request.GET, user=request.user)
    children = district_records(request.user, Child).select_related("member")
    if form.is_valid():
        if form.cleaned_data["member"]:
            children = children.filter(member=form.cleaned_data["member"])
    else:
        children = children.none()
    query = request.GET.get("q", "").strip()
    if query:
        children = children.filter(
            Q(name__icontains=query)
            | Q(member__member_no__icontains=query)
            | Q(member__full_name__icontains=query)
        )
    children = children.order_by("member__member_no", "name", "pk")
    return render(
        request,
        "children/child_list.html",
        {
            "section": "children",
            "title": "Children",
            "form": form,
            "selected_member": form.cleaned_data.get("member"),
            **paginated(request, children),
        },
    )


@login_required
def child_form(request, pk=None):
    require_permission(request.user, Child, "change" if pk is not None else "add")
    child = (
        get_object_or_404(district_records(request.user, Child), pk=pk)
        if pk is not None
        else None
    )
    form = ChildForm(
        request.POST if request.method == "POST" else None,
        instance=child,
        user=request.user,
        initial=initial_relation(request, Member, "member", pk),
    )
    if request.method == "POST" and form.is_valid():
        queued = form_checkpoint(request, form)
        if queued is not None:
            return queued
        saved = form.save()
        messages.success(request, "Child details saved.")
        return after_form(request, Child, saved, "child")
    return render(
        request,
        "children/form.html",
        {
            "section": "children",
            "title": "Edit child" if child else "Add child",
            "form": form,
            "kind": "child",
            "back": "child_list",
            "can_view": request.user.has_perm("members.view_child"),
        },
    )


@login_required
def child_detail(request, pk):
    require_permission(request.user, Child, "view")
    child = get_object_or_404(
        district_records(request.user, Child).select_related("member"), pk=pk
    )
    as_of = timezone.localdate()
    deposits = []
    if request.user.has_perm("members.view_childdeposit"):
        deposits = [
            display_balance(item, as_of)
            for item in deposits_with_balances(request.user, as_of).filter(child=child)
        ]
    return render(
        request,
        "children/child_detail.html",
        {
            "section": "children",
            "title": child.name,
            "child": child,
            "deposits": deposits,
            "total_balance": sum((item.balance for item in deposits), Decimal("0.00")),
            "as_of": as_of,
        },
    )


@login_required
def child_delete(request, pk):
    require_permission(request.user, Child, "delete")
    return delete_record(
        request,
        get_object_or_404(district_records(request.user, Child), pk=pk),
        "child_list",
        "children",
    )


@login_required
def child_deposit_type_list(request):
    require_permission(request.user, ChildDepositType, "view")
    types = ChildDepositType.objects.all()
    query = request.GET.get("q", "").strip()
    if query:
        types = types.filter(name__icontains=query)
    return render(
        request,
        "children/type_list.html",
        {
            "section": "child_deposit_types",
            "title": "Child Deposit Types",
            **paginated(request, types),
        },
    )


@login_required
def child_deposit_type_form(request, pk=None):
    return master_form(
        request,
        ChildDepositType,
        ChildDepositTypeForm,
        pk,
        "child_deposit_types",
        "Child deposit type",
        "child_deposit_type_list",
    )


@login_required
def child_deposit_type_delete(request, pk):
    require_permission(request.user, ChildDepositType, "delete")
    return delete_record(
        request,
        get_object_or_404(ChildDepositType, pk=pk),
        "child_deposit_type_list",
        "child_deposit_types",
    )


@login_required
def child_deposit_list(request):
    require_permission(request.user, ChildDeposit, "view")
    form = ChildDepositFilterForm(request.GET, user=request.user)
    deposits = ChildDeposit.objects.none()
    total = Decimal("0.00")
    as_of = timezone.localdate()
    if form.is_valid():
        data = form.cleaned_data
        as_of = data["as_of"]
        deposits = deposits_with_balances(request.user, as_of)
        for name, lookup in (
            ("member", "child__member"),
            ("child", "child"),
            ("deposit_type", "deposit_type"),
        ):
            if data[name]:
                deposits = deposits.filter(**{lookup: data[name]})
        total = deposits.aggregate(total=Sum("as_of_balance", default=Decimal("0.00")))[
            "total"
        ]
    context = paginated(request, deposits)
    for deposit in context["page_obj"]:
        display_balance(deposit, as_of)
    return render(
        request,
        "children/deposit_list.html",
        {
            "section": "child_deposits",
            "title": "Child Deposits",
            "form": form,
            "as_of": as_of,
            "total_balance": total,
            **context,
        },
    )


@login_required
def child_deposit_form(request, pk=None):
    require_permission(
        request.user, ChildDeposit, "change" if pk is not None else "add"
    )
    deposit = scoped_deposit(request.user, pk) if pk is not None else None
    form = ChildDepositForm(
        request.POST if request.method == "POST" else None,
        instance=deposit,
        user=request.user,
        initial=initial_relation(request, Child, "child", pk),
    )
    if request.method == "POST" and form.is_valid():
        queued = form_checkpoint(request, form)
        if queued is not None:
            return queued
        saved = form.save()
        messages.success(request, "Child deposit saved.")
        return after_form(request, ChildDeposit, saved, "child_deposit")
    return render(
        request,
        "children/form.html",
        {
            "section": "child_deposits",
            "title": "Edit child deposit" if deposit else "Open child deposit",
            "form": form,
            "kind": "deposit",
            "back": "child_deposit_list",
            "can_view": request.user.has_perm("members.view_childdeposit"),
            "child_birth_dates": {
                str(item.pk): item.date_of_birth.isoformat()
                for item in form.fields["child"].queryset
            },
            "child_rate_rules": {
                str(item.pk): {
                    "age": item.age_threshold,
                    "below": str(item.rate_below_threshold),
                    "from": str(item.rate_from_threshold),
                }
                for item in form.fields["deposit_type"].queryset
            },
        },
    )


@login_required
def child_deposit_detail(request, pk):
    require_permission(request.user, ChildDeposit, "view")
    deposit = scoped_deposit(request.user, pk)
    form = ChildStatementFilterForm(request.GET, user=request.user)
    entries = ChildDepositTransaction.objects.none()
    context = {
        "section": "child_deposits",
        "title": deposit.account_no,
        "deposit": deposit,
        "form": form,
        "interest_years": (
            deposit.interest_years.filter(posted_at__isnull=False).order_by(
                "-year_start"
            )
            if request.user.has_perm("members.view_depositinterestyear")
            else []
        ),
    }
    if form.is_valid():
        data = form.cleaned_data
        statement = child_statement(
            deposit,
            data["date"] or data["date_from"],
            data["date"] or data["date_to"] or timezone.localdate(),
        )
        entries = statement.pop("entries")
        context.update(statement)
        context["statement_ready"] = True
    context.update(paginated(request, entries.order_by("transaction_date", "pk")))
    from .ledger_tracking import attach_ledger_references

    context["page_obj"].object_list = attach_ledger_references(
        context["page_obj"].object_list, ChildDepositTransaction, request.user
    )
    return render(request, "children/deposit_detail.html", context)


@login_required
def child_deposit_delete(request, pk):
    require_permission(request.user, ChildDeposit, "delete")
    return delete_record(
        request,
        scoped_deposit(request.user, pk),
        "child_deposit_list",
        "child_deposits",
    )


@login_required
def child_entry_form(request, deposit_pk=None, pk=None):
    require_permission(
        request.user, ChildDeposit, "change" if pk is not None else "add"
    )
    entry = (
        get_object_or_404(
            district_records(request.user, ChildDepositTransaction), pk=pk
        )
        if pk is not None
        else None
    )
    deposit = scoped_deposit(request.user, entry.deposit_id if entry else deposit_pk)
    if entry and hasattr(entry, "receipt_allocation"):
        raise PermissionDenied(
            "Allocated contribution entries are retained with their receipt and cannot be edited."
        )
    original_date = entry.transaction_date if entry else None
    correction_id = (
        request.POST.get("reverses", "")
        if request.method == "POST"
        else request.GET.get("reverse", "")
    )
    correction = (
        get_object_or_404(ChildDepositTransaction, deposit=deposit, pk=correction_id)
        if entry is None and str(correction_id).isdigit()
        else None
    )
    form = ChildEntryForm(
        request.POST if request.method == "POST" else None,
        instance=entry,
        deposit=deposit,
        user=request.user,
        correction=correction,
    )
    if request.method == "POST" and form.is_valid():
        queued = form_checkpoint(
            request,
            form,
            model=ChildDeposit,
            action="change" if entry else "add",
            target=entry or deposit,
        )
        if queued is not None:
            return queued
        saved = form.save(commit=False)
        if entry is None or saved.transaction_date != original_date:
            saved.interest_rate = deposit.rate_on(saved.transaction_date)
        if entry is None:
            saved.created_by = request.user
        try:
            saved.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Child deposit debit / credit entry saved.")
            if request.user.has_perm("members.view_childdeposit"):
                return redirect("child_deposit_detail", pk=deposit.pk)
            if entry:
                return redirect("child_entry_edit", pk=saved.pk)
            return redirect("child_entry_create", deposit_pk=deposit.pk)
    try:
        as_of = forms.DateField().clean(form["transaction_date"].value())
    except ValidationError:
        as_of = None
    return render(
        request,
        "children/entry_form.html",
        {
            "section": "child_deposits",
            "title": (
                "Edit child entry"
                if entry
                else (
                    "Correct with opposite entry"
                    if correction
                    else "Add child debit / credit"
                )
            ),
            "deposit": deposit,
            "entry": entry,
            "correction": correction,
            "form": form,
            "saved_balance": (
                deposit_balance(deposit, as_of, exclude_entry=entry) if as_of else None
            ),
            "configured_rate": deposit.rate_on(as_of) if as_of else None,
            "child_age": deposit.child.age_on(as_of) if as_of else None,
        },
    )


@login_required
def child_entry_delete(request, pk):
    if not any(
        has_module_permission(request.user, ChildDeposit, action)
        for action in ("view", "add", "change", "delete")
    ):
        raise PermissionDenied
    get_object_or_404(district_records(request.user, ChildDepositTransaction), pk=pk)
    raise PermissionDenied(
        "Debit / credit entries cannot be deleted. Record an opposite correction instead."
    )


@login_required
@require_GET
def child_deposit_status(request, pk):
    if not any(
        has_module_permission(request.user, ChildDeposit, action)
        for action in ("view", "add", "change")
    ):
        raise PermissionDenied
    deposit = scoped_deposit(request.user, pk)
    try:
        as_of = forms.DateField().clean(request.GET.get("date", timezone.localdate()))
    except ValidationError:
        return JsonResponse({"error": "Choose a valid transaction date."}, status=400)
    entry = None
    if "entry" in request.GET:
        require_permission(request.user, ChildDeposit, "change")
        entry_id = request.GET["entry"]
        if not entry_id.isdigit():
            return JsonResponse({"error": "Invalid entry."}, status=400)
        entry = get_object_or_404(deposit.transactions, pk=entry_id)
    rate = deposit.rate_on(as_of)
    return JsonResponse(
        {
            "balance": format(
                deposit_balance(deposit, as_of, exclude_entry=entry), ".2f"
            ),
            "rate": format(rate, ".2f") if rate is not None else None,
            "age": deposit.child.age_on(as_of),
            "as_of": as_of.isoformat(),
        }
    )
