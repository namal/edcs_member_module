from uuid import uuid4
from decimal import Decimal

from django import forms
from django.utils import timezone

from .contribution_services import allocation_preview
from .models import Member, PaymentType
from .permissions import district_records


class ContributionForm(forms.Form):
    member = forms.ModelChoiceField(queryset=Member.objects.none())
    received_on = forms.DateField(
        label="Received on",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    amount = forms.DecimalField(
        label="Salary deduction / contribution received (LKR)",
        min_value=Decimal("0.01"),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
    )
    payment_type = forms.ModelChoiceField(
        queryset=PaymentType.objects.filter(is_active=True),
        required=False,
        label="Allocation",
        empty_label="Proportionally across my payment plan",
        help_text="Leave at proportional, or choose one configured category for a category-only payment.",
    )
    reference = forms.CharField(max_length=100, required=False)
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    operation_key = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput())
    review_hash = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.preview = None
        self.fields["member"].queryset = district_records(user, Member).order_by(
            "member_no", "pk"
        )
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs["class"] = "field-input"
        self.fields["member"].widget.attrs["data-searchable-select"] = ""

    def clean(self):
        values = super().clean()
        if (
            all(
                values.get(key) is not None
                for key in ("member", "received_on", "amount")
            )
            and not self.errors
        ):
            self.preview = allocation_preview(
                values["member"],
                values["received_on"],
                values["amount"],
                values.get("payment_type"),
            )
        return values
