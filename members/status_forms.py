from django import forms
from django.utils import timezone

from . import models as m
from .permissions import district_records
from .status_services import REASONS, TRANSITIONS, validated_documents


class MultipleDocumentsInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class SupportingDocumentsField(forms.FileField):
    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        files = [
            super(SupportingDocumentsField, self).clean(item, initial) for item in files
        ]
        validated_documents(files)
        return files


class MemberStatusForm(forms.Form):
    member = forms.ModelChoiceField(queryset=m.Member.objects.none(), label="Member")
    kind = forms.ChoiceField(
        choices=m.MemberStatusRequest.Kind.choices, label="Requested action"
    )
    reason = forms.ChoiceField(choices=[], label="Reason")
    description = forms.CharField(
        min_length=10,
        max_length=3000,
        strip=True,
        label="Detailed reason description",
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    requested_effective_date = forms.DateField(
        initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"})
    )
    documents = SupportingDocumentsField(
        required=False,
        label="Supporting documents",
        widget=MultipleDocumentsInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}),
        help_text="Up to five PDF, JPG or PNG files, maximum 5 MB each. Downloads require authorized access.",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = (
            district_records(user, m.Member)
            .filter(status__in=["pending", "active", "inactive"])
            .select_related("lager", "district", "office_location")
        )
        member_id = (
            self.data.get("member") if self.is_bound else self.initial.get("member")
        )
        self.selected_member = (
            self.fields["member"].queryset.filter(pk=member_id).first()
            if str(member_id or "").isdigit()
            else None
        )
        kind = self.data.get("kind") if self.is_bound else self.initial.get("kind")
        if self.selected_member and not kind:
            kind = {
                "pending": "activate",
                "active": "inactivate",
                "inactive": "reactivate",
            }[self.selected_member.status]
            self.initial["kind"] = kind
        if self.selected_member:
            expected = {
                "pending": "activate",
                "active": "inactivate",
                "inactive": "reactivate",
            }[self.selected_member.status]
            self.fields["kind"].choices = [
                (expected, dict(m.MemberStatusRequest.Kind.choices)[expected])
            ]
        self.fields["reason"].choices = [
            ("", "Select a valid reason"),
            *REASONS.get(kind, []),
        ]
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "field-input")
        self.fields["member"].widget.attrs["data-searchable-select"] = ""
        self.fields["requested_effective_date"].widget.attrs[
            "min"
        ] = timezone.localdate().isoformat()

    def clean(self):
        data = super().clean()
        member, kind, day = (
            data.get("member"),
            data.get("kind"),
            data.get("requested_effective_date"),
        )
        if member and kind and member.status != TRANSITIONS[kind][0]:
            self.add_error(
                "kind", "Choose the action that matches this member’s current status."
            )
        if day and day < timezone.localdate():
            self.add_error(
                "requested_effective_date", "Choose today or a future effective date."
            )
        return data
