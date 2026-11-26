"""Independent, permanent audit storage for every workspace module."""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.deletion import ProtectedError
from django.utils import timezone


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Audit logs cannot be changed.")

    def bulk_update(self, *args, **kwargs):
        raise ValidationError("Audit logs cannot be changed.")

    def delete(self):
        raise ProtectedError("Audit logs must be retained.", self)


class AuditLog(models.Model):
    module = models.CharField(max_length=80, db_index=True)
    module_label = models.CharField(max_length=120)
    source_model = models.CharField(max_length=100)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    record_label = models.CharField(max_length=600)
    action = models.CharField(max_length=40, db_index=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    # Scalar identities preserve the log when source records/users are deleted.
    actor_id = models.PositiveBigIntegerField(null=True, blank=True)
    actor_username = models.CharField(max_length=150, blank=True)
    actor_name = models.CharField(max_length=350, blank=True)
    actor_epf = models.CharField(max_length=30, blank=True)
    member_pk = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    district_pk = models.PositiveBigIntegerField(null=True, blank=True)
    lager_pk = models.PositiveBigIntegerField(null=True, blank=True)
    zonal_office_pk = models.PositiveBigIntegerField(null=True, blank=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    changes = models.JSONField(default=list)
    request_path = models.CharField(max_length=500, blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    origin = models.CharField(max_length=100, default="live")
    origin_id = models.PositiveBigIntegerField(null=True, blank=True)
    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_at", "-pk"]
        default_permissions = ("view",)
        permissions = [
            ("view_securityauditlog", "Can view user and role security audit logs")
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["origin", "origin_id"],
                condition=models.Q(origin_id__isnull=False),
                name="audit_unique_import_source",
            )
        ]

    @property
    def name(self):
        return self.record_label

    @property
    def action_label(self):
        return {
            "created": "Added",
            "updated": "Edited",
            "deleted": "Deleted",
            "access_changed": "Access changed",
            "login": "Signed in",
            "logout": "Signed out",
            "login_failed": "Failed sign-in",
        }.get(self.action, self.action.replace("_", " ").capitalize())

    def __str__(self):
        return f"{self.module_label}: {self.record_label} · {self.action}"

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Audit logs cannot be changed.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Audit logs must be retained.", [self])
