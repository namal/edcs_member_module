from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from .models import District, Gender, Lager, Member, OfficeLocation, School, Subdivision
from .permissions import scope_queryset
from .officers import maintenance_officers, officer_label, validate_master_officer


def selected_id(form, name):
    value = (
        form.data.get(form.add_prefix(name))
        if form.is_bound
        else form.initial.get(name, getattr(form.instance, f"{name}_id", None))
    )
    return value if str(value or "").isdigit() else None


def active_choices(model, current_id=None):
    return model.objects.filter(Q(is_active=True) | Q(pk=current_id))


def school_choices(current_id=None, *, user=None):
    schools = School.objects.select_related(
        "district", "zonal_office", "subdivision"
    ).filter(
        Q(is_active=True, district__is_active=True, zonal_office__is_active=True)
        | Q(pk=current_id)
    )
    schools = schools.filter(
        Q(subdivision__isnull=True) | Q(subdivision__is_active=True) | Q(pk=current_id)
    ).order_by(Lower("name"), "pk")
    return scope_queryset(user, schools) if user is not None else schools


def location_fields(form, office_field):
    district_id = selected_id(form, "district")
    office_id = selected_id(form, office_field)
    form.fields["district"].queryset = active_choices(
        District, form.instance.district_id
    )
    offices = OfficeLocation.objects.select_related("district").filter(
        Q(is_active=True, district__is_active=True)
        | Q(pk=getattr(form.instance, f"{office_field}_id", None))
    )
    form.fields[office_field].queryset = (
        offices.filter(district_id=district_id) if district_id else offices.none()
    )
    form.fields[office_field].empty_label = "Select a zonal office"
    subdivisions = Subdivision.objects.select_related("zonal_office__district").filter(
        Q(
            is_active=True,
            zonal_office__is_active=True,
            zonal_office__district__is_active=True,
        )
        | Q(pk=form.instance.subdivision_id)
    )
    form.fields["subdivision"].queryset = (
        subdivisions.filter(
            zonal_office_id=office_id, zonal_office__district_id=district_id
        )
        if office_id
        else subdivisions.none()
    )
    form.fields["subdivision"].empty_label = "No subdivision"
    form.scope_fields()
    province = (
        form.fields["district"]
        .queryset.filter(pk=district_id)
        .values("province", "province_short_name")
        .first()
        if district_id
        else None
    )
    for name in ["province", "province_short_name"]:
        form.fields[name].initial = province[name] if province else ""
        form.fields[name].help_text = "Filled automatically from the selected branch."


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        for name, field in self.fields.items():
            if name == "district":
                field.label = "Branch"
            field.widget.attrs.update(
                {
                    "class": "field-input",
                    "placeholder": field.label or name.replace("_", " ").capitalize(),
                }
            )
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = 3
            if "phone" in name:
                field.widget.attrs.update({"type": "tel", "inputmode": "tel"})
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "field-check"
        self.scope_fields()

    def scope_fields(self):
        if self.user is not None:
            for field in self.fields.values():
                if isinstance(field, forms.ModelChoiceField):
                    field.queryset = scope_queryset(self.user, field.queryset)


