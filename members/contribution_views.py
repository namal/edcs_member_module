from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from .approval_scope import PAYMENT_EXCEPTIONS
from .approval_services import checkpoint, visible_requests
from .contribution_forms import ContributionForm
from .contribution_services import (
    allocation_preview,
    allocation_input,
    allocation_summary,
    existing_receipt,
    post_receipt,
)
from .models import (
    ChildDeposit,
    Member,
    PaymentPlan,
    PaymentReceipt,
    PaymentTransaction,
)
from .permissions import district_records, require_permission
from .views import paginated


@login_required
@require_GET
def contribution_child_accounts(request):
    require_permission(request.user, PaymentTransaction, "add")
    require_permission(request.user, ChildDeposit, "add")
    member_id = request.GET.get("member", "")
    if not member_id.isdigit():
        return JsonResponse({"error": "Select a valid member."}, status=400)
    member = get_object_or_404(district_records(request.user, Member), pk=member_id)
    accounts = (
        district_records(request.user, ChildDeposit)
        .filter(
            child__member=member,
            child__is_active=True,
            is_active=True,
            deposit_type__is_active=True,
        )
        .select_related("child", "deposit_type")
    )
    return JsonResponse(
        {
            "accounts": [
                {
                    "id": account.pk,
                    "deposit_type_id": account.deposit_type_id,
                    "label": str(account),
                }
                for account in accounts
            ]
        }
    )


@login_required
def contribution_list(request):
    require_permission(request.user, PaymentTransaction, "view")
    rows = district_records(request.user, PaymentReceipt).select_related(
        "member", "created_by", "approval_request"
    )
    query = request.GET.get("q", "").strip()
    if query:
        rows = rows.filter(
            Q(reference__icontains=query)
            | Q(member__member_no__icontains=query)
            | Q(member__full_name__icontains=query)
        )
    member = request.GET.get("member", "")
    if member:
        if not member.isdigit():
            rows = rows.none()
        else:
            get_object_or_404(district_records(request.user, Member), pk=member)
            rows = rows.filter(member_id=member)
    pending = visible_requests(request.user).filter(
        view_name="contribution_create", status="pending"
    )[:25]
    return render(
        request,
        "contributions/list.html",
        {
            "title": "Member contributions",
            "section": "payment_transactions",
            "pending": pending,
            **paginated(request, rows),
        },
    )


def receipt_destination(request, receipt):
    if request.user.has_perm("members.view_paymenttransaction"):
        return redirect("contribution_detail", pk=receipt.pk)
    return redirect("contribution_create")


@login_required
def contribution_create(request):
    require_permission(request.user, PaymentTransaction, "add")
    form = ContributionForm(
        request.POST if request.method == "POST" else None,
        user=request.user,
        initial={"member": request.GET.get("member", "")},
    )
    preview = None
    valid = request.method == "POST" and form.is_valid()
    preview = form.preview
    if valid:
        if request.POST.get("intent") == "post":
            try:
                with transaction.atomic():
                    member = get_object_or_404(
                        district_records(request.user, Member).select_for_update(),
                        pk=form.cleaned_data["member"].pk,
                    )
                    prior = existing_receipt(request.user, form.cleaned_data)
                    if prior:
                        messages.info(
                            request,
                            "This contribution was already posted; no duplicate credits were created.",
                        )
                        return receipt_destination(request, prior)
                    PaymentPlan.objects.select_for_update().filter(
                        member=member
                    ).first()
                    preview = allocation_preview(
                        member,
                        form.cleaned_data["received_on"],
                        form.cleaned_data["amount"],
                        form.cleaned_data["payment_type"],
                        allocation_input(form.cleaned_data),
                        remaining_payment_type=form.cleaned_data.get(
                            "remaining_payment_type"
                        ),
                        remaining_allocations=form.cleaned_data.get(
                            "remaining_allocations"
                        ),
                    )
                    if not preview["is_balanced"]:
                        raise ValidationError(
                            "The allocated total must equal the received contribution exactly."
                        )
                    if form.cleaned_data["review_hash"] != preview["fingerprint"]:
                        raise ValidationError(
                            "The amount, payment plan or member ledger changed. Preview the allocation again before posting."
                        )
                    if preview["requires_approval"]:
                        queued = checkpoint(
                            request,
                            PaymentTransaction,
                            "add",
                            f"Below-plan contribution: {member}",
                            target=preview["plan"],
                            form=form,
                            extra_summary=allocation_summary(preview),
                            approval_module=PAYMENT_EXCEPTIONS,
                            required=True,
                        )
                        if queued is not None:
                            return queued
                    receipt = post_receipt(
                        request.user, form.cleaned_data, preview["fingerprint"]
                    )
            except ValidationError as error:
                form.add_error(None, error)
            except IntegrityError:
                form.add_error(
                    None,
                    "This contribution reference has already been used. Check receipt history before trying again.",
                )
            else:
                messages.success(
                    request,
                    f"{receipt.receipt_number} posted: LKR {receipt.amount:,.2f}, allocated exactly across the payment categories.",
                )
                return receipt_destination(request, receipt)
        else:
            form.data = form.data.copy()
            form.data["review_hash"] = preview["fingerprint"]
    return render(
        request,
        "contributions/form.html",
        {
            "title": "Receive contribution",
            "section": "payment_transactions",
            "form": form,
            "preview": preview,
        },
    )


@login_required
def contribution_detail(request, pk):
    require_permission(request.user, PaymentTransaction, "view")
    receipt = get_object_or_404(
        district_records(request.user, PaymentReceipt).select_related(
            "member", "created_by", "approval_request"
        ),
        pk=pk,
    )
    can_view_approval = (
        receipt.approval_request_id
        and visible_requests(request.user)
        .filter(pk=receipt.approval_request_id)
        .exists()
    )
    return render(
        request,
        "contributions/detail.html",
        {
            "title": "Contribution receipt",
            "section": "payment_transactions",
            "receipt": receipt,
            "rows": receipt.plan_snapshot["rows"],
            "can_view_approval": can_view_approval,
        },
    )
