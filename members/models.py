from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from .ledger_tracking import LedgerTrackedModel
from .member_audit import AuditQuerySet, MemberQuerySet
from .posting_policies import PostingQuerySet

phone_validator = RegexValidator(
    r"^\+?[\d\s()\-]{7,22}$", "Enter a valid phone number (7–15 digits)."
)
nic_validator = RegexValidator(
    r"^(?:\d{9}[VXvx]|\d{12})$",
    "Enter a valid NIC: 9 digits followed by V/X, or 12 digits.",
)


def child_deposit_reference():
    return f"CD-{uuid4().hex[:12].upper()}"


def fix_loan_reference():
    return f"FDL-{uuid4().hex[:12].upper()}"


class District(models.Model):
    authorized_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="assigned_districts"
    )
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
        verbose_name = "Branch"
        verbose_name_plural = "Branches"

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
        District,
        verbose_name="Branch",
        on_delete=models.PROTECT,
        related_name="offices",
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
                or self.lagers.exists()
                or self.member_ledgers.exists()
            ):
                raise ValidationError(
                    {
                        "district": "This zonal office has linked records. Its branch cannot be changed."
                    }
                )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


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
                self.members.exists()
                or self.schools.exists()
                or self.ledger_pages.exists()
            ):
                raise ValidationError(
                    {
                        "zonal_office": "This subdivision has linked records. Its zonal office cannot be changed."
                    }
                )


class School(models.Model):
    district = models.ForeignKey(
        District,
        verbose_name="Branch",
        on_delete=models.PROTECT,
        related_name="schools",
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
    location = models.CharField("Department location", max_length=200)
    code = models.CharField(max_length=20, unique=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "department"
        verbose_name_plural = "departments"
        ordering = ["district__name", "zonal_office__name", "name"]

    def __str__(self):
        parts = [
            self.name,
            self.location,
            self.zonal_office.name,
            self.district.name,
            self.district.province_display,
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
            errors["zonal_office"] = "Choose a zonal office in the selected branch."
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
                and (self.members.exists() or self.ledger_pages.exists())
                and any(previous[field] != getattr(self, field) for field in previous)
            ):
                errors["district"] = (
                    "This department has registered members. Its location cannot be changed."
                )
        if errors:
            raise ValidationError(errors)


class Lager(models.Model):
    maintenance_officer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assigned_lagers",
        verbose_name="Ledger Maintenance Officer",
    )
    zonal_office = models.ForeignKey(
        OfficeLocation, on_delete=models.PROTECT, related_name="lagers"
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["zonal_office__district__name", "zonal_office__name", "name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["zonal_office", "name"], name="unique_lager_in_zone"
            )
        ]

    def __str__(self):
        return f"{self.name} · {self.zonal_office}"

    @property
    def district(self):
        return self.zonal_office.district if self.zonal_office_id else None

    @property
    def district_id(self):
        return self.zonal_office.district_id if self.zonal_office_id else None

    def clean(self):
        super().clean()
        if self.maintenance_officer_id:
            from .officers import validate_master_officer

            validate_master_officer(self.maintenance_officer)
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("zonal_office_id", flat=True)
                .first()
            )
            if previous != self.zonal_office_id and (
                self.members.exists() or self.ledger_pages.exists()
            ):
                raise ValidationError(
                    {
                        "zonal_office": "This lager has registered members. Its zonal office cannot be changed."
                    }
                )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                type(self).objects.select_for_update().filter(pk=self.pk).first()
                from .ledger_tracking import ledger_actor
                from .permissions import require_lager_access

                if ledger_actor.get() is not None:
                    require_lager_access(ledger_actor.get(), self.pk)
            self.clean()
            return super().save(*args, **kwargs)


