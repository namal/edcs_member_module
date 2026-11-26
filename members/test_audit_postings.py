import json
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from . import models as m
from .audit_views import visible_logs
from .ledger_tracking import ledger_actor
from .test_contributions import setup_records
from .transaction_balances import member_balances


class AuditPostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        setup_records(cls)

    def setUp(self):
        self.client.force_login(self.operator)

    def entry(self, **kwargs):
        return m.PaymentTransaction.objects.create(
            member=self.member,
            payment_type=self.types["Share"],
            transaction_date=self.today,
            credit=Decimal("500"),
            **kwargs,
        )

    def test_deletion_is_disabled_for_all_users_and_all_model_paths(self):
        entry = self.entry()
        for actor in (self.operator, self.admin):
            self.client.force_login(actor)
            url = reverse("payment_transaction_delete", args=[entry.pk])
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url).status_code, 403)
        with self.assertRaises(ProtectedError):
            entry.delete()
        with self.assertRaises(ProtectedError):
            m.PaymentTransaction.objects.filter(pk=entry.pk).delete()
        with self.assertRaises(ValidationError):
            m.PaymentTransaction.objects.filter(pk=entry.pk).update(credit=Decimal("1"))
        self.assertTrue(m.PaymentTransaction.objects.filter(pk=entry.pk).exists())
        self.assertFalse(
            Permission.objects.filter(codename="delete_paymenttransaction").exists()
        )

    def test_edit_requires_permission_in_views_and_model_saves(self):
        entry = self.entry()
        self.operator.user_permissions.remove(
            Permission.objects.get(codename="change_paymenttransaction")
        )
        self.assertEqual(
            self.client.get(
                reverse("payment_transaction_edit", args=[entry.pk])
            ).status_code,
            403,
        )
        token = ledger_actor.set(get_user_model().objects.get(pk=self.operator.pk))
        try:
            entry.description = "Unauthorized edit"
            with self.assertRaises(PermissionDenied):
                entry.save()
        finally:
            ledger_actor.reset(token)
        entry.refresh_from_db()
        self.assertEqual(entry.description, "")

    def test_mistaken_credit_is_corrected_by_linked_debit_without_changing_original(
        self,
    ):
        entry = self.entry()
        page = self.client.get(
            reverse("payment_transaction_create"), {"reverse": entry.pk}
        )
        self.assertContains(page, "Correction reason")
        self.assertEqual(page.context["form"]["debit"].value(), Decimal("500"))
        response = self.client.post(
            reverse("payment_transaction_create"),
            {
                "reverses": entry.pk,
                "transaction_date": self.today,
                "description": "Duplicate contribution received",
                "reference": "CORRECT-1",
                "debit": "1",
                "credit": "999",
            },
        )
        self.assertEqual(response.status_code, 302)
        correction = m.PaymentTransaction.objects.get(reverses=entry)
        self.assertEqual(correction.debit, Decimal("500"))
        self.assertEqual(correction.credit, Decimal("0"))
        entry.refresh_from_db()
        self.assertEqual(entry.credit, Decimal("500"))
        self.assertEqual(member_balances(self.member, self.today)["total"], "0.00")
        self.assertTrue(
            m.AuditLog.objects.filter(
                source_model="members.paymenttransaction",
                source_id=correction.pk,
                actor_id=self.operator.pk,
            ).exists()
        )
        self.assertEqual(
            self.client.post(
                reverse("payment_transaction_create"),
                {
                    "reverses": entry.pk,
                    "transaction_date": self.today,
                    "description": "Double click",
                },
            ).status_code,
            200,
        )
        self.assertEqual(m.PaymentTransaction.objects.filter(reverses=entry).count(), 1)

    def test_mistaken_debit_correction_is_not_treated_as_a_below_plan_contribution(
        self,
    ):
        entry = m.PaymentTransaction.objects.create(
            member=self.member,
            payment_type=self.types["Pension"],
            transaction_date=self.today,
            debit=Decimal("100"),
        )
        response = self.client.post(
            reverse("payment_transaction_create"),
            {
                "reverses": entry.pk,
                "transaction_date": self.today,
                "description": "Debit entered by mistake",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            m.PaymentTransaction.objects.get(reverses=entry).credit, Decimal("100")
        )
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def test_correction_reason_and_exact_opposite_are_enforced(self):
        entry = self.entry()
        self.assertEqual(
            self.client.post(
                reverse("payment_transaction_create"),
                {
                    "reverses": entry.pk,
                    "transaction_date": self.today,
                    "description": "",
                },
            ).status_code,
            200,
        )
        with self.assertRaises(ValidationError):
            m.PaymentTransaction.objects.create(
                member=self.member,
                payment_type=entry.payment_type,
                reverses=entry,
                debit=Decimal("499"),
                description="Incorrect correction",
            )
        self.assertFalse(m.PaymentTransaction.objects.filter(reverses=entry).exists())

    def test_child_entries_also_use_opposite_corrections_and_cannot_be_deleted(self):
        self.client.force_login(self.admin)
        child = m.Child.objects.create(
            member=self.member, name="Audit child", date_of_birth=date(2010, 1, 1)
        )
        typ = m.ChildDepositType.objects.create(name="Audit child savings")
        deposit = m.ChildDeposit.objects.create(
            child=child, deposit_type=typ, opened_on=self.today
        )
        entry = m.ChildDepositTransaction.objects.create(
            deposit=deposit, transaction_date=self.today, credit=Decimal("100")
        )
        self.assertEqual(
            self.client.post(
                reverse("child_entry_delete", args=[entry.pk])
            ).status_code,
            403,
        )
        with self.assertRaises(ProtectedError):
            entry.delete()
        response = self.client.post(
            reverse("child_entry_create", args=[deposit.pk]),
            {
                "reverses": entry.pk,
                "transaction_date": self.today,
                "description": "Wrong child credit",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            m.ChildDepositTransaction.objects.get(reverses=entry).debit, Decimal("100")
        )

    def test_user_creation_access_changes_and_password_changes_are_logged_without_secrets(
        self,
    ):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("access_user_create"),
            {
                "username": "audited-new-user",
                "first_name": "Audited",
                "is_active": "on",
                "password1": "New-Test-Secret-4192!",
                "password2": "New-Test-Secret-4192!",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="audited-new-user")
        event = m.AuditLog.objects.get(
            source_model="auth.user", source_id=user.pk, action="created"
        )
        self.assertEqual(event.actor_id, self.admin.pk)
        self.assertEqual(event.request_path, reverse("access_user_create"))
        role = Group.objects.create(name="Audit role")
        token = ledger_actor.set(self.admin)
        try:
            user.groups.add(role)
            user.set_password("Replacement-Secret-514!")
            user.save()
        finally:
            ledger_actor.reset(token)
        records = m.AuditLog.objects.filter(source_model="auth.user", source_id=user.pk)
        self.assertTrue(records.filter(action="access_changed").exists())
        self.assertTrue(
            records.filter(
                action="updated",
                changes__contains=[
                    {
                        "field": "password",
                        "label": "Password",
                        "before": "[redacted]",
                        "after": "[changed]",
                    }
                ],
            ).exists()
        )
        serialized = json.dumps(list(records.values("before", "after", "changes")))
        for secret in (
            "New-Test-Secret-4192!",
            "Replacement-Secret-514!",
            user.password,
        ):
            self.assertNotIn(secret, serialized)

    def test_member_master_transaction_and_bulk_member_changes_share_a_separate_audit_table(
        self,
    ):
        token = ledger_actor.set(self.admin)
        try:
            self.member.private_address = "Audited new home"
            self.member.save()
            m.Member.objects.filter(pk=self.member.pk).update(
                office_address="Audited office"
            )
            gender = m.Gender.objects.create(code="audit-gender", name="Audit gender")
            self.entry()
            gender.delete()
        finally:
            ledger_actor.reset(token)
        events = m.AuditLog.objects.filter(
            source_model="members.member", source_id=self.member.pk, action="updated"
        )
        self.assertTrue(
            events.filter(after__private_address__value="Audited new home").exists()
        )
        self.assertTrue(
            events.filter(after__office_address__value="Audited office").exists()
        )
        self.assertTrue(
            m.AuditLog.objects.filter(
                module="gender", action="deleted", actor_id=self.admin.pk
            ).exists()
        )
        self.assertTrue(
            m.AuditLog.objects.filter(
                module="paymenttransaction", action="created", actor_id=self.admin.pk
            ).exists()
        )

    def test_rollback_does_not_leave_audit_events_for_failed_business_changes(self):
        count = m.AuditLog.objects.count()
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.entry()
            raise RuntimeError("rollback")
        self.assertEqual(m.AuditLog.objects.count(), count)
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_audit_module_permission_scope_filters_and_read_only_history(self):
        self.assertEqual(self.client.get(reverse("audit_log_list")).status_code, 403)
        self.operator.user_permissions.add(
            Permission.objects.get(codename="view_auditlog")
        )
        entry = self.entry()
        event = m.AuditLog.objects.filter(
            source_model="members.paymenttransaction", source_id=entry.pk
        ).get()
        self.assertEqual(
            self.client.get(reverse("audit_log_detail", args=[event.pk])).status_code,
            200,
        )
        response = self.client.get(
            reverse("audit_log_list"),
            {"module": "paymenttransaction", "action": "created"},
        )
        self.assertContains(response, "View changes")
        self.assertFalse(
            visible_logs(self.operator).filter(module__in=("users", "roles")).exists()
        )
        self.operator.assigned_districts.clear()
        self.assertEqual(
            self.client.get(reverse("audit_log_detail", args=[event.pk])).status_code,
            404,
        )
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            m.AuditLog.objects.filter(pk=event.pk).update(actor_name="Changed")
        with self.assertRaises(ProtectedError):
            event.delete()
        with self.assertRaises(ProtectedError):
            m.AuditLog.objects.filter(pk=event.pk).delete()
        self.client.force_login(self.admin)
        for route in ("audit_log_edit", "audit_log_delete"):
            self.assertEqual(
                self.client.post(reverse(route, args=[event.pk])).status_code, 403
            )

    def test_security_logs_need_a_separate_explicit_grant(self):
        self.operator.user_permissions.add(
            *Permission.objects.filter(
                codename__in=("view_auditlog", "view_securityauditlog")
            )
        )
        self.assertTrue(visible_logs(self.operator).filter(module="users").exists())

    def test_audit_keeps_deleted_user_identity(self):
        token = ledger_actor.set(self.admin)
        try:
            user = get_user_model().objects.create_user("temporary-audit-user")
            pk = user.pk
            user.delete()
        finally:
            ledger_actor.reset(token)
        self.assertTrue(
            m.AuditLog.objects.filter(
                source_model="auth.user", source_id=pk, action="created"
            ).exists()
        )
        self.assertTrue(
            m.AuditLog.objects.filter(
                source_model="auth.user",
                source_id=pk,
                action="deleted",
                actor_id=self.admin.pk,
            ).exists()
        )

    def test_authorized_edit_records_actual_actor_and_before_after_amounts(self):
        entry = self.entry()
        response = self.client.post(
            reverse("payment_transaction_edit", args=[entry.pk]),
            {
                "member": self.member.pk,
                "payment_type": entry.payment_type_id,
                "transaction_date": self.today,
                "credit": "600",
                "debit": "0",
                "description": "Authorized amount correction",
            },
        )
        self.assertEqual(response.status_code, 302)
        event = m.AuditLog.objects.get(
            source_model="members.paymenttransaction",
            source_id=entry.pk,
            action="updated",
        )
        self.assertEqual(event.actor_id, self.operator.pk)
        self.assertEqual(event.before["credit"]["value"], "500.00")
        self.assertEqual(event.after["credit"]["value"], "600.00")

    def test_existing_audit_history_is_imported_with_original_time_and_actor(self):
        from django.apps import apps
        from django.db import connection
        from .migrations._audit_backfill import backfill_audit_logs

        token = ledger_actor.set(self.admin)
        try:
            self.member.private_address = "History import fixture"
            self.member.save()
        finally:
            ledger_actor.reset(token)
        original = m.MemberAuditEvent.objects.filter(member_pk=self.member.pk).first()
        with connection.schema_editor(atomic=False) as editor:
            backfill_audit_logs(apps, editor)
        imported = m.AuditLog.objects.get(
            origin="members.memberauditevent", origin_id=original.pk
        )
        self.assertEqual(imported.occurred_at, original.occurred_at)
        self.assertEqual(imported.actor_id, self.admin.pk)
        self.assertEqual(imported.changes, original.changes)

    def test_failed_write_request_rolls_back_business_and_audit_together(self):
        from django.http import HttpResponse
        from django.test import RequestFactory
        from .audit_tracking import AuditTransactionMiddleware

        before = m.AuditLog.objects.count()

        def rejected(request):
            self.entry()
            return HttpResponse(status=400)

        response = AuditTransactionMiddleware(rejected)(
            RequestFactory().post("/test-write/")
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(m.PaymentTransaction.objects.exists())
        self.assertEqual(m.AuditLog.objects.count(), before)

    def test_partial_user_save_does_not_invent_a_password_change(self):
        user = get_user_model().objects.get(pk=self.operator.pk)
        original_password = user.password
        user.set_password("Unsaved-password-445!")
        user.first_name = "Changed name only"
        user.save(update_fields=["first_name"])
        event = m.AuditLog.objects.filter(
            source_model="auth.user", source_id=user.pk, action="updated"
        ).first()
        self.assertNotIn("password", event.after)
        user.refresh_from_db()
        self.assertEqual(user.password, original_password)
