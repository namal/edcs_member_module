"""Atomic member transfers, carried balances and ledger responsibility changes."""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from . import models as m
from .ledger_tracking import ledger_actor, officer_identity
from .permissions import (
    accessible_districts,
    require_district,
    require_member_access,
    require_permission,
    require_zonal_office,
    scope_queryset,
)


def json_values(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def member_snapshot(member):
    return json_values(
        {
            field.attname: getattr(member, field.attname)
            for field in member._meta.concrete_fields
        }
    )


def location_snapshot(member):
    return {
        "district": member.district.name,
        "zonal_office": member.office_location.name,
        "lager": member.lager.name if member.lager_id else "",
        "school": member.school.name if member.school_id else "",
        "subdivision": member.subdivision.name if member.subdivision_id else "",
    }


def eligible_officers(office):
    users = (
        get_user_model()
        .objects.filter(
            is_active=True,
            ledger_profile__epf_number__isnull=False,
        )
        .exclude(ledger_profile__epf_number="")
    )
    if office is not None:
        zone = m.OfficeLocation.objects.get(pk=getattr(office, "pk", office))
        users = users.filter(
            Q(is_superuser=True)
            | Q(assigned_districts=zone.district_id, ledger_profile__zonal_offices=zone)
        )
    return (
        users.select_related("ledger_profile")
        .distinct()
        .order_by("first_name", "last_name", "username")
    )


def validate_officer(officer, office, *, lager=None):
    if officer is not None:
        get_user_model().objects.select_for_update().filter(pk=officer.pk).first()
        m.LedgerOfficerProfile.objects.select_for_update().filter(
            user_id=officer.pk
        ).first()
    if (
        lager is not None
        and lager.maintenance_officer_id == getattr(officer, "pk", None)
        and officer is not None
    ):
        from .officers import validate_master_officer

        validate_master_officer(officer)
        return
    if officer is None or not eligible_officers(office).filter(pk=officer.pk).exists():
        raise ValidationError(
            {
                "maintenance_officer": "Choose an active officer with an EPF number and assigned access to this receiving branch and zonal office."
            }
        )


def _save_ledger(ledger, **kwargs):
    ledger._ledger_service_write = True
    try:
        ledger.save(**kwargs)
    finally:
        ledger._ledger_service_write = False
    return ledger


@transaction.atomic
def initialize_member_ledger(member, officer=None, actor=None):
    member = m.Member.objects.select_for_update().get(pk=member.pk)
    existing = m.MemberLedger.objects.filter(member=member, status="active").first()
    if existing:
        return existing
    if m.MemberLedger.objects.filter(member=member).exists():
        raise ValidationError(
            "This member has no active ledger. Review the transfer history before posting."
        )
    lager = (
        m.Lager.objects.select_for_update().get(pk=member.lager_id)
        if member.lager_id
        else None
    )
    if lager and lager.maintenance_officer_id:
        officer = lager.maintenance_officer
    if officer:
        validate_officer(officer, member.office_location, lager=lager)
    name, epf = officer_identity(officer)
    ledger = _save_ledger(
        m.MemberLedger(
            member=member,
            district_id=member.district_id,
            zonal_office_id=member.office_location_id,
            lager_id=member.lager_id,
            school_id=member.school_id,
            subdivision_id=member.subdivision_id,
            opened_on=member.admit_date,
            maintenance_officer=officer,
            officer_name=name,
            officer_epf=epf,
            legacy_assignment_pending=officer is None,
            registration_snapshot=member_snapshot(member),
            location_snapshot=location_snapshot(member),
            created_by=actor,
        )
    )
    if officer:
        m.LedgerOfficerAssignment.objects.create(
            ledger=ledger,
            officer=officer,
            officer_name=name,
            officer_epf=epf,
            reason="Initial ledger responsibility",
            changed_by=actor,
            changed_by_name=officer_identity(actor)[0],
        )
    return ledger


def capture_balances(member, as_of):
    from .transaction_balances import balance_report
    from .child_services import deposit_balance
    from .fix_deposit_services import loan_balances

    deposits = balance_report(m.Member.objects.filter(pk=member.pk), None, as_of)
    children = []
    for account in m.ChildDeposit.objects.filter(
        child__member=member, opened_on__lte=as_of
    ).select_related("child", "deposit_type"):
        children.append(
            {
                "account_id": account.pk,
                "account_no": account.account_no,
                "child_name": account.child.name,
                "payment_type_name": account.deposit_type.name,
                "balance": deposit_balance(account, as_of),
            }
        )
    loans = []
    for loan in (
        m.FixDepositLoan.objects.filter(account__member=member, issued_on__lte=as_of)
        .select_related("account__policy__payment_type")
        .prefetch_related("entries")
    ):
        loans.append(
            {
                "loan_id": loan.pk,
                "reference": loan.reference,
                "payment_type_name": loan.account.policy.payment_type.name,
                "principal": loan.principal,
                "settled_on": loan.settled_on,
                **loan_balances(loan, as_of),
            }
        )
    return json_values(
        {
            "as_of": as_of,
            "captured_at": timezone.now(),
            "member_profile": member_snapshot(member),
            "departure_location": location_snapshot(member),
            "member_deposits": deposits,
            "child_deposits": children,
            "loans": loans,
        }
    )


def visible_balances(user, snapshot):
    permissions = {
        "member_deposits": "paymenttransaction",
        "child_deposits": "childdeposit",
        "loans": "fixdepositaccount",
    }
    return {
        key: rows
        for key, rows in snapshot.items()
        if key in permissions and user.has_perm(f"members.view_{permissions[key]}")
    }


@transaction.atomic
def assign_ledger_officer(actor, ledger_pk, officer, reason, *, initializing=False):
    require_permission(actor, m.MemberLedger, "add" if initializing else "change")
    member_id = get_object_or_404(m.MemberLedger, pk=ledger_pk).member_id
    member = m.Member.objects.select_for_update().get(pk=member_id)
    m.Lager.objects.select_for_update().filter(pk=member.lager_id).first()
    ledger = get_object_or_404(
        scope_queryset(actor, m.MemberLedger.objects.select_for_update()), pk=ledger_pk
    )
    require_district(actor, ledger.district_id)
    if ledger.status != "active":
        raise ValidationError(
            "Historical ledgers retain their assigned officer. Change responsibility on the active ledger instead."
        )
    if initializing and not ledger.legacy_assignment_pending:
        raise ValidationError(
            "This ledger already has an assigned officer. Use Change officer to record a responsibility change."
        )
    validate_officer(officer, ledger.zonal_office)
    if not reason.strip():
        raise ValidationError(
            {"reason": "Enter the reason for this responsibility change."}
        )
    name, epf = officer_identity(officer)
    m.LedgerOfficerAssignment.objects.create(
        ledger=ledger,
        previous_officer=ledger.maintenance_officer,
        officer=officer,
        previous_name=ledger.officer_name,
        previous_epf=ledger.officer_epf,
        officer_name=name,
        officer_epf=epf,
        reason=reason.strip(),
        changed_by=actor,
        changed_by_name=officer_identity(actor)[0],
    )
    ledger.maintenance_officer, ledger.officer_name, ledger.officer_epf = (
        officer,
        name,
        epf,
    )
    ledger.legacy_assignment_pending = False
    return _save_ledger(
        ledger,
        update_fields=[
            "maintenance_officer",
            "officer_name",
            "officer_epf",
            "legacy_assignment_pending",
        ],
    )


def guard_member_location(member):
    if not member.pk or getattr(member, "_member_transfer", False):
        return
    previous = (
        m.Member.objects.filter(pk=member.pk)
        .values("district_id", "office_location_id", "school_id")
        .first()
    )
    if previous and any(
        previous[field] != getattr(member, field) for field in previous
    ):
        raise ValidationError(
            {
                "office_location": "Use Member Transfers to change a registered member's zonal office or department and retain their original ledger."
            }
        )


def validate_receiving_location(office, lager, school, subdivision):
    errors = {}
    if lager.zonal_office_id != office.pk or not lager.is_active:
        errors["lager"] = "Choose an active lager in the receiving zonal office."
    if (
        school.zonal_office_id != office.pk
        or school.district_id != office.district_id
        or not school.is_active
        or (school.subdivision_id and not school.subdivision.is_active)
    ):
        errors["school"] = "Choose an active department in the receiving zonal office."
    if subdivision and (
        subdivision.zonal_office_id != office.pk
        or not subdivision.is_active
        or school.subdivision_id != subdivision.pk
    ):
        errors["subdivision"] = (
            "Choose an active subdivision matching the receiving department and office."
        )
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def transfer_member(
    actor,
    *,
    member_pk,
    expected_ledger_pk,
    office,
    lager,
    school,
    subdivision,
    officer,
    effective_date,
    reason,
):
    require_permission(actor, m.MemberTransfer, "add")
    member = get_object_or_404(m.Member.objects.select_for_update(), pk=member_pk)
    list(
        m.Lager.objects.select_for_update()
        .filter(pk__in=[member.lager_id, lager.pk])
        .order_by("pk")
    )
    require_member_access(actor, member)
    office = get_object_or_404(
        m.OfficeLocation.objects.select_for_update().select_related("district"),
        pk=office.pk,
    )
    lager = get_object_or_404(m.Lager.objects.select_for_update(), pk=lager.pk)
    school = get_object_or_404(m.School.objects.select_for_update(), pk=school.pk)
    if subdivision:
        subdivision = get_object_or_404(
            m.Subdivision.objects.select_for_update(), pk=subdivision.pk
        )
    require_district(actor, member.district_id)
    require_district(actor, office.district_id)
    require_zonal_office(actor, office.pk)
    current = m.MemberLedger.objects.select_for_update().get(
        member=member, status="active"
    )
    if (
        current.zonal_office_id != member.office_location_id
        or current.district_id != member.district_id
    ):
        raise ValidationError(
            "The member's location does not match their current ledger. Review the ledger history before transferring."
        )
    if current.pk != expected_ledger_pk:
        raise ValidationError(
            "This member's ledger changed while this form was open. Reload the transfer form and review the current ledger."
        )
    if current.maintenance_officer_id is None:
        raise ValidationError(
            "Assign the original ledger's maintenance officer before transferring this member."
        )
    if (
        effective_date > timezone.localdate()
        or effective_date < current.opened_on
        or (current.previous_ledger_id and effective_date <= current.opened_on)
    ):
        raise ValidationError(
            {
                "effective_date": "Use today or a valid earlier date after the previous transfer and on or after this ledger's opening date."
            }
        )
    if current.events.filter(effective_on__gt=effective_date).exists():
        raise ValidationError(
            {
                "effective_date": "Later activity already exists on this ledger. Choose a date on or after its latest recorded activity."
            }
        )
    if not reason.strip():
        raise ValidationError({"reason": "Enter a reason for the transfer."})
    if (
        office.pk == member.office_location_id
        and school.pk == member.school_id
        and lager.pk == member.lager_id
        and getattr(subdivision, "pk", None) == member.subdivision_id
    ):
        raise ValidationError("Choose a different receiving location or department.")
    if not office.is_active or not office.district.is_active:
        raise ValidationError(
            {"zonal_office": "Choose an active receiving zonal office and branch."}
        )
    validate_officer(officer, office)
    validate_receiving_location(office, lager, school, subdivision)
    snapshot = capture_balances(member, effective_date)
    current.status, current.closed_on, current.closing_balances = (
        "transferred",
        effective_date,
        snapshot,
    )
    current.lager_id = member.lager_id
    current.location_snapshot = location_snapshot(member)
    _save_ledger(
        current,
        update_fields=[
            "status",
            "closed_on",
            "closing_balances",
            "lager",
            "location_snapshot",
        ],
    )
    member.district_id, member.office_location = office.district_id, office
    member.lager, member.school, member.subdivision = lager, school, subdivision
    member._member_transfer = True
    try:
        member.save(
            update_fields=[
                "district",
                "office_location",
                "lager",
                "school",
                "subdivision",
                "updated_at",
            ]
        )
    finally:
        member._member_transfer = False
    name, epf = officer_identity(officer)
    receiving = _save_ledger(
        m.MemberLedger(
            member=member,
            district_id=office.district_id,
            zonal_office=office,
            lager=lager,
            school=school,
            subdivision=subdivision,
            previous_ledger=current,
            opened_on=effective_date,
            maintenance_officer=officer,
            officer_name=name,
            officer_epf=epf,
            registration_snapshot=current.registration_snapshot,
            location_snapshot=location_snapshot(member),
            opening_balances=snapshot,
            created_by=actor,
        )
    )
    m.LedgerOfficerAssignment.objects.create(
        ledger=receiving,
        officer=officer,
        officer_name=name,
        officer_epf=epf,
        reason="Receiving ledger responsibility on transfer",
        changed_by=actor,
        changed_by_name=officer_identity(actor)[0],
    )
    actor_name, actor_epf = officer_identity(actor)
    transfer = m.MemberTransfer(
        member=member,
        from_ledger=current,
        to_ledger=receiving,
        effective_date=effective_date,
        reason=reason.strip(),
        performed_by=actor,
        performed_by_name=actor_name,
        performed_by_epf=actor_epf,
    )
    transfer._transfer_service_write = True
    transfer.save()
    return transfer
