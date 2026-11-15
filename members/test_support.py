"""Shared, explicitly identified ledger officers for registration fixtures."""

from django.contrib.auth import get_user_model

from .models import LedgerOfficerProfile


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
    return user
