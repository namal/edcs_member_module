"""Shared officer identities for ledger master assignments and registration."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError


def maintenance_officers():
    return (
        get_user_model()
        .objects.filter(is_active=True, ledger_profile__epf_number__isnull=False)
        .exclude(ledger_profile__epf_number__regex=r"^\s*$")
        .select_related("ledger_profile")
        .order_by("first_name", "last_name", "username")
    )


def officer_label(user):
    return f"EPF {user.ledger_profile.epf_number} · {user.get_full_name() or user.username}"


def officer_options():
    return [
        {
            "id": user.pk,
            "name": officer_label(user),
            "officer_name": user.get_full_name() or user.username,
            "epf_number": user.ledger_profile.epf_number,
        }
        for user in maintenance_officers()
    ]


def validate_master_officer(user):
    if user is None or not maintenance_officers().filter(pk=user.pk).exists():
        raise ValidationError(
            {
                "maintenance_officer": "Select an active Ledger Maintenance Officer with a valid recorded EPF number."
            }
        )
