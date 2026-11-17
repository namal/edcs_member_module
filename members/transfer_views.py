import csv
from .approval_services import form_checkpoint
import json

from django.contrib import messages
from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import models as m
from .ledger_tracking import TRACKED
from .permissions import district_records, require_permission, accessible_districts
from .transfer_forms import (
    TransferForm,
    OfficerAssignmentForm,
    LedgerFilterForm,
    LedgerEventFilterForm,
)
from .transfer_services import (
    assign_ledger_officer,
    capture_balances,
    transfer_member,
    visible_balances,
)
from .views import paginated, location_context, member_school_data


def _errors(form, error):
    if hasattr(error, "message_dict"):
        for name, errors in error.message_dict.items():
            form.add_error(name if name in form.fields else None, errors)
    else:
        form.add_error(None, error)


def ledger_chain(user, ledger):
    visible = set(
        district_records(user, m.MemberLedger)
        .filter(member_id=ledger.member_id)
        .values_list("pk", flat=True)
    )
    return [
        {"ledger": item, "can_view": item.pk in visible}
        for item in m.MemberLedger.objects.filter(member_id=ledger.member_id)
        .select_related("zonal_office", "district")
        .order_by("created_at", "pk")
    ]


@login_required
def member_transfer_list(request):
    require_permission(request.user, m.MemberTransfer, "view")
    rows = district_records(request.user, m.MemberTransfer).select_related(
        "member", "from_ledger__zonal_office", "to_ledger__zonal_office"
    )
    member = request.GET.get("member", "")
    if member.isdigit():
        rows = rows.filter(member_id=member)
    query = request.GET.get("q", "").strip()
    if query:
        from django.db.models import Q

        rows = rows.filter(
            Q(reference__icontains=query)
            | Q(from_ledger__registration_snapshot__member_no__icontains=query)
            | Q(from_ledger__registration_snapshot__full_name__icontains=query)
            | Q(
                from_ledger__closing_balances__member_profile__full_name__icontains=query
            )
        )
    return render(
        request,
        "transfers/transfer_list.html",
        {
            "section": "member_transfers",
            "title": "Member Transfers",
            **paginated(request, rows),
        },
    )


