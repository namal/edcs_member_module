from uuid import uuid4
from decimal import Decimal
import re

from django import forms
from django.utils import timezone

from .contribution_services import allocation_input, allocation_preview
from .models import ChildDeposit, ChildDepositType, Member, PaymentType
from .permissions import district_records, has_module_permission


class ChildDepositSelect(forms.Select):
    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex, attrs
        )
        if hasattr(value, "instance"):
            option["attrs"]["data-deposit-type"] = value.instance.deposit_type_id
        return option


class RemainingAllocationForm(forms.Form):
    payment_type = forms.ChoiceField(
        label="Account",
        widget=forms.Select(
            attrs={"class": "field-input", "data-remaining-account": ""}
        ),
    )
    child_deposit = forms.ModelChoiceField(
        queryset=ChildDeposit.objects.none(),
        required=False,
        label="Child deposit account",
        empty_label="Select the child's registered deposit account",
        widget=ChildDepositSelect(
            attrs={"class": "field-input", "data-child-deposit": ""}
        ),
        help_text="For a child deposit type, choose the child's existing account. Register the account in Child Deposits first if needed.",
    )
    amount = forms.DecimalField(
        label="Allocated amount (LKR)",
        min_value=Decimal("0.01"),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "class": "field-input",
                "min": "0.01",
                "max": "9999999999.99",
                "step": "0.01",
                "inputmode": "decimal",
                "data-remaining-amount": "",
            }
        ),
    )

    def __init__(self, *args, member=None, user, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [
            (str(item.pk), item.name)
            for item in PaymentType.objects.filter(is_active=True)
        ]
        child_types = []
        if has_module_permission(user, ChildDeposit, "add"):
            child_types = [
                (f"child-type:{item.pk}", item.name)
                for item in ChildDepositType.objects.filter(is_active=True)
            ]
            if member:
                self.fields["child_deposit"].queryset = (
                    district_records(user, ChildDeposit)
                    .filter(
                        child__member=member,
                        child__is_active=True,
                        is_active=True,
                        deposit_type__is_active=True,
                    )
                    .select_related("child", "deposit_type")
                )
        self.fields["payment_type"].choices = [
            ("", "Select an account"),
            ("Member accounts", choices),
            ("Child deposit types", child_types),
        ]

    def clean(self):
        values = super().clean()
        account = values.get("payment_type", "")
        deposit = values.get("child_deposit")
        if account.startswith("child-type:"):
            if deposit is None:
                self.add_error("child_deposit", "Select the child's deposit account.")
            elif str(deposit.deposit_type_id) != account.split(":", 1)[1]:
                self.add_error(
                    "child_deposit",
                    "Choose an account of the selected child deposit type.",
                )
        elif deposit is not None:
            self.add_error(
                "child_deposit",
                "A child deposit account requires a child deposit type.",
            )
        return values


RemainingAllocationFormSet = forms.formset_factory(
    RemainingAllocationForm,
    extra=1,
    can_delete=True,
    max_num=100,
    validate_max=True,
    absolute_max=100,
)


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
        empty_label="All categories in the payment plan",
        help_text="Enter each category's allocated amount in the preview, or select a single category.",
    )
    reference = forms.CharField(max_length=100, required=False)
    remaining_payment_type = forms.ModelChoiceField(
        queryset=PaymentType.objects.filter(is_active=True),
        required=False,
        label="Payment type for remaining balance credit",
        empty_label="Select a payment type for the remaining balance",
        help_text="Any received amount left after the allocations below will be posted as a credit to this payment type when saved.",
    )
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    operation_key = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput())
    review_hash = forms.CharField(required=False, widget=forms.HiddenInput())
    allocation_context = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        # Older single-account submissions remain replayable; new screens use a formset.
        has_remaining_data = self.is_bound and any(
            key.startswith("remaining-") for key in self.data
        )
        self.preview = None
        self.plan_rows = []
        self.context_matches = False
        self.manual_allocation = False
        self.fields["member"].queryset = district_records(user, Member).order_by(
            "member_no", "pk"
        )
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs["class"] = "field-input"
        self.fields["member"].widget.attrs["data-searchable-select"] = ""
        source = self.data if self.is_bound else self.initial
        member_id = source.get("member", "")
        category_id = source.get("payment_type", "")
        member = (
            self.fields["member"].queryset.filter(pk=member_id).first()
            if str(member_id).isdigit()
            else None
        )
        self.remaining_formset = RemainingAllocationFormSet(
            self.data if has_remaining_data else None,
            prefix="remaining",
            form_kwargs={"member": member, "user": user},
        )
        if member is not None:
            from .models import PaymentPlanAmount

            rows = PaymentPlanAmount.objects.filter(
                plan__member=member, amount__gt=0
            ).select_related("payment_type")
            if category_id:
                rows = (
                    rows.filter(payment_type_id=category_id)
                    if str(category_id).isdigit()
                    else rows.none()
                )
            self.plan_rows = list(
                rows.order_by(
                    "payment_type__sort_order", "payment_type__name", "payment_type_id"
                )
            )
            try:
                received = self.fields["amount"].clean(source.get("amount"))
            except forms.ValidationError:
                received = None
            self.manual_allocation = (
                received is not None and received != self.plan_total
            )
            context = f"{member.pk}:{category_id or ''}"
            self.initial["allocation_context"] = context
            self.context_matches = (
                self.is_bound and self.data.get("allocation_context") == context
            )
            reset = (
                self.is_bound
                and not self.context_matches
                and self.data.get("intent") != "post"
            )
            if reset:
                self.data = self.data.copy()
                for key in list(self.data):
                    if key.startswith("allocated_"):
                        del self.data[key]
                self.data["allocation_context"] = context
            for row in self.plan_rows:
                initial = (
                    source.get("amount")
                    if category_id and source.get("amount")
                    else row.amount
                )
                name = f"allocated_{row.payment_type_id}"
                self.fields[name] = forms.DecimalField(
                    label=f"Allocated amount for {row.payment_type.name} (LKR)",
                    min_value=Decimal("0"),
                    max_digits=12,
                    decimal_places=2,
                    initial=initial,
                    widget=forms.NumberInput(
                        attrs={
                            "class": "field-input",
                            "min": "0",
                            "max": "9999999999.99",
                            "step": "0.01",
                            "inputmode": "decimal",
                            "data-allocation-amount": "",
                            "data-plan-amount": str(row.amount),
                        }
                    ),
                )
                if not self.manual_allocation:
                    self.fields[name].widget.attrs["readonly"] = True
                if reset:
                    self.data[name] = str(initial)
                if (
                    self.is_bound
                    and received == self.plan_total
                    and self.data.get("intent") != "post"
                ):
                    self.data = self.data.copy()
                    self.data[name] = str(row.amount)

    @property
    def plan_total(self):
        return sum((row.amount for row in self.plan_rows), Decimal("0.00"))

    @property
    def allocation_rows(self):
        rows = (
            self.preview["rows"]
            if self.preview
            else [
                {
                    "payment_type_id": row.payment_type_id,
                    "name": row.payment_type.name,
                    "configured": row.amount,
                }
                for row in self.plan_rows
            ]
        )
        return [(row, self[f"allocated_{row['payment_type_id']}"]) for row in rows]

    def clean(self):
        values = super().clean()
        if self.remaining_formset.is_bound:
            if not self.remaining_formset.is_valid():
                self.add_error(
                    None, "Review the remaining balance account allocations."
                )
            else:
                values["remaining_allocations"] = [
                    (
                        {
                            "child_deposit_id": row["child_deposit"].pk,
                            "child_deposit_type_id": row[
                                "child_deposit"
                            ].deposit_type_id,
                            "amount": row["amount"],
                        }
                        if row["payment_type"].startswith("child-type:")
                        else {
                            "payment_type_id": int(row["payment_type"]),
                            "amount": row["amount"],
                        }
                    )
                    for row in self.remaining_formset.cleaned_data
                    if row and not row.get("DELETE")
                ]
            total = self.remaining_formset.total_form_count()
            if total < 0:
                self.add_error(
                    None, "The remaining account row count cannot be negative."
                )
            if any(
                (match := re.fullmatch(r"remaining-(\d+)-.+", key))
                and int(match[1]) >= total
                for key in self.data
            ):
                self.add_error(
                    None, "An account allocation is outside the submitted row count."
                )
            if hasattr(self.data, "getlist") and any(
                key.startswith("remaining-") and len(self.data.getlist(key)) != 1
                for key in self.data
            ):
                self.add_error(None, "Submit each remaining account field only once.")
        if self.is_bound:
            names = {f"allocated_{row.payment_type_id}" for row in self.plan_rows}
            if any(
                key.startswith("allocated_") and key not in names for key in self.data
            ):
                self.add_error(
                    None,
                    "An allocated payment category is not in this member's selected plan.",
                )
            if hasattr(self.data, "getlist") and any(
                len(self.data.getlist(name)) > 1 for name in names
            ):
                self.add_error(
                    None, "Submit only one allocated amount for each payment category."
                )
            if self.data.get("intent") == "post" and not self.context_matches:
                self.add_error(
                    None,
                    "The member or allocation selection changed. Preview the allocation again before posting.",
                )
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
                allocation_input(values),
                remaining_payment_type=values.get("remaining_payment_type"),
                remaining_allocations=values.get("remaining_allocations"),
            )
            if not self.preview["is_balanced"] and self.context_matches:
                self.add_error(
                    None,
                    "The allocated total must equal the received contribution exactly. Allocate the remaining balance to accounts; allocations cannot exceed the received amount.",
                )
        return values
