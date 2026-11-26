from django import forms
from django.contrib.auth import get_user_model
from django.forms import formset_factory

from .approval_models import ApprovalHierarchy
from .approval_scope import ALL_MODULES, PAYMENT_EXCEPTIONS
from .permissions import MODULES


class HierarchyForm(forms.ModelForm):
    reason = forms.CharField(
        label="Reason for configuration", widget=forms.Textarea(attrs={"rows": 2})
    )
    version = forms.IntegerField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = ApprovalHierarchy
        fields = ["name", "module", "action", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["module"] = forms.ChoiceField(
            choices=[
                (ALL_MODULES, "All modules — edits only"),
                (PAYMENT_EXCEPTIONS, "Payment exceptions — below-plan contributions"),
            ]
            + [(model._meta.model_name, label) for model, label, *_ in MODULES]
        )
        self.fields["action"] = forms.ChoiceField(
            choices=[
                ("change", "Edit existing records"),
                ("add", "Member transfer / below-plan payment"),
            ]
        )
        self.fields["module"].widget.attrs["data-approval-module"] = ""
        self.fields["action"].widget.attrs["data-approval-action"] = ""
        self.initial["action"] = (
            "add"
            if self.instance.module in ("membertransfer", PAYMENT_EXCEPTIONS)
            else "change"
        )
        self.initial["version"] = self.instance.version if self.instance.pk else 0
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "field-check"
                if isinstance(field.widget, forms.CheckboxInput)
                else "field-input"
            )

    def clean(self):
        data = super().clean()
        expected_action = (
            "add"
            if data.get("module") in ("membertransfer", PAYMENT_EXCEPTIONS)
            else "change"
        )
        if data.get("action") and data["action"] != expected_action:
            self.add_error(
                "action",
                "Select Member transfer / below-plan payment for Member Transfers or Payment exceptions; select Edit existing records for other modules.",
            )
        return data


class ApproverChoices(forms.ModelMultipleChoiceField):
    def label_from_instance(self, user):
        return f"{user.get_full_name() or user.username} ({user.username})"


class LevelForm(forms.Form):
    position = forms.IntegerField(min_value=1, max_value=100, label="Approval order")
    name = forms.CharField(max_length=100, label="Level name")
    approvers = ApproverChoices(
        queryset=get_user_model().objects.none(), widget=forms.CheckboxSelectMultiple()
    )
    allow_rejection = forms.BooleanField(
        required=False, initial=True, label="Assigned users may reject requests"
    )
    allow_self_approval = forms.BooleanField(
        required=False,
        label="Allow requester approval at this level (Membership Status only)",
        help_text="The requester must also be explicitly assigned as an approver and have Membership Status Edit/review permission. Leave off to require another reviewer.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["approvers"].queryset = (
            get_user_model().objects.filter(is_active=True).order_by("username")
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "field-check"
                if isinstance(
                    field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)
                )
                else "field-input"
            )


class LevelFormSet(forms.BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        levels = [
            form.cleaned_data
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        if not levels:
            raise forms.ValidationError(
                "Add at least one level with designated approvers."
            )
        positions = [row["position"] for row in levels]
        if len(positions) != len(set(positions)):
            raise forms.ValidationError(
                "Each level must have a different approval order."
            )


HierarchyLevels = formset_factory(
    LevelForm,
    formset=LevelFormSet,
    extra=1,
    can_delete=True,
    max_num=20,
    validate_max=True,
)


class DecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            ("approve", "Approve level"),
            ("reject", "Reject"),
            ("override_approve", "Approve all remaining levels"),
            ("cancel", "Cancel request"),
        ]
    )
    reason = forms.CharField(
        required=False,
        max_length=3000,
        widget=forms.Textarea(attrs={"class": "field-input", "rows": 3}),
    )
