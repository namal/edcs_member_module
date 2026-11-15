"""Permanent ledger ownership and append-only transfer/responsibility history."""

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError
from django.utils import timezone


def ledger_reference():
    return f"ML-{uuid4().hex[:12].upper()}"


def transfer_reference():
    return f"MT-{uuid4().hex[:12].upper()}"


class LedgerOfficerProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ledger_profile",
    )
    epf_number = models.CharField(
        "EPF number", max_length=30, unique=True, null=True, blank=True
    )
    zonal_office = models.ForeignKey(
        "members.OfficeLocation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_officers",
    )

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} · EPF {self.epf_number or 'not entered'}"

    def clean(self):
        super().clean()
        self.epf_number = (self.epf_number or "").strip().upper() or None
        if (
            self.pk
            and self.user.responsible_ledgers.filter(status="active")
            .exclude(zonal_office_id=self.zonal_office_id)
            .exists()
        ):
            raise ValidationError(
                {
                    "zonal_office": "Reassign this officer's active ledgers before changing their zonal office."
                }
            )

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class PermanentHistory(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Historical audit records cannot be changed.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Ledger and transfer history must be retained.", [self])


class MemberLedger(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        TRANSFERRED = "transferred", "Transferred / historical"

    member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT, related_name="ledgers"
    )
    reference = models.CharField(
        max_length=20, unique=True, default=ledger_reference, editable=False
    )
    district = models.ForeignKey(
        "members.District", on_delete=models.PROTECT, related_name="member_ledgers"
    )
    zonal_office = models.ForeignKey(
        "members.OfficeLocation",
        on_delete=models.PROTECT,
        related_name="member_ledgers",
    )
    lager = models.ForeignKey(
        "members.Lager",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_pages",
    )
    school = models.ForeignKey(
        "members.School",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_pages",
    )
    subdivision = models.ForeignKey(
        "members.Subdivision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_pages",
    )
    previous_ledger = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="next_ledger",
    )
    opened_on = models.DateField(default=timezone.localdate)
    closed_on = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.ACTIVE
    )
    maintenance_officer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="responsible_ledgers",
    )
    officer_name = models.CharField(max_length=350, blank=True)
    officer_epf = models.CharField(max_length=30, blank=True)
    legacy_assignment_pending = models.BooleanField(default=False)
    registration_snapshot = models.JSONField(default=dict)
    location_snapshot = models.JSONField(default=dict)
    opening_balances = models.JSONField(default=dict)
    closing_balances = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_member_ledgers",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["member__member_no", "opened_on", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["member"],
                condition=models.Q(status="active"),
                name="one_active_member_ledger",
            ),
            models.CheckConstraint(
                condition=models.Q(closed_on__isnull=True, status="active")
                | models.Q(
                    closed_on__isnull=False,
                    status="transferred",
                    closed_on__gte=models.F("opened_on"),
                ),
                name="ledger_valid_closing_state",
            ),
        ]

    @property
    def name(self):
        return self.reference

    def __str__(self):
        return f"{self.reference} · {self.member.member_no}"

    def save(self, *args, **kwargs):
        if not getattr(self, "_ledger_service_write", False):
            raise ValidationError(
                "Use Ledger Management or Member Transfers to maintain ledger pages."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "Original and receiving ledger pages must be retained.", [self]
        )


class MemberTransfer(PermanentHistory):
    member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT, related_name="transfers"
    )
    reference = models.CharField(
        max_length=20, unique=True, default=transfer_reference, editable=False
    )
    from_ledger = models.OneToOneField(
        MemberLedger, on_delete=models.PROTECT, related_name="outgoing_transfer"
    )
    to_ledger = models.OneToOneField(
        MemberLedger, on_delete=models.PROTECT, related_name="incoming_transfer"
    )
    effective_date = models.DateField()
    reason = models.TextField()
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="performed_member_transfers",
    )
    performed_by_name = models.CharField(max_length=350)
    performed_by_epf = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_date", "-pk"]

    @property
    def name(self):
        return self.reference

    def __str__(self):
        return self.reference

    def save(self, *args, **kwargs):
        if not getattr(self, "_transfer_service_write", False):
            raise ValidationError(
                "Use the Member Transfer workflow to create a transfer."
            )
        return super().save(*args, **kwargs)


class LedgerOfficerAssignment(PermanentHistory):
    ledger = models.ForeignKey(
        MemberLedger, on_delete=models.PROTECT, related_name="officer_history"
    )
    previous_officer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="previous_ledger_assignments",
    )
    officer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ledger_assignments",
    )
    previous_name = models.CharField(max_length=350, blank=True)
    previous_epf = models.CharField(max_length=30, blank=True)
    officer_name = models.CharField(max_length=350)
    officer_epf = models.CharField(max_length=30)
    reason = models.TextField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="ledger_officer_changes",
    )
    changed_by_name = models.CharField(max_length=350, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["changed_at", "pk"]


class LedgerRecord(PermanentHistory):
    """Stable source identity, independent of later member/location changes."""

    member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT, related_name="ledger_records"
    )
    original_ledger = models.ForeignKey(
        MemberLedger, on_delete=models.PROTECT, related_name="original_records"
    )
    source_model = models.CharField(max_length=80)
    source_id = models.PositiveBigIntegerField()

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["source_model", "source_id"], name="unique_ledger_source_record"
            )
        ]


class LedgerEvent(PermanentHistory):
    record = models.ForeignKey(
        LedgerRecord, on_delete=models.PROTECT, related_name="events"
    )
    ledger = models.ForeignKey(
        MemberLedger, on_delete=models.PROTECT, related_name="events"
    )
    action = models.CharField(
        max_length=10,
        choices=[
            ("created", "Created"),
            ("updated", "Updated"),
            ("deleted", "Deleted"),
        ],
    )
    effective_on = models.DateField()
    snapshot = models.JSONField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="ledger_events",
    )
    actor_name = models.CharField(max_length=350, blank=True)
    actor_epf = models.CharField(max_length=30, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-recorded_at", "-pk"]
        indexes = [models.Index(fields=["ledger", "effective_on"])]
