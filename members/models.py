from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
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
        verbose_name = "department"
        verbose_name_plural = "departments"
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
                    "This department has registered members. Its location cannot be changed."
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
        School,
        verbose_name="Department",
        on_delete=models.PROTECT,
        related_name="members",
        null=True,
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
                    "Choose a department matching the selected district, zonal office and subdivision."
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


class PaymentTransaction(models.Model):
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


class PaymentPlan(models.Model):
    member = models.OneToOneField(
        Member, on_delete=models.PROTECT, related_name="payment_plan"
    )
    agreement_date = models.DateField(default=timezone.localdate)
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


class PaymentPlanAmount(models.Model):
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
        ]

    def __str__(self):
        return f"{self.payment_type} · {self.amount}"


class OpeningBalance(models.Model):
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

    @property
    def total(self):
        if not self.pk:
            return Decimal("0.00")
        return sum((item.amount for item in self.amounts.all()), Decimal("0.00"))

    @property
    def type_balances(self):
        """Include new payment types without presenting missing amounts as saved zeros."""
        existing = {item.payment_type_id: item.amount for item in self.amounts.all()}
        return [
            {"payment_type": item, "amount": existing.get(item.pk)}
            for item in PaymentType.objects.filter(
                models.Q(is_active=True) | models.Q(pk__in=existing)
            )
        ]


class OpeningBalanceAmount(models.Model):
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
