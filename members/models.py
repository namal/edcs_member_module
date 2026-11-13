from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

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
        from .fix_deposit_services import guard_transaction

        guard_transaction(self)

    def save(self, *args, **kwargs):
        from .fix_deposit_services import guard_transaction

        with transaction.atomic():
            if self.member_id:
                Member.objects.select_for_update().filter(pk=self.member_id).first()
            guard_transaction(self)
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .fix_deposit_services import guard_transaction

        with transaction.atomic():
            Member.objects.select_for_update().filter(pk=self.member_id).first()
            try:
                guard_transaction(self, deleting=True)
            except ValidationError as error:
                raise ProtectedError(" ".join(error.messages), [self])
            return super().delete(*args, **kwargs)


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

    def clean(self):
        super().clean()
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

    def clean(self):
        super().clean()
        from .fix_deposit_services import guard_opening_amount

        if self.opening_balance_id and self.payment_type_id:
            guard_opening_amount(self)

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
            except ValidationError as error:
                raise ProtectedError(" ".join(error.messages), [self])
            return super().delete(*args, **kwargs)


class Child(models.Model):
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


class ChildDeposit(models.Model):
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


class ChildDepositTransaction(models.Model):
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


class FixDepositAccount(models.Model):
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


class FixDepositLoan(models.Model):
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


class FixLoanEntry(models.Model):
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


class FixDepositWithdrawal(models.Model):
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
