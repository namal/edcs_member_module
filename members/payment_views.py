from django.contrib import messages
from .approval_services import form_checkpoint, checkpoint
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render

from .models import Member, OpeningBalance, PaymentPlan, PaymentType
from .payment_forms import PaymentPlanForm, PaymentTypeForm
from .permissions import district_records, require_permission
from .views import delete_record, master_form, paginated


def member_directory(request, selecting=False):
    members = (
        district_records(request.user, Member)
        .select_related("payment_plan")
        .prefetch_related("payment_plan__amounts")
    )
    query = request.GET.get("q", "").strip()
    if query:
        members = members.filter(
            Q(member_no__icontains=query) | Q(full_name__icontains=query)
        )
    status = request.GET.get("status", "")
    if selecting or status == "pending":
        members = members.filter(payment_plan__isnull=True)
    elif status == "ready":
        members = members.filter(payment_plan__isnull=False)
    return render(
        request,
        "payments/member_list.html",
        {
            "section": "payment_plans",
            "title": "Payment Plan",
            "selecting": selecting,
            **paginated(request, members.order_by("member_no", "pk")),
        },
    )


@login_required
def payment_plan_list(request):
    require_permission(request.user, PaymentPlan, "view")
    return member_directory(request)


@login_required
def payment_plan_form(request, pk=None):
    require_permission(request.user, PaymentPlan, "change" if pk is not None else "add")
    plan = (
        get_object_or_404(
            district_records(request.user, PaymentPlan).select_related("member"), pk=pk
        )
        if pk is not None
        else None
    )
    if plan:
        member = plan.member
    else:
        member_id = request.GET.get("member", "") or request.POST.get("member", "")
        if not member_id:
            if request.method == "POST":
                return HttpResponseBadRequest(
                    "Select a member before saving a payment plan."
                )
            return member_directory(request, selecting=True)
        member = (
            get_object_or_404(district_records(request.user, Member), pk=member_id)
            if str(member_id).isdigit()
            else None
        )
        if member is None:
            return HttpResponseBadRequest("Select a valid member.")
        existing = PaymentPlan.objects.filter(member=member).first()
        if request.method == "GET" and existing:
            if request.user.has_perm("members.change_paymentplan"):
                return redirect("payment_plan_edit", pk=existing.pk)
            if request.user.has_perm("members.view_paymentplan"):
                return redirect("payment_plan_detail", pk=existing.pk)
    form = PaymentPlanForm(
        request.POST if request.method == "POST" else None,
        instance=plan,
        member=member,
        user=request.user,
        initial={"apply_template": request.GET.get("template", "")},
    )
    if request.method == "POST" and form.is_valid():
        queued = form_checkpoint(request, form)
        if queued is not None:
            return queued
        try:
            saved = form.save()
        except IntegrityError:
            if (
                plan is not None
                or not PaymentPlan.objects.filter(member=member).exists()
            ):
                raise
            form.add_error(
                None,
                "This member already has a payment plan. Open the existing plan to edit it.",
            )
        else:
            messages.success(
                request, f"Payment plan for {member.member_no} has been saved."
            )
            if request.user.has_perm("members.view_paymentplan"):
                return redirect("payment_plan_detail", pk=saved.pk)
            if pk is not None:
                return redirect("payment_plan_edit", pk=saved.pk)
            return redirect("payment_plan_create")
    return render(
        request,
        "payments/plan_form.html",
        {
            "section": "payment_plans",
            "title": "Edit payment plan" if plan else "Set payment plan",
            "form": form,
            "plan": plan,
            "member": member,
            "member_opening_balance": (
                OpeningBalance.objects.filter(member=member)
                .prefetch_related("amounts")
                .first()
                if request.user.has_perm("members.view_openingbalance")
                else None
            ),
        },
    )


@login_required
def payment_plan_detail(request, pk):
    require_permission(request.user, PaymentPlan, "view")
    plan = get_object_or_404(
        district_records(request.user, PaymentPlan)
        .select_related("member")
        .prefetch_related("amounts__payment_type"),
        pk=pk,
    )
    return render(
        request,
        "payments/plan_detail.html",
        {
            "section": "payment_plans",
            "title": "Payment plan",
            "plan": plan,
            "member_opening_balance": (
                OpeningBalance.objects.filter(member=plan.member)
                .prefetch_related("amounts")
                .first()
                if request.user.has_perm("members.view_openingbalance")
                else None
            ),
        },
    )


@login_required
def payment_plan_delete(request, pk):
    require_permission(request.user, PaymentPlan, "delete")
    plan = get_object_or_404(
        district_records(request.user, PaymentPlan).select_related("member"), pk=pk
    )
    if request.method == "POST":
        queued = checkpoint(
            request, PaymentPlan, "delete", f"Delete payment plan: {plan}", target=plan
        )
        if queued is not None:
            return queued
        try:
            plan.delete()
        except ProtectedError:
            messages.error(
                request,
                "This payment plan is retained by contribution receipts or ledger history. Edit its future amounts instead.",
            )
            return redirect(
                (
                    "payment_plan_detail"
                    if request.user.has_perm("members.view_paymentplan")
                    else "dashboard"
                ),
                **(
                    {"pk": plan.pk}
                    if request.user.has_perm("members.view_paymentplan")
                    else {}
                ),
            )
        messages.success(
            request,
            "The payment plan has been deleted. The member is still registered.",
        )
        return redirect(
            "payment_plan_list"
            if request.user.has_perm("members.view_paymentplan")
            else "dashboard"
        )
    return render(
        request,
        "payments/plan_delete.html",
        {"section": "payment_plans", "title": "Delete payment plan", "plan": plan},
    )


@login_required
def payment_type_list(request):
    require_permission(request.user, PaymentType, "view")
    types = PaymentType.objects.annotate(
        plan_count=Count(
            "plan_amounts",
            filter=Q(
                plan_amounts__plan__in=district_records(request.user, PaymentPlan)
            ),
        )
    ).order_by("sort_order", "name", "pk")
    query = request.GET.get("q", "").strip()
    if query:
        types = types.filter(name__icontains=query)
    return render(
        request,
        "payments/type_list.html",
        {
            "section": "payment_types",
            "title": "Payment Types",
            **paginated(request, types),
        },
    )


@login_required
def payment_type_form(request, pk=None):
    return master_form(
        request,
        PaymentType,
        PaymentTypeForm,
        pk,
        "payment_types",
        "Payment type",
        "payment_type_list",
    )


@login_required
def payment_type_delete(request, pk):
    require_permission(request.user, PaymentType, "delete")
    return delete_record(
        request,
        get_object_or_404(PaymentType, pk=pk),
        "payment_type_list",
        "payment_types",
    )
