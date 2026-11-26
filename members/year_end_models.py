"""Approval evidence for batches closed through the existing account-year services."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError

from .transfer_models import PermanentHistory


class ClosingQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Financial year-end closing history cannot be changed.")

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("Financial year-end closing history cannot be changed.")

    def delete(self):
        raise ProtectedError(
            "Financial year-end closing history must be retained.", self
        )


class FinancialYearClosing(PermanentHistory):
    objects = ClosingQuerySet.as_manager()
    year_start = models.DateField()
    year_end = models.DateField()
    calculation_hash = models.CharField(max_length=64)
    account_count = models.PositiveIntegerField()
    newly_posted_count = models.PositiveIntegerField()
    total_closing = models.DecimalField(max_digits=28, decimal_places=2)
    total_interest = models.DecimalField(max_digits=28, decimal_places=2)
    accounts = models.JSONField(default=list)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    approved_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()

    class Meta:
        ordering = ["-year_start", "-approved_at", "-pk"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["year_start", "calculation_hash"],
                name="unique_year_closing_review",
            )
        ]

    def __str__(self):
        return f"Financial year {self.year_start} – {self.year_end}"

    def save(self, *args, **kwargs):
        if not getattr(self, "_year_end_service_write", False):
            raise ValidationError("Use the financial year-end approval process.")
        return super().save(*args, **kwargs)
