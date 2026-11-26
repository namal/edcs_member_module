from calendar import monthrange

from django import forms
from django.utils import timezone

from .models import Member
from .transaction_forms import member_choices


class MemberBalanceForm(forms.Form):
    member = forms.ModelChoiceField(
        queryset=Member.objects.none(), empty_label="Select a member"
    )
    month = forms.DateField(
        input_formats=["%Y-%m"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
    )
    history = forms.ChoiceField(
        choices=[
            ("all", "All transactions up to this month"),
            ("month", "Selected month only"),
        ]
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = member_choices(user)
        self.fields["member"].widget.attrs["data-searchable-select"] = ""
        self.fields["month"].widget.attrs["max"] = timezone.localdate().strftime(
            "%Y-%m"
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "field-input"

    def clean_month(self):
        month = self.cleaned_data["month"].replace(day=1)
        if month > timezone.localdate().replace(day=1):
            raise forms.ValidationError("Choose the current month or an earlier month.")
        return month

    @property
    def as_of(self):
        month = self.cleaned_data["month"]
        return min(
            month.replace(day=monthrange(month.year, month.month)[1]),
            timezone.localdate(),
        )
