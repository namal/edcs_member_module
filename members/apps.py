from django.apps import AppConfig


class MembersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "members"

    def ready(self):
        from django.db.models.signals import pre_delete, post_delete, m2m_changed
        from .member_audit import audit_member_delete, protect_gender_delete
        from .posting_policies import prevent_posting_delete

        for name in ("PaymentTransaction", "ChildDepositTransaction"):
            pre_delete.connect(
                prevent_posting_delete,
                sender=self.get_model(name),
                dispatch_uid=f"no-posting-delete-{name}",
            )

        pre_delete.connect(
            audit_member_delete,
            sender=self.get_model("Member"),
            dispatch_uid="member-audit-delete",
        )
        pre_delete.connect(
            protect_gender_delete,
            sender=self.get_model("Gender"),
            dispatch_uid="gender-in-use",
        )
        from .transfer_models import (
            LedgerOfficerProfile,
            validate_zonal_office_assignments,
        )

        m2m_changed.connect(
            validate_zonal_office_assignments,
            sender=LedgerOfficerProfile.zonal_offices.through,
            dispatch_uid="user-zonal-office-assignments",
        )
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
            "MemberAuditEvent",
            "AuditLog",
            "FinancialYearClosing",
            "MemberStatusRequest",
            "MemberStatusDocument",
            "MemberStatusEvent",
            "MemberActivePeriod",
        ):
            pre_delete.connect(
                protect_history_delete,
                sender=self.get_model(name),
                dispatch_uid=f"ledger-permanent-{name}",
            )
        from .audit_tracking import connect_audit_signals

        connect_audit_signals()
