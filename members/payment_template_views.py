from django.contrib import messages
from .approval_services import form_checkpoint, checkpoint
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from .models import PaymentPlan, PaymentPlanTemplate
from .payment_forms import PaymentPlanTemplateForm
from .permissions import has_module_permission, require_permission
from .views import paginated


@login_required
def payment_plan_template_list(request):
    require_permission(request.user, PaymentPlanTemplate, "view")
    templates = PaymentPlanTemplate.objects.prefetch_related("amounts__payment_type")
    query = request.GET.get("q", "").strip()
    if query:
        templates = templates.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        )
    return render(
        request,
        "payments/template_list.html",
        {
            "section": "payment_plan_templates",
            "title": "Payment plan templates",
            **paginated(request, templates),
        },
    )


@login_required
def payment_plan_template_form(request, pk=None):
    require_permission(
        request.user, PaymentPlanTemplate, "change" if pk is not None else "add"
    )
    template = get_object_or_404(PaymentPlanTemplate, pk=pk) if pk is not None else None
    form = PaymentPlanTemplateForm(
        request.POST if request.method == "POST" else None,
        instance=template,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        queued = form_checkpoint(request, form)
        if queued is not None:
            return queued
        form.save()
        messages.success(
            request,
            "Payment plan template saved. Existing member plans keep their own amounts.",
        )
        return redirect(
            "payment_plan_template_list"
            if request.user.has_perm("members.view_paymentplantemplate")
            else "dashboard"
        )
    return render(
        request,
        "payments/template_form.html",
        {
            "section": "payment_plan_templates",
            "title": (
                "Edit payment plan template"
                if template
                else "Create payment plan template"
            ),
            "form": form,
        },
    )


@login_required
def payment_plan_template_delete(request, pk):
    require_permission(request.user, PaymentPlanTemplate, "delete")
    template = get_object_or_404(PaymentPlanTemplate, pk=pk)
    if request.method == "POST":
        queued = checkpoint(
            request,
            PaymentPlanTemplate,
            "delete",
            f"Delete payment plan template: {template}",
            target=template,
        )
        if queued is not None:
            return queued
        template.delete()
        messages.success(
            request,
            "Template deleted. Member payment plans retain their copied amounts.",
        )
        return redirect(
            "payment_plan_template_list"
            if request.user.has_perm("members.view_paymentplantemplate")
            else "dashboard"
        )
    return render(
        request,
        "payments/template_delete.html",
        {
            "section": "payment_plan_templates",
            "title": "Delete payment plan template",
            "template": template,
        },
    )


@login_required
@require_GET
def payment_plan_template_options(request):
    if not any(
        has_module_permission(request.user, PaymentPlan, action)
        for action in ("add", "change")
    ):
        raise PermissionDenied
    pk = request.GET.get("template", "")
    template = (
        get_object_or_404(PaymentPlanTemplate, pk=pk, is_active=True)
        if pk.isdigit()
        else None
    )
    if template is None:
        return JsonResponse({"error": "Select a valid template."}, status=400)
    amounts = list(
        template.amounts.filter(payment_type__is_active=True).values(
            "payment_type_id", "amount"
        )
    )
    if not amounts:
        return JsonResponse(
            {"error": "This template has no active payment types."}, status=400
        )
    return JsonResponse({"name": template.name, "amounts": amounts})
