"""Shared permission checks for the workspace and its navigation."""

from django.core.exceptions import PermissionDenied

from .models import (
    District,
    Lager,
    Member,
    OfficeLocation,
    OpeningBalance,
    PaymentPlan,
    PaymentTransaction,
    PaymentType,
    School,
    Subdivision,
)

MODULES = (
    (Member, "Members", "members", "member", "users"),
    (District, "Districts", "districts", "district", "pin"),
    (OfficeLocation, "Zonal offices", "offices", "office", "building"),
    (Subdivision, "Subdivisions", "subdivisions", "subdivision", "building"),
    (School, "Departments", "schools", "school", "building"),
    (Lager, "Lagers", "lagers", "lager", "building"),
    (PaymentPlan, "Payment Plan", "payment_plans", "payment_plan", "wallet"),
    (
        PaymentTransaction,
        "Debit / Credit",
        "payment_transactions",
        "payment_transaction",
        "wallet",
    ),
    (
        OpeningBalance,
        "Opening Balances",
        "opening_balances",
        "opening_balance",
        "wallet",
    ),
    (PaymentType, "Payment Types", "payment_types", "payment_type", "wallet"),
)
ACTIONS = ("view", "add", "change", "delete")

DISTRICT_LOOKUPS = {
    District: "pk",
    Member: "district_id",
    OfficeLocation: "district_id",
    Lager: "district_id",
    School: "district_id",
    Subdivision: "zonal_office__district_id",
    PaymentPlan: "member__district_id",
    PaymentTransaction: "member__district_id",
    OpeningBalance: "member__district_id",
}


def accessible_districts(user):
    if not user or not user.is_authenticated or not user.is_active:
        return District.objects.none()
    if user.is_superuser:
        return District.objects.all()
    return District.objects.filter(authorized_users=user)


def scope_queryset(user, queryset):
    lookup = DISTRICT_LOOKUPS.get(queryset.model)
    if lookup is None:
        return queryset
    if user and user.is_authenticated and user.is_active and user.is_superuser:
        return queryset
    return queryset.filter(**{f"{lookup}__in": accessible_districts(user).values("pk")})


def require_district(user, district_id):
    if not accessible_districts(user).filter(pk=district_id).exists():
        raise PermissionDenied("This district is outside your assigned access.")


def district_records(user, model):
    return scope_queryset(user, model.objects.all())


def has_module_permission(user, model, action):
    if model is District and action == "add" and not user.is_superuser:
        return False
    if model is PaymentType and action != "view" and not user.is_superuser:
        return False
    return user.is_active and user.has_perm(permission_name(model, action))


def permission_name(model, action):
    return f"{model._meta.app_label}.{action}_{model._meta.model_name}"


def require_permission(user, model, action):
    if not has_module_permission(user, model, action):
        raise PermissionDenied


def require_location_access(user):
    # Registration needs lookup data, without granting access to master-data pages.
    allowed = (
        (Member, ("view", "add", "change")),
        (OfficeLocation, ("view", "add", "change")),
        (School, ("view", "add", "change")),
        (Subdivision, ("view", "add", "change")),
    )
    if not any(
        user.has_perm(permission_name(model, action))
        for model, actions in allowed
        for action in actions
    ):
        raise PermissionDenied


def module_access(user):
    modules = []
    for model, label, section, prefix, icon in MODULES:
        access = {
            action: has_module_permission(user, model, action) for action in ACTIONS
        }
        modules.append(
            {
                **access,
                "label": label,
                "section": section,
                "prefix": prefix,
                "icon": icon,
                "url": f"{prefix}_list" if access["view"] else f"{prefix}_create",
                "visible": access["view"] or access["add"],
            }
        )
    return modules
