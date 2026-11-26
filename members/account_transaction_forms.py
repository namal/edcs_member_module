"""One entry screen, validated by the existing member or child posting form."""

from django import forms

from .child_forms import ChildEntryForm
from .contribution_forms import ChildDepositSelect, RemainingAllocationForm
from .models import (
    ChildDeposit,
    ChildDepositTransaction,
    ChildDepositType,
    PaymentTransaction,
    PaymentType,
)
from .permissions import district_records, has_module_permission
from .transaction_forms import PaymentTransactionForm, TransactionFilterForm


class AccountTransactionForm(RemainingAllocationForm):
    def __init__(self, *args, user, **kwargs):
        source = (
            args[0]
            if args and args[0] is not None
            else kwargs.get("data") or kwargs.get("initial", {})
        )
        member_form = PaymentTransactionForm(user=user, initial=kwargs.get("initial"))
        member_id = source.get("member", "")
        member = (
            member_form.fields["member"].queryset.filter(pk=member_id).first()
            if str(member_id).isdigit()
            else None
        )
        super().__init__(*args, member=member, user=user, **kwargs)
        selector_fields = self.fields
        self.fields = member_form.fields
        self.fields["payment_type"] = selector_fields["payment_type"]
        self.fields["child_deposit"] = selector_fields["child_deposit"]
        self.fields["transaction_date"].initial = member_form.instance.transaction_date
        self.user = user
        self.instance = PaymentTransaction()
        self.delegate = None

    def clean(self):
        values = super().clean()
        if self.errors:
            return values
        if values.get("child_deposit"):
            if values.get("reverses"):
                self.add_error(
                    "reverses",
                    "Use the child account statement to correct a child entry.",
                )
                return values
            self.delegate = ChildEntryForm(
                self.data, user=self.user, deposit=values["child_deposit"]
            )
        else:
            self.delegate = PaymentTransactionForm(self.data, user=self.user)
        if not self.delegate.is_valid():
            for name, errors in self.delegate.errors.as_data().items():
                self.add_error(name if name in self.fields else None, errors)
        else:
            self.instance = self.delegate.instance
            if not isinstance(self.instance, ChildDepositTransaction):
                values["payment_type"] = self.delegate.cleaned_data["payment_type"]
        return values

    def save(self, commit=True):
        return self.delegate.save(commit=commit)


class AccountStatementFilterForm(TransactionFilterForm):
    child_deposit = forms.ModelChoiceField(
        queryset=ChildDeposit.objects.none(),
        required=False,
        label="Child deposit account",
        empty_label="Select the child's deposit account",
        widget=ChildDepositSelect(
            attrs={"class": "field-input", "data-child-deposit": ""}
        ),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.member_type_field = self.fields["payment_type"]
        child_choices = []
        if has_module_permission(user, ChildDeposit, "view"):
            child_choices = [
                (
                    f"child-type:{item.pk}",
                    item.name + (" (inactive)" if not item.is_active else ""),
                )
                for item in ChildDepositType.objects.all()
            ]
            member_id = self.data.get("member", "")
            if str(member_id).isdigit():
                self.fields["child_deposit"].queryset = (
                    district_records(user, ChildDeposit)
                    .filter(child__member_id=member_id)
                    .select_related("child", "deposit_type")
                )
        self.fields["payment_type"] = forms.ChoiceField(
            label="Account",
            widget=forms.Select(attrs={"class": "field-input"}),
            choices=[
                ("", "Select an account"),
                (
                    "Member accounts",
                    [
                        (str(item.pk), self.member_type_field.label_from_instance(item))
                        for item in PaymentType.objects.all()
                    ],
                ),
                ("Child deposit types", child_choices),
            ],
        )

    def clean(self):
        data = super().clean()
        account = data.get("payment_type", "")
        deposit = data.get("child_deposit")
        if account.startswith("child-type:"):
            if deposit is None:
                self.add_error("child_deposit", "Select the child's deposit account.")
            elif str(deposit.deposit_type_id) != account.split(":", 1)[1]:
                self.add_error(
                    "child_deposit",
                    "Choose an account of the selected child deposit type.",
                )
        elif account:
            if deposit is not None:
                self.add_error(
                    "child_deposit",
                    "A child deposit account requires a child deposit type.",
                )
            data["payment_type"] = self.member_type_field.clean(account)
        return data
