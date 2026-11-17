from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.models import Group, Permission

from .permissions import ACTIONS, MODULES, MODULE_ACTIONS
from .models import District, OfficeLocation, LedgerOfficerProfile, MemberLedger


def workspace_permissions():
    codenames = [
        f"{action}_{model._meta.model_name}"
        for model, *_ in MODULES
        for action in MODULE_ACTIONS.get(model, ACTIONS)
    ]
    return Permission.objects.filter(
        content_type__app_label="members", codename__in=codenames
    ).select_related("content_type")


class AccessFormMixin:
    def prepare_access_fields(self, relation_name):
        catalog = workspace_permissions()
        self.fields["module_permissions"].queryset = catalog
        if self.instance.pk:
            relation = getattr(self.instance, relation_name)
            self.initial["module_permissions"] = relation.filter(pk__in=catalog)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "field-check"
                if isinstance(
                    field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)
                )
                else "field-input"
            )

    def save_permissions(self, relation):
        # Keep any advanced Django permissions outside the workspace catalog.
        preserved = list(relation.exclude(pk__in=workspace_permissions()))
        relation.set(preserved + list(self.cleaned_data["module_permissions"]))

    def permission_rows(self):
        selected = {str(value) for value in self["module_permissions"].value() or []}
        catalog = {
            permission.codename: permission
            for permission in self.fields["module_permissions"].queryset
        }
        return [
            {
                "label": label,
                "cells": [
                    {
                        "id": (
                            catalog[f"{action}_{model._meta.model_name}"].pk
                            if action in MODULE_ACTIONS.get(model, ACTIONS)
                            else None
                        ),
                        "label": f"{action.capitalize()} {label.lower()}",
                        "checked": action in MODULE_ACTIONS.get(model, ACTIONS)
                        and str(catalog[f"{action}_{model._meta.model_name}"].pk)
                        in selected,
                    }
                    for action in ACTIONS
                ],
            }
            for model, label, *_ in MODULES
        ]


class WorkspaceUserForm(AccessFormMixin, forms.ModelForm):
    epf_number = forms.CharField(label="EPF number", max_length=30, required=False)
    ledger_office = forms.ModelChoiceField(
        label="Ledger officer zonal office",
        queryset=OfficeLocation.objects.all(),
        required=False,
    )
    districts = forms.ModelMultipleChoiceField(
        queryset=District.objects.order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="Assigned districts",
        help_text="Choose one or more districts. Module permissions apply only within these districts. No selection means no district record access. Administrators have access to every district.",
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by("name"),
        required=False,
        label="Roles",
        widget=forms.CheckboxSelectMultiple(),
        help_text="Role permissions are added to the direct permissions below.",
    )
    module_permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(), required=False
    )
    password1 = forms.CharField(
        label="New password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Required for a new account. Leave blank to keep an existing password.",
    )
    password2 = forms.CharField(
        label="Confirm password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = get_user_model()
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        profile = (
            LedgerOfficerProfile.objects.filter(user=self.instance).first()
            if self.instance.pk
            else None
        )
        if profile:
            self.initial.update(
                epf_number=profile.epf_number, ledger_office=profile.zonal_office_id
            )
        self.fields["epf_number"].help_text = (
            "Required when this user is assigned as a Ledger Maintenance Officer."
        )
        self.fields["ledger_office"].help_text = (
            "Officers may maintain active ledgers in this zonal office. Assign its district below."
        )
        self.fields["email"].help_text = (
            "Optional. A welcome email is sent after a new account is saved "
            "when an email address is provided."
        )
        self.prepare_access_fields("user_permissions")
        if self.instance.pk:
            self.initial["roles"] = self.instance.groups.all()
            self.initial["districts"] = self.instance.assigned_districts.all()
        else:
            self.fields["password1"].required = True
            self.fields["password2"].required = True

    def clean(self):
        data = super().clean()
        epf = (data.get("epf_number") or "").strip().upper() or None
        data["epf_number"] = epf
        if (
            epf
            and LedgerOfficerProfile.objects.filter(epf_number=epf)
            .exclude(user_id=self.instance.pk)
            .exists()
        ):
            self.add_error(
                "epf_number", "This EPF number is already assigned to another user."
            )
        office = data.get("ledger_office")
        if office and not epf:
            self.add_error("epf_number", "Enter an EPF number for a ledger officer.")
        if (
            office
            and not self.instance.is_superuser
            and office.district_id
            not in {item.pk for item in data.get("districts", [])}
        ):
            self.add_error(
                "districts", "Assign the ledger officer's district to this user."
            )
        if self.instance.pk:
            if self.instance.assigned_lagers.exists() and (
                not epf or not data.get("is_active")
            ):
                self.add_error(
                    "epf_number" if not epf else "is_active",
                    "Reassign this user's ledger masters before removing the EPF number or deactivating the account.",
                )
            active = MemberLedger.objects.filter(
                maintenance_officer=self.instance, status="active"
            )
            old_office = self.initial.get("ledger_office")
            if (
                old_office != (office.pk if office else None)
                and active.exclude(
                    zonal_office_id=office.pk if office else None
                ).exists()
            ):
                self.add_error(
                    "ledger_office",
                    "Reassign active ledgers before changing this officer's zonal office.",
                )
            if active.exists() and (not epf or not data.get("is_active")):
                self.add_error(
                    "is_active",
                    "Reassign active ledgers before deactivating this officer or removing the EPF number.",
                )
            if (
                not self.instance.is_superuser
                and active.filter(
                    district_id__in=set(
                        self.instance.assigned_districts.values_list("pk", flat=True)
                    )
                    - {item.pk for item in data.get("districts", [])}
                ).exists()
            ):
                self.add_error(
                    "districts",
                    "Reassign active ledgers before removing their district access from this officer.",
                )
        if data.get("password1") != data.get("password2"):
            self.add_error("password2", "The passwords do not match.")
        if self.instance.pk and not data.get("is_active"):
            if self.instance.pk == self.actor.pk:
                self.add_error("is_active", "You cannot deactivate your own account.")
            elif self.instance.is_superuser:
                self.add_error(
                    "is_active", "Superuser accounts must remain active in this panel."
                )
        return data

    def _post_clean(self):
        super()._post_clean()
        if password := self.cleaned_data.get("password1"):
            try:
                password_validation.validate_password(password, self.instance)
            except forms.ValidationError as error:
                self.add_error("password1", error)

    def save(self):
        user = super().save(commit=False)
        if password := self.cleaned_data.get("password1"):
            user.set_password(password)
        user.save()
        user.groups.set(self.cleaned_data["roles"])
        self.save_permissions(user.user_permissions)
        user.assigned_districts.set(self.cleaned_data["districts"])
        LedgerOfficerProfile.objects.update_or_create(
            user=user,
            defaults={
                "epf_number": self.cleaned_data.get("epf_number"),
                "zonal_office": self.cleaned_data.get("ledger_office"),
            },
        )
        return user


class WorkspaceRoleForm(AccessFormMixin, forms.ModelForm):
    module_permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(), required=False
    )

    class Meta:
        model = Group
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prepare_access_fields("permissions")

    def save(self):
        role = super().save()
        self.save_permissions(role.permissions)
        return role
