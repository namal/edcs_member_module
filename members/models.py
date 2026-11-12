from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

phone_validator = RegexValidator(
    r"^\+?[\d\s()\-]{7,22}$", "Enter a valid phone number (7–15 digits)."
)
nic_validator = RegexValidator(
    r"^(?:\d{9}[VXvx]|\d{12})$",
    "Enter a valid NIC: 9 digits followed by V/X, or 12 digits.",
)


class District(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=12, unique=True)
    province = models.CharField("Province full name", max_length=100)
    province_short_name = models.CharField(
        "Province short name", max_length=20, blank=True
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def province_display(self):
        return (
            f"{self.province} ({self.province_short_name})"
            if self.province_short_name
            else self.province
        )

    def clean(self):
        super().clean()
        self.province_short_name = self.province_short_name.strip().upper()


class OfficeLocation(models.Model):
    district = models.ForeignKey(
        District, on_delete=models.PROTECT, related_name="offices"
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20, unique=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=22, blank=True, validators=[phone_validator])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "zonal office"
        verbose_name_plural = "zonal offices"
        ordering = ["district__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["district", "name"], name="unique_office_in_district"
            )
        ]

    def __str__(self):
        return f"{self.name} · {self.district.name}"

    def clean(self):
        super().clean()
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("district_id", flat=True)
                .first()
            )
            if previous != self.district_id and (
                self.members.exists()
                or self.subdivisions.exists()
                or self.schools.exists()
            ):
                raise ValidationError(
                    {
                        "district": "This zonal office has linked records. Its district cannot be changed."
                    }
                )


class Subdivision(models.Model):
    zonal_office = models.ForeignKey(
        OfficeLocation, on_delete=models.PROTECT, related_name="subdivisions"
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["zonal_office__district__name", "zonal_office__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["zonal_office", "name"], name="unique_subdivision_in_zone"
            )
        ]

    def __str__(self):
        return f"{self.name} · {self.zonal_office}"

    def clean(self):
        super().clean()
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("zonal_office_id", flat=True)
                .first()
            )
            if previous != self.zonal_office_id and (
                self.members.exists() or self.schools.exists()
            ):
                raise ValidationError(
                    {
                        "zonal_office": "This subdivision has linked records. Its zonal office cannot be changed."
                    }
                )


class School(models.Model):
    district = models.ForeignKey(
        District, on_delete=models.PROTECT, related_name="schools"
    )
    zonal_office = models.ForeignKey(
        OfficeLocation, on_delete=models.PROTECT, related_name="schools"
    )
    subdivision = models.ForeignKey(
        Subdivision,
        on_delete=models.PROTECT,
        related_name="schools",
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20, unique=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["district__name", "zonal_office__name", "name"]

    def __str__(self):
        parts = [
            self.name,
            self.district.province_display,
            self.district.name,
            self.zonal_office.name,
        ]
        if self.subdivision_id:
            parts.append(self.subdivision.name)
        return " · ".join(part for part in parts if part)

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.zonal_office_id
            and self.district_id
            and self.zonal_office.district_id != self.district_id
        ):
            errors["zonal_office"] = "Choose a zonal office in the selected district."
        if (
            self.subdivision_id
            and self.subdivision.zonal_office_id != self.zonal_office_id
        ):
            errors["subdivision"] = "Choose a subdivision in the selected zonal office."
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("district_id", "zonal_office_id", "subdivision_id")
                .first()
            )
            if (
                previous
                and self.members.exists()
                and any(previous[field] != getattr(self, field) for field in previous)
            ):
                errors["district"] = (
                    "This school has registered members. Its location cannot be changed."
                )
        if errors:
            raise ValidationError(errors)


class Lager(models.Model):
    district = models.ForeignKey(
        District, on_delete=models.PROTECT, related_name="lagers"
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["district__name", "name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["district", "name"], name="unique_lager_in_district"
            )
        ]

    def __str__(self):
        return f"{self.name} · {self.district.name}"

    def clean(self):
        super().clean()
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("district_id", flat=True)
                .first()
            )
            if previous != self.district_id and self.members.exists():
                raise ValidationError(
                    {
                        "district": "This lager has registered members. Its district cannot be changed."
                    }
                )


