from datetime import timedelta
from .approval_services import checkpoint, form_checkpoint
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .deposit_interest import (
    add_months,
    calculation_fingerprint,
    current_financial_year,
    financial_year_start,
    interest_report,
    post_interest_year,
    save_interest_year,
)
from .interest_forms import InterestYearForm
from .child_interest import (
    child_calculation_fingerprint,
    child_interest_report,
    post_child_interest_year,
    save_child_interest_year,
)
from .models import (
    ChildDeposit,
    ChildDepositInterestYear,
    DepositInterestYear,
    Member,
    PaymentType,
)
from .permissions import district_records, require_permission
from .views import paginated


def _report(request, action):
    require_permission(request.user, DepositInterestYear, action)
    params = (request.POST if request.method == "POST" else request.GET).copy()
    if request.method != "POST":
        params.setdefault("year", current_financial_year())
    form = InterestYearForm(params, user=request.user)
    rows = []
    start = end = None
    if form.is_valid():
        start = financial_year_start(form.cleaned_data["year"])
        end = add_months(start, 12) - timedelta(days=1)
        members = district_records(request.user, Member)
        if form.cleaned_data["member"]:
            members = members.filter(pk=form.cleaned_data["member"].pk)
        data = form.cleaned_data
        child_filters = any(
            data[name] for name in ("child", "child_deposit_type", "deposit")
        )
        if data["account_group"] != "children" and not child_filters:
            rows = interest_report(members, start, payment_type=data["payment_type"])
            for row in rows:
                row.update(
                    account_group="members",
                    detail_url_name="deposit_interest_detail",
                    post_url_name="deposit_interest_edit",
                    delete_url_name="deposit_interest_delete",
                )
        deposits = district_records(request.user, ChildDeposit).filter(
            child__member__in=members
        )
        if data["account_group"] != "members" and not data["payment_type"]:
            for name, lookup in (
                ("child", "child"),
                ("child_deposit_type", "deposit_type"),
                ("deposit", "pk"),
            ):
                if data[name]:
                    deposits = deposits.filter(**{lookup: data[name].pk})
            rows += child_interest_report(deposits, start)
        rows.sort(
            key=lambda row: (
                row["member_no"],
                row.get("child_name", ""),
                row["payment_type_name"],
                row.get("account_no", ""),
            )
        )
        if request.method == "POST":
            queued = form_checkpoint(
                request,
                form,
                model=DepositInterestYear,
                action=action,
                description=f"Save quarterly interest calculations: {start.year}",
            )
            if queued is not None:
                return queued
            try:
                with transaction.atomic():
                    member_map = members.in_bulk()
                    type_map = PaymentType.objects.in_bulk()
                    deposit_map = deposits.select_related(
                        "child__member", "deposit_type"
                    ).in_bulk()
                    for row in rows:
                        if row["account_group"] == "children":
                            save_child_interest_year(
                                deposit_map[row["deposit_id"]], start
                            )
                        else:
                            save_interest_year(
                                member_map[row["member_id"]],
                                type_map[row["payment_type_id"]],
                                start,
                            )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(
                    request,
                    "Quarterly interest calculations saved. No interest has been credited yet.",
                )
                destination = (
                    "deposit_interest_list"
                    if request.user.has_perm("members.view_depositinterestyear")
                    else "deposit_interest_create"
                )
                return redirect(
                    reverse(destination)
                    + "?"
                    + urlencode(
                        {
                            key: value
                            for key, value in params.items()
                            if key
                            in (
                                "year",
                                "member",
                                "payment_type",
                                "account_group",
                                "child",
                                "child_deposit_type",
                                "deposit",
                            )
                        }
                    )
                )
    return render(
        request,
        "interest/report.html",
        {
            "section": "deposit_interest",
            "title": "Quarterly Interest",
            "form": form,
            "year_start": start,
            "year_end": end,
            "today": timezone.localdate(),
            "total_interest": sum(
                (row["record"].annual_interest for row in rows), Decimal("0.00")
            ),
            "account_count": len(rows),
            **paginated(request, rows),
        },
    )


