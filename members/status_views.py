import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from . import models as m
from .approval_services import decision_rights
from .permissions import district_records, require_permission
from .status_forms import MemberStatusForm
from .status_services import submit_status_request, visible_status_requests
from .views import paginated


@login_required
@require_GET
def member_status_list(request):
    if not request.user.is_superuser and not any(
        request.user.has_perm(f"members.{action}_memberstatusrequest")
        for action in ("view", "add", "change")
    ):
        require_permission(request.user, m.MemberStatusRequest, "view")
    rows = visible_status_requests(request.user).select_related(
        "member", "requested_by", "approval"
    )
    query, state = request.GET.get("q", "").strip(), request.GET.get("state", "")
    if query:
        rows = rows.filter(
            Q(reference__icontains=query)
            | Q(snapshot__member_no__icontains=query)
            | Q(snapshot__member_name__icontains=query)
        )
    if state in m.MemberStatusRequest.State.values:
        rows = rows.filter(state=state)
    return render(
        request,
        "membership_status/list.html",
        {
            "title": "Membership Status",
            "section": "membership_status",
            "states": m.MemberStatusRequest.State.choices,
            "status_policy": m.ApprovalHierarchy.objects.filter(
                module="memberstatusrequest", action="change", is_active=True
            ).first(),
            **paginated(request, rows),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def member_status_create(request):
    require_permission(request.user, m.MemberStatusRequest, "add")
    form = MemberStatusForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        user=request.user,
        initial={"member": request.GET.get("member", "")},
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            record = submit_status_request(
                request.user,
                data["member"].pk,
                data["kind"],
                data["reason"],
                data["description"],
                data["requested_effective_date"],
                data["documents"],
            )
        except (ValidationError, IntegrityError) as error:
            form.add_error(
                None,
                (
                    error
                    if isinstance(error, ValidationError)
                    else "An open request already exists. Refresh the member status history."
                ),
            )
        else:
            messages.success(
                request,
                f"{record.reference} submitted. Membership status remains unchanged until configured workflow approvals and the effective date.",
            )
            return redirect("member_status_detail", pk=record.pk)
    return render(
        request,
        "membership_status/form.html",
        {
            "title": "Request membership status change",
            "section": "membership_status",
            "form": form,
            "selected_member": form.selected_member,
        },
    )


@login_required
@require_GET
def member_status_detail(request, pk):
    record = get_object_or_404(
        visible_status_requests(request.user).select_related(
            "member", "ledger", "approval", "approved_by"
        ),
        pk=pk,
    )
    # History follows the same authorized member request scope, including assigned reviewers.
    history = visible_status_requests(request.user).filter(member_id=record.member_id)
    return render(
        request,
        "membership_status/detail.html",
        {
            "title": record.reference,
            "section": "membership_status",
            "record": record,
            "history": history,
            "periods": record.member.active_periods.select_related(
                "ledger", "opened_by_request", "closed_by_request"
            ),
            "rights": decision_rights(request.user, record.approval),
        },
    )


@login_required
@require_GET
def member_status_document(request, pk):
    document = get_object_or_404(
        m.MemberStatusDocument, pk=pk, request__in=visible_status_requests(request.user)
    )
    response = FileResponse(
        io.BytesIO(bytes(document.content)),
        as_attachment=True,
        filename=document.name,
        content_type=document.content_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response
