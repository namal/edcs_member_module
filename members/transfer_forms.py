from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db.models import OuterRef, Subquery

from . import models as m
from .forms import selected_id, school_choices
from .permissions import district_records, district_only_queryset
from .transfer_services import eligible_officers


class OfficerChoice(forms.ModelChoiceField):
    def label_from_instance(self, user):
        return f"{user.get_full_name() or user.username} · EPF {user.ledger_profile.epf_number}"


class TransferForm(forms.Form):
    member = forms.ModelChoiceField(queryset=m.Member.objects.none())
    source_ledger = forms.IntegerField(widget=forms.HiddenInput())
    district = forms.ModelChoiceField(
        queryset=m.District.objects.none(), label="Receiving district"
    )
    zonal_office = forms.ModelChoiceField(
        queryset=m.OfficeLocation.objects.none(), label="Receiving zonal office"
    )
    subdivision = forms.ModelChoiceField(
        queryset=m.Subdivision.objects.none(), required=False
    )
    school = forms.ModelChoiceField(
        queryset=m.School.objects.none(), label="Receiving department / school"
    )
    lager = forms.ModelChoiceField(
        queryset=m.Lager.objects.none(), label="Receiving lager"
    )
    maintenance_officer = OfficerChoice(
        queryset=get_user_model().objects.none(),
        label="Receiving Ledger Maintenance Officer",
    )
    effective_date = forms.DateField(
        initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"})
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), label="Reason for transfer"
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.ledger_receiving_locations = True
        self.instance = m.Member()
        self.fields["member"].queryset = district_records(user, m.Member).order_by(
            "member_no", "pk"
        )
        self.fields["member"].widget.attrs["data-searchable-select"] = ""
        member_id = selected_id(self, "member")
        self.selected_member = (
            self.fields["member"].queryset.filter(pk=member_id).first()
            if member_id
            else None
        )
        self.current_ledger = (
            self.selected_member.ledgers.filter(status="active").first()
            if self.selected_member
            else None
        )
        if self.current_ledger:
            self.initial.setdefault("source_ledger", self.current_ledger.pk)
            self.initial.setdefault("district", self.current_ledger.district_id)
        self.fields["district"].queryset = district_records(user, m.District).filter(
            is_active=True
        )
        district = selected_id(self, "district")
        zone = selected_id(self, "zonal_office")
        self.fields["zonal_office"].queryset = (
            district_records(user, m.OfficeLocation).filter(
                district_id=district, is_active=True, district__is_active=True
            )
            if district
            else m.OfficeLocation.objects.none()
        )
        self.fields["lager"].queryset = (
            district_only_queryset(user, m.Lager.objects.all()).filter(
                zonal_office_id=zone, zonal_office__district_id=district, is_active=True
            )
            if zone and district
            else m.Lager.objects.none()
        )
        self.fields["school"].queryset = (
            school_choices(user=user).filter(zonal_office_id=zone, district_id=district)
            if zone and district
            else m.School.objects.none()
        )
        self.fields["subdivision"].queryset = (
            district_records(user, m.Subdivision).filter(
                zonal_office_id=zone, is_active=True
            )
            if zone
            else m.Subdivision.objects.none()
        )
        self.fields["maintenance_officer"].queryset = (
            eligible_officers(zone)
            if self.fields["zonal_office"].queryset.filter(pk=zone).exists()
            else get_user_model().objects.none()
        )
        self.fields["effective_date"].widget.attrs[
            "max"
        ] = timezone.localdate().isoformat()
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "field-input")

    def clean(self):
        data = super().clean()
        school, subdivision = data.get("school"), data.get("subdivision")
        if school and subdivision and school.subdivision_id != subdivision.pk:
            self.add_error(
                "subdivision", "Select a subdivision matching the receiving department."
            )
        if self.current_ledger and data.get("source_ledger") != self.current_ledger.pk:
            self.add_error(
                None, "The current ledger changed. Reload and review the transfer form."
            )
        return data


class OfficerAssignmentForm(forms.Form):
    maintenance_officer = OfficerChoice(
        queryset=get_user_model().objects.none(), label="Ledger Maintenance Officer"
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, ledger, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["maintenance_officer"].queryset = eligible_officers(
            ledger.zonal_office
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "field-input"


class RetainedMemberChoice(forms.ModelChoiceField):
    def label_from_instance(self, member):
        return f"{member.retained_number} · {member.retained_name}"


class LedgerFilterForm(forms.Form):
    member = RetainedMemberChoice(queryset=m.Member.objects.none(), required=False)
    district = forms.ModelChoiceField(
        queryset=m.District.objects.none(), required=False
    )
    status = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All ledger pages"),
            ("active", "Active"),
            ("transferred", "Transferred / historical"),
            ("pending", "Officer assignment required"),
        ],
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["district"].queryset = district_records(user, m.District)
        visible = district_records(user, m.MemberLedger)
        latest = visible.filter(member_id=OuterRef("pk")).order_by("-created_at", "-pk")
        self.fields["member"].queryset = (
            m.Member.objects.filter(pk__in=visible.values("member_id"))
            .annotate(
                retained_name=Subquery(
                    latest.values("registration_snapshot__full_name")[:1]
                ),
                retained_number=Subquery(
                    latest.values("registration_snapshot__member_no")[:1]
                ),
            )
            .order_by("member_no", "pk")
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "field-input"
        self.fields["member"].widget.attrs["data-searchable-select"] = ""


class LedgerEventFilterForm(forms.Form):
    date_from = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    include_previous = forms.BooleanField(
        required=False, label="Include previous linked ledgers"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "field-check"
                if isinstance(field.widget, forms.CheckboxInput)
                else "field-input"
            )

    def clean(self):
        data = super().clean()
        if (
            data.get("date_from")
            and data.get("date_to")
            and data["date_from"] > data["date_to"]
        ):
            self.add_error("date_to", "To date must be on or after From date.")
        return data
