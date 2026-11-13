from decimal import Decimal
from uuid import uuid4

from django import forms
from django.db.models import Q
from django.utils import timezone

from .forms import StyledModelForm
from .models import (
    FixDepositAccount,
    FixDepositPolicy,
    FixLoanEntry,
    Member,
    PaymentType,
)
from .permissions import district_records


class FixPolicyForm(StyledModelForm):
    class Meta:
        model = FixDepositPolicy
        fields = [
            "name",
            "payment_type",
            "maturity_months",
            "withdrawal_percent",
            "loan_percent",
            "loan_interval_months",
            "loan_interest_rate",
            "is_active",
        ]
        widgets = {
            name: forms.NumberInput(attrs={"step": "0.01"})
            for name in ("withdrawal_percent", "loan_percent", "loan_interest_rate")
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_type"].queryset = PaymentType.objects.filter(
            Q(is_active=True) | Q(pk=self.instance.payment_type_id)
        )
        for name, minimum in (("maturity_months", 36), ("loan_interval_months", 12)):
            self.fields[name].widget.attrs.update({"min": str(minimum), "max": "1200"})
        for name, maximum in (
            ("withdrawal_percent", 75),
            ("loan_percent", 90),
            ("loan_interest_rate", 100),
        ):
            self.fields[name].widget.attrs.update({"min": "0.01", "max": str(maximum)})
        if self.instance.pk and (
            self.instance.accounts.exists()
            or self.instance.payment_type.transactions.exists()
            or self.instance.payment_type.opening_amounts.exists()
        ):
            self.fields["payment_type"].disabled = True


class FixAccountForm(StyledModelForm):
    class Meta:
        model = FixDepositAccount
        fields = ["member", "policy", "cycle_started_on", "is_active", "notes"]
        widgets = {"cycle_started_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = district_records(self.user, Member).order_by(
            "member_no", "pk"
        )
        self.fields["member"].widget.attrs["data-searchable-select"] = ""
        self.fields["policy"].queryset = FixDepositPolicy.objects.filter(
            Q(is_active=True) | Q(pk=self.instance.policy_id)
        )
        self.fields["cycle_started_on"].widget.attrs[
            "max"
        ] = timezone.localdate().isoformat()
        if self.instance.pk:
            self.fields["member"].disabled = self.fields["policy"].disabled = True
            if self.instance.loans.exists() or self.instance.withdrawals.exists():
                self.fields["cycle_started_on"].disabled = True


class FixFilterForm(forms.Form):
    member = forms.ModelChoiceField(
        queryset=Member.objects.none(), required=False, empty_label="All members"
    )
    as_of = forms.DateField(
        required=False,
        label="As of date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = district_records(user, Member).order_by(
            "member_no", "pk"
        )
        self.fields["member"].widget.attrs["data-searchable-select"] = ""
        for field in self.fields.values():
            field.widget.attrs["class"] = "field-input"

    def clean_as_of(self):
        return self.cleaned_data["as_of"] or timezone.localdate()


class ActionForm(forms.Form):
    day = forms.DateField(
        label="Date",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "field-check"
                if isinstance(field.widget, forms.CheckboxInput)
                else "field-input"
            )
        self.fields["day"].widget.attrs["max"] = timezone.localdate().isoformat()

    def clean_day(self):
        day = self.cleaned_data["day"]
        if day > timezone.localdate():
            raise forms.ValidationError("The date cannot be in the future.")
        return day


class AmountActionForm(ActionForm):
    amount = forms.DecimalField(
        label="Amount (LKR)",
        min_value=Decimal("0.01"),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"min": "0.01", "step": "0.01", "max": "9999999999.99"}
        ),
    )


class LoanEntryForm(ActionForm):
    kind = forms.ChoiceField(choices=FixLoanEntry.Kind.choices)
    principal = forms.DecimalField(
        label="Principal paid (LKR)",
        required=False,
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
    )
    interest = forms.DecimalField(
        label="Interest charged or paid (LKR)",
        required=False,
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
    )
    reference = forms.CharField(required=False, max_length=100)
    operation_key = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput())

    def clean_principal(self):
        return self.cleaned_data["principal"] or Decimal("0.00")

    def clean_interest(self):
        return self.cleaned_data["interest"] or Decimal("0.00")

    def clean(self):
        data = super().clean()
        principal, interest = data.get("principal") or 0, data.get("interest") or 0
        if data.get("kind") == FixLoanEntry.Kind.INTEREST:
            if principal:
                self.add_error(
                    "principal", "Principal must be zero for an interest charge."
                )
            if not interest:
                self.add_error("interest", "Enter a positive interest charge.")
        elif data.get("kind") == FixLoanEntry.Kind.REPAYMENT and not (
            principal or interest
        ):
            raise forms.ValidationError("Enter a principal or interest repayment.")
        return data


class SettlementForm(ActionForm):
    interest_confirmed = forms.BooleanField(
        label="I confirm all interest through this date has been assessed by the bank and fully paid.",
        required=True,
    )


class WithdrawalReviewForm(ActionForm):
    decision = forms.ChoiceField(
        label="Decision",
        choices=[
            ("release", "Approve and release to member"),
            ("reject", "Reject request"),
        ],
    )
