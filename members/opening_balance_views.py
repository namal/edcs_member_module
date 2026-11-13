from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render

from .models import Member, OpeningBalance
from .opening_balance_forms import OpeningBalanceForm, OpeningBalanceMemberForm
from .permissions import district_records, require_permission
from .views import paginated


def member_directory(request, selecting=False):
    members = (
        district_records(request.user, Member)
        .select_related("opening_balance")
        .prefetch_related("opening_balance__amounts")
    )
    selection_form = OpeningBalanceMemberForm(request.GET, user=request.user)
    selected_member = selected_balance = None
    if selection_form.is_valid():
        selected_member = selection_form.cleaned_data["member"]
        if selected_member:
            members = members.filter(pk=selected_member.pk)
            if request.user.has_perm("members.view_openingbalance"):
                selected_balance = (
                    OpeningBalance.objects.filter(member=selected_member)
                    .prefetch_related("amounts")
                    .first()
                ) or OpeningBalance(member=selected_member)
    else:
        members = members.none()
    query = request.GET.get("q", "").strip()
    if query:
        members = members.filter(
            Q(member_no__icontains=query) | Q(full_name__icontains=query)
        )
    status = request.GET.get("status", "")
    if status == "pending":
        members = members.filter(opening_balance__isnull=True)
    elif status == "ready":
        members = members.filter(opening_balance__isnull=False)
    return render(
        request,
        "opening_balances/member_list.html",
        {
            "section": "opening_balances",
            "title": "Opening Balances",
            "selecting": selecting,
            "selection_form": selection_form,
            "selected_member": selected_member,
            "selected_balance": selected_balance,
            **paginated(request, members.order_by("member_no", "pk")),
        },
    )


@login_required
def opening_balance_list(request):
    require_permission(request.user, OpeningBalance, "view")
    return member_directory(request)


@login_required
def opening_balance_form(request, pk=None):
    require_permission(
        request.user, OpeningBalance, "change" if pk is not None else "add"
    )
    balance = (
        get_object_or_404(
            district_records(request.user, OpeningBalance).select_related("member"),
            pk=pk,
        )
        if pk is not None
        else None
    )
    if balance:
        member = balance.member
    else:
        member_id = request.GET.get("member", "") or request.POST.get("member", "")
        if not member_id:
            if request.method == "POST":
                return HttpResponseBadRequest(
                    "Select a member before saving opening balances."
                )
            return member_directory(request, selecting=True)
        if not str(member_id).isdigit():
            return HttpResponseBadRequest("Select a valid member.")
        member = get_object_or_404(district_records(request.user, Member), pk=member_id)
        existing = OpeningBalance.objects.filter(member=member).first()
        if request.method == "GET" and existing:
            if request.user.has_perm("members.change_openingbalance"):
                return redirect("opening_balance_edit", pk=existing.pk)
            if request.user.has_perm("members.view_openingbalance"):
                return redirect("opening_balance_detail", pk=existing.pk)
            messages.error(
                request,
                "Opening balances are already recorded for this member. "
                "Ask an administrator for permission to view or edit them.",
            )
            return redirect("opening_balance_create")
    form = OpeningBalanceForm(
        request.POST if request.method == "POST" else None,
        instance=balance,
        member=member,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        try:
            saved = form.save()
        except ValidationError as error:
            form.add_error(None, " ".join(error.messages))
        except IntegrityError:
            if (
                balance is not None
                or not OpeningBalance.objects.filter(member=member).exists()
            ):
                raise
            form.add_error(
                None,
                "This member already has opening balances. Open the existing record to edit it.",
            )
        else:
            messages.success(
                request, f"Opening balances for {member.member_no} have been saved."
            )
            if request.user.has_perm("members.view_openingbalance"):
                return redirect("opening_balance_detail", pk=saved.pk)
            if pk is not None:
                return redirect("opening_balance_edit", pk=saved.pk)
            return redirect("opening_balance_create")
    return render(
        request,
        "opening_balances/balance_form.html",
        {
            "section": "opening_balances",
            "title": "Edit opening balances" if balance else "Set opening balances",
            "form": form,
            "balance": balance,
            "member": member,
        },
    )


@login_required
def opening_balance_detail(request, pk):
    require_permission(request.user, OpeningBalance, "view")
    balance = get_object_or_404(
        district_records(request.user, OpeningBalance)
        .select_related("member")
        .prefetch_related("amounts__payment_type"),
        pk=pk,
    )
    return render(
        request,
        "opening_balances/balance_detail.html",
        {
            "section": "opening_balances",
            "title": "Opening balances",
            "balance": balance,
        },
    )


@login_required
def opening_balance_delete(request, pk):
    require_permission(request.user, OpeningBalance, "delete")
    balance = get_object_or_404(
        district_records(request.user, OpeningBalance).select_related("member"), pk=pk
    )
    if request.method == "POST":
        balance.delete()
        messages.success(
            request,
            "The opening balances have been deleted. The member is still registered.",
        )
        return redirect(
            "opening_balance_list"
            if request.user.has_perm("members.view_openingbalance")
            else "dashboard"
        )
    return render(
        request,
        "opening_balances/balance_delete.html",
        {
            "section": "opening_balances",
            "title": "Delete opening balances",
            "balance": balance,
        },
    )
