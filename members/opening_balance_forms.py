from decimal import Decimal

from django import forms
from django.db import transaction
from django.db.models import Q

from .forms import StyledModelForm
from .models import (
    DepositInterestYear,
    Member,
    OpeningBalance,
    OpeningBalanceAmount,
    PaymentType,
)
from .permissions import accessible_districts, district_records


class OpeningBalanceMemberForm(forms.Form):
    member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        required=False,
        label="Member number / name",
        empty_label="All members — select a member to view balances",
        widget=forms.Select(attrs={"class": "field-input"}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = district_records(user, Member).order_by(
            "member_no", "pk"
        )


class OpeningBalanceForm(StyledModelForm):
    class Meta:
        model = OpeningBalance
        fields = ["balance_date"]
        widgets = {"balance_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, member, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.member = member
        from .fix_deposit_services import locked_payment_types

        self.locked_types = locked_payment_types(member.pk)
        self.locked_types.update(
            DepositInterestYear.objects.filter(
                member=member, posted_at__isnull=False
            ).values_list("payment_type_id", flat=True)
        )
        if self.instance.pk and self.locked_types:
            self.fields["balance_date"].disabled = True
            self.fields["balance_date"].help_text = (
                "Locked by posted interest or Fix Deposit loan/withdrawal history."
            )
        existing = (
            {item.payment_type_id: item.amount for item in self.instance.amounts.all()}
            if self.instance.pk
            else {}
        )
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
                help_text="Opening balance in LKR. Leave blank or enter 0 if unused.",
                widget=forms.NumberInput(
                    attrs={
                        "class": "field-input",
                        "min": "0",
                        "step": "0.01",
                        "max": "9999999999.99",
                        "inputmode": "decimal",
                        "data-balance-amount": "",
                    }
                ),
            )
            if payment_type.pk in self.locked_types:
                self.fields[f"amount_{payment_type.pk}"].disabled = True
                self.fields[f"amount_{payment_type.pk}"].help_text = (
                    "Locked by posted interest or Fix Deposit loan/withdrawal history."
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
                "Add an active payment type before saving opening balances."
            )
        if (
            OpeningBalance.objects.filter(member=self.instance.member)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError(
                "This member already has opening balances. Open the existing record to edit it."
            )
        return data

    @transaction.atomic
    def save(self):
        balance = super().save()
        for payment_type in self.payment_types:
            if (
                payment_type.pk in self.locked_types
                and not balance.amounts.filter(payment_type=payment_type).exists()
            ):
                continue
            OpeningBalanceAmount.objects.update_or_create(
                opening_balance=balance,
                payment_type=payment_type,
                defaults={
                    "amount": self.cleaned_data[f"amount_{payment_type.pk}"]
                    or Decimal("0.00")
                },
            )
        return balance