class Gender(models.Model):
    code = models.SlugField(max_length=15, unique=True)
    name = models.CharField(max_length=100, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name", "pk"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        self.code = self.code.strip().lower()
        if (
            self.pk
            and type(self).objects.filter(pk=self.pk).exclude(code=self.code).exists()
        ):
            raise ValidationError(
                {"code": "Gender codes cannot be changed. Edit the name instead."}
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class MemberAuditEvent(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Added"
        UPDATED = "updated", "Edited"
        DELETED = "deleted", "Deleted"

    # Keep the member's identity even when its source record is deleted.
    member_pk = models.PositiveBigIntegerField(db_index=True)
    member_no = models.CharField(max_length=30)
    member_name = models.CharField(max_length=200)
    district = models.ForeignKey(
        District, verbose_name="Branch", on_delete=models.PROTECT
    )
    lager = models.ForeignKey(Lager, on_delete=models.PROTECT, null=True, blank=True)
    action = models.CharField(max_length=10, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True
    )
    actor_name = models.CharField(max_length=300, blank=True)
    actor_epf = models.CharField(max_length=30, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    changes = models.JSONField(default=list)
    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_at", "-pk"]
        default_permissions = ("view",)

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Member audit history cannot be changed.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Member audit history must be retained.", [self])


class Member(models.Model):
    objects = MemberQuerySet.as_manager()

    class Title(models.TextChoices):
        MR = "Mr.", "Mr."
        MISS = "Miss", "Miss"
        MRS = "Mrs.", "Mrs."
        MS = "Ms.", "Ms."
        REV = "Rev.", "Rev."

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"
        UNSPECIFIED = "unspecified", "Prefer not to say"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        RETIRED = "retired", "Retired"
        PENDING = "pending", "Awaiting activation"

    district = models.ForeignKey(
        District,
        verbose_name="Branch",
        on_delete=models.PROTECT,
        related_name="members",
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
        School,
        verbose_name="Department",
        on_delete=models.PROTECT,
        related_name="members",
        null=True,
    )
    member_no = models.CharField("Member number", max_length=30, unique=True)
    computer_no = models.CharField(
        "Computer number", max_length=30, unique=True, null=True
    )
    title = models.CharField("Title / Salutation", max_length=5, choices=Title.choices)
    full_name = models.CharField(max_length=200)
    short_name = models.CharField(
        "Short Name", max_length=600, blank=True, editable=False
    )
    date_of_birth = models.DateField("Date of birth", null=True)
    retirement_age = models.PositiveSmallIntegerField(default=60, editable=False)
    expected_retirement_date = models.DateField(null=True, blank=True, editable=False)
    salary = models.DecimalField(
        "Monthly salary (LKR)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
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
    gender = models.CharField(max_length=15)
    nic_no = models.CharField(
        "NIC number", max_length=12, unique=True, validators=[nic_validator]
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    pension_date = models.DateField(
        "Pension date",
        blank=True,
        null=True,
        help_text="The quarter containing this date earns no deposit interest. Other completed quarters are calculated normally.",
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

    def get_gender_display(self):
        return (
            Gender.objects.filter(code=self.gender)
            .values_list("name", flat=True)
            .first()
            or self.gender
        )

    @property
    def initials(self):
        return "".join(part[0] for part in self.full_name.split()[:2]).upper()

    def update_generated_fields(self):
        from .member_identity import retirement_date, short_name

        self.short_name = short_name(self.full_name, self.title)
        birth_field = self._meta.get_field("date_of_birth")
        self.date_of_birth = birth_field.to_python(self.date_of_birth)
        self.expected_retirement_date = retirement_date(
            self.date_of_birth, self.retirement_age
        )

    def validate_retirement(self):
        errors = {}
        if self.date_of_birth and self.date_of_birth > timezone.localdate():
            errors["date_of_birth"] = "Date of birth cannot be in the future."
        pension_date = self._meta.get_field("pension_date").to_python(self.pension_date)
        if self.status == self.Status.RETIRED and not pension_date:
            errors["pension_date"] = "Record the retirement date for a retired member."
        if self.date_of_birth and pension_date and pension_date <= self.date_of_birth:
            errors["pension_date"] = "Retirement must be after the date of birth."
        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        from .permissions import guard_member_actor

        guard_member_actor(self)
        self.update_generated_fields()
        self.validate_retirement()
        from .transfer_services import guard_member_location

        guard_member_location(self)
        from .deposit_interest import guard_member_pension

        guard_member_pension(self)
        errors = {}
        if self.gender:
            previous_gender = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("gender", flat=True)
                .first()
                if self.pk
                else None
            )
            genders = Gender.objects.filter(code=self.gender)
            if self.gender != previous_gender:
                genders = genders.filter(is_active=True)
            if not genders.exists():
                errors["gender"] = "Choose an active gender from master data."
        self.nic_no = self.nic_no.strip().upper()
        self.member_no = self.member_no.strip().upper()
        self.computer_no = (
            self.computer_no.strip().upper() if self.computer_no else None
        )
        if self.office_location_id and self.district_id:
            if self.office_location.district_id != self.district_id:
                errors["office_location"] = (
                    "Choose a zonal office in the selected branch."
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
            errors["district"] = "Choose an active branch."
        if self.lager_id:
            if self.lager.district_id != self.district_id:
                errors["lager"] = "Choose a lager in the selected branch."
            elif self.lager.zonal_office_id != self.office_location_id:
                errors["lager"] = "Choose a lager in the selected zonal office."
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
                    "Choose a department matching the selected branch, zonal office and subdivision."
                )
            if not self.school.is_active and (
                not self.pk
                or type(self).objects.get(pk=self.pk).school_id != self.school_id
            ):
                errors["school"] = "Choose an active department."
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

    def save(self, *args, **kwargs):
        from .deposit_interest import guard_member_pension
        from .transfer_services import guard_member_location, initialize_member_ledger

        self.update_generated_fields()
        self.validate_retirement()
        if kwargs.get("update_fields") is not None:
            fields = set(kwargs["update_fields"])
            if fields.intersection({"full_name", "title"}):
                fields.add("short_name")
            if fields.intersection({"date_of_birth", "retirement_age"}):
                fields.add("expected_retirement_date")
            kwargs["update_fields"] = fields
        with transaction.atomic():
            from .member_audit import record_member_change, snapshot

            before = {}
            if self.pk:
                previous = (
                    type(self).objects.select_for_update().filter(pk=self.pk).first()
                )
                if previous:
                    before = snapshot(previous)
            from .permissions import guard_member_actor

            guard_member_actor(self, locking=True)
            created = self.pk is None
            from .status_services import guard_status_change

            guard_status_change(self)
            guard_member_location(self)
            guard_member_pension(self)
            if self.lager_id and (
                self.lager.district_id != self.district_id
                or self.lager.zonal_office_id != self.office_location_id
            ):
                raise ValidationError(
                    {"lager": "Choose a lager in the member's branch and zonal office."}
                )
            result = super().save(*args, **kwargs)
            if created:
                from .ledger_tracking import ledger_actor

                initialize_member_ledger(
                    self,
                    officer=getattr(self, "_maintenance_officer", None),
                    actor=ledger_actor.get(),
                )
            record_member_change(
                type(self).objects.get(pk=self.pk),
                before,
                "created" if created else "updated",
            )
            return result


class PaymentType(models.Model):
    class InterestFrequency(models.TextChoices):
        DAILY = "daily", "Daily (every day)"
        MONTHLY = "monthly", "Monthly (every month)"
        QUARTERLY = "quarterly", "Quarterly (every 3 months)"
        YEARLY = "yearly", "Yearly (every year)"
        CUSTOM = "custom", "Custom period"

    class InterestPeriodType(models.TextChoices):
        REPEATING = "repeating", "Repeating interval"
        DATE_RANGE = "date_range", "Specific start and end dates"

    class InterestIntervalUnit(models.TextChoices):
        DAYS = "days", "Days"
        MONTHS = "months", "Months"
        YEARS = "years", "Years"

    name = models.CharField(max_length=120, unique=True)
    interest_rate = models.DecimalField(
        "Interest rate (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Configured percentage for this payment type. Interest is not automatically calculated or posted.",
    )
    interest_frequency = models.CharField(
        "Interest calculation frequency",
        max_length=12,
        choices=InterestFrequency.choices,
        default=InterestFrequency.QUARTERLY,
        help_text="Quarterly means every 3 months and is the default. The bank can choose another frequency or a custom period. Stored for reference; no automatic calculation or posting.",
    )
    interest_period_type = models.CharField(
        "Custom period type",
        max_length=12,
        choices=InterestPeriodType.choices,
        blank=True,
        default="",
        help_text="For Custom period, choose a repeating interval or specific dates.",
    )
    interest_interval_count = models.PositiveSmallIntegerField(
        "Repeat every",
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="For a repeating custom period, enter a whole number of 1 or more.",
    )
    interest_interval_unit = models.CharField(
        "Interval unit",
        max_length=6,
        choices=InterestIntervalUnit.choices,
        blank=True,
        default="",
        help_text="Days, months or years for the repeating interval.",
    )
    interest_start_date = models.DateField(
        "Interest period start date",
        null=True,
        blank=True,
    )
    interest_end_date = models.DateField(
        "Interest period end date",
        null=True,
        blank=True,
        help_text="For a specific period, choose an end date on or after its start date.",
    )
    sort_order = models.PositiveIntegerField("Display order", default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name", "pk"]
        verbose_name = "payment type"
        verbose_name_plural = "payment types"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(interest_rate__gte=0, interest_rate__lte=100),
                name="payment_type_interest_rate_range",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    interest_frequency__in=[
                        "daily",
                        "monthly",
                        "quarterly",
                        "yearly",
                        "custom",
                    ]
                ),
                name="payment_type_interest_frequency_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        interest_frequency__in=[
                            "daily",
                            "monthly",
                            "quarterly",
                            "yearly",
                        ],
                        interest_period_type="",
                        interest_interval_count__isnull=True,
                        interest_interval_unit="",
                        interest_start_date__isnull=True,
                        interest_end_date__isnull=True,
                    )
                    | models.Q(
                        interest_frequency="custom",
                        interest_period_type="repeating",
                        interest_interval_count__isnull=False,
                        interest_interval_count__gte=1,
                        interest_interval_unit__in=["days", "months", "years"],
                        interest_start_date__isnull=True,
                        interest_end_date__isnull=True,
                    )
                    | models.Q(
                        interest_frequency="custom",
                        interest_period_type="date_range",
                        interest_interval_count__isnull=True,
                        interest_interval_unit="",
                        interest_start_date__isnull=False,
                        interest_end_date__isnull=False,
                        interest_end_date__gte=models.F("interest_start_date"),
                    )
                ),
                name="payment_type_interest_period_valid",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def interest_period_display(self):
        if self.interest_frequency != self.InterestFrequency.CUSTOM:
            return self.get_interest_frequency_display()
        if (
            self.interest_period_type == self.InterestPeriodType.REPEATING
            and self.interest_interval_count
            and self.interest_interval_unit
        ):
            unit = self.interest_interval_unit
            if self.interest_interval_count == 1:
                unit = unit.removesuffix("s")
            return f"Custom: every {self.interest_interval_count} {unit}"
        if (
            self.interest_period_type == self.InterestPeriodType.DATE_RANGE
            and self.interest_start_date
            and self.interest_end_date
        ):
            return f"Custom: {self.interest_start_date.isoformat()} to {self.interest_end_date.isoformat()}"
        return "Custom period"

    def clean(self):
        super().clean()
        if self.interest_frequency != self.InterestFrequency.CUSTOM:
            self.interest_period_type = ""
            self.interest_interval_count = None
            self.interest_interval_unit = ""
            self.interest_start_date = self.interest_end_date = None
            return
        errors = {}
        if self.interest_period_type == self.InterestPeriodType.REPEATING:
            self.interest_start_date = self.interest_end_date = None
            if not self.interest_interval_count or self.interest_interval_count < 1:
                errors["interest_interval_count"] = "Enter a whole number of 1 or more."
            if not self.interest_interval_unit:
                errors["interest_interval_unit"] = "Choose days, months or years."
        elif self.interest_period_type == self.InterestPeriodType.DATE_RANGE:
            self.interest_interval_count = None
            self.interest_interval_unit = ""
            if not self.interest_start_date:
                errors["interest_start_date"] = "Choose the interest period start date."
            if not self.interest_end_date:
                errors["interest_end_date"] = "Choose the interest period end date."
            elif (
                self.interest_start_date
                and self.interest_end_date < self.interest_start_date
            ):
                errors["interest_end_date"] = (
                    "The end date must be on or after the start date."
                )
        else:
            errors["interest_period_type"] = (
                "Choose a repeating interval or specific dates."
            )
        if errors:
            raise ValidationError(errors)


class PaymentTransaction(LedgerTrackedModel):
    objects = PostingQuerySet.as_manager()
    reverses = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correction",
        verbose_name="Corrects entry",
    )
    member = models.ForeignKey(
        Member, on_delete=models.PROTECT, related_name="payment_transactions"
    )
    payment_type = models.ForeignKey(
        PaymentType, on_delete=models.PROTECT, related_name="transactions"
    )
    transaction_date = models.DateField("Transaction date", default=timezone.localdate)
    debit = models.DecimalField(
        "Debit (LKR)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    credit = models.DecimalField(
        "Credit (LKR)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    reference = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_payment_transactions",
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "debit / credit entry"
        default_permissions = ("view", "add", "change")
        verbose_name_plural = "debit / credit entries"
        ordering = ["transaction_date", "pk"]
        indexes = [models.Index(fields=["member", "payment_type", "transaction_date"])]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(debit__gt=0, credit=0) | models.Q(credit__gt=0, debit=0)
                ),
                name="payment_transaction_one_side_positive",
            ),
        ]

    def __str__(self):
        return f"{self.member} · {self.payment_type} · {self.transaction_date}"

    def clean(self):
        super().clean()
        from .posting_policies import validate_correction

        validate_correction(self)
        if (
            self.debit is not None
            and self.credit is not None
            and not (
                (self.debit > 0 and self.credit == 0)
                or (self.credit > 0 and self.debit == 0)
            )
        ):
            raise ValidationError(
                "Enter a positive debit or credit, with the other amount set to zero."
            )
        from .fix_deposit_services import guard_transaction

        guard_transaction(self)
        from .deposit_interest import guard_interest_transaction

        guard_interest_transaction(self)

    def save(self, *args, **kwargs):
        from .fix_deposit_services import guard_transaction
        from .posting_policies import guard_posting_edit, validate_correction

        with transaction.atomic():
            if self.member_id:
                Member.objects.select_for_update().filter(pk=self.member_id).first()
            guard_posting_edit(self)
            validate_correction(self)
            from .contribution_services import guard_receipt_transaction

            guard_receipt_transaction(self)
            guard_transaction(self)
            from .deposit_interest import guard_interest_transaction

            guard_interest_transaction(self)
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .fix_deposit_services import guard_transaction

        with transaction.atomic():
            Member.objects.select_for_update().filter(pk=self.member_id).first()
            try:
                from .contribution_services import guard_receipt_transaction

                guard_receipt_transaction(self, deleting=True)
                guard_transaction(self, deleting=True)
                from .deposit_interest import guard_interest_transaction

                guard_interest_transaction(self, deleting=True)
            except ValidationError as error:
                raise ProtectedError(" ".join(error.messages), [self])
            return super().delete(*args, **kwargs)


