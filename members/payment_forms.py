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
    def add_contribution_fields(
        self, existing, *, locked=None, fixed=None, template=False
    ):
        self.locked_amounts = locked or {}
        self.fixed_flags = fixed or {}
        self.payment_types = list(
            PaymentType.objects.filter(Q(is_active=True) | Q(pk__in=existing))
        )
        for payment_type in self.payment_types:
            key = f"amount_{payment_type.pk}"
            locked_amount = self.locked_amounts.get(payment_type.pk)
            attrs = {
                "class": "field-input",
                "min": "0",
                "step": "0.01",
                "max": "9999999999.99",
                "inputmode": "decimal",
                "data-monthly-amount": "",
            }
            widget = forms.NumberInput(attrs=attrs)
            help_text = "Monthly amount in LKR. Leave blank or enter 0 if unused."
            initial = existing.get(payment_type.pk, Decimal("0.00"))
            if locked_amount is not None:
                initial = locked_amount
                widget.attrs.update(
                    {"readonly": True, "data-locked-amount": str(initial)}
                )
                help_text = "Fixed monthly amount. The saved amount cannot be changed."
                if self.is_bound:
                    self.data = self.data.copy()
                    self.data.setdefault(self.add_prefix(key), str(initial))
            self.fields[key] = forms.DecimalField(
                label=payment_type.name
                + (" (inactive)" if not payment_type.is_active else ""),
                required=False,
                min_value=Decimal("0"),
                max_digits=12,
                decimal_places=2,
                initial=initial,
                help_text=help_text,
                widget=widget,
            )
            if template:
                self.fields[f"fixed_{payment_type.pk}"] = forms.BooleanField(
                    label="Fixed amount",
                    required=False,
                    initial=self.fixed_flags.get(payment_type.pk, False)
                    or locked_amount is not None,
                    disabled=locked_amount is not None,
                    help_text="Lock this monthly amount in the member plan.",
                    widget=forms.CheckboxInput(
                        attrs={"class": "field-check", "data-fixed-toggle": ""}
                    ),
                )

    def clean_contribution_amounts(self, data):
        for payment_type in self.payment_types:
            key = f"amount_{payment_type.pk}"
            if key not in data:
                continue
            amount = data[key] or Decimal("0.00")
            expected = self.locked_amounts.get(payment_type.pk)
            if expected is not None:
                if amount != expected:
                    self.add_error(key, f"This amount is fixed at LKR {expected:,.2f}.")
            fixed_key = f"fixed_{payment_type.pk}"
            if data.get(fixed_key) and not amount:
                self.add_error(
                    key, "Enter a positive amount before marking this row as fixed."
                )

    @property
    def amount_fields(self):
        return [self[f"amount_{item.pk}"] for item in self.payment_types]

    @property
    def contribution_rows(self):
        return [
            (self[f"amount_{item.pk}"], self[f"fixed_{item.pk}"])
            for item in self.payment_types
        ]


class PaymentPlanTemplateForm(ContributionFields, StyledModelForm):
    class Meta:
        model = PaymentPlanTemplate
        fields = ["name", "description", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rows = list(self.instance.amounts.all()) if self.instance.pk else []
        existing = {item.payment_type_id: item.amount for item in rows}
        self.add_contribution_fields(
            existing,
            template=True,
            locked={
                item.payment_type_id: item.amount for item in rows if item.is_fixed
            },
            fixed={item.payment_type_id: item.is_fixed for item in rows},
        )

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
        self.clean_contribution_amounts(data)
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
                    defaults={
                        "amount": amount,
                        "is_fixed": self.cleaned_data[f"fixed_{payment_type.pk}"],
                    },
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
        rows = (
            list(self.instance.amounts.select_related("payment_type"))
            if self.instance.pk
            else []
        )
        existing = {item.payment_type_id: item.amount for item in rows}
        locked = {item.payment_type_id: item.amount for item in rows if item.is_fixed}
        saved_locked = dict(locked)
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
            for item in template.amounts.all():
                if item.payment_type.is_active and item.is_fixed:
                    locked.setdefault(item.payment_type_id, item.amount)
            if self.is_bound:
                self.data = self.data.copy()
                for item in PaymentType.objects.filter(is_active=True):
                    self.data.setdefault(
                        self.add_prefix(f"amount_{item.pk}"),
                        locked.get(item.pk, copied.get(item.pk, Decimal("0.00"))),
                    )
            else:
                existing = {**{key: Decimal("0.00") for key in existing}, **copied}
        self.add_contribution_fields(existing, locked=locked)
        for payment_type_id, amount in saved_locked.items():
            self.fields[f"amount_{payment_type_id}"].widget.attrs[
                "data-saved-fixed-amount"
            ] = str(amount)

    def clean(self):
        data = super().clean()
        self.clean_contribution_amounts(data)
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
                "This member is outside your assigned branches."
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
                    or Decimal("0.00"),
                    "is_fixed": payment_type.pk in self.locked_amounts,
                },
            )
        return plan
