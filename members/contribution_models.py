"""Permanent receipts for plan-allocated member contributions."""

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .transfer_models import PermanentHistory


class PaymentReceipt(PermanentHistory):
    operation_key = models.UUIDField(default=uuid4, unique=True, editable=False)
    member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT, related_name="payment_receipts"
    )
    plan = models.ForeignKey("members.PaymentPlan", on_delete=models.PROTECT)
    received_on = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    plan_snapshot = models.JSONField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    posted_at = models.DateTimeField(auto_now_add=True)
    approval_request = models.OneToOneField(
        "members.ApprovalRequest", on_delete=models.PROTECT, null=True, blank=True
    )

    class Meta:
        default_permissions = ()
        ordering = ["-posted_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="receipt_amount_positive"
            )
        ]

    @property
    def receipt_number(self):
        return f"RC-{str(self.operation_key).upper()}"

    def __str__(self):
        return f"{self.receipt_number} · {self.member}"

    def save(self, *args, **kwargs):
        if not getattr(self, "_receipt_service_write", False):
            raise ValidationError("Use Receive contribution to post a payment receipt.")
        return super().save(*args, **kwargs)


class PaymentReceiptAllocation(PermanentHistory):
    receipt = models.ForeignKey(
        PaymentReceipt, on_delete=models.PROTECT, related_name="allocations"
    )
    payment_type = models.ForeignKey("members.PaymentType", on_delete=models.PROTECT)
    configured_amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction = models.OneToOneField(
        "members.PaymentTransaction",
        on_delete=models.PROTECT,
        related_name="receipt_allocation",
        null=True,
        blank=True,
    )

    class Meta:
        default_permissions = ()
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["receipt", "payment_type"], name="receipt_type_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(configured_amount__gt=0, amount__gte=0),
                name="receipt_allocation_valid_amounts",
            ),
            models.CheckConstraint(
                condition=models.Q(amount=0, transaction__isnull=True)
                | models.Q(amount__gt=0, transaction__isnull=False),
                name="receipt_allocation_transaction_required",
            ),
        ]

    def save(self, *args, **kwargs):
        if not getattr(self, "_receipt_service_write", False):
            raise ValidationError("Receipt allocations are managed by their receipt.")
        return super().save(*args, **kwargs)