class GenderForm(StyledModelForm):
    class Meta:
        model = Gender
        fields = ["code", "name", "sort_order", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["code"].disabled = True


class MemberForm(StyledModelForm):
    gender = forms.ChoiceField()
    maintenance_officer = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        label="Ledger Maintenance Officer",
    )
    officer_epf = forms.CharField(
        label="Ledger Maintenance Officer EPF number", disabled=True, required=False
    )
    short_name = forms.CharField(label="Short Name", required=False, disabled=True)
    retirement_age = forms.IntegerField(required=False, disabled=True, initial=60)
    expected_retirement_date = forms.DateField(
        required=False, disabled=True, widget=forms.DateInput(attrs={"type": "date"})
    )
    province = forms.CharField(
        label="Province full name", required=False, disabled=True
    )
    province_short_name = forms.CharField(
        label="Province short name", required=False, disabled=True
    )

    class Meta:
        model = Member
        fields = [
            "district",
            "province",
            "province_short_name",
            "lager",
            "office_location",
            "subdivision",
            "school",
            "member_no",
            "computer_no",
            "title",
            "full_name",
            "date_of_birth",
            "salary",
            "office_address",
            "private_address",
            "phone_office",
            "phone_mobile",
            "phone_home",
            "admit_date",
            "gender",
            "nic_no",
            "status",
            "pension_date",
        ]
        widgets = {
            "admit_date": forms.DateInput(attrs={"type": "date"}),
            "pension_date": forms.DateInput(attrs={"type": "date"}),
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "salary": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.user is not None:
            self.fields["status"].disabled = True
            if not self.instance.pk:
                self.instance.status = Member.Status.PENDING
                self.initial["status"] = Member.Status.PENDING
            self.fields["status"].help_text = (
                "Activation, inactivation and reactivation require a Membership Status request and workflow approval."
            )
        self.fields["gender"].choices = [
            ("", "Select a gender"),
            *Gender.objects.filter(
                Q(is_active=True) | Q(code=self.instance.gender)
            ).values_list("code", "name"),
        ]
        self.fields["salary"].help_text = (
            "Optional record only; not used to calculate contributions or transactions."
        )
        self.initial.update(
            short_name=self.instance.short_name,
            retirement_age=self.instance.retirement_age,
            expected_retirement_date=self.instance.expected_retirement_date,
        )
        if self.is_bound:
            from .member_identity import short_name, retirement_date

            self.initial["short_name"] = short_name(
                self.data.get(self.add_prefix("full_name"), ""),
                self.data.get(self.add_prefix("title"), ""),
            )
            try:
                birth = forms.DateField(required=False).clean(
                    self.data.get(self.add_prefix("date_of_birth"))
                )
            except forms.ValidationError:
                birth = None
            self.initial["expected_retirement_date"] = (
                retirement_date(birth)
                if birth and birth <= timezone.localdate()
                else None
            )
        self.fields["short_name"].help_text = (
            "Generated automatically from the title and full name."
        )
        self.fields["expected_retirement_date"].help_text = (
            "Calculated from date of birth at the standard retirement age of 60."
        )
        self.fields["pension_date"].label = "Recorded retirement / pension date"
        self.fields["pension_date"].help_text = (
            "Record an actual or scheduled pension date, including early retirement. This date controls the pension-quarter interest exclusion."
        )
        schools = school_choices(self.instance.school_id, user=self.user)
        school_id = selected_id(self, "school")
        school = schools.filter(pk=school_id).first() if school_id else None
        if school:
            location = {
                "district": school.district_id,
                "office_location": school.zonal_office_id,
            }
            if self.is_bound:
                self.data = self.data.copy()
                for name, value in location.items():
                    self.data[self.add_prefix(name)] = value
                if self.add_prefix("subdivision") not in self.data:
                    self.data[self.add_prefix("subdivision")] = (
                        school.subdivision_id or ""
                    )
            else:
                self.initial.update(location)
                if "subdivision" not in self.initial:
                    self.initial["subdivision"] = school.subdivision_id
        location_fields(self, "office_location")
        district_id = selected_id(self, "district")
        office_id = selected_id(self, "office_location")
        if self.instance.pk:
            self.fields.pop("maintenance_officer")
            self.fields.pop("officer_epf")
        else:
            self.fields["maintenance_officer"].required = True
            self.fields["officer_epf"].required = True
            self.fields["maintenance_officer"].queryset = maintenance_officers()
            self.fields["maintenance_officer"].label_from_instance = officer_label
            self.fields["maintenance_officer"].help_text = (
                "All active officers with a recorded EPF are listed. Selecting a lager assigns its officer automatically. Maintain this assignment in Master data → Lagers."
            )
        lagers = Lager.objects.select_related("zonal_office__district").filter(
            Q(
                is_active=True,
                zonal_office__is_active=True,
                zonal_office__district__is_active=True,
            )
            | Q(pk=self.instance.lager_id)
        )
        self.fields["lager"].queryset = (
            lagers.filter(
                zonal_office_id=office_id, zonal_office__district_id=district_id
            )
            if district_id and office_id
            else lagers.none()
        )
        self.fields["lager"].empty_label = "Select a lager"
        if not self.instance.pk:
            lager = (
                self.fields["lager"]
                .queryset.select_related("maintenance_officer__ledger_profile")
                .filter(pk=selected_id(self, "lager"))
                .first()
            )
            if lager and lager.maintenance_officer_id:
                self.initial["maintenance_officer"] = lager.maintenance_officer_id
                profile = getattr(lager.maintenance_officer, "ledger_profile", None)
                self.initial["officer_epf"] = profile.epf_number if profile else ""
                if self.is_bound:
                    self.data = self.data.copy()
                    self.data[self.add_prefix("maintenance_officer")] = str(
                        lager.maintenance_officer_id
                    )
        if district_id:
            schools = schools.filter(district_id=district_id)
        office_id = selected_id(self, "office_location")
        if office_id:
            schools = schools.filter(zonal_office_id=office_id)
        self.fields["school"].queryset = schools
        self.fields["school"].empty_label = "Select a department"
        self.fields["school"].help_text = (
            "Type a department name, location or code in the combo. Branch and zonal office narrow the list. "
            "Selecting a department fills its location automatically."
        )
        self.fields["school"].error_messages["required"] = "Select a department."
        self.fields["lager"].error_messages["required"] = "Select a lager."
        self.fields["district"].empty_label = "Select a branch"
        self.fields["full_name"].widget.attrs["autocomplete"] = "name"
        self.fields["phone_mobile"].widget.attrs["autocomplete"] = "tel"
        self.fields["phone_office"].help_text = (
            "Enter the member's office number, separate from the zonal office number."
        )
        self.fields["office_address"].help_text = (
            "Enter the member's office address manually."
        )
        self.fields["nic_no"].help_text = "12 digits, or 9 digits followed by V / X."
        self.fields["computer_no"].help_text = "Optional. Must be unique when provided."
        self.scope_fields()

    def clean_member_no(self):
        return self.cleaned_data["member_no"].strip().upper()

    def clean_computer_no(self):
        return (self.cleaned_data.get("computer_no") or "").strip().upper() or None

    def clean_nic_no(self):
        return self.cleaned_data["nic_no"].strip().upper()

    def clean(self):
        data = super().clean()
        if (
            self.user is not None
            and self.instance.pk
            and self.data.get("status", self.instance.status) != self.instance.status
        ):
            self.add_error(
                "status",
                "Use Membership Status to request a status change with a reason and approval.",
            )
        if not self.instance.pk and (lager := data.get("lager")):
            if not lager.maintenance_officer_id:
                self.add_error(
                    "lager",
                    "Configure this lager's Ledger Maintenance Officer and EPF in Master data before registering a member.",
                )
            else:
                try:
                    validate_master_officer(lager.maintenance_officer)
                except forms.ValidationError as error:
                    self.add_error("lager", " ".join(error.messages))
                else:
                    data["maintenance_officer"] = lager.maintenance_officer
        return data

    def save(self, commit=True):
        if not self.instance.pk:
            self.instance._maintenance_officer = self.cleaned_data.get(
                "maintenance_officer"
            )
        return super().save(commit=commit)


class DistrictForm(StyledModelForm):
    class Meta:
        model = District
        fields = ["name", "code", "province", "province_short_name", "is_active"]
        help_texts = {
            "province_short_name": "For example: WP for Western or NCP for North Central."
        }

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()

    def clean_province_short_name(self):
        return self.cleaned_data["province_short_name"].strip().upper()


class OfficeLocationForm(StyledModelForm):
    class Meta:
        model = OfficeLocation
        fields = ["district", "name", "code", "address", "phone", "is_active"]

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["district"].queryset = District.objects.filter(
            Q(is_active=True) | Q(pk=self.instance.district_id)
        )
        self.scope_fields()


class SubdivisionForm(StyledModelForm):
    district = forms.ModelChoiceField(queryset=District.objects.none())

    class Meta:
        model = Subdivision
        fields = ["district", "zonal_office", "name", "code", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["district"].queryset = active_choices(
            District,
            (
                self.instance.zonal_office.district_id
                if self.instance.zonal_office_id
                else None
            ),
        )
        if not self.is_bound and self.instance.zonal_office_id:
            self.initial["district"] = self.instance.zonal_office.district_id
        offices = OfficeLocation.objects.select_related("district").filter(
            Q(is_active=True, district__is_active=True)
            | Q(pk=self.instance.zonal_office_id)
        )
        district_id = selected_id(self, "district")
        self.fields["zonal_office"].queryset = (
            offices.filter(district_id=district_id) if district_id else offices.none()
        )
        self.fields["zonal_office"].empty_label = "Select a zonal office"
        self.scope_fields()

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class LagerForm(StyledModelForm):
    district = forms.ModelChoiceField(queryset=District.objects.none())
    officer_epf = forms.CharField(
        label="Ledger Maintenance Officer EPF number", required=True, disabled=True
    )

    class Meta:
        model = Lager
        fields = [
            "district",
            "zonal_office",
            "name",
            "code",
            "maintenance_officer",
            "officer_epf",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["district"].queryset = active_choices(
            District, self.instance.district_id
        )
        if not self.is_bound and self.instance.zonal_office_id:
            self.initial["district"] = self.instance.district_id
        district_id = selected_id(self, "district")
        offices = OfficeLocation.objects.select_related("district").filter(
            Q(is_active=True, district__is_active=True)
            | Q(pk=self.instance.zonal_office_id)
        )
        self.fields["zonal_office"].queryset = (
            offices.filter(district_id=district_id) if district_id else offices.none()
        )
        self.fields["zonal_office"].empty_label = "Select a zonal office"
        self.scope_fields()

        self.fields["maintenance_officer"].required = True
        self.fields["maintenance_officer"].queryset = maintenance_officers()
        self.fields["maintenance_officer"].label_from_instance = officer_label
        self.fields["maintenance_officer"].help_text = (
            "Choose any active officer with a recorded EPF. The EPF below is filled from the officer's user profile."
        )
        officer = (
            self.fields["maintenance_officer"]
            .queryset.filter(pk=selected_id(self, "maintenance_officer"))
            .first()
        )
        self.initial["officer_epf"] = (
            officer.ledger_profile.epf_number if officer else ""
        )

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class SchoolForm(StyledModelForm):
    province = forms.CharField(
        label="Province full name", required=False, disabled=True
    )
    province_short_name = forms.CharField(
        label="Province short name", required=False, disabled=True
    )

    class Meta:
        model = School
        fields = [
            "district",
            "province",
            "province_short_name",
            "zonal_office",
            "subdivision",
            "name",
            "location",
            "code",
            "address",
            "is_active",
        ]
        labels = {"name": "Department name"}
        help_texts = {
            "location": "Enter the department's town or location. It appears after the department name when selecting a department for a member."
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        location_fields(self, "zonal_office")
        self.scope_fields()

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()