class PaymentPlanTemplate(models.Model):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]
        verbose_name = "payment plan template"

    def __str__(self):
        return self.name

    @property
    def monthly_total(self):
        if not self.pk:
            return Decimal("0.00")
        return sum((item.amount for item in self.amounts.all()), Decimal("0.00"))


class FixedContributionMixin:
    """Keep saved fixed amounts immutable in templates and member plans."""

    def validate_fixed_amount(self):
        previous = (
            type(self).objects.select_related("payment_type").filter(pk=self.pk).first()
            if self.pk
            else None
        )
        if (
            previous
            and previous.is_fixed
            and (
                (Decimal(self.amount) if self.amount is not None else None)
                != previous.amount
                or self.payment_type_id != previous.payment_type_id
                or getattr(self, self._fixed_owner_field)
                != getattr(previous, self._fixed_owner_field)
                or not self.is_fixed
            )
        ):
            raise ValidationError("This saved fixed monthly amount cannot be changed.")

    def clean(self):
        super().clean()
        self.validate_fixed_amount()

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                type(self).objects.select_for_update().filter(pk=self.pk).first()
            self.validate_fixed_amount()
            return super().save(*args, **kwargs)


class PaymentPlanTemplateAmount(FixedContributionMixin, models.Model):
    _fixed_owner_field = "template_id"
    template = models.ForeignKey(
        PaymentPlanTemplate, on_delete=models.CASCADE, related_name="amounts"
    )
    payment_type = models.ForeignKey(
        PaymentType, on_delete=models.PROTECT, related_name="template_amounts"
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    is_fixed = models.BooleanField(
        "Fixed amount",
        default=False,
        help_text="Lock this monthly amount when copied to a member plan.",
    )

    class Meta:
        ordering = ["payment_type__sort_order", "payment_type__name", "pk"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["template", "payment_type"], name="unique_template_payment_type"
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="template_amount_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(is_fixed=False) | models.Q(amount__gt=0),
                name="template_fixed_amount_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.is_fixed and self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "Enter a positive fixed monthly amount."})


class PaymentPlan(LedgerTrackedModel):
    member = models.OneToOneField(
        Member, on_delete=models.PROTECT, related_name="payment_plan"
    )
    agreement_date = models.DateField(default=timezone.localdate)
    source_template = models.ForeignKey(
        PaymentPlanTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_plans",
    )
    source_template_name = models.CharField(max_length=150, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "payment plan"
        verbose_name_plural = "payment plans"
        ordering = ["member__member_no"]

    def __str__(self):
        return f"Payment plan · {self.member}"

    @property
    def monthly_total(self):
        if not self.pk:
            return Decimal("0.00")
        return sum((item.amount for item in self.amounts.all()), Decimal("0.00"))


class PaymentPlanAmount(FixedContributionMixin, LedgerTrackedModel):
    _fixed_owner_field = "plan_id"
    plan = models.ForeignKey(
        PaymentPlan, on_delete=models.CASCADE, related_name="amounts"
    )
    payment_type = models.ForeignKey(
        PaymentType, on_delete=models.PROTECT, related_name="plan_amounts"
    )
    amount = models.DecimalField(
        "Monthly amount (LKR)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    is_fixed = models.BooleanField(
        "Fixed amount",
        default=False,
        help_text="This member's agreed monthly amount is locked.",
    )

    class Meta:
        ordering = ["payment_type__sort_order", "payment_type__name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "payment_type"], name="unique_payment_plan_type"
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="payment_plan_amount_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(is_fixed=False) | models.Q(amount__gt=0),
                name="plan_fixed_amount_positive",
            ),
        ]

    def __str__(self):
        return f"{self.payment_type} · {self.amount}"


class OpeningBalance(LedgerTrackedModel):
    member = models.OneToOneField(
        Member, on_delete=models.PROTECT, related_name="opening_balance"
    )
    balance_date = models.DateField("Balance as of", default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["member__member_no"]

    def __str__(self):
        return f"Opening balances · {self.member}"

    def clean(self):
        super().clean()
        from .deposit_interest import guard_interest_opening

        guard_interest_opening(self)
        from .fix_deposit_services import locked_payment_types

        if (
            self.pk
            and locked_payment_types(self.member_id)
            and type(self)
            .objects.filter(pk=self.pk)
            .exclude(balance_date=self.balance_date)
            .exists()
        ):
            raise ValidationError(
                {
                    "balance_date": "The opening date is locked by Fix Deposit loan or withdrawal history."
                }
            )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.member_id:
                Member.objects.select_for_update().filter(pk=self.member_id).first()
            self.clean()
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .fix_deposit_services import protect_opening_delete

        with transaction.atomic():
            Member.objects.select_for_update().filter(pk=self.member_id).first()
            from .deposit_interest import protect_interest_opening_delete

            protect_interest_opening_delete(self)
            protect_opening_delete(self)
            return super().delete(*args, **kwargs)

    @property
    def total(self):
        if not self.pk:
            return Decimal("0.00")
        return sum((item.amount for item in self.amounts.all()), Decimal("0.00"))

    @property
    def type_balances(self):
        """Include new payment types without presenting missing amounts as saved zeros."""
        existing = (
            {item.payment_type_id: item.amount for item in self.amounts.all()}
            if self.pk
            else {}
        )
        return [
            {"payment_type": item, "amount": existing.get(item.pk)}
            for item in PaymentType.objects.filter(
                models.Q(is_active=True) | models.Q(pk__in=existing)
            )
        ]


class OpeningBalanceAmount(LedgerTrackedModel):
    opening_balance = models.ForeignKey(
        OpeningBalance, on_delete=models.CASCADE, related_name="amounts"
    )
    payment_type = models.ForeignKey(
        PaymentType, on_delete=models.PROTECT, related_name="opening_amounts"
    )
    amount = models.DecimalField(
        "Opening balance (LKR)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        ordering = ["payment_type__sort_order", "payment_type__name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["opening_balance", "payment_type"],
                name="unique_opening_balance_type",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="opening_balance_amount_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.payment_type} · {self.amount}"

    def clean(self):
        super().clean()
        from .fix_deposit_services import guard_opening_amount

        if self.opening_balance_id and self.payment_type_id:
            guard_opening_amount(self)
            from .deposit_interest import guard_interest_opening_amount

            guard_interest_opening_amount(self)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            Member.objects.select_for_update().filter(
                pk=self.opening_balance.member_id
            ).first()
            self.clean()
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .fix_deposit_services import guard_opening_amount

        with transaction.atomic():
            Member.objects.select_for_update().filter(
                pk=self.opening_balance.member_id
            ).first()
            try:
                guard_opening_amount(self, deleting=True)
                from .deposit_interest import guard_interest_opening_amount

                guard_interest_opening_amount(self, deleting=True)
            except ValidationError as error:
                raise ProtectedError(" ".join(error.messages), [self])
            return super().delete(*args, **kwargs)


class DepositInterestYear(LedgerTrackedModel):
    year_opening_balance = models.DecimalField(
        max_digits=24, decimal_places=2, null=True, blank=True
    )
    opening_effective_on = models.DateField(null=True, blank=True)
    period_debit = models.DecimalField(
        max_digits=24, decimal_places=2, null=True, blank=True
    )
    period_credit = models.DecimalField(
        max_digits=24, decimal_places=2, null=True, blank=True
    )
    member = models.ForeignKey(
        Member, on_delete=models.PROTECT, related_name="interest_years"
    )
    payment_type = models.ForeignKey(
        PaymentType, on_delete=models.PROTECT, related_name="interest_years"
    )
    year_start = models.DateField()
    year_end = models.DateField()
    annual_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    quarters = models.JSONField(default=list)
    calculated_through = models.DateField()
    annual_interest = models.DecimalField(
        max_digits=24, decimal_places=2, default=Decimal("0.00")
    )
    closing_balance = models.DecimalField(
        max_digits=24, decimal_places=2, default=Decimal("0.00")
    )
    next_opening_balance = models.DecimalField(
        max_digits=24, decimal_places=2, default=Decimal("0.00")
    )
    ledger_transaction = models.OneToOneField(
        PaymentTransaction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="interest_posting",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posted_interest_years",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "deposit interest year"
        verbose_name_plural = "deposit interest years"
        ordering = ["-year_start", "member__member_no", "payment_type__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["member", "payment_type", "year_start"],
                name="unique_member_type_interest_year",
            ),
            models.CheckConstraint(
                condition=models.Q(year_end__gte=models.F("year_start")),
                name="interest_year_valid_dates",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    annual_rate__gte=0, annual_rate__lte=100, annual_interest__gte=0
                ),
                name="deposit_interest_valid_rate_amount",
            ),
        ]

    def __str__(self):
        return f"{self.member} · {self.payment_type} · {self.year_start.year}"

    @property
    def final_closing_balance(self):
        return self.next_opening_balance

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                type(self).objects.filter(pk=self.pk, posted_at__isnull=False).first()
            )
            if previous and any(
                getattr(previous, field.attname) != getattr(self, field.attname)
                for field in self._meta.concrete_fields
                if field.name != "updated_at"
            ):
                raise ValidationError("A posted interest year cannot be changed.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted_at:
            raise ProtectedError("A posted interest year cannot be deleted.", [self])
        return super().delete(*args, **kwargs)


class Child(LedgerTrackedModel):
    member = models.ForeignKey(
        Member, on_delete=models.PROTECT, related_name="children"
    )
    name = models.CharField("Child name", max_length=200)
    date_of_birth = models.DateField("Date of birth")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["member__member_no", "name", "pk"]
        verbose_name_plural = "children"

    def __str__(self):
        return f"{self.name} · {self.date_of_birth} · {self.member.member_no}"

    def age_on(self, as_of):
        if as_of < self.date_of_birth:
            return None
        return (
            as_of.year
            - self.date_of_birth.year
            - (
                (as_of.month, as_of.day)
                < (self.date_of_birth.month, self.date_of_birth.day)
            )
        )

    @property
    def age(self):
        return self.age_on(timezone.localdate())

    def clean(self):
        super().clean()
        from .child_interest import guard_child_identity

        guard_child_identity(self)
        self.name = self.name.strip()
        errors = {}
        if not self.name:
            errors["name"] = "Enter the child's name."
        if self.date_of_birth and self.date_of_birth > timezone.localdate():
            errors["date_of_birth"] = "Date of birth cannot be in the future."
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("member_id", flat=True)
                .first()
            )
            if previous != self.member_id:
                errors["member"] = (
                    "The member cannot be changed on a saved child record."
                )
            if (
                self.date_of_birth
                and self.deposits.filter(opened_on__lt=self.date_of_birth).exists()
            ):
                errors["date_of_birth"] = (
                    "Date of birth cannot be after an existing deposit's opening date."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        from .child_interest import guard_child_identity

        with transaction.atomic():
            if self.member_id:
                Member.objects.select_for_update().filter(pk=self.member_id).first()
            guard_child_identity(self)
            return super().save(*args, **kwargs)


class ChildDepositType(models.Model):
    name = models.CharField(max_length=120, unique=True)
    age_threshold = models.PositiveSmallIntegerField(
        "Rate changes at age",
        default=18,
        validators=[MinValueValidator(1), MaxValueValidator(150)],
        help_text="The second rate applies from this birthday. Default: age 18.",
    )
    rate_below_threshold = models.DecimalField(
        "Rate below this age (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("8.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    rate_from_threshold = models.DecimalField(
        "Rate from this age (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("5.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    sort_order = models.PositiveIntegerField("Display order", default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(age_threshold__gte=1, age_threshold__lte=150),
                name="child_type_valid_age",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    rate_below_threshold__gte=0,
                    rate_below_threshold__lte=100,
                    rate_from_threshold__gte=0,
                    rate_from_threshold__lte=100,
                ),
                name="child_type_valid_rates",
            ),
        ]

    def __str__(self):
        return self.name

    def rate_for(self, child, as_of):
        age = child.age_on(as_of)
        if age is None:
            return None
        return (
            self.rate_from_threshold
            if age >= self.age_threshold
            else self.rate_below_threshold
        )


class ChildDeposit(LedgerTrackedModel):
    child = models.ForeignKey(Child, on_delete=models.PROTECT, related_name="deposits")
    deposit_type = models.ForeignKey(
        ChildDepositType, on_delete=models.PROTECT, related_name="deposits"
    )
    account_no = models.CharField(
        "Deposit reference",
        max_length=20,
        unique=True,
        default=child_deposit_reference,
        editable=False,
    )
    opened_on = models.DateField("Opening date", default=timezone.localdate)
    opening_amount = models.DecimalField(
        "Opening amount (LKR)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["child__member__member_no", "child__name", "account_no"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(opening_amount__gte=0),
                name="child_deposit_opening_nonnegative",
            )
        ]

    @property
    def name(self):
        return self.account_no

    def __str__(self):
        return f"{self.account_no} · {self.child.name} · {self.deposit_type.name}"

    def rate_on(self, as_of):
        return self.deposit_type.rate_for(self.child, as_of)

    def clean(self):
        super().clean()
        from .child_interest import guard_child_deposit

        guard_child_deposit(self)
        errors = {}
        if (
            self.child_id
            and self.opened_on
            and self.opened_on < self.child.date_of_birth
        ):
            errors["opened_on"] = (
                "The opening date cannot be before the child's date of birth."
            )
        if self.opened_on and self.opened_on > timezone.localdate():
            errors["opened_on"] = "The opening date cannot be in the future."
        if not self.pk:
            if self.child_id and not self.child.is_active:
                errors["child"] = "Choose an active child."
            if self.deposit_type_id and not self.deposit_type.is_active:
                errors["deposit_type"] = "Choose an active child deposit type."
        else:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("child_id", "deposit_type_id", "opened_on", "opening_amount")
                .first()
            )
            if previous:
                for field in ("child", "deposit_type"):
                    if previous[f"{field}_id"] != getattr(self, f"{field}_id"):
                        errors[field] = (
                            "This selection cannot be changed on a saved deposit."
                        )
                if self.transactions.exists():
                    for field in ("opened_on", "opening_amount"):
                        if previous[field] != getattr(self, field):
                            errors[field] = (
                                "The opening amount and date stay fixed after transactions are recorded."
                            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        from .child_interest import guard_child_deposit

        with transaction.atomic():
            if self.child_id:
                Member.objects.select_for_update().filter(
                    pk=self.child.member_id
                ).first()
            guard_child_deposit(self)
            return super().save(*args, **kwargs)


class ChildDepositTransaction(LedgerTrackedModel):
    objects = PostingQuerySet.as_manager()
    reverses = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="correction",
        verbose_name="Corrects entry",
    )
    deposit = models.ForeignKey(
        ChildDeposit, on_delete=models.PROTECT, related_name="transactions"
    )
    transaction_date = models.DateField("Transaction date", default=timezone.localdate)
    debit = models.DecimalField(
        "Debit (LKR)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    credit = models.DecimalField(
        "Credit (LKR)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    interest_rate = models.DecimalField(
        "Rate at entry (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        editable=False,
    )
    reference = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="recorded_child_deposit_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["transaction_date", "pk"]
        default_permissions = ()  # Entry actions use the Child Deposits module permissions.
        indexes = [models.Index(fields=["deposit", "transaction_date"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(debit__gt=0, credit=0)
                | models.Q(credit__gt=0, debit=0),
                name="child_entry_one_positive_side",
            ),
            models.CheckConstraint(
                condition=models.Q(interest_rate__gte=0, interest_rate__lte=100),
                name="child_entry_valid_rate",
            ),
        ]

    def __str__(self):
        return f"{self.deposit.account_no} · {self.transaction_date}"

    def clean(self):
        super().clean()
        from .contribution_services import guard_child_receipt_transaction

        guard_child_receipt_transaction(self)
        from .posting_policies import validate_correction

        validate_correction(self)
        from .child_interest import guard_child_transaction

        guard_child_transaction(self)
        if (
            self.pk
            and type(self)
            .objects.filter(pk=self.pk)
            .exclude(deposit_id=self.deposit_id)
            .exists()
        ):
            raise ValidationError(
                {"deposit": "The deposit cannot be changed on a saved entry."}
            )
        if (
            self.debit is not None
            and self.credit is not None
            and not (
                (self.debit > 0 and self.credit == 0)
                or (self.credit > 0 and self.debit == 0)
            )
        ):
            raise ValidationError(
                "Enter a positive debit or credit, with the other side blank or zero."
            )
        if (
            self.deposit_id
            and self.transaction_date
            and self.transaction_date < self.deposit.opened_on
        ):
            raise ValidationError(
                {
                    "transaction_date": "The transaction date cannot be before the deposit's opening date."
                }
            )
        if (
            not self.pk
            and not getattr(self, "_annual_interest_posting", False)
            and self.deposit_id
            and not (
                self.deposit.is_active
                and self.deposit.child.is_active
                and self.deposit.deposit_type.is_active
            )
        ):
            raise ValidationError(
                "This deposit, child or deposit type is inactive. New entries cannot be added."
            )

    def save(self, *args, **kwargs):
        from .child_interest import guard_child_transaction
        from .contribution_services import guard_child_receipt_transaction
        from .posting_policies import guard_posting_edit, validate_correction

        with transaction.atomic():
            if self.deposit_id:
                Member.objects.select_for_update().filter(
                    pk=self.deposit.child.member_id
                ).first()
            guard_posting_edit(self)
            guard_child_receipt_transaction(self)
            validate_correction(self)
            guard_child_transaction(self)
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .child_interest import guard_child_transaction
        from .contribution_services import guard_child_receipt_transaction

        with transaction.atomic():
            Member.objects.select_for_update().filter(
                pk=self.deposit.child.member_id
            ).first()
            try:
                guard_child_receipt_transaction(self)
                guard_child_transaction(self, deleting=True)
            except ValidationError as error:
                raise ProtectedError(" ".join(error.messages), [self])
            return super().delete(*args, **kwargs)


class ChildDepositInterestYear(LedgerTrackedModel):
    year_opening_balance = models.DecimalField(
        max_digits=24, decimal_places=2, null=True, blank=True
    )
    opening_effective_on = models.DateField(null=True, blank=True)
    period_debit = models.DecimalField(
        max_digits=24, decimal_places=2, null=True, blank=True
    )
    period_credit = models.DecimalField(
        max_digits=24, decimal_places=2, null=True, blank=True
    )
    deposit = models.ForeignKey(
        ChildDeposit, on_delete=models.PROTECT, related_name="interest_years"
    )
    year_start = models.DateField()
    year_end = models.DateField()
    annual_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    rate_rules = models.JSONField(default=dict)
    quarters = models.JSONField(default=list)
    calculated_through = models.DateField()
    annual_interest = models.DecimalField(
        max_digits=24, decimal_places=2, default=Decimal("0.00")
    )
    closing_balance = models.DecimalField(
        max_digits=24, decimal_places=2, default=Decimal("0.00")
    )
    next_opening_balance = models.DecimalField(
        max_digits=24, decimal_places=2, default=Decimal("0.00")
    )
    ledger_transaction = models.OneToOneField(
        ChildDepositTransaction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="interest_posting",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posted_child_interest_years",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()  # Both account groups use Quarterly Interest permissions.
        ordering = [
            "-year_start",
            "deposit__child__member__member_no",
            "deposit__account_no",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["deposit", "year_start"],
                name="unique_child_deposit_interest_year",
            ),
            models.CheckConstraint(
                condition=models.Q(year_end__gte=models.F("year_start")),
                name="child_interest_valid_dates",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    annual_rate__gte=0, annual_rate__lte=100, annual_interest__gte=0
                ),
                name="child_interest_valid_rate_amount",
            ),
        ]

    @property
    def member(self):
        return self.deposit.child.member

    @property
    def member_id(self):
        return self.deposit.child.member_id

    @property
    def payment_type(self):
        return self.deposit.deposit_type

    @property
    def payment_type_id(self):
        return self.deposit.deposit_type_id

    def __str__(self):
        return f"{self.deposit} · {self.year_start.year}"

    @property
    def final_closing_balance(self):
        return self.next_opening_balance

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                type(self).objects.filter(pk=self.pk, posted_at__isnull=False).first()
            )
            if previous and any(
                getattr(previous, field.attname) != getattr(self, field.attname)
                for field in self._meta.concrete_fields
                if field.name != "updated_at"
            ):
                raise ValidationError("A posted child interest year cannot be changed.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted_at:
            raise ProtectedError(
                "A posted child interest year cannot be deleted.", [self]
            )
        return super().delete(*args, **kwargs)


class FixDepositPolicy(models.Model):
    name = models.CharField(max_length=120, unique=True)
    payment_type = models.OneToOneField(
        PaymentType, on_delete=models.PROTECT, related_name="fix_deposit_policy"
    )
    maturity_months = models.PositiveSmallIntegerField(
        "Withdrawal cycle (months)",
        default=36,
        validators=[MinValueValidator(36), MaxValueValidator(1200)],
    )
    withdrawal_percent = models.DecimalField(
        "Maximum withdrawal (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("75.00"),
        validators=[
            MinValueValidator(Decimal("0.01")),
            MaxValueValidator(Decimal("75")),
        ],
    )
    loan_percent = models.DecimalField(
        "Maximum loan security (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("90.00"),
        validators=[
            MinValueValidator(Decimal("0.01")),
            MaxValueValidator(Decimal("90")),
        ],
    )
    loan_interval_months = models.PositiveSmallIntegerField(
        "Months between loan issues",
        default=12,
        validators=[MinValueValidator(12), MaxValueValidator(1200)],
    )
    loan_interest_rate = models.DecimalField(
        "Annual loan interest rate (%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("0.01")),
            MaxValueValidator(Decimal("100")),
        ],
        help_text="Set the bank's loan rate before issuing loans. Interest charges are assessed by the bank and recorded separately from principal repayments.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "pk"]
        verbose_name = "Fix Deposit rule"
        verbose_name_plural = "Fix Deposit rules"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    maturity_months__gte=36,
                    maturity_months__lte=1200,
                    loan_interval_months__gte=12,
                    loan_interval_months__lte=1200,
                ),
                name="fix_policy_valid_periods",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    withdrawal_percent__gt=0,
                    withdrawal_percent__lte=75,
                    loan_percent__gt=0,
                    loan_percent__lte=90,
                ),
                name="fix_policy_valid_limits",
            ),
            models.CheckConstraint(
                condition=models.Q(loan_interest_rate__isnull=True)
                | models.Q(loan_interest_rate__gt=0, loan_interest_rate__lte=100),
                name="fix_policy_valid_interest",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.pk:
            old_type = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("payment_type_id", flat=True)
                .first()
            )
            if old_type != self.payment_type_id and (
                self.accounts.exists()
                or PaymentTransaction.objects.filter(payment_type_id=old_type).exists()
                or OpeningBalanceAmount.objects.filter(
                    payment_type_id=old_type
                ).exists()
            ):
                raise ValidationError(
                    {
                        "payment_type": "The linked payment type cannot change after balances or accounts are recorded."
                    }
                )

    def delete(self, *args, **kwargs):
        if (
            self.payment_type.transactions.exists()
            or self.payment_type.opening_amounts.exists()
        ):
            raise ProtectedError(
                "This rule protects recorded Fix Deposit balances. Mark it inactive instead.",
                [self],
            )
        return super().delete(*args, **kwargs)


