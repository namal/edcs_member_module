from django.apps import AppConfig


class MembersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "members"

    def ready(self):
        from django.db.models.signals import pre_delete, post_delete
        from .ledger_tracking import (
            TRACKED,
            before_tracked_delete,
            after_tracked_delete,
            protect_history_delete,
        )

        for name in TRACKED:
            model = self.get_model(name)
            pre_delete.connect(
                before_tracked_delete,
                sender=model,
                dispatch_uid=f"ledger-delete-{name}",
            )
            post_delete.connect(
                after_tracked_delete,
                sender=model,
                dispatch_uid=f"ledger-deleted-{name}",
            )
        for name in (
            "MemberLedger",
            "MemberTransfer",
            "LedgerOfficerAssignment",
            "LedgerRecord",
            "LedgerEvent",
            "ApprovalRequest",
            "ApprovalAuditEvent",
            "PaymentReceipt",
            "PaymentReceiptAllocation",
        ):
            pre_delete.connect(
                protect_history_delete,
                sender=self.get_model(name),
                dispatch_uid=f"ledger-permanent-{name}",
            )
