from django import forms

from .deposit_interest import current_financial_year
from .models import Child, ChildDeposit, ChildDepositType, Member, PaymentType
from .permissions import district_records


class InterestYearForm(forms.Form):
    account_group = forms.ChoiceField(
        choices=[
            ("all", "Member payments and child deposits"),
            ("members", "Member Payment Types"),
            ("children", "Child Deposits"),
        ],
        required=False,
        initial="all",
        label="Accounts",
    )
    year = forms.IntegerField(
        label="Financial year starting in", min_value=1900, max_value=9998
    )
    member = forms.ModelChoiceField(
        queryset=Member.objects.none(), required=False, empty_label="All members"
    )
    payment_type = forms.ModelChoiceField(
        queryset=PaymentType.objects.all(),
        required=False,
        empty_label="All payment types",
        label="Member Payment Type",
    )
    child = forms.ModelChoiceField(
        queryset=Child.objects.none(), required=False, empty_label="All children"
    )
    child_deposit_type = forms.ModelChoiceField(
        queryset=ChildDepositType.objects.all(),
        required=False,
        empty_label="All child deposit types",
    )
    deposit = forms.ModelChoiceField(
        queryset=ChildDeposit.objects.none(),
        required=False,
        empty_label="All child accounts",
        label="Child deposit reference",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = district_records(user, Member).order_by(
            "member_no", "pk"
        )
        self.fields["child"].queryset = district_records(user, Child).select_related(
            "member"
        )
        self.fields["deposit"].queryset = district_records(
            user, ChildDeposit
        ).select_related("child", "deposit_type")
        self.initial["year"] = current_financial_year()
        for field in self.fields.values():
            field.widget.attrs["class"] = "field-input"
        for name in (
            "member",
            "payment_type",
            "child",
            "child_deposit_type",
            "deposit",
        ):
            self.fields[name].widget.attrs["data-searchable-select"] = ""

    def clean_account_group(self):
        return self.cleaned_data["account_group"] or "all"

    def clean(self):
        data = super().clean()
        group = data.get("account_group")
        member, child, deposit = (
            data.get(name) for name in ("member", "child", "deposit")
        )
        child_filters = any(
            data.get(name) for name in ("child", "child_deposit_type", "deposit")
        )
        if group == "members" and child_filters:
            raise forms.ValidationError(
                "Choose Child Deposits or both account groups to use child filters."
            )
        if (group == "children" or child_filters) and data.get("payment_type"):
            raise forms.ValidationError(
                "Use the child deposit type filter for child accounts; clear Member Payment Type."
            )
        if member and child and child.member_id != member.pk:
            self.add_error("child", "Choose a child of the selected member.")
        if deposit and (
            (member and deposit.child.member_id != member.pk)
            or (child and deposit.child_id != child.pk)
            or (
                data.get("child_deposit_type")
                and deposit.deposit_type_id != data["child_deposit_type"].pk
            )
        ):
            self.add_error(
                "deposit",
                "Choose a child account matching the selected member, child and deposit type.",
            )
        return data
