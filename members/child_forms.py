from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from .forms import StyledModelForm
from .models import (
    Child,
    ChildDeposit,
    ChildDepositTransaction,
    ChildDepositType,
    Member,
)
from .permissions import district_records
from .transaction_forms import TransactionFilterForm


def searchable(field, label):
    field.widget.attrs.update(
        {"data-searchable-select": "", "data-search-label": label}
    )


class ChildForm(StyledModelForm):
    class Meta:
        model = Child
        fields = ["member", "name", "date_of_birth", "is_active"]
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = district_records(self.user, Member).order_by(
            "member_no", "pk"
        )
        searchable(self.fields["member"], "member")
        self.fields["member"].disabled = bool(self.instance.pk)
        self.fields["date_of_birth"].widget.attrs[
            "max"
        ] = timezone.localdate().isoformat()


class ChildDepositTypeForm(StyledModelForm):
    class Meta:
        model = ChildDepositType
        fields = [
            "name",
            "age_threshold",
            "rate_below_threshold",
            "rate_from_threshold",
            "sort_order",
            "is_active",
        ]
        widgets = {
            name: forms.NumberInput(attrs={"min": "0", "max": "100", "step": "0.01"})
            for name in ("rate_below_threshold", "rate_from_threshold")
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["age_threshold"].widget.attrs.update({"min": "1", "max": "150"})

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if (
            ChildDepositType.objects.filter(name__iexact=name)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError(
                "A child deposit type with this name already exists."
            )
        return name


class ChildDepositForm(StyledModelForm):
    class Meta:
        model = ChildDeposit
        fields = ["child", "deposit_type", "opened_on", "opening_amount", "is_active"]
        widgets = {
            "opened_on": forms.DateInput(attrs={"type": "date"}),
            "opening_amount": forms.NumberInput(
                attrs={"min": "0", "step": "0.01", "max": "9999999999.99"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = (
            district_records(self.user, Child)
            .select_related("member")
            .filter(Q(is_active=True) | Q(pk=self.instance.child_id))
        )
        self.fields["deposit_type"].queryset = ChildDepositType.objects.filter(
            Q(is_active=True) | Q(pk=self.instance.deposit_type_id)
        )
        searchable(self.fields["child"], "child")
        searchable(self.fields["deposit_type"], "deposit type")
        self.fields["opened_on"].widget.attrs["max"] = timezone.localdate().isoformat()
        if self.instance.pk:
            self.fields["child"].disabled = self.fields["deposit_type"].disabled = True
            if self.instance.transactions.exists():
                for name in ("opened_on", "opening_amount"):
                    self.fields[name].disabled = True
                    self.fields[name].help_text = (
                        "Fixed after dated entries have been recorded."
                    )


class ChildEntryForm(StyledModelForm):
    class Meta:
        model = ChildDepositTransaction
        fields = ["transaction_date", "debit", "credit", "reference", "description"]
        widgets = {
            "transaction_date": forms.DateInput(attrs={"type": "date"}),
            "debit": forms.NumberInput(
                attrs={"min": "0", "step": "0.01", "max": "9999999999.99"}
            ),
            "credit": forms.NumberInput(
                attrs={"min": "0", "step": "0.01", "max": "9999999999.99"}
            ),
        }

    def __init__(self, *args, deposit, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.deposit = deposit
        self.fields["transaction_date"].widget.attrs[
            "min"
        ] = deposit.opened_on.isoformat()
        for name in ("debit", "credit"):
            self.fields[name].required = False
            self.fields[name].help_text = (
                "Enter a positive amount on one side only; leave the other blank or zero."
            )

    def clean_debit(self):
        return self.cleaned_data["debit"] or Decimal("0.00")

    def clean_credit(self):
        return self.cleaned_data["credit"] or Decimal("0.00")


class ChildDepositFilterForm(forms.Form):
    member = forms.ModelChoiceField(
        queryset=Member.objects.none(), required=False, empty_label="All members"
    )
    child = forms.ModelChoiceField(
        queryset=Child.objects.none(), required=False, empty_label="All children"
    )
    deposit_type = forms.ModelChoiceField(
        queryset=ChildDepositType.objects.all(),
        required=False,
        empty_label="All child deposit types",
    )
    as_of = forms.DateField(
        required=False,
        label="Balance as of",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = district_records(user, Member).order_by(
            "member_no", "pk"
        )
        self.fields["child"].queryset = district_records(user, Child).select_related(
            "member"
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "field-input"
        for name, label in (
            ("member", "member"),
            ("child", "child"),
            ("deposit_type", "deposit type"),
        ):
            searchable(self.fields[name], label)

    def clean_as_of(self):
        return self.cleaned_data["as_of"] or timezone.localdate()

    def clean(self):
        data = super().clean()
        if (
            data.get("member")
            and data.get("child")
            and data["child"].member_id != data["member"].pk
        ):
            self.add_error("child", "Choose a child of the selected member.")
        return data


class ChildStatementFilterForm(TransactionFilterForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("member")
        self.fields.pop("payment_type")