@login_required
def deposit_interest_list(request):
    return _report(request, "view")


@login_required
def deposit_interest_create(request):
    return _report(request, "add")


def _record(request, pk, action, child_account=False):
    require_permission(request.user, DepositInterestYear, action)
    return get_object_or_404(
        district_records(
            request.user,
            ChildDepositInterestYear if child_account else DepositInterestYear,
        ).select_related(
            *(
                (
                    "deposit__child__member",
                    "deposit__deposit_type",
                    "ledger_transaction",
                )
                if child_account
                else ("member", "payment_type", "ledger_transaction")
            )
        ),
        pk=pk,
    )


@login_required
def deposit_interest_detail(request, pk, child_account=False):
    record = _record(request, pk, "view", child_account)
    return render(
        request,
        "interest/detail.html",
        {
            "section": "deposit_interest",
            "title": "Deposit interest year",
            "record": record,
            "child_account": child_account,
            "post_url_name": (
                "child_interest_edit" if child_account else "deposit_interest_edit"
            ),
            "next_year_start": record.year_end + timedelta(days=1),
        },
    )


@login_required
def deposit_interest_edit(request, pk, child_account=False):
    record = _record(request, pk, "change", child_account)
    if request.method == "POST":
        queued = checkpoint(
            request,
            DepositInterestYear,
            "change",
            f"Post annual interest: {record}",
            target=record,
            extra_summary=[
                {"label": "Annual interest (LKR)", "value": str(record.annual_interest)}
            ],
        )
        if queued is not None:
            return queued
        try:
            if not request.POST.get("calculation_hash"):
                raise ValidationError("Review the year-end calculation before posting.")
            post = post_child_interest_year if child_account else post_interest_year
            record = post(
                record.pk,
                actor=request.user,
                expected_fingerprint=request.POST["calculation_hash"],
            )
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(
                request,
                f"Annual interest posted once. Opening balance on {record.year_end + timedelta(days=1)}: LKR {record.next_opening_balance:,.2f}.",
            )
        if request.user.has_perm("members.view_depositinterestyear"):
            return redirect(
                "child_interest_detail" if child_account else "deposit_interest_detail",
                pk=record.pk,
            )
        return redirect(
            "child_interest_edit" if child_account else "deposit_interest_edit",
            pk=record.pk,
        )
    if not record.posted_at:
        preview = (
            child_interest_report(
                ChildDeposit.objects.filter(pk=record.deposit_id), record.year_start
            )
            if child_account
            else interest_report(
                Member.objects.filter(pk=record.member_id),
                record.year_start,
                payment_type=record.payment_type,
            )
        )
        if preview:
            record = preview[0]["record"]
    return render(
        request,
        "interest/detail.html",
        {
            "section": "deposit_interest",
            "title": "Post annual interest",
            "record": record,
            "child_account": child_account,
            "post_url_name": (
                "child_interest_edit" if child_account else "deposit_interest_edit"
            ),
            "calculation_hash": (
                child_calculation_fingerprint(record)
                if child_account
                else calculation_fingerprint(record)
            ),
            "posting": True,
            "can_post": timezone.localdate() > record.year_end and not record.posted_at,
            "next_year_start": record.year_end + timedelta(days=1),
        },
    )


@login_required
def deposit_interest_delete(request, pk, child_account=False):
    record = _record(request, pk, "delete", child_account)
    if request.method == "POST":
        queued = checkpoint(
            request,
            DepositInterestYear,
            "delete",
            f"Delete interest calculation: {record}",
            target=record,
        )
        if queued is not None:
            return queued
        try:
            record.delete()
        except ProtectedError as error:
            messages.error(request, str(error.args[0]))
        else:
            messages.success(
                request,
                "Draft interest calculation removed. No ledger amounts were changed.",
            )
        return redirect(
            "deposit_interest_list"
            if request.user.has_perm("members.view_depositinterestyear")
            else "dashboard"
        )
    return render(
        request,
        "interest/delete.html",
        {
            "section": "deposit_interest",
            "title": "Remove draft calculation",
            "record": record,
        },
    )
