from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.models import Group, Permission

from .permissions import ACTIONS, MODULES
from .models import District


def workspace_permissions():
    codenames = [
        f"{action}_{model._meta.model_name}"
        for model, *_ in MODULES
        for action in ACTIONS
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
                        "id": catalog[f"{action}_{model._meta.model_name}"].pk,
                        "label": f"{action.capitalize()} {label.lower()}",
                        "checked": str(catalog[f"{action}_{model._meta.model_name}"].pk)
                        in selected,
                    }
                    for action in ACTIONS
                ],
            }
            for model, label, *_ in MODULES
        ]


class WorkspaceUserForm(AccessFormMixin, forms.ModelForm):
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
