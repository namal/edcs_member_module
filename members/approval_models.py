"""Configurable workflows and permanent approval records."""

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .transfer_models import PermanentHistory


def approval_reference():
    return f"AR-{uuid4().hex[:12].upper()}"


class ApprovalHierarchy(models.Model):
    name = models.CharField(max_length=150)
    module = models.CharField(max_length=60)
    action = models.CharField(
        max_length=10,
        choices=[
            ("*", "All changes"),
            ("add", "Create"),
            ("change", "Edit / process"),
            ("delete", "Delete"),
        ],
        default="*",
    )
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        ordering = ["module", "action", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["module", "action"],
                condition=models.Q(is_active=True),
                name="one_active_approval_policy",
            )
        ]

    def __str__(self):
        return self.name


class ApprovalLevel(models.Model):
    hierarchy = models.ForeignKey(
        ApprovalHierarchy, on_delete=models.CASCADE, related_name="levels"
    )
    position = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=100)
    approvers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="approval_levels", blank=True
    )
    allow_rejection = models.BooleanField(default=True)
    allow_self_approval = models.BooleanField(
        default=False,
        help_text="Membership Status only: allow the requester to approve this level when explicitly assigned as an approver.",
    )

    class Meta:
        default_permissions = ()
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["hierarchy", "position"], name="unique_approval_level_position"
            )
        ]

    def __str__(self):
        return f"{self.position}. {self.name}"


class ApprovalRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting approval"
        APPROVED = "approved", "Approved and applied"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Could not apply"

    reference = models.CharField(
        max_length=20, unique=True, default=approval_reference, editable=False
    )
    hierarchy = models.ForeignKey(
        ApprovalHierarchy, on_delete=models.PROTECT, related_name="requests"
    )
    hierarchy_name = models.CharField(max_length=150)
    hierarchy_version = models.PositiveIntegerField()
    module = models.CharField(max_length=60)
    action = models.CharField(max_length=10)
    description = models.CharField(max_length=250)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approval_requests",
    )
    requester_name = models.CharField(max_length=350)
    requester_epf = models.CharField(max_length=30, blank=True)
    steps = models.JSONField(default=list)
    current_step = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    view_name = models.CharField(max_length=100)
    view_kwargs = models.JSONField(default=dict)
    query = models.JSONField(default=dict)
    payload = models.JSONField(default=dict)
    summary = models.JSONField(default=list)
    target_model = models.CharField(max_length=100, blank=True)
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    target_fingerprint = models.CharField(max_length=64, blank=True)
    deduplication_key = models.CharField(max_length=64)
    result_url = models.CharField(max_length=500, blank=True)
    outcome = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["deduplication_key"],
                condition=models.Q(status="pending"),
                name="one_pending_identical_request",
            )
        ]

    def __str__(self):
        return self.reference

    def get_status_display(self):
        if self.module == "memberstatusrequest" and self.status == "approved":
            request = self.membership_request
            if request.state == "scheduled":
                return "Approved · scheduled"
            if request.state == "failed":
                return "Approved · could not apply"
        return dict(self.Status.choices).get(self.status, self.status)

    def save(self, *args, **kwargs):
        if not getattr(self, "_approval_service_write", False):
            raise ValidationError(
                "Use the approval workflow to submit or decide requests."
            )
        return super().save(*args, **kwargs)


class ApprovalAuditEvent(PermanentHistory):
    hierarchy = models.ForeignKey(
        ApprovalHierarchy, on_delete=models.PROTECT, related_name="audit_events"
    )
    request = models.ForeignKey(
        ApprovalRequest,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(
        max_length=40,
        choices=[
            ("hierarchy_created", "Hierarchy created"),
            ("hierarchy_updated", "Hierarchy updated"),
            ("submitted", "Submitted for approval"),
            ("approve", "Level approved"),
            ("reject", "Request rejected"),
            ("override_approve", "Remaining levels approved by administrator"),
            ("cancel", "Request cancelled"),
            ("application_failed", "Approved change could not be applied"),
            ("applied", "Approved change applied"),
        ],
    )
    level_name = models.CharField(max_length=100, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    actor_name = models.CharField(max_length=350)
    actor_epf = models.CharField(max_length=30, blank=True)
    is_override = models.BooleanField(default=False)
    reason = models.TextField(blank=True)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["created_at", "pk"]