@login_required
def member_transfer_create(request):
    require_permission(request.user, m.MemberTransfer, "add")
    initial = {"member": request.GET.get("member", "")}
    form = TransferForm(
        request.POST if request.method == "POST" else None,
        user=request.user,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        queued = form_checkpoint(
            request,
            form,
            model=m.MemberTransfer,
            action="add",
            target=form.selected_member,
            description=f"Transfer member: {form.selected_member}",
        )
        if queued is not None:
            return queued
        data = form.cleaned_data
        try:
            result = transfer_member(
                request.user,
                member_pk=data["member"].pk,
                expected_ledger_pk=data["source_ledger"],
                office=data["zonal_office"],
                lager=data["lager"],
                school=data["school"],
                subdivision=data["subdivision"],
                officer=data["maintenance_officer"],
                effective_date=data["effective_date"],
                reason=data["reason"],
            )
        except ValidationError as error:
            _errors(form, error)
        else:
            messages.success(
                request,
                f"Transfer {result.reference} completed. Original ledger {result.from_ledger.reference} is retained; new ledger {result.to_ledger.reference} is active.",
            )
            if request.user.has_perm("members.view_membertransfer"):
                return redirect("member_transfer_detail", pk=result.pk)
            if request.user.has_perm("members.view_memberledger"):
                return redirect("member_ledger_detail", pk=result.to_ledger_id)
            return redirect("member_transfer_create")
    return render(
        request,
        "transfers/transfer_form.html",
        {
            "section": "member_transfers",
            "title": "Transfer member",
            "form": form,
            "selected_member": form.selected_member,
            "current_ledger": form.current_ledger,
            "member_schools": member_school_data(form),
            "today": timezone.localdate(),
            **location_context(form),
        },
    )


@login_required
def member_transfer_detail(request, pk):
    require_permission(request.user, m.MemberTransfer, "view")
    transfer = get_object_or_404(
        district_records(request.user, m.MemberTransfer).select_related(
            "member", "from_ledger__zonal_office", "to_ledger__zonal_office"
        ),
        pk=pk,
    )
    return render(
        request,
        "transfers/transfer_detail.html",
        {
            "section": "member_transfers",
            "title": "Transfer history",
            "transfer": transfer,
            "chain": ledger_chain(request.user, transfer.to_ledger),
        },
    )


@login_required
def permanent_history_action(request, pk):
    raise PermissionDenied(
        "Ledger and transfer histories cannot be edited or deleted. Use Change officer for active ledger responsibility."
    )


@login_required
def member_ledger_list(request):
    require_permission(request.user, m.MemberLedger, "view")
    form = LedgerFilterForm(request.GET, user=request.user)
    rows = district_records(request.user, m.MemberLedger).select_related(
        "member__lager__maintenance_officer__ledger_profile",
        "district",
        "zonal_office",
        "lager__maintenance_officer__ledger_profile",
    )
    if form.is_valid():
        data = form.cleaned_data
        if data["member"]:
            rows = rows.filter(member=data["member"])
        if data["district"]:
            rows = rows.filter(district=data["district"])
        if data["status"] == "pending":
            rows = rows.filter(legacy_assignment_pending=True, status="active")
        elif data["status"]:
            rows = rows.filter(status=data["status"])
    else:
        rows = rows.none()
    return render(
        request,
        "transfers/ledger_list.html",
        {
            "section": "member_ledgers",
            "title": "Member Ledgers",
            "form": form,
            **paginated(request, rows),
        },
    )


def event_query(request, ledger, form):
    ids = [ledger.pk]
    if form.cleaned_data["include_previous"]:
        previous = ledger.previous_ledger
        while previous:
            ids.append(previous.pk)
            previous = previous.previous_ledger
    visible = district_records(request.user, m.MemberLedger).filter(pk__in=ids)
    modules = {
        config[2]
        for config in TRACKED.values()
        if request.user.has_perm(f"members.view_{config[2]}")
    }
    rows = m.LedgerEvent.objects.filter(
        ledger__in=visible, snapshot__permission__in=modules
    ).select_related("record", "ledger")
    for name, lookup in (
        ("date_from", "effective_on__gte"),
        ("date_to", "effective_on__lte"),
    ):
        if form.cleaned_data[name]:
            rows = rows.filter(**{lookup: form.cleaned_data[name]})
    return rows


def decorate_event(row):
    model = apps.get_model(row.record.source_model)
    values = row.snapshot.get("data", {})
    row.details = [
        {"label": field.verbose_name, "value": values.get(field.attname)}
        for field in model._meta.concrete_fields
        if not field.is_relation and field.name not in ("id", "operation_key")
    ]
    return row


@login_required
def member_ledger_detail(request, pk, export=False):
    require_permission(request.user, m.MemberLedger, "view")
    ledger = get_object_or_404(
        district_records(request.user, m.MemberLedger).select_related(
            "member", "zonal_office", "district", "previous_ledger"
        ),
        pk=pk,
    )
    form = LedgerEventFilterForm(request.GET)
    events = m.LedgerEvent.objects.none()
    if form.is_valid():
        events = event_query(request, ledger, form)
    elif export:
        return HttpResponseBadRequest("Correct the ledger filters before exporting.")
    if export:
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="ledger-{ledger.reference}.csv"'
        )
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(
            [
                "Ledger",
                "Recorded at",
                "Effective date",
                "Action",
                "Source record",
                "Activity",
                "Account",
                "Child",
                "Details",
                "Recorded by",
                "EPF",
            ]
        )
        for row in events.iterator():
            values = [
                row.ledger.reference,
                row.recorded_at.isoformat(),
                row.effective_on.isoformat(),
                row.action,
                f"{row.record.source_model}:{row.record.source_id}",
                row.snapshot.get("label", ""),
                row.snapshot.get("account", ""),
                row.snapshot.get("child", ""),
                json.dumps(row.snapshot.get("data", {}), ensure_ascii=False),
                row.actor_name,
                row.actor_epf,
            ]
            writer.writerow(
                [
                    (
                        f"'{value}"
                        if isinstance(value, str)
                        and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                        else value
                    )
                    for value in values
                ]
            )
        return response
    snapshot = (
        ledger.closing_balances
        if ledger.status == "transferred"
        else capture_balances(ledger.member, timezone.localdate())
    )
    page = paginated(request, events)
    page["page_obj"].object_list = [
        decorate_event(row) for row in page["page_obj"].object_list
    ]
    retained_profile = (
        ledger.closing_balances.get("member_profile", ledger.registration_snapshot)
        if ledger.status == "transferred"
        else ledger.registration_snapshot
    )
    registration = [
        {
            "label": field.verbose_name,
            "value": retained_profile.get(field.attname),
        }
        for field in m.Member._meta.concrete_fields
        if not field.is_relation and field.name != "id"
    ]
    return render(
        request,
        "transfers/ledger_detail.html",
        {
            "section": "member_ledgers",
            "title": "Member ledger",
            "ledger": ledger,
            "form": form,
            "registration_fields": registration,
            "chain": ledger_chain(request.user, ledger),
            "balances": visible_balances(request.user, snapshot),
            "opening_balances": visible_balances(request.user, ledger.opening_balances),
            "can_view_current_member": request.user.has_perm("members.view_member")
            and district_records(request.user, m.Member)
            .filter(pk=ledger.member_id)
            .exists(),
            "officer_history": ledger.officer_history.all(),
            **page,
        },
    )


