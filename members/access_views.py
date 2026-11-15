from functools import wraps

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from .access_forms import WorkspaceRoleForm, WorkspaceUserForm, workspace_permissions
from .notifications import notify_user_created
from .permissions import module_access
from .views import paginated


def superuser_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


def audit(request, obj, action, message):
    LogEntry.objects.log_actions(
        user_id=request.user.pk,
        queryset=[obj],
        action_flag=action,
        change_message=message,
        single_object=True,
    )


@superuser_required
def administration(request):
    users = get_user_model().objects.all()
    return render(
        request,
        "administration/index.html",
        {
            "section": "administration",
            "title": "Administration",
            "user_count": users.count(),
            "active_count": users.filter(is_active=True).count(),
            "role_count": Group.objects.count(),
            "permission_count": workspace_permissions().count(),
            "latest_users": users.prefetch_related(
                "groups", "assigned_districts"
            ).order_by("-date_joined")[:5],
            "activity": LogEntry.objects.select_related("user").order_by(
                "-action_time"
            )[:6],
        },
    )


@superuser_required
def user_list(request):
    users = (
        get_user_model()
        .objects.prefetch_related("groups", "assigned_districts")
        .select_related("ledger_profile__zonal_office")
        .order_by("username")
    )
    query = request.GET.get("q", "").strip()
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
            | Q(ledger_profile__epf_number__icontains=query)
        )
    if request.GET.get("status") in ("active", "inactive"):
        users = users.filter(is_active=request.GET["status"] == "active")
    return render(
        request,
        "administration/users.html",
        {
            "section": "users",
            "title": "Users & access",
            "record_count": users.count(),
            **paginated(request, users),
        },
    )


@superuser_required
def user_form(request, pk=None):
    user = get_object_or_404(get_user_model(), pk=pk) if pk is not None else None
    form = WorkspaceUserForm(
        request.POST if request.method == "POST" else None,
        instance=user,
        actor=request.user,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            saved_user = form.save()
            audit(
                request,
                saved_user,
                CHANGE if pk is not None else ADDITION,
                "Saved user profile, roles, district assignments and workspace access.",
            )
            if pk is None:
                notify_user_created(request, saved_user)
        if saved_user.pk == request.user.pk:
            update_session_auth_hash(request, saved_user)
        messages.success(request, f"Access for {saved_user.username} has been saved.")
        return redirect("access_users")
    return render(
        request,
        "administration/user_form.html",
        {
            "section": "users",
            "title": "Edit user" if pk is not None else "Create user",
            "form": form,
            "account": user,
            "permission_rows": form.permission_rows(),
            "effective_access": module_access(user) if user else [],
        },
    )


@superuser_required
def user_delete(request, pk):
    user = get_object_or_404(get_user_model(), pk=pk)
    if user.pk == request.user.pk or user.is_superuser:
        raise PermissionDenied
    if request.method == "POST":
        try:
            with transaction.atomic():
                audit(request, user, DELETION, "Deleted user account.")
                user.delete()
        except ProtectedError:
            messages.error(
                request,
                "This user is retained in ledger responsibility or approval history. Deactivate the account after reassigning active ledgers and resolving pending approvals instead.",
            )
            return redirect("access_users")
        messages.success(request, "The user account has been deleted.")
        return redirect("access_users")
    return render(
        request,
        "administration/delete.html",
        {
            "section": "users",
            "title": "Delete user",
            "object": user,
            "back": "access_users",
            "explanation": "This account will lose access. Member records will stay in the workspace.",
        },
    )


@superuser_required
def role_list(request):
    roles = (
        Group.objects.annotate(user_count=Count("user", distinct=True))
        .prefetch_related("permissions__content_type")
        .order_by("name")
    )
    query = request.GET.get("q", "").strip()
    if query:
        roles = roles.filter(name__icontains=query)
    return render(
        request,
        "administration/roles.html",
        {
            "section": "roles",
            "title": "Roles & permissions",
            "record_count": roles.count(),
            **paginated(request, roles),
        },
    )


@superuser_required
def role_form(request, pk=None):
    role = get_object_or_404(Group, pk=pk) if pk is not None else None
    form = WorkspaceRoleForm(
        request.POST if request.method == "POST" else None, instance=role
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            saved_role = form.save()
            audit(
                request,
                saved_role,
                CHANGE if pk is not None else ADDITION,
                "Saved role and workspace permissions.",
            )
        messages.success(
            request, "Role permissions have been saved for all assigned users."
        )
        return redirect("access_roles")
    return render(
        request,
        "administration/role_form.html",
        {
            "section": "roles",
            "title": "Edit role" if pk is not None else "Create role",
            "form": form,
            "role": role,
            "permission_rows": form.permission_rows(),
        },
    )


@superuser_required
def role_delete(request, pk):
    role = get_object_or_404(Group, pk=pk)
    if request.method == "POST":
        with transaction.atomic():
            audit(
                request, role, DELETION, "Deleted role and its permission assignments."
            )
            role.delete()
        messages.success(
            request, "Role deleted. Its permissions no longer apply to assigned users."
        )
        return redirect("access_roles")
    return render(
        request,
        "administration/delete.html",
        {
            "section": "roles",
            "title": "Delete role",
            "object": role,
            "back": "access_roles",
            "explanation": "Users keep their accounts and direct permissions. Access granted through this role will be removed.",
        },
    )
