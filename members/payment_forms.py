from decimal import Decimal

from django import forms
from django.db import transaction
from django.db.models import Q

from .forms import StyledModelForm
from .models import (
    PaymentPlan,
    PaymentPlanAmount,
    PaymentType,
    PaymentPlanTemplate,
    PaymentPlanTemplateAmount,
)
from .permissions import accessible_districts


class PaymentTypeForm(StyledModelForm):
    class Meta:
        model = PaymentType
        fields = [
            "name",
            "interest_rate",
            "interest_frequency",
            "interest_period_type",
            "interest_interval_count",
            "interest_interval_unit",
            "interest_start_date",
            "interest_end_date",
            "sort_order",
            "is_active",
        ]
        widgets = {
            "interest_rate": forms.NumberInput(
                attrs={"min": "0", "max": "100", "step": "0.01"}
            ),
            "interest_interval_count": forms.NumberInput(
                attrs={"min": "1", "step": "1", "max": "32767"}
            ),
            "interest_start_date": forms.DateInput(attrs={"type": "date"}),
            "interest_end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["interest_rate"].required = False
        # Django's positive-integer widget defaults to zero; custom periods start at one.
        self.fields["interest_interval_count"].widget.attrs["min"] = "1"
        # Older callers can omit the new setting; retain the bank's saved choice.
        frequency_key = self.add_prefix("interest_frequency")
        if self.is_bound and frequency_key not in self.data:
            self.data = self.data.copy()
            self.data[frequency_key] = (
                self.instance.interest_frequency
                or PaymentType.InterestFrequency.QUARTERLY
            )

    def clean(self):
        data = super().clean()
        # Ignore fields that do not belong to the selected period, including stale
        # browser values when changing from a custom period to a preset.
        mode = (
            data.get("interest_period_type")
            if data.get("interest_frequency") == PaymentType.InterestFrequency.CUSTOM
            else ""
        )
        names = {
            "interest_period_type": bool(mode),
            "interest_interval_count": mode == PaymentType.InterestPeriodType.REPEATING,
            "interest_interval_unit": mode == PaymentType.InterestPeriodType.REPEATING,
            "interest_start_date": mode == PaymentType.InterestPeriodType.DATE_RANGE,
            "interest_end_date": mode == PaymentType.InterestPeriodType.DATE_RANGE,
        }
        for name, relevant in names.items():
            if not relevant:
                self._errors.pop(name, None)
                data[name] = (
                    ""
                    if name in ("interest_period_type", "interest_interval_unit")
                    else None
                )
        return data

    def clean_interest_rate(self):
        return self.cleaned_data["interest_rate"] or Decimal("0.00")

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if (
            PaymentType.objects.filter(name__iexact=name)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError("A payment type with this name already exists.")
        return name


class ContributionFields:
    def add_contribution_fields(self, existing):
        self.payment_types = list(
            PaymentType.objects.filter(Q(is_active=True) | Q(pk__in=existing))
        )
        for payment_type in self.payment_types:
            self.fields[f"amount_{payment_type.pk}"] = forms.DecimalField(
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


class PaymentPlanTemplateForm(ContributionFields, StyledModelForm):
    class Meta:
        model = PaymentPlanTemplate
        fields = ["name", "description", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        existing = (
            {item.payment_type_id: item.amount for item in self.instance.amounts.all()}
            if self.instance.pk
            else {}
        )
        self.add_contribution_fields(existing)

    def clean_name(self):
        value = self.cleaned_data["name"].strip()
        if (
            PaymentPlanTemplate.objects.filter(name__iexact=value)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError("A template with this name already exists.")
        return value

    def clean(self):
        data = super().clean()
        if not any(
            data.get(f"amount_{item.pk}", Decimal("0")) for item in self.payment_types
        ):
            raise forms.ValidationError(
                "Enter a positive monthly amount for at least one payment type."
            )
        return data

    @transaction.atomic
    def save(self):
        template = super().save()
        for payment_type in self.payment_types:
            amount = self.cleaned_data[f"amount_{payment_type.pk}"] or Decimal("0")
            if amount:
                PaymentPlanTemplateAmount.objects.update_or_create(
                    template=template,
                    payment_type=payment_type,
                    defaults={"amount": amount},
                )
            else:
                template.amounts.filter(payment_type=payment_type).delete()
        return template


class PaymentPlanForm(ContributionFields, StyledModelForm):
    apply_template = forms.ModelChoiceField(
        queryset=PaymentPlanTemplate.objects.none(),
        required=False,
        label="Payment plan template",
        empty_label="Custom plan / keep current amounts",
    )

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
        templates = PaymentPlanTemplate.objects.filter(is_active=True)
        self.fields["apply_template"].queryset = templates
        selected = (
            self.data.get(self.add_prefix("apply_template"))
            if self.is_bound
            else self.initial.get("apply_template")
        )
        template = (
            templates.prefetch_related("amounts__payment_type")
            .filter(pk=selected)
            .first()
            if str(selected or "").isdigit()
            else None
        )
        copied = (
            {
                item.payment_type_id: item.amount
                for item in template.amounts.all()
                if item.payment_type.is_active
            }
            if template
            else {}
        )
        if template:
            if self.is_bound:
                self.data = self.data.copy()
                for item in PaymentType.objects.filter(is_active=True):
                    self.data.setdefault(
                        self.add_prefix(f"amount_{item.pk}"),
                        copied.get(item.pk, Decimal("0.00")),
                    )
            else:
                existing = {**{key: Decimal("0.00") for key in existing}, **copied}
        self.add_contribution_fields(existing)

    def clean(self):
        data = super().clean()
        template = data.get("apply_template")
        if (
            template
            and not template.amounts.filter(
                payment_type__is_active=True, amount__gt=0
            ).exists()
        ):
            self.add_error(
                "apply_template",
                "This template has no active payment type amounts to copy.",
            )
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
        plan = super().save(commit=False)
        template = self.cleaned_data.get("apply_template")
        if template:
            plan.source_template = template
            plan.source_template_name = template.name
        plan.save()
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
