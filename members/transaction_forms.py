from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from .forms import StyledModelForm
from .models import Member, PaymentTransaction, PaymentType
from .permissions import district_records


class PaymentTypeChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name}{' (inactive)' if not obj.is_active else ''}"


def member_choices(user):
    return district_records(user, Member).order_by("member_no", "pk")


class TransactionFilterForm(forms.Form):
    member = forms.ModelChoiceField(
        queryset=Member.objects.none(), empty_label="Select a member"
    )
    payment_type = PaymentTypeChoiceField(
        queryset=PaymentType.objects.all(), empty_label="Select a payment type"
    )
    date = forms.DateField(
        required=False,
        label="Single date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_from = forms.DateField(
        required=False,
        label="From date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False, label="To date", widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, user, require_selection=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = member_choices(user)
        if not require_selection:
            for name in ("member", "payment_type"):
                self.fields[name].required = False
                self.fields[name].empty_label = (
                    "All members" if name == "member" else "All payment types"
                )
                self.fields[name].help_text = "Leave blank to include all."
        for field in self.fields.values():
            field.widget.attrs["class"] = "field-input"
        for name in ("member", "payment_type"):
            self.fields[name].widget.attrs["data-searchable-select"] = ""

    def clean(self):
        data = super().clean()
        single, start, end = (data.get(key) for key in ("date", "date_from", "date_to"))
        if single and (start or end):
            raise forms.ValidationError(
                "Use a single date or a date range, then clear the other date fields."
            )
        if start and end and start > end:
            self.add_error("date_to", "To date must be on or after From date.")
        if start and not end and start > timezone.localdate():
            self.add_error(
                "date_to", "For a future From date, choose a To date on or after it."
            )
        return data


class PaymentTransactionForm(StyledModelForm):
    payment_type = PaymentTypeChoiceField(queryset=PaymentType.objects.none())

    class Meta:
        model = PaymentTransaction
        fields = [
            "member",
            "payment_type",
            "transaction_date",
            "debit",
            "credit",
            "reference",
            "description",
        ]
        widgets = {
            "transaction_date": forms.DateInput(attrs={"type": "date"}),
            "debit": forms.NumberInput(
                attrs={"min": "0", "step": "0.01", "max": "9999999999.99"}
            ),
            "credit": forms.NumberInput(
                attrs={"min": "0", "step": "0.01", "max": "9999999999.99"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = member_choices(self.user)
        self.fields["payment_type"].queryset = PaymentType.objects.filter(
            Q(is_active=True) | Q(pk=self.instance.payment_type_id)
        )
        for name in ("member", "payment_type"):
            self.fields[name].widget.attrs["data-searchable-select"] = ""
            self.fields[name].empty_label = f"Select a {name.replace('_', ' ')}"
        # Keep historical entries on their original member and payment type.
        if self.instance.pk:
            self.fields["member"].disabled = True
            self.fields["payment_type"].disabled = True
        for name in ("debit", "credit"):
            self.fields[name].required = False
            self.fields[name].help_text = (
                "Enter an amount on one side only. Leave the other side blank or zero."
            )

    def clean_debit(self):
        return self.cleaned_data["debit"] or Decimal("0.00")

    def clean_credit(self):
        return self.cleaned_data["credit"] or Decimal("0.00")
