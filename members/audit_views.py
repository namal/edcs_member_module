from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import AuditLog, Member
from .permissions import (
    MODULES,
    accessible_districts,
    accessible_members,
    accessible_zonal_offices,
    assigned_lagers,
    require_permission,
)
from .views import paginated


def visible_logs(user):
    rows = AuditLog.objects.all()
    if user.is_superuser:
        return rows
    allowed = [
        model._meta.model_name
        for model, *_ in MODULES
        if model is not AuditLog
        and user.has_perm(f"members.view_{model._meta.model_name}")
    ]
    business = rows.filter(module__in=allowed).filter(
        Q(member_pk__in=accessible_members(user).values("pk"))
        | (
            Q(
                member_pk__isnull=False,
                district_pk__in=accessible_districts(user).values("pk"),
                lager_pk__in=assigned_lagers(user).values("pk"),
            )
            & ~Q(member_pk__in=Member.objects.values("pk"))
        )
        | (
            Q(member_pk__isnull=True)
            & (
                Q(district_pk__isnull=True)
                | Q(district_pk__in=accessible_districts(user).values("pk"))
            )
            & (
                Q(zonal_office_pk__isnull=True)
                | Q(zonal_office_pk__in=accessible_zonal_offices(user).values("pk"))
            )
        )
    )
    result = Q(pk__in=business.values("pk"))
    if user.has_perm("members.view_securityauditlog"):
        result |= Q(module__in=("users", "roles"))
    from .approval_services import visible_requests

    requests = visible_requests(user).values("pk")
    result |= Q(
        module="approvals",
        source_model="members.approvalrequest",
        source_id__in=requests,
    )
    from .models import ApprovalAuditEvent

    result |= Q(
        module="approvals",
        source_model="members.approvalauditevent",
        source_id__in=ApprovalAuditEvent.objects.filter(request_id__in=requests).values(
            "pk"
        ),
    )
    return rows.filter(result)


class AuditFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Record, user or reference")
    module = forms.ChoiceField(required=False)
    action = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All actions"),
            ("created", "Added"),
            ("updated", "Edited"),
            ("deleted", "Deleted"),
            ("access_changed", "Access changed"),
            ("login", "Signed in"),
            ("logout", "Signed out"),
            ("login_failed", "Failed sign-in"),
        ],
    )
    date_from = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, rows, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["module"].choices = [
            ("", "All permitted modules"),
            *rows.order_by("module_label")
            .values_list("module", "module_label")
            .distinct(),
        ]
        self.fields["action"].choices = [
            ("", "All actions"),
            *[
                (action, AuditLog(action=action).action_label)
                for action in rows.order_by("action")
                .values_list("action", flat=True)
                .distinct()
            ],
        ]
        for field in self.fields.values():
            field.widget.attrs["class"] = "field-input"

    def clean(self):
        values = super().clean()
        if (
            values.get("date_from")
            and values.get("date_to")
            and values["date_from"] > values["date_to"]
        ):
            self.add_error("date_to", "Choose an end date on or after the start date.")
        return values


@login_required
def audit_log_list(request):
    require_permission(request.user, AuditLog, "view")
    rows = visible_logs(request.user)
    form = AuditFilterForm(request.GET, rows=rows)
    if form.is_valid():
        values = form.cleaned_data
        if values["q"]:
            query = values["q"]
            rows = rows.filter(
                Q(record_label__icontains=query)
                | Q(actor_name__icontains=query)
                | Q(actor_username__icontains=query)
                | Q(actor_epf__icontains=query)
                | Q(source_model__icontains=query)
                | Q(after__reference__value__icontains=query)
                | Q(before__reference__value__icontains=query)
                | Q(after__reference__icontains=query)
            )
        for name in ("module", "action"):
            if values[name]:
                rows = rows.filter(**{name: values[name]})
        if values["date_from"]:
            rows = rows.filter(occurred_at__date__gte=values["date_from"])
        if values["date_to"]:
            rows = rows.filter(occurred_at__date__lte=values["date_to"])
    else:
        rows = rows.none()
    return render(
        request,
        "audit/list.html",
        {
            "title": "Audit Logs",
            "section": "audit_logs",
            "form": form,
            **paginated(request, rows),
        },
    )


@login_required
def audit_log_detail(request, pk):
    require_permission(request.user, AuditLog, "view")
    event = get_object_or_404(visible_logs(request.user), pk=pk)
    return render(
        request,
        "audit/detail.html",
        {"title": "Audit entry", "section": "audit_logs", "event": event},
    )


@login_required
def audit_log_write_disabled(request, pk=None):
    raise PermissionDenied(
        "Audit logs are read-only and cannot be added, edited or deleted."
    )