@login_required
def member_ledger_officer(request, pk):
    require_permission(request.user, m.MemberLedger, "change")
    ledger = get_object_or_404(
        district_records(request.user, m.MemberLedger).select_related(
            "member", "zonal_office"
        ),
        pk=pk,
    )
    if ledger.status != "active":
        raise PermissionDenied(
            "Historical officer details are retained on the original ledger."
        )
    form = OfficerAssignmentForm(
        request.POST if request.method == "POST" else None, ledger=ledger
    )
    if request.method == "POST" and form.is_valid():
        queued = form_checkpoint(
            request,
            form,
            model=m.MemberLedger,
            action="change",
            target=ledger,
            description=f"Change ledger officer: {ledger.reference}",
        )
        if queued is not None:
            return queued
        try:
            assign_ledger_officer(
                request.user,
                ledger.pk,
                form.cleaned_data["maintenance_officer"],
                form.cleaned_data["reason"],
            )
        except ValidationError as error:
            _errors(form, error)
        else:
            messages.success(
                request,
                "Ledger officer changed. The previous responsibility record is retained.",
            )
            return (
                redirect("member_ledger_detail", pk=ledger.pk)
                if request.user.has_perm("members.view_memberledger")
                else redirect("member_ledger_edit", pk=ledger.pk)
            )
    return render(
        request,
        "transfers/officer_form.html",
        {
            "section": "member_ledgers",
            "title": "Change ledger officer",
            "ledger": ledger,
            "form": form,
        },
    )


@login_required
def member_ledger_create(request):
    require_permission(request.user, m.MemberLedger, "add")
    member_id = (
        request.POST.get("member", "")
        if request.method == "POST"
        else request.GET.get("member", "")
    )
    members = (
        district_records(request.user, m.Member)
        .filter(ledgers__status="active", ledgers__legacy_assignment_pending=True)
        .order_by("member_no", "pk")
    )
    member = get_object_or_404(members, pk=member_id) if member_id.isdigit() else None
    ledger = member.ledgers.get(status="active") if member else None
    form = (
        OfficerAssignmentForm(
            request.POST if request.method == "POST" else None,
            ledger=ledger,
            initial={"reason": "Initial assignment of existing original ledger"},
        )
        if ledger
        else None
    )
    if request.method == "POST" and form and form.is_valid():
        queued = form_checkpoint(
            request,
            form,
            model=m.MemberLedger,
            action="add",
            target=ledger,
            description=f"Assign original ledger officer: {ledger.reference}",
        )
        if queued is not None:
            return queued
        try:
            assign_ledger_officer(
                request.user,
                ledger.pk,
                form.cleaned_data["maintenance_officer"],
                form.cleaned_data["reason"],
                initializing=True,
            )
        except ValidationError as error:
            _errors(form, error)
        else:
            messages.success(
                request,
                "Original ledger officer assigned. Existing history is retained.",
            )
            return redirect(
                "member_ledger_list"
                if request.user.has_perm("members.view_memberledger")
                else "member_ledger_create"
            )
    return render(
        request,
        "transfers/ledger_create.html",
        {
            "section": "member_ledgers",
            "title": "Assign original ledger officer",
            "members": members,
            "selected_member": member,
            "ledger": ledger,
            "form": form,
        },
    )
