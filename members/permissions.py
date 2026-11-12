"""Shared permission checks for the workspace and its navigation."""

from django.core.exceptions import PermissionDenied

from .models import District, Lager, Member, OfficeLocation, School, Subdivision

MODULES = (
    (Member, "Members", "members", "member", "users"),
    (District, "Districts", "districts", "district", "pin"),
    (OfficeLocation, "Zonal offices", "offices", "office", "building"),
    (Subdivision, "Subdivisions", "subdivisions", "subdivision", "building"),
    (School, "Schools", "schools", "school", "building"),
    (Lager, "Lagers", "lagers", "lager", "building"),
)
ACTIONS = ("view", "add", "change", "delete")


def permission_name(model, action):
    return f"{model._meta.app_label}.{action}_{model._meta.model_name}"


def require_permission(user, model, action):
    if not user.has_perm(permission_name(model, action)):
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
            action: user.has_perm(permission_name(model, action)) for action in ACTIONS
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