class FixDepositAccount(LedgerTrackedModel):
    member = models.ForeignKey(
        Member, on_delete=models.PROTECT, related_name="fix_deposit_accounts"
    )
    policy = models.ForeignKey(
        FixDepositPolicy, on_delete=models.PROTECT, related_name="accounts"
    )
    cycle_started_on = models.DateField(
        "Start of current withdrawal cycle",
        help_text="Enter the actual start of the current 3-year cycle, even if the opening balance was imported more recently. Each released withdrawal restarts the cycle.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["member__member_no", "pk"]
        verbose_name = "Fix Deposit account"
        constraints = [
            models.UniqueConstraint(
                fields=["member", "policy"], name="unique_member_fix_policy"
            )
        ]

    @property
    def name(self):
        return self.member.member_no

    def __str__(self):
        return f"Fix Deposit · {self.member}"

    def clean(self):
        super().clean()
        if self.cycle_started_on and self.cycle_started_on > timezone.localdate():
            raise ValidationError(
                {"cycle_started_on": "The cycle start cannot be in the future."}
            )
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("member_id", "policy_id", "cycle_started_on")
                .first()
            )
            if previous:
                errors = {
                    field: "This selection stays fixed on a saved account."
                    for field in ("member", "policy")
                    if previous[f"{field}_id"] != getattr(self, f"{field}_id")
                }
                if previous["cycle_started_on"] != self.cycle_started_on and (
                    self.loans.exists() or self.withdrawals.exists()
                ):
                    errors["cycle_started_on"] = (
                        "The cycle start stays fixed after loan or withdrawal activity."
                    )
                if errors:
                    raise ValidationError(errors)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            self.cycle_started_on = self._meta.get_field("cycle_started_on").to_python(
                self.cycle_started_on
            )
            if self.member_id:
                Member.objects.select_for_update().filter(pk=self.member_id).first()
            if self.policy_id:
                FixDepositPolicy.objects.select_for_update().get(pk=self.policy_id)
            self.clean()
            return super().save(*args, **kwargs)


