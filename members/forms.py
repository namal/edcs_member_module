from django import forms
from django.db.models import Q
from django.db.models.functions import Lower

from .models import District, Lager, Member, OfficeLocation, School, Subdivision


def selected_id(form, name):
    value = (
        form.data.get(form.add_prefix(name))
        if form.is_bound
        else form.initial.get(name, getattr(form.instance, f"{name}_id", None))
    )
    return value if str(value or "").isdigit() else None


def active_choices(model, current_id=None):
    return model.objects.filter(Q(is_active=True) | Q(pk=current_id))


def school_choices(current_id=None):
    schools = School.objects.select_related(
        "district", "zonal_office", "subdivision"
    ).filter(
        Q(is_active=True, district__is_active=True, zonal_office__is_active=True)
        | Q(pk=current_id)
    )
    return schools.filter(
        Q(subdivision__isnull=True) | Q(subdivision__is_active=True) | Q(pk=current_id)
    ).order_by(Lower("name"), "pk")


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
    province = (
        District.objects.filter(pk=district_id)
        .values("province", "province_short_name")
        .first()
        if district_id
        else None
    )
    for name in ["province", "province_short_name"]:
        form.fields[name].initial = province[name] if province else ""
        form.fields[name].help_text = "Filled automatically from the selected district."


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
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


class MemberForm(StyledModelForm):
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
            "full_name",
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
        ]
        widgets = {
            "admit_date": forms.DateInput(attrs={"type": "date"}),
            "salary": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        schools = school_choices(self.instance.school_id)
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
        lagers = Lager.objects.select_related("district").filter(
            Q(is_active=True, district__is_active=True) | Q(pk=self.instance.lager_id)
        )
        self.fields["lager"].queryset = (
            lagers.filter(district_id=district_id) if district_id else lagers.none()
        )
        self.fields["lager"].empty_label = "Select a lager"
        if district_id:
            schools = schools.filter(district_id=district_id)
        office_id = selected_id(self, "office_location")
        if office_id:
            schools = schools.filter(zonal_office_id=office_id)
        self.fields["school"].queryset = schools
        self.fields["school"].empty_label = "Select a school"
        self.fields["school"].help_text = (
            "Search by school name or code. District and zonal office narrow the list. "
            "Selecting a school fills its location automatically."
        )
        self.fields["school"].error_messages["required"] = "Select a school."
        self.fields["lager"].error_messages["required"] = "Select a lager."
        self.fields["district"].empty_label = "Select a district"
        self.fields["full_name"].widget.attrs["autocomplete"] = "name"
        self.fields["phone_mobile"].widget.attrs["autocomplete"] = "tel"
        self.fields["nic_no"].help_text = "12 digits, or 9 digits followed by V / X."
        self.fields["computer_no"].help_text = "Optional. Must be unique when provided."

    def clean_member_no(self):
        return self.cleaned_data["member_no"].strip().upper()

    def clean_computer_no(self):
        return (self.cleaned_data.get("computer_no") or "").strip().upper() or None

    def clean_nic_no(self):
        return self.cleaned_data["nic_no"].strip().upper()


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

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class LagerForm(StyledModelForm):
    class Meta:
        model = Lager
        fields = ["district", "name", "code", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["district"].queryset = active_choices(
            District, self.instance.district_id
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
            "code",
            "address",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        location_fields(self, "zonal_office")

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()
