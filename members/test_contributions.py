from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from . import models as m
from .approval_forms import HierarchyForm
from .approval_scope import PAYMENT_EXCEPTIONS
from .approval_services import decide
from .contribution_forms import ContributionForm
from .contribution_services import allocation_preview, post_receipt
from .ledger_tracking import ledger_actor


def setup_records(cls):
    cls.today = timezone.localdate()
    cls.admin = get_user_model().objects.create_superuser(
        "allocation-admin", password="test-only-pass"
    )
    cls.operator = get_user_model().objects.create_user(
        "allocation-operator", password="test-only-pass"
    )
    cls.operator.user_permissions.set(
        Permission.objects.filter(
            content_type__app_label="members",
            codename__in=[
                "view_paymenttransaction",
                "add_paymenttransaction",
                "change_paymenttransaction",
            ],
        )
    )
    cls.district, _ = m.District.objects.get_or_create(
        code="COL",
        defaults={
            "name": "Colombo",
            "province": "Western",
            "province_short_name": "WP",
        },
    )
    cls.operator.assigned_districts.add(cls.district)
    cls.approvers = [
        get_user_model().objects.create_user(f"allocation-approver-{i}")
        for i in range(2)
    ]
    office = m.OfficeLocation.objects.create(
        district=cls.district, name="Allocation zone", code="ALLOC-Z"
    )
    school = m.School.objects.create(
        district=cls.district,
        zonal_office=office,
        name="Allocation department",
        code="ALLOC-D",
    )
    lager = m.Lager.objects.create(
        zonal_office=office, name="Allocation lager", code="ALLOC-L"
    )
    cls.member = m.Member.objects.create(
        district=cls.district,
        office_location=office,
        school=school,
        lager=lager,
        member_no="ALLOC-001",
        full_name="Allocation Member",
        salary="10000",
        office_address="Office",
        private_address="Home",
        phone_mobile="0771234567",
        gender="female",
        nic_no="199012345678",
        admit_date=date(2020, 1, 1),
    )
    cls.plan = m.PaymentPlan.objects.create(
        member=cls.member, agreement_date=date(cls.today.year, 1, 1)
    )
    cls.types = {}
    from .test_support import assign_member_ledgers

    assign_member_ledgers(cls.operator, [cls.member])
    for name, amount in [
        ("Fix Deposit", "200"),
        ("Special Deposit", "500"),
        ("Pension", "400"),
        ("Funeral", "100"),
        ("Scholarship", "300"),
        ("Share", "0"),
    ]:
        typ, _ = m.PaymentType.objects.get_or_create(name=name)
        cls.types[name] = typ
        m.PaymentPlanAmount.objects.create(
            plan=cls.plan, payment_type=typ, amount=amount
        )


class ContributionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        setup_records(cls)

    def setUp(self):
        self.client.force_login(self.operator)

    def payload(self, amount="3500.00", **overrides):
        return {
            "member": self.member.pk,
            "received_on": self.today.isoformat(),
            "amount": amount,
            "payment_type": "",
            "reference": "SALARY-001",
            "description": "Salary contribution",
            "operation_key": str(uuid4()),
            **overrides,
        }

    def preview(self, data):
        result = self.client.post(
            reverse("contribution_create"), {**data, "intent": "preview"}
        )
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.context["form"].errors, result.context["form"].errors)
        return {
            **data,
            "intent": "post",
            "review_hash": result.context["form"]["review_hash"].value(),
        }

    def post(self, data=None):
        return self.client.post(
            reverse("contribution_create"), self.preview(data or self.payload())
        )

    def policy(self):
        hierarchy = m.ApprovalHierarchy.objects.create(
            name="Below-plan payments", module=PAYMENT_EXCEPTIONS, action="add"
        )
        for i, approver in enumerate(self.approvers, 1):
            level = m.ApprovalLevel.objects.create(
                hierarchy=hierarchy,
                position=i,
                name=f"Review {i}",
                allow_rejection=True,
            )
            level.approvers.add(approver)
        return hierarchy

    def test_admin_below_plan_receipt_posts_without_hierarchy_or_approval(self):
        self.client.force_login(self.admin)
        response = self.post(self.payload("750"))
        self.assertEqual(response.status_code, 302)
        receipt = m.PaymentReceipt.objects.get()
        self.assertEqual(receipt.amount, Decimal("750"))
        self.assertEqual(receipt.created_by_id, self.admin.pk)
        self.assertIsNone(receipt.approval_request_id)
        self.assertEqual(receipt.allocations.count(), 5)
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def test_admin_below_plan_receipt_posts_immediately_with_active_hierarchy(self):
        self.policy()
        self.client.force_login(self.admin)
        data = self.payload("250")
        data["payment_type"] = self.types["Pension"].pk
        page = self.client.post(reverse("contribution_create"), data)
        self.assertContains(page, "Post contribution")
        self.assertNotContains(page, "Submit for approval")
        response = self.post(data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(m.PaymentReceipt.objects.get().amount, Decimal("250"))
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def test_admin_direct_below_plan_credit_does_not_queue(self):
        self.policy()
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("payment_transaction_create"),
            {
                "member": self.member.pk,
                "payment_type": self.types["Pension"].pk,
                "transaction_date": self.today,
                "credit": "250",
                "debit": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(m.PaymentTransaction.objects.get().credit, Decimal("250"))
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def approve(self, record):
        decide(self.admin, record.pk, "approve", "First level payment review")
        return decide(self.admin, record.pk, "approve", "Second level payment review")

    def test_example_3500_is_exact_and_uses_unrounded_ratios(self):
        response = self.post()
        self.assertEqual(response.status_code, 302)
        receipt = m.PaymentReceipt.objects.get()
        amounts = dict(receipt.allocations.values_list("payment_type__name", "amount"))
        self.assertEqual(
            amounts,
            {
                "Fix Deposit": Decimal("466.67"),
                "Special Deposit": Decimal("1166.67"),
                "Pension": Decimal("933.33"),
                "Funeral": Decimal("233.33"),
                "Scholarship": Decimal("700.00"),
            },
        )
        self.assertEqual(sum(amounts.values()), Decimal("3500.00"))
        self.assertEqual(
            m.PaymentTransaction.objects.aggregate(total=Sum("credit"))["total"],
            receipt.amount,
        )
        self.assertFalse(m.ApprovalRequest.objects.exists())
        self.assertEqual(
            m.LedgerEvent.objects.filter(
                record__source_model="members.paymenttransaction", actor=self.operator
            ).count(),
            5,
        )

    def test_equal_plan_posts_without_exception_or_global_edit_approval(self):
        self.policy()
        m.ApprovalHierarchy.objects.create(
            name="Global edits", module="all_modules", action="change"
        )
        self.assertEqual(self.post(self.payload("1500")).status_code, 302)
        self.assertEqual(m.PaymentReceipt.objects.get().amount, Decimal("1500"))
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def test_preview_has_no_financial_writes_and_excludes_zero_categories(self):
        self.preview(self.payload())
        self.assertEqual(
            len(allocation_preview(self.member, self.today, "3500")["rows"]), 5
        )
        self.assertFalse(m.PaymentTransaction.objects.exists())
        self.assertFalse(m.PaymentReceipt.objects.exists())

    def test_below_plan_waits_for_both_levels_then_posts_once(self):
        self.policy()
        self.assertEqual(self.post(self.payload("750")).status_code, 302)
        record = m.ApprovalRequest.objects.get()
        self.assertEqual(record.module, PAYMENT_EXCEPTIONS)
        self.assertFalse(m.PaymentReceipt.objects.exists())
        self.assertFalse(m.PaymentTransaction.objects.exists())
        decide(self.admin, record.pk, "approve", "First level payment review")
        self.assertFalse(m.PaymentTransaction.objects.exists())
        result = decide(self.admin, record.pk, "approve", "Second level payment review")
        self.assertEqual(result.status, "approved")
        receipt = m.PaymentReceipt.objects.get()
        self.assertEqual(receipt.approval_request_id, record.pk)
        self.assertEqual(receipt.created_by, self.operator)
        self.assertEqual(
            receipt.allocations.aggregate(total=Sum("amount"))["total"], Decimal("750")
        )
        with self.assertRaises(PermissionDenied):
            decide(self.approvers[1], record.pk, "approve")
        self.assertEqual(m.PaymentTransaction.objects.count(), 5)

    def test_single_category_pension_250_requires_approval(self):
        self.policy()
        self.post(self.payload("250", payment_type=self.types["Pension"].pk))
        record = m.ApprovalRequest.objects.get()
        self.assertEqual(self.approve(record).status, "approved")
        entry = m.PaymentTransaction.objects.get()
        self.assertEqual(entry.payment_type, self.types["Pension"])
        self.assertEqual(entry.credit, Decimal("250"))

    def test_single_category_special_equal_or_above_is_automatic(self):
        for amount in ("500", "800"):
            with self.subTest(amount=amount):
                self.assertEqual(
                    self.post(
                        self.payload(
                            amount, payment_type=self.types["Special Deposit"].pk
                        )
                    ).status_code,
                    302,
                )
        self.assertEqual(m.PaymentReceipt.objects.count(), 2)
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def test_missing_or_incomplete_hierarchy_cannot_post_underpayment(self):
        response = self.post(self.payload("750"))
        self.assertContains(
            response, "Configure an active Payment exceptions hierarchy"
        )
        self.assertFalse(m.PaymentReceipt.objects.exists())
        m.ApprovalHierarchy.objects.create(
            name="Unconfigured", module=PAYMENT_EXCEPTIONS, action="add"
        )
        self.assertEqual(self.post(self.payload("750")).status_code, 403)
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_rejection_and_cancellation_never_post_money(self):
        self.policy()
        for decision in ("reject", "cancel"):
            self.post(self.payload("750"))
            record = m.ApprovalRequest.objects.latest("pk")
            actor = self.admin if decision == "reject" else self.operator
            decide(actor, record.pk, decision, "Payment withdrawn from processing")
        self.assertFalse(m.PaymentReceipt.objects.exists())
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_admin_can_approve_an_exception_at_any_level_with_reason(self):
        self.policy()
        self.post(self.payload("750"))
        record = m.ApprovalRequest.objects.get()
        with self.assertRaises(ValidationError):
            decide(self.admin, record.pk, "override_approve")
        result = decide(
            self.admin, record.pk, "override_approve", "Approved financial exception"
        )
        self.assertEqual(result.status, "approved")
        self.assertTrue(
            record.audit_events.filter(
                is_override=True, reason="Approved financial exception"
            ).exists()
        )

    def test_plan_change_during_approval_fails_without_credits(self):
        self.policy()
        self.post(self.payload("750"))
        row = self.plan.amounts.get(payment_type=self.types["Pension"])
        row.amount = 450
        row.save()
        self.assertEqual(self.approve(m.ApprovalRequest.objects.get()).status, "failed")
        self.assertFalse(m.PaymentReceipt.objects.exists())
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_inactive_category_during_approval_stops_application(self):
        self.policy()
        self.post(self.payload("750"))
        self.types["Pension"].is_active = False
        self.types["Pension"].save()
        self.assertEqual(self.approve(m.ApprovalRequest.objects.get()).status, "failed")
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_revoked_district_or_action_stops_final_approval(self):
        self.policy()
        self.post(self.payload("750"))
        self.operator.assigned_districts.clear()
        self.assertEqual(self.approve(m.ApprovalRequest.objects.get()).status, "failed")
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_duplicate_post_and_pending_submission_are_idempotent(self):
        data = self.preview(self.payload())
        for _ in range(2):
            self.assertEqual(
                self.client.post(reverse("contribution_create"), data).status_code, 302
            )
        self.assertEqual(m.PaymentReceipt.objects.count(), 1)
        self.assertEqual(m.PaymentTransaction.objects.count(), 5)
        self.policy()
        below = self.preview(self.payload("750"))
        for _ in range(2):
            self.client.post(reverse("contribution_create"), below)
        self.assertEqual(m.ApprovalRequest.objects.count(), 1)

    def test_conflicting_operation_key_cannot_reuse_a_receipt(self):
        data = self.payload()
        self.post(data)
        response = self.post({**data, "amount": "4000"})
        self.assertContains(response, "already used")
        self.assertEqual(m.PaymentReceipt.objects.count(), 1)

    def test_changed_amount_or_plan_requires_another_preview(self):
        data = self.preview(self.payload())
        response = self.client.post(
            reverse("contribution_create"), {**data, "amount": "4000"}
        )
        self.assertContains(response, "Preview the allocation again")
        self.assertFalse(m.PaymentReceipt.objects.exists())
        row = self.plan.amounts.get(payment_type=self.types["Pension"])
        row.amount = 450
        row.save()
        response = self.client.post(reverse("contribution_create"), data)
        self.assertContains(response, "Preview the allocation again")

    def test_one_cent_is_exact_and_zero_allocations_do_not_create_zero_credits(self):
        self.policy()
        self.post(self.payload("0.01"))
        self.assertEqual(
            self.approve(m.ApprovalRequest.objects.get()).status, "approved"
        )
        receipt = m.PaymentReceipt.objects.get()
        self.assertEqual(receipt.allocations.count(), 5)
        self.assertEqual(m.PaymentTransaction.objects.count(), 1)
        self.assertEqual(m.PaymentTransaction.objects.get().credit, Decimal("0.01"))

    def test_partial_post_failure_rolls_back_receipt_entries_and_events(self):
        save = m.PaymentTransaction.save

        def failing(entry, *args, **kwargs):
            if entry.payment_type_id == self.types["Pension"].pk:
                raise ValidationError("Account year is closed")
            return save(entry, *args, **kwargs)

        with patch.object(m.PaymentTransaction, "save", failing):
            self.assertContains(self.post(), "Account year is closed")
        self.assertFalse(m.PaymentReceipt.objects.exists())
        self.assertFalse(m.PaymentReceiptAllocation.objects.exists())
        self.assertFalse(m.PaymentTransaction.objects.exists())
        self.assertFalse(
            m.LedgerEvent.objects.filter(
                record__source_model="members.paymenttransaction"
            ).exists()
        )

    def test_receipt_snapshot_does_not_change_with_plan_and_entries_are_immutable(self):
        self.post()
        receipt = m.PaymentReceipt.objects.get()
        snapshot = receipt.plan_snapshot
        row = self.plan.amounts.get(payment_type=self.types["Pension"])
        row.amount = 999
        row.save()
        receipt.refresh_from_db()
        self.assertEqual(receipt.plan_snapshot, snapshot)
        with self.assertRaises(ValidationError):
            receipt.save()
        with self.assertRaises(ProtectedError):
            m.PaymentReceipt.objects.filter(pk=receipt.pk).delete()
        entry = m.PaymentTransaction.objects.first()
        entry.credit = 1
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ProtectedError):
            entry.delete()
        self.assertEqual(
            self.client.get(
                reverse("payment_transaction_edit", args=[entry.pk])
            ).status_code,
            403,
        )

    def test_direct_credit_form_cannot_bypass_below_plan_approval(self):
        self.policy()
        response = self.client.post(
            reverse("payment_transaction_create"),
            {
                "member": self.member.pk,
                "payment_type": self.types["Special Deposit"].pk,
                "transaction_date": self.today,
                "credit": "300",
                "debit": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(m.PaymentTransaction.objects.exists())
        self.assertEqual(
            self.approve(m.ApprovalRequest.objects.get()).status, "approved"
        )
        self.assertEqual(m.PaymentTransaction.objects.get().credit, Decimal("300"))
        record = m.ApprovalRequest.objects.get()
        event = m.LedgerEvent.objects.get(
            record__source_model="members.paymenttransaction", action="created"
        )
        self.assertEqual(event.snapshot["approval_reference"], record.reference)
        self.assertEqual(event.snapshot["approval_request_id"], record.pk)

    def test_plan_with_receipts_is_retained_without_an_error_page(self):
        self.post()
        self.operator.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="members",
                codename__in=["view_paymentplan", "delete_paymentplan"],
            )
        )
        response = self.client.post(reverse("payment_plan_delete", args=[self.plan.pk]))
        self.assertRedirects(
            response, reverse("payment_plan_detail", args=[self.plan.pk])
        )
        self.assertTrue(m.PaymentPlan.objects.filter(pk=self.plan.pk).exists())
        self.assertEqual(m.PaymentReceipt.objects.count(), 1)

    def test_direct_credit_without_hierarchy_and_request_context_save_are_blocked(self):
        response = self.client.post(
            reverse("payment_transaction_create"),
            {
                "member": self.member.pk,
                "payment_type": self.types["Pension"].pk,
                "transaction_date": self.today,
                "credit": "250",
                "debit": "0",
            },
        )
        self.assertContains(response, "Configure an active Payment exceptions")
        token = ledger_actor.set(self.operator)
        try:
            with self.assertRaises(ValidationError):
                m.PaymentTransaction.objects.create(
                    member=self.member,
                    payment_type=self.types["Pension"],
                    transaction_date=self.today,
                    credit=250,
                )
        finally:
            ledger_actor.reset(token)

    def test_advanced_admin_allows_below_plan_and_disallows_receipt_mutation(
        self,
    ):
        from .admin import PaymentTransactionAdminForm

        form = PaymentTransactionAdminForm(
            {
                "member": self.member.pk,
                "payment_type": self.types["Pension"].pk,
                "transaction_date": self.today,
                "credit": "250",
                "debit": "0",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.post()
        entry = m.PaymentTransaction.objects.first()
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(
                reverse("admin:members_paymenttransaction_change", args=[entry.pk]), {}
            ).status_code,
            403,
        )

    def test_no_plan_zero_plan_inactive_or_unconfigured_category_are_rejected(self):
        self.assertFalse(
            ContributionForm(
                self.payload(payment_type=self.types["Share"].pk), user=self.operator
            ).is_valid()
        )
        self.types["Pension"].is_active = False
        self.types["Pension"].save()
        self.assertFalse(
            ContributionForm(self.payload(), user=self.operator).is_valid()
        )
        self.types["Pension"].is_active = True
        self.types["Pension"].save()
        self.plan.agreement_date = self.today + timedelta(days=1)
        self.plan.save()
        self.assertFalse(
            ContributionForm(self.payload(), user=self.operator).is_valid()
        )

    def test_invalid_money_dates_and_missing_review_cannot_post(self):
        for amount in ("0", "-1", "NaN", "Infinity", "1.001", "10000000000"):
            self.assertFalse(
                ContributionForm(self.payload(amount), user=self.operator).is_valid(),
                amount,
            )
        self.assertFalse(
            ContributionForm(
                self.payload(received_on=self.today + timedelta(days=1)),
                user=self.operator,
            ).is_valid()
        )
        self.assertContains(
            self.client.post(
                reverse("contribution_create"), {**self.payload(), "intent": "post"}
            ),
            "Preview the allocation again",
        )
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_action_and_district_access_and_csrf_are_enforced(self):
        viewer = get_user_model().objects.create_user("allocation-viewer")
        viewer.assigned_districts.add(self.district)
        viewer.user_permissions.set(
            Permission.objects.filter(codename="view_paymenttransaction")
        )
        self.client.force_login(viewer)
        self.assertEqual(
            self.client.get(reverse("contribution_create")).status_code, 403
        )
        self.client.force_login(self.operator)
        self.operator.assigned_districts.clear()
        self.assertFalse(
            ContributionForm(self.payload(), user=self.operator).is_valid()
        )
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(
            strict.post(reverse("contribution_create"), self.payload()).status_code, 403
        )

    def test_receipt_pages_render_and_allocations_keep_statement_balances(self):
        self.post()
        receipt = m.PaymentReceipt.objects.get()
        self.assertContains(
            self.client.get(reverse("contribution_detail", args=[receipt.pk])),
            "3500.00",
        )
        self.assertContains(self.client.get(reverse("contribution_list")), "SALARY-001")
        response = self.client.get(
            reverse("payment_transaction_list"),
            {"member": self.member.pk, "payment_type": self.types["Pension"].pk},
        )
        self.assertEqual(response.context["final_balance"], Decimal("933.33"))
        self.assertNotContains(
            response,
            reverse(
                "payment_transaction_edit",
                args=[
                    m.PaymentTransaction.objects.get(
                        payment_type=self.types["Pension"]
                    ).pk
                ],
            ),
        )
        self.operator.assigned_districts.clear()
        self.assertEqual(
            self.client.get(
                reverse("contribution_detail", args=[receipt.pk])
            ).status_code,
            404,
        )

    def test_payment_exception_hierarchy_is_configurable(self):
        form = HierarchyForm(
            {
                "name": "Payment exceptions",
                "module": PAYMENT_EXCEPTIONS,
                "action": "add",
                "is_active": "on",
                "reason": "Configure below-plan approval",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.client.force_login(self.admin)
        self.assertContains(
            self.client.get(reverse("approval_hierarchy_create")), "Payment exceptions"
        )


class ConcurrentContributionTests(TransactionTestCase):
    def setUp(self):
        setup_records(self)

    def test_concurrent_retry_posts_one_receipt(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock/concurrency integration test")
        data = {
            "member": self.member,
            "received_on": self.today,
            "amount": Decimal("3500"),
            "payment_type": None,
            "reference": "CONCURRENT",
            "description": "",
            "operation_key": uuid4(),
        }
        fingerprint = allocation_preview(self.member, self.today, "3500")["fingerprint"]

        def post():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.operator.pk)
                return post_receipt(actor, data, fingerprint).pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: post(), range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(m.PaymentReceipt.objects.count(), 1)
        self.assertEqual(m.PaymentTransaction.objects.count(), 5)
