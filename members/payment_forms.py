from decimal import Decimal

from django import forms
from django.db import transaction
from django.db.models import Q

from .forms import StyledModelForm
from .models import PaymentPlan, PaymentPlanAmount, PaymentType
from .permissions import accessible_districts


class PaymentTypeForm(StyledModelForm):
    class Meta:
        model = PaymentType
        fields = ["name", "sort_order", "is_active"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if (
            PaymentType.objects.filter(name__iexact=name)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError("A payment type with this name already exists.")
        return name


class PaymentPlanForm(StyledModelForm):
    class Meta:
        model = PaymentPlan
        fields = ["agreement_date"]
        widgets = {"agreement_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, member, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.member = member
        existing = (
            {item.payment_type_id: item.amount for item in self.instance.amounts.all()}
            if self.instance.pk
            else {}
        )
        self.payment_types = list(
            PaymentType.objects.filter(Q(is_active=True) | Q(pk__in=existing))
        )
        for payment_type in self.payment_types:
            name = f"amount_{payment_type.pk}"
            self.fields[name] = forms.DecimalField(
                label=payment_type.name
                + (" (inactive)" if not payment_type.is_active else ""),
                required=False,
                min_value=Decimal("0"),
                max_digits=12,
                decimal_places=2,
                initial=existing.get(payment_type.pk, Decimal("0.00")),
                help_text="Monthly amount in LKR. Leave blank or enter 0 if unused.",
                widget=forms.NumberInput(
                    attrs={
                        "class": "field-input",
                        "min": "0",
                        "step": "0.01",
                        "max": "9999999999.99",
                        "inputmode": "decimal",
                        "data-monthly-amount": "",
                    }
                ),
            )

    @property
    def amount_fields(self):
        return [self[f"amount_{item.pk}"] for item in self.payment_types]

    def clean(self):
        data = super().clean()
        if (
            self.user is not None
            and not accessible_districts(self.user)
            .filter(pk=self.instance.member.district_id)
            .exists()
        ):
            raise forms.ValidationError(
                "This member is outside your assigned districts."
            )
        if not self.payment_types:
            raise forms.ValidationError(
                "Add an active payment type before saving a payment plan."
            )
        if (
            PaymentPlan.objects.filter(member=self.instance.member)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError(
                "This member already has a payment plan. Open the existing plan to edit it."
            )
        return data

    @transaction.atomic
    def save(self):
        plan = super().save()
        for payment_type in self.payment_types:
            PaymentPlanAmount.objects.update_or_create(
                plan=plan,
                payment_type=payment_type,
                defaults={
                    "amount": self.cleaned_data[f"amount_{payment_type.pk}"]
                    or Decimal("0.00")
                },
            )
        return plan