class Member(models.Model):
    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"
        UNSPECIFIED = "unspecified", "Prefer not to say"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    district = models.ForeignKey(
        District, on_delete=models.PROTECT, related_name="members"
    )
    lager = models.ForeignKey(
        Lager, on_delete=models.PROTECT, related_name="members", null=True
    )
    office_location = models.ForeignKey(
        OfficeLocation,
        verbose_name="Zonal office",
        on_delete=models.PROTECT,
        related_name="members",
    )
    subdivision = models.ForeignKey(
        Subdivision,
        on_delete=models.PROTECT,
        related_name="members",
        blank=True,
        null=True,
    )
    school = models.ForeignKey(
        School, on_delete=models.PROTECT, related_name="members", null=True
    )
    member_no = models.CharField("Member number", max_length=30, unique=True)
    computer_no = models.CharField(
        "Computer number", max_length=30, unique=True, blank=True, null=True
    )
    full_name = models.CharField(max_length=200)
    salary = models.DecimalField(
        "Monthly salary (LKR)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    office_address = models.TextField()
    private_address = models.TextField()
    phone_office = models.CharField(
        "Office phone", max_length=22, blank=True, validators=[phone_validator]
    )
    phone_mobile = models.CharField(
        "Mobile phone", max_length=22, validators=[phone_validator]
    )
    phone_home = models.CharField(
        "Home phone", max_length=22, blank=True, validators=[phone_validator]
    )
    admit_date = models.DateField("Admission date", default=timezone.localdate)
    gender = models.CharField(max_length=15, choices=Gender.choices)
    nic_no = models.CharField(
        "NIC number", max_length=12, unique=True, validators=[nic_validator]
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["district", "status"]),
            models.Index(fields=["full_name"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(salary__gte=0), name="member_salary_nonnegative"
            )
        ]

    def __str__(self):
        return f"{self.member_no} · {self.full_name}"

    @property
    def initials(self):
        return "".join(part[0] for part in self.full_name.split()[:2]).upper()

    def clean(self):
        super().clean()
        errors = {}
        self.nic_no = self.nic_no.strip().upper()
        self.member_no = self.member_no.strip().upper()
        self.computer_no = (
            self.computer_no.strip().upper() if self.computer_no else None
        )
        if self.office_location_id and self.district_id:
            if self.office_location.district_id != self.district_id:
                errors["office_location"] = (
                    "Choose a zonal office in the selected district."
                )
            if not self.office_location.is_active and (
                not self.pk
                or type(self).objects.get(pk=self.pk).office_location_id
                != self.office_location_id
            ):
                errors["office_location"] = "Choose an active zonal office."
        if (
            self.district_id
            and not self.district.is_active
            and (
                not self.pk
                or type(self).objects.get(pk=self.pk).district_id != self.district_id
            )
        ):
            errors["district"] = "Choose an active district."
        if self.lager_id:
            if self.lager.district_id != self.district_id:
                errors["lager"] = "Choose a lager in the selected district."
            if not self.lager.is_active and (
                not self.pk
                or type(self).objects.get(pk=self.pk).lager_id != self.lager_id
            ):
                errors["lager"] = "Choose an active lager."
        if self.subdivision_id:
            if self.subdivision.zonal_office_id != self.office_location_id:
                errors["subdivision"] = (
                    "Choose a subdivision in the selected zonal office."
                )
            if not self.subdivision.is_active and (
                not self.pk
                or type(self).objects.get(pk=self.pk).subdivision_id
                != self.subdivision_id
            ):
                errors["subdivision"] = "Choose an active subdivision."
        if self.school_id:
            if (
                self.school.district_id != self.district_id
                or self.school.zonal_office_id != self.office_location_id
                or (
                    self.subdivision_id
                    and self.school.subdivision_id != self.subdivision_id
                )
            ):
                errors["school"] = (
                    "Choose a school matching the selected district, zonal office and subdivision."
                )
            if not self.school.is_active and (
                not self.pk
                or type(self).objects.get(pk=self.pk).school_id != self.school_id
            ):
                errors["school"] = "Choose an active school."
        if not self.office_address.strip():
            errors["office_address"] = "Enter the office address."
        if self.admit_date and self.admit_date > timezone.localdate():
            errors["admit_date"] = "Admission date cannot be in the future."
        for field in ["phone_office", "phone_mobile", "phone_home"]:
            value = getattr(self, field, "")
            if value and not 7 <= sum(c.isdigit() for c in value) <= 15:
                errors[field] = "Enter a phone number with 7–15 digits."
        if errors:
            raise ValidationError(errors)
