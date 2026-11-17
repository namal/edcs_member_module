"""Shared, explicitly identified ledger officers for registration fixtures."""

from django.contrib.auth import get_user_model

from .models import LedgerOfficerProfile, Lager


def assign_lagers(user, lagers):
    """Give an ordinary test operator an explicit EPF and ledger assignment."""
    profile, _ = LedgerOfficerProfile.objects.get_or_create(
        user=user, defaults={"epf_number": f"TEST-ACCESS-{user.pk}"}
    )
    if not profile.epf_number:
        profile.epf_number = f"TEST-ACCESS-{user.pk}"
        profile.save()
    for lager in lagers:
        Lager.objects.filter(pk=lager.pk).update(maintenance_officer=user)


def assign_member_ledgers(user, members):
    from .models import Member, MemberLedger

    for member in members:
        if not member.lager_id:
            lager, _ = Lager.objects.get_or_create(
                zonal_office=member.office_location,
                code=f"ACL-{user.pk}-{member.office_location_id}",
                defaults={
                    "name": f"Fixture ledger {user.pk}/{member.office_location_id}"
                },
            )
            Member.objects.filter(pk=member.pk).update(lager=lager)
            MemberLedger.objects.filter(member=member, status="active").update(
                lager=lager
            )
            member.lager = lager
        assign_lagers(user, [member.lager])


def officer_for(office):
    user, _ = get_user_model().objects.get_or_create(
        username=f"registration-officer-{office.pk}",
        defaults={"first_name": "Registration", "last_name": f"Officer {office.pk}"},
    )
    user.assigned_districts.add(office.district_id)
    LedgerOfficerProfile.objects.get_or_create(
        user=user,
        defaults={"epf_number": f"TEST-REG-{office.pk}", "zonal_office": office},
    )
    Lager.objects.filter(zonal_office=office, maintenance_officer__isnull=True).update(
        maintenance_officer=user
    )
    return user
