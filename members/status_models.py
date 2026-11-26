"""Private status requests and retained membership periods on the same ledger."""

from contextvars import ContextVar
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError
from django.utils import timezone

status_write = ContextVar("membership_status_write", default=False)


def status_reference():
    return f"MS-{uuid4().hex[:12].upper()}"


class StatusHistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if not status_write.get():
            raise ValidationError("Use the membership status approval workflow.")
        return super().update(**kwargs)

    def bulk_update(self, *args, **kwargs):
        if not status_write.get():
            raise ValidationError("Membership status history cannot be rewritten.")
        return super().bulk_update(*args, **kwargs)

    def bulk_create(self, *args, **kwargs):
        if not status_write.get():
            raise ValidationError("Use the membership status approval workflow.")
        return super().bulk_create(*args, **kwargs)

    def delete(self):
        raise ProtectedError("Membership status history must be retained.", self)


class StatusHistoryModel(models.Model):
    objects = StatusHistoryQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not status_write.get():
            raise ValidationError("Use the membership status approval workflow.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Membership status history must be retained.", [self])


class MemberStatusRequest(StatusHistoryModel):
    class Kind(models.TextChoices):
        ACTIVATE = "activate", "Initial activation"
        INACTIVATE = "inactivate", "Inactivation"
        REACTIVATE = "reactivate", "Reactivation"

    class State(models.TextChoices):
        PENDING = "pending", "Awaiting approval"
        SCHEDULED = "scheduled", "Approved · scheduled"
        APPLIED = "applied", "Applied"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Could not apply"

    reference = models.CharField(
        max_length=20, unique=True, default=status_reference, editable=False
    )
    member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT, related_name="status_requests"
    )
    lager = models.ForeignKey(
        "members.Lager", on_delete=models.PROTECT, related_name="status_requests"
    )
    ledger = models.ForeignKey(
        "members.MemberLedger", on_delete=models.PROTECT, related_name="status_requests"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    source_status = models.CharField(max_length=10)
    reason = models.CharField(max_length=40)
    description = models.TextField()
    requested_effective_date = models.DateField()
    snapshot = models.JSONField(default=dict)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="membership_requests",
    )
    requester_name = models.CharField(max_length=350)
    requester_epf = models.CharField(max_length=30, blank=True)
    requested_at = models.DateTimeField(default=timezone.now, editable=False)
    approval = models.OneToOneField(
        "members.ApprovalRequest",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="membership_request",
    )
    state = models.CharField(
        max_length=12, choices=State.choices, default=State.PENDING
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_membership_requests",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    outcome = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        verbose_name = "membership status request"
        verbose_name_plural = "membership status requests"
        default_permissions = ("view", "add", "change")
        constraints = [
            models.UniqueConstraint(
                fields=["member"],
                condition=models.Q(state__in=["pending", "scheduled"]),
                name="one_open_membership_request",
            )
        ]

    def __str__(self):
        return f'{self.reference} · {self.snapshot.get("member_no", self.member_id)} · {self.get_kind_display()}'

    @property
    def reason_label(self):
        from .status_services import REASONS

        return dict(REASONS[self.kind]).get(self.reason, self.reason)


class MemberStatusDocument(StatusHistoryModel):
    request = models.ForeignKey(
        MemberStatusRequest, on_delete=models.PROTECT, related_name="documents"
    )
    name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    content = models.BinaryField(editable=False)
    uploaded_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return self.name


class MemberStatusEvent(StatusHistoryModel):
    member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT, related_name="status_events"
    )
    ledger = models.ForeignKey(
        "members.MemberLedger",
        on_delete=models.PROTECT,
        related_name="membership_events",
    )
    request = models.OneToOneField(
        MemberStatusRequest, on_delete=models.PROTECT, related_name="status_event"
    )
    from_status = models.CharField(max_length=10)
    to_status = models.CharField(max_length=10)
    reason = models.CharField(max_length=100)
    description = models.TextField()
    effective_at = models.DateTimeField(default=timezone.now, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    actor_name = models.CharField(max_length=350)
    actor_epf = models.CharField(max_length=30, blank=True)

    class Meta:
        default_permissions = ()
        ordering = ["-effective_at", "-pk"]

    def __str__(self):
        return f"{self.member_id}: {self.from_status} → {self.to_status}"


class MemberActivePeriod(StatusHistoryModel):
    member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT, related_name="active_periods"
    )
    ledger = models.ForeignKey(
        "members.MemberLedger",
        on_delete=models.PROTECT,
        related_name="membership_periods",
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    opened_by_request = models.OneToOneField(
        MemberStatusRequest,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opened_period",
    )
    closed_by_request = models.OneToOneField(
        MemberStatusRequest,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="closed_period",
    )
    imported = models.BooleanField(default=False)

    class Meta:
        default_permissions = ()
        ordering = ["-started_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["member"],
                condition=models.Q(ended_at__isnull=True),
                name="one_open_member_active_period",
            ),
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True)
                | models.Q(ended_at__gte=models.F("started_at")),
                name="member_active_period_order",
            ),
        ]

    def __str__(self):
        return f"{self.member_id} · {self.started_at}"