class FixDepositLoan(LedgerTrackedModel):
    account = models.ForeignKey(
        FixDepositAccount, on_delete=models.PROTECT, related_name="loans"
    )
    reference = models.CharField(
        max_length=20, unique=True, default=fix_loan_reference, editable=False
    )
    issued_on = models.DateField()
    principal = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    interest_rate = models.DecimalField(
        "Annual rate at issue (%)", max_digits=5, decimal_places=2
    )
    deposit_balance_at_issue = models.DecimalField(max_digits=14, decimal_places=2)
    maximum_loan_at_issue = models.DecimalField(max_digits=14, decimal_places=2)
    settled_on = models.DateField(null=True, blank=True)
    settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="settled_fix_loans",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_fix_loans",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_on", "-pk"]
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    principal__gt=0,
                    principal__lte=models.F("maximum_loan_at_issue"),
                    interest_rate__gt=0,
                    interest_rate__lte=100,
                ),
                name="fix_loan_valid_amount_rate",
            ),
            models.CheckConstraint(
                condition=models.Q(settled_on__isnull=True)
                | models.Q(settled_on__gte=models.F("issued_on")),
                name="fix_loan_valid_settlement_date",
            ),
        ]

    def __str__(self):
        return self.reference


class FixLoanEntry(LedgerTrackedModel):
    class Kind(models.TextChoices):
        REPAYMENT = "repayment", "Principal / interest repayment"
        INTEREST = "interest", "Interest charge assessed by bank"

    loan = models.ForeignKey(
        FixDepositLoan, on_delete=models.PROTECT, related_name="entries"
    )
    operation_key = models.UUIDField(default=uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    entry_date = models.DateField(default=timezone.localdate)
    principal_amount = models.DecimalField(
        "Principal paid (LKR)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    interest_amount = models.DecimalField(
        "Interest (LKR)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fix_loan_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entry_date", "pk"]
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    kind="interest", principal_amount=0, interest_amount__gt=0
                )
                | (
                    models.Q(
                        kind="repayment",
                        principal_amount__gte=0,
                        interest_amount__gte=0,
                    )
                    & (
                        models.Q(principal_amount__gt=0)
                        | models.Q(interest_amount__gt=0)
                    )
                ),
                name="fix_loan_entry_valid_amounts",
            )
        ]

    def __str__(self):
        return f"{self.loan.reference} · {self.entry_date} · {self.get_kind_display()}"


