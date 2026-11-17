from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .access_views import superuser_required
from .approval_forms import HierarchyForm, HierarchyLevels, DecisionForm
from .approval_models import ApprovalHierarchy, ApprovalLevel, ApprovalRequest
from .approval_services import (
    audit,
    decide,
    decision_rights,
    hierarchy_snapshot,
    visible_requests,
)
from .permissions import MODULES
from .approval_scope import ALL_MODULES, PAYMENT_EXCEPTIONS
from .views import paginated


@superuser_required
def hierarchy_list(request):
    rows = ApprovalHierarchy.objects.prefetch_related("levels__approvers")
    labels = {model._meta.model_name: label for model, label, *_ in MODULES}
    labels[ALL_MODULES] = "All modules"
    labels[PAYMENT_EXCEPTIONS] = "Payment exceptions"
    rows = list(rows)
    for row in rows:
        row.module_label = labels.get(row.module, row.module)
        row.scope_label = (
            "Below-plan payment"
            if row.module == PAYMENT_EXCEPTIONS
            else (
                "Member transfer"
                if row.module == "membertransfer"
                else "Edit existing records"
            )
        )
    return render(
        request,
        "approvals/hierarchy_list.html",
        {
            "title": "Approval hierarchies",
            "section": "approval_hierarchies",
            **paginated(request, rows),
        },
    )


@superuser_required
def hierarchy_form(request, pk=None):
    hierarchy = get_object_or_404(ApprovalHierarchy, pk=pk) if pk else None
    form = HierarchyForm(
        request.POST if request.method == "POST" else None, instance=hierarchy
    )
    initial = (
        [
            {
                "position": level.position,
                "name": level.name,
                "approvers": list(level.approvers.values_list("pk", flat=True)),
                "allow_rejection": level.allow_rejection,
            }
            for level in hierarchy.levels.all()
        ]
        if hierarchy
        else [{"position": 1, "name": "First approval", "allow_rejection": True}]
    )
    formset = HierarchyLevels(
        request.POST if request.method == "POST" else None,
        prefix="levels",
        initial=initial,
    )
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        try:
            with transaction.atomic():
                old = (
                    ApprovalHierarchy.objects.select_for_update().get(pk=pk)
                    if pk
                    else None
                )
                if old and old.version != form.cleaned_data["version"]:
                    raise ValidationError(
                        "This hierarchy was updated by another administrator. Reload it before saving."
                    )
                before = hierarchy_snapshot(old) if old else {}
                saved = form.save(commit=False)
                saved.version = old.version + 1 if old else 1
                saved.save()
                saved.levels.all().delete()
                for item in formset.cleaned_data:
                    if not item or item.get("DELETE"):
                        continue
                    level = ApprovalLevel.objects.create(
                        hierarchy=saved,
                        position=item["position"],
                        name=item["name"],
                        allow_rejection=item["allow_rejection"],
                    )
                    level.approvers.set(item["approvers"])
                audit(
                    request.user,
                    saved,
                    "hierarchy_updated" if old else "hierarchy_created",
                    reason=form.cleaned_data["reason"],
                    details={"before": before, "after": hierarchy_snapshot(saved)},
                )
        except ValidationError as error:
            form.add_error(None, error)
        except IntegrityError:
            form.add_error(
                None,
                "An active hierarchy already covers this module and action. Edit it or deactivate it first.",
            )
        else:
            messages.success(
                request,
                "Approval hierarchy saved. Pending requests retain the levels and users assigned when submitted.",
            )
            return redirect("approval_hierarchy_list")
    return render(
        request,
        "approvals/hierarchy_form.html",
        {
            "title": "Edit approval hierarchy" if pk else "Create approval hierarchy",
            "section": "approval_hierarchies",
            "form": form,
            "formset": formset,
            "hierarchy": hierarchy,
        },
    )


@login_required
def request_list(request):
    rows = visible_requests(request.user).select_related("requested_by")
    status = request.GET.get("status", "pending")
    if status in ApprovalRequest.Status.values:
        rows = rows.filter(status=status)
    query = request.GET.get("q", "").strip()
    if query:
        rows = rows.filter(
            Q(reference__icontains=query)
            | Q(description__icontains=query)
            | Q(requester_name__icontains=query)
        )
    if request.GET.get("mine"):
        rows = rows.filter(requested_by=request.user)
    return render(
        request,
        "approvals/request_list.html",
        {
            "title": "Approvals",
            "section": "approvals",
            "statuses": ApprovalRequest.Status.choices,
            "selected_status": status,
            **paginated(request, rows),
        },
    )


@login_required
def request_detail(request, pk):
    record = get_object_or_404(
        visible_requests(request.user).select_related("hierarchy"), pk=pk
    )
    form = DecisionForm()
    form.fields["reason"].required = request.user.is_superuser
    if request.method == "POST":
        form = DecisionForm(request.POST)
        form.fields["reason"].required = request.user.is_superuser
        if form.is_valid():
            try:
                record = decide(
                    request.user,
                    record.pk,
                    form.cleaned_data["decision"],
                    form.cleaned_data["reason"],
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                if record.status == "failed":
                    messages.error(request, record.outcome)
                else:
                    messages.success(request, "Approval decision recorded.")
                return redirect("approval_request_detail", pk=pk)
    steps = []
    overridden = (
        record.status == "approved"
        and record.audit_events.filter(action="override_approve").exists()
    )
    for index, item in enumerate(record.steps):
        steps.append(
            {
                **item,
                "current": record.status == "pending" and index == record.current_step,
                "completed": index < record.current_step or record.status == "approved",
                "overridden": overridden and index >= record.current_step,
            }
        )
    return render(
        request,
        "approvals/request_detail.html",
        {
            "title": "Approval request",
            "section": "approvals",
            "record": record,
            "steps": steps,
            "rights": decision_rights(request.user, record),
            "can_open_result": record.status == "approved"
            and bool(record.result_url)
            and (
                request.user.is_superuser or record.requested_by_id == request.user.pk
            ),
            "form": form,
            "events": record.audit_events.all(),
        },
    )


@superuser_required
def hierarchy_history(request, pk):
    hierarchy = get_object_or_404(ApprovalHierarchy, pk=pk)
    return render(
        request,
        "approvals/hierarchy_history.html",
        {
            "title": "Hierarchy configuration history",
            "section": "approval_hierarchies",
            "hierarchy": hierarchy,
            "events": hierarchy.audit_events.filter(request__isnull=True).order_by(
                "-created_at", "-pk"
            ),
        },
    )