class FixDepositWithdrawal(LedgerTrackedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending release"
        RELEASED = "released", "Released"
        REJECTED = "rejected", "Rejected"

    account = models.ForeignKey(
        FixDepositAccount, on_delete=models.PROTECT, related_name="withdrawals"
    )
    requested_on = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(
        "Requested withdrawal (LKR)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    released_on = models.DateField(null=True, blank=True)
    balance_before = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    balance_after = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    maximum_at_release = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    ledger_transaction = models.OneToOneField(
        PaymentTransaction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fix_deposit_withdrawal",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_fix_withdrawals",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_fix_withdrawals",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-requested_on", "-pk"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["account"],
                condition=models.Q(status="pending"),
                name="one_pending_fix_withdrawal",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="fix_withdrawal_positive"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="released",
                        released_on__isnull=False,
                        ledger_transaction__isnull=False,
                        balance_before__isnull=False,
                        balance_after__isnull=False,
                        maximum_at_release__isnull=False,
                        amount__lte=models.F("maximum_at_release"),
                        balance_after__gte=0,
                    )
                    | models.Q(
                        status__in=["pending", "rejected"],
                        released_on__isnull=True,
                        ledger_transaction__isnull=True,
                    )
                ),
                name="fix_withdrawal_valid_release",
            ),
        ]

    def __str__(self):
        return f"{self.account.member.member_no} · {self.amount} · {self.get_status_display()}"


# Import these models after the account models so Django registers the ledger app schema.
from .transfer_models import (
    LedgerOfficerProfile,
    MemberLedger,
    MemberTransfer,
    LedgerOfficerAssignment,
    LedgerRecord,
    LedgerEvent,
)

from .approval_models import (
    ApprovalHierarchy,
    ApprovalLevel,
    ApprovalRequest,
    ApprovalAuditEvent,
)

from .contribution_models import PaymentReceipt, PaymentReceiptAllocation
from .audit_models import AuditLog
from .year_end_models import FinancialYearClosing
from .status_models import (
    MemberStatusRequest,
    MemberStatusDocument,
    MemberStatusEvent,
    MemberActivePeriod,
)
