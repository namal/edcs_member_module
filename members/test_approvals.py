from decimal import Decimal
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from . import models as m
from .approval_services import decide


class ApprovalWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_member_transfers import MemberTransferTests

        MemberTransferTests.setUpTestData.__func__(cls)
        cls.requester = get_user_model().objects.create_user(
            "approval-requester", password="test-only-pass"
        )
        cls.requester.assigned_districts.add(
            *(office.district for office in cls.offices)
        )
        cls.requester.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        from .test_support import assign_member_ledgers

        assign_member_ledgers(cls.requester, [cls.member])
        cls.first = get_user_model().objects.create_user(
            "first-approver", password="test-only-pass", first_name="First Approver"
        )
        cls.second = get_user_model().objects.create_user(
            "second-approver", password="test-only-pass", first_name="Second Approver"
        )
        cls.outsider = get_user_model().objects.create_user("outside-approver")

    def setUp(self):
        self.client.force_login(self.requester)

    def hierarchy(
        self, module="paymenttransaction", action="change", *, rejection=True
    ):
        policy = m.ApprovalHierarchy.objects.create(
            name=f"Two levels for {module}", module=module, action=action
        )
        for position, user in enumerate((self.first, self.second), 1):
            level = m.ApprovalLevel.objects.create(
                hierarchy=policy,
                position=position,
                name=f"Review {position}",
                allow_rejection=rejection,
            )
            level.approvers.add(user)
        return policy

    def entry_payload(self):
        return {
            "member": self.member.pk,
            "payment_type": self.payment_type.pk,
            "transaction_date": self.today.isoformat(),
            "credit": "50.00",
            "debit": "0",
            "reference": "PENDING-APPROVAL",
            "description": "Reviewed deposit",
        }

    def submit_entry(self):
        response = self.client.post(
            reverse("payment_transaction_edit", args=[self.old_entry.pk]),
            self.entry_payload(),
        )
        self.assertEqual(response.status_code, 302)
        record = m.ApprovalRequest.objects.latest("pk")
        self.assertRedirects(
            response, reverse("approval_request_detail", args=[record.pk])
        )
        return record

    def test_all_levels_required_and_business_write_applied_exactly_once(self):
        self.hierarchy()
        count = m.PaymentTransaction.objects.count()
        record = self.submit_entry()
        self.assertEqual(m.PaymentTransaction.objects.count(), count)
        self.old_entry.refresh_from_db()
        self.assertEqual(self.old_entry.credit, Decimal("200"))
        decide(self.actor, record.pk, "approve", "First level reviewed")
        record.refresh_from_db()
        self.assertEqual(record.current_step, 1)
        self.assertEqual(record.status, "pending")
        self.assertEqual(m.PaymentTransaction.objects.count(), count)
        decide(self.actor, record.pk, "approve", "Second level reviewed")
        record.refresh_from_db()
        self.assertEqual(record.status, "approved")
        self.assertEqual(m.PaymentTransaction.objects.count(), count)
        entry = m.PaymentTransaction.objects.get(reference="PENDING-APPROVAL")
        self.assertEqual(entry.pk, self.old_entry.pk)
        self.assertEqual(entry.credit, Decimal("50"))
        event = m.LedgerEvent.objects.get(
            record__source_model="members.paymenttransaction",
            record__source_id=entry.pk,
            action="updated",
        )
        self.assertEqual(event.actor_id, self.requester.pk)
        self.assertEqual(
            list(record.audit_events.values_list("action", flat=True)),
            ["submitted", "approve", "approve", "applied"],
        )
        with self.assertRaises(PermissionDenied):
            decide(self.second, record.pk, "approve")
        self.assertEqual(m.PaymentTransaction.objects.count(), count)

    def test_duplicate_submission_reuses_pending_request(self):
        self.hierarchy()
        first = self.submit_entry()
        second = self.submit_entry()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(m.ApprovalRequest.objects.count(), 1)
        self.assertEqual(first.audit_events.count(), 1)

    def test_invalid_form_does_not_create_approval_request(self):
        self.hierarchy()
        response = self.client.post(
            reverse("payment_transaction_edit", args=[self.old_entry.pk]),
            {**self.entry_payload(), "credit": "invalid"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def test_only_current_designated_user_can_decide_and_cannot_self_approve(self):
        policy = self.hierarchy()
        policy.levels.first().approvers.add(self.requester)
        record = self.submit_entry()
        for user in (self.outsider, self.second, self.requester):
            with self.assertRaises(PermissionDenied):
                decide(user, record.pk, "approve")
        self.assertEqual(record.audit_events.count(), 1)

    def test_level_can_reserve_rejections_for_administrator(self):
        self.hierarchy(rejection=False)
        record = self.submit_entry()
        with self.assertRaises(PermissionDenied):
            decide(self.first, record.pk, "reject", "No rejection right")
        decide(self.actor, record.pk, "reject", "Administrator review")
        record.refresh_from_db()
        self.assertEqual(record.status, "rejected")
        self.assertTrue(record.audit_events.last().is_override)

    def test_rejection_and_cancellation_do_not_apply_change(self):
        self.hierarchy()
        record = self.submit_entry()
        with self.assertRaises(ValidationError):
            decide(self.actor, record.pk, "reject")
        decide(self.actor, record.pk, "reject", "Incorrect supporting information")
        self.assertFalse(
            m.PaymentTransaction.objects.filter(reference="PENDING-APPROVAL").exists()
        )
        other = self.submit_entry()
        decide(self.requester, other.pk, "cancel", "Correcting submission")
        other.refresh_from_db()
        self.assertEqual(other.status, "cancelled")

    def test_administrator_can_approve_remaining_levels(self):
        self.hierarchy()
        record = self.submit_entry()
        with self.assertRaises(ValidationError):
            decide(self.actor, record.pk, "override_approve")
        decide(self.actor, record.pk, "override_approve", "Authorized urgent posting")
        record.refresh_from_db()
        self.assertEqual(record.status, "approved")
        event = record.audit_events.get(action="override_approve")
        self.assertTrue(event.is_override)
        self.assertEqual(event.actor_id, self.actor.pk)
        self.assertEqual(event.reason, "Authorized urgent posting")

    def test_ordinary_user_cannot_override(self):
        self.hierarchy()
        record = self.submit_entry()
        with self.assertRaises(PermissionDenied):
            decide(self.first, record.pk, "override_approve", "Not an administrator")

    def test_request_visibility_and_assignment_permissions(self):
        self.hierarchy()
        record = self.submit_entry()
        for user, expected in (
            (self.first, 404),
            (self.second, 404),
            (self.actor, 200),
            (self.outsider, 404),
        ):
            client = Client()
            client.force_login(user)
            self.assertEqual(
                client.get(
                    reverse("approval_request_detail", args=[record.pk])
                ).status_code,
                expected,
            )
        self.assertEqual(
            self.client.get(reverse("approval_hierarchy_list")).status_code, 403
        )
        self.assertEqual(
            self.client.post(reverse("approval_hierarchy_create"), {}).status_code, 403
        )

    def test_revoked_requester_permissions_are_rechecked_before_application(self):
        self.hierarchy()
        record = self.submit_entry()
        self.requester.user_permissions.clear()
        decide(
            self.actor, record.pk, "override_approve", "Review after permission change"
        )
        record.refresh_from_db()
        self.assertEqual(record.status, "failed")
        self.assertFalse(
            m.PaymentTransaction.objects.filter(reference="PENDING-APPROVAL").exists()
        )
        self.assertTrue(
            record.audit_events.filter(action="application_failed").exists()
        )

    def test_stale_record_cannot_be_overwritten_after_approval(self):
        self.hierarchy(action="change")
        payload = {**self.entry_payload(), "reference": "EDIT-APPROVAL"}
        response = self.client.post(
            reverse("payment_transaction_edit", args=[self.old_entry.pk]), payload
        )
        self.assertEqual(response.status_code, 302)
        record = m.ApprovalRequest.objects.get()
        self.old_entry.description = "Another legitimate update"
        self.old_entry.save()
        decide(self.actor, record.pk, "override_approve", "Final review")
        record.refresh_from_db()
        self.assertEqual(record.status, "failed")
        self.old_entry.refresh_from_db()
        self.assertEqual(self.old_entry.reference, "BEFORE-TRANSFER")
        self.assertEqual(self.old_entry.description, "Another legitimate update")

    def test_inactive_approver_cannot_decide(self):
        self.hierarchy()
        record = self.submit_entry()
        self.first.is_active = False
        self.first.save()
        with self.assertRaises(PermissionDenied):
            decide(self.first, record.pk, "approve")

    def test_no_policy_keeps_existing_immediate_behavior(self):
        response = self.client.post(
            reverse("payment_transaction_create"), self.entry_payload()
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            m.PaymentTransaction.objects.filter(reference="PENDING-APPROVAL").exists()
        )
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def test_specific_action_takes_priority_over_all_changes(self):
        self.hierarchy(action="*")
        specific = self.hierarchy(action="change")
        record = self.submit_entry()
        self.assertEqual(record.hierarchy_id, specific.pk)

    def test_one_global_hierarchy_covers_edits_across_modules(self):
        policy = self.hierarchy(module="all_modules")
        entry_request = self.submit_entry()
        self.assertEqual(entry_request.hierarchy_id, policy.pk)
        response = self.client.post(
            reverse("payment_plan_edit", args=[self.plan.pk]),
            {"agreement_date": "2025-01-01", f"amount_{self.payment_type.pk}": "75"},
        )
        self.assertEqual(response.status_code, 302)
        plan_request = m.ApprovalRequest.objects.latest("pk")
        self.assertEqual(plan_request.hierarchy_id, policy.pk)
        self.assertEqual(plan_request.module, "paymentplan")
        self.assertEqual(
            self.plan.amounts.get(payment_type=self.payment_type).amount, Decimal("25")
        )
        decide(self.actor, plan_request.pk, "override_approve", "Plan edit verified")
        plan_request.refresh_from_db()
        self.assertEqual(plan_request.status, "approved", plan_request.outcome)

    def test_module_specific_edit_hierarchy_overrides_global_hierarchy(self):
        self.hierarchy(module="all_modules")
        specific = self.hierarchy()
        self.assertEqual(self.submit_entry().hierarchy_id, specific.pk)

    def test_creates_and_deletes_skip_global_and_legacy_wildcard_hierarchies(self):
        self.hierarchy(module="all_modules")
        self.hierarchy(action="*")
        self.client.post(reverse("payment_transaction_create"), self.entry_payload())
        entry = m.PaymentTransaction.objects.get(reference="PENDING-APPROVAL")
        self.assertFalse(m.ApprovalRequest.objects.exists())
        response = self.client.post(
            reverse("payment_transaction_delete", args=[entry.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(m.PaymentTransaction.objects.filter(pk=entry.pk).exists())
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def test_pending_assignment_snapshot_survives_configuration_change(self):
        policy = self.hierarchy()
        record = self.submit_entry()
        policy.levels.first().approvers.set([self.outsider])
        policy.is_active = False
        policy.save()
        decide(self.actor, record.pk, "approve", "Submitted hierarchy reviewed")
        record.refresh_from_db()
        self.assertEqual(record.current_step, 1)
        self.assertEqual(record.steps[0]["approvers"][0]["id"], self.first.pk)

    def test_transfer_is_not_applied_until_final_approval(self):
        self.hierarchy(module="membertransfer", action="add")
        payload = {
            "member": self.member.pk,
            "source_ledger": self.original.pk,
            "district": self.offices[1].district_id,
            "zonal_office": self.offices[1].pk,
            "lager": self.lagers[1].pk,
            "school": self.schools[1].pk,
            "maintenance_officer": self.officers[1].pk,
            "effective_date": self.today.isoformat(),
            "reason": "New workplace",
        }
        response = self.client.post(reverse("member_transfer_create"), payload)
        self.assertEqual(response.status_code, 302)
        record = m.ApprovalRequest.objects.get()
        self.assertFalse(m.MemberTransfer.objects.exists())
        self.member.refresh_from_db()
        self.assertEqual(self.member.office_location_id, self.offices[0].pk)
        decide(self.actor, record.pk, "override_approve", "Verified receiving officer")
        record.refresh_from_db()
        self.assertEqual(record.status, "approved", record.outcome)
        self.assertEqual(m.MemberTransfer.objects.count(), 1)
        self.member.refresh_from_db()
        self.assertEqual(self.member.office_location_id, self.offices[1].pk)

    def test_child_entry_edits_and_payment_plan_amounts_wait_for_approval(self):
        self.hierarchy(module="childdeposit")
        response = self.client.post(
            reverse("child_entry_edit", args=[self.old_child_entry.pk]),
            {
                "transaction_date": self.today.isoformat(),
                "credit": "10",
                "debit": "0",
                "reference": "CHILD-APPROVAL",
            },
        )
        self.assertEqual(response.status_code, 302)
        record = m.ApprovalRequest.objects.get()
        self.assertFalse(
            m.ChildDepositTransaction.objects.filter(
                reference="CHILD-APPROVAL"
            ).exists()
        )
        decide(self.actor, record.pk, "override_approve", "Child deposit verified")
        record.refresh_from_db()
        self.assertEqual(record.status, "approved", record.outcome)
        self.assertTrue(
            m.ChildDepositTransaction.objects.filter(
                reference="CHILD-APPROVAL"
            ).exists()
        )
        self.hierarchy(module="paymentplan", action="change")
        response = self.client.post(
            reverse("payment_plan_edit", args=[self.plan.pk]),
            {"agreement_date": "2025-01-01", f"amount_{self.payment_type.pk}": "75"},
        )
        self.assertEqual(response.status_code, 302)
        plan_request = m.ApprovalRequest.objects.latest("pk")
        self.assertEqual(
            self.plan.amounts.get(payment_type=self.payment_type).amount, Decimal("25")
        )
        decide(
            self.actor, plan_request.pk, "override_approve", "Plan change authorized"
        )
        plan_request.refresh_from_db()
        self.assertEqual(plan_request.status, "approved", plan_request.outcome)
        self.assertEqual(
            self.plan.amounts.get(payment_type=self.payment_type).amount, Decimal("75")
        )

    def test_approval_requests_and_audits_cannot_be_rewritten_or_deleted(self):
        self.hierarchy()
        record = self.submit_entry()
        record.status = "approved"
        with self.assertRaises(ValidationError):
            record.save()
        event = record.audit_events.first()
        event.reason = "Rewritten"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ProtectedError):
            m.ApprovalRequest.objects.filter(pk=record.pk).delete()
        with self.assertRaises(ProtectedError):
            event.delete()

    def test_template_defaults_are_frozen_at_submission(self):
        template = m.PaymentPlanTemplate.objects.create(name="Approved default plan")
        amount = m.PaymentPlanTemplateAmount.objects.create(
            template=template, payment_type=self.payment_type, amount=Decimal("60")
        )
        self.hierarchy(module="paymentplan", action="change")
        response = self.client.post(
            reverse("payment_plan_edit", args=[self.plan.pk]),
            {"agreement_date": "2025-01-01", "apply_template": template.pk},
        )
        self.assertEqual(response.status_code, 302)
        record = m.ApprovalRequest.objects.get()
        amount.amount = Decimal("90")
        amount.save()
        decide(
            self.actor,
            record.pk,
            "override_approve",
            "Original submitted amounts approved",
        )
        record.refresh_from_db()
        self.assertEqual(record.status, "approved", record.outcome)
        self.assertEqual(
            self.plan.amounts.get(payment_type=self.payment_type).amount, Decimal("60")
        )

    def test_retirement_and_opening_balance_changes_require_approval(self):
        self.hierarchy(module="member", action="change")
        response = self.client.post(
            reverse("member_retire", args=[self.member.pk]),
            {"retirement_date": self.today.isoformat()},
        )
        self.assertEqual(response.status_code, 302)
        record = m.ApprovalRequest.objects.get()
        self.member.refresh_from_db()
        self.assertNotEqual(self.member.status, "retired")
        decide(self.actor, record.pk, "override_approve", "Early retirement authorized")
        record.refresh_from_db()
        self.assertEqual(record.status, "approved", record.outcome)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "retired")
        self.hierarchy(module="openingbalance", action="change")
        opening = self.member.opening_balance
        response = self.client.post(
            reverse("opening_balance_edit", args=[opening.pk]),
            {"balance_date": "2025-01-01", f"amount_{self.payment_type.pk}": "1500"},
        )
        self.assertEqual(response.status_code, 302)
        record = m.ApprovalRequest.objects.latest("pk")
        self.assertEqual(
            opening.amounts.get(payment_type=self.payment_type).amount, Decimal("1000")
        )
        decide(self.actor, record.pk, "override_approve", "Verified opening amount")
        record.refresh_from_db()
        self.assertEqual(record.status, "approved", record.outcome)
        self.assertEqual(
            opening.amounts.get(payment_type=self.payment_type).amount, Decimal("1500")
        )

    def test_deletion_remains_immediate_even_with_a_legacy_delete_policy(self):
        self.hierarchy(action="delete")
        response = self.client.post(
            reverse("payment_transaction_delete", args=[self.old_entry.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(m.ApprovalRequest.objects.exists())
        self.assertFalse(
            m.PaymentTransaction.objects.filter(pk=self.old_entry.pk).exists()
        )

    def test_member_and_child_annual_interest_postings_wait_for_approval(self):
        from .deposit_interest import save_interest_year, calculation_fingerprint
        from .child_interest import (
            save_child_interest_year,
            child_calculation_fingerprint,
        )

        self.hierarchy(module="depositinterestyear", action="change")
        self.payment_type.interest_rate = Decimal("8")
        self.payment_type.save()
        member_year = save_interest_year(
            self.member, self.payment_type, date(2025, 1, 1), today=self.today
        )
        child_year = save_child_interest_year(
            self.child_account, date(2025, 1, 1), today=self.today
        )
        for year, route, fingerprint in (
            (member_year, "deposit_interest_edit", calculation_fingerprint),
            (child_year, "child_interest_edit", child_calculation_fingerprint),
        ):
            with self.subTest(route=route):
                response = self.client.post(
                    reverse(route, args=[year.pk]),
                    {"calculation_hash": fingerprint(year)},
                )
                self.assertEqual(response.status_code, 302)
                record = m.ApprovalRequest.objects.latest("pk")
                year.refresh_from_db()
                self.assertIsNone(year.posted_at)
                decide(
                    self.actor,
                    record.pk,
                    "override_approve",
                    "Annual calculation verified",
                )
                record.refresh_from_db()
                self.assertEqual(record.status, "approved", record.outcome)
                year.refresh_from_db()
                self.assertIsNotNone(year.posted_at)

    def test_new_loans_and_repayments_are_immediate_but_settlement_needs_approval(self):
        policy = m.FixDepositPolicy.objects.first()
        policy.loan_interest_rate = Decimal("8")
        policy.save()
        m.OpeningBalanceAmount.objects.create(
            opening_balance=self.member.opening_balance,
            payment_type=policy.payment_type,
            amount=Decimal("1000"),
        )
        account = m.FixDepositAccount.objects.create(
            member=self.member, policy=policy, cycle_started_on=date(2020, 1, 1)
        )
        self.hierarchy(module="fixdepositaccount", action="*")
        response = self.client.post(
            reverse("fix_loan_create", args=[account.pk]),
            {"day": self.today.isoformat(), "amount": "100", "notes": "Member loan"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(m.ApprovalRequest.objects.exists())
        loan = account.loans.get()
        from uuid import uuid4

        response = self.client.post(
            reverse("fix_loan_entry", args=[loan.pk]),
            {
                "day": self.today.isoformat(),
                "kind": "repayment",
                "principal": "100",
                "interest": "0",
                "operation_key": str(uuid4()),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(loan.entries.count(), 1)
        self.assertFalse(m.ApprovalRequest.objects.exists())
        response = self.client.post(
            reverse("fix_loan_settle", args=[loan.pk]),
            {"day": self.today.isoformat(), "interest_confirmed": "on"},
        )
        self.assertEqual(response.status_code, 302)
        record = m.ApprovalRequest.objects.get()
        loan.refresh_from_db()
        self.assertIsNone(loan.settled_on)
        decide(
            self.actor,
            record.pk,
            "override_approve",
            "Existing loan settlement verified",
        )
        record.refresh_from_db()
        self.assertEqual(record.status, "approved", record.outcome)
        loan.refresh_from_db()
        self.assertIsNotNone(loan.settled_on)

    def hierarchy_payload(self, **overrides):
        data = {
            "name": "Deposit authorization",
            "module": "paymenttransaction",
            "action": "change",
            "is_active": "on",
            "version": "0",
            "reason": "New organizational approval policy",
            "levels-TOTAL_FORMS": "2",
            "levels-INITIAL_FORMS": "0",
            "levels-MIN_NUM_FORMS": "0",
            "levels-MAX_NUM_FORMS": "20",
            "levels-0-position": "1",
            "levels-0-name": "Office review",
            "levels-0-approvers": [self.first.pk],
            "levels-0-allow_rejection": "on",
            "levels-1-position": "2",
            "levels-1-name": "Finance review",
            "levels-1-approvers": [self.second.pk],
            "levels-1-allow_rejection": "on",
        }
        data.update(overrides)
        return data

    def test_administrator_configures_levels_and_changes_are_audited(self):
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse("approval_hierarchy_create"), self.hierarchy_payload()
        )
        self.assertRedirects(response, reverse("approval_hierarchy_list"))
        policy = m.ApprovalHierarchy.objects.get()
        self.assertEqual(policy.levels.count(), 2)
        self.assertEqual(
            policy.audit_events.get().details["after"]["levels"][1]["name"],
            "Finance review",
        )
        payload = self.hierarchy_payload(
            version="1",
            **{"name": "Updated authorization", "levels-INITIAL_FORMS": "2"},
        )
        self.assertEqual(
            self.client.post(
                reverse("approval_hierarchy_edit", args=[policy.pk]), payload
            ).status_code,
            302,
        )
        policy.refresh_from_db()
        self.assertEqual(policy.version, 2)
        self.assertEqual(policy.audit_events.count(), 2)
        self.assertContains(
            self.client.get(reverse("approval_hierarchy_history", args=[policy.pk])),
            "Updated authorization",
        )
        # A stale admin page cannot overwrite the newer configuration.
        response = self.client.post(
            reverse("approval_hierarchy_edit", args=[policy.pk]), payload
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "updated by another administrator")

    def test_configuration_rejects_duplicate_orders_and_missing_approvers(self):
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse("approval_hierarchy_create"),
            self.hierarchy_payload(**{"levels-1-position": "1"}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(m.ApprovalHierarchy.objects.exists())

    def test_configuration_allows_only_transfers_and_existing_record_edits(self):
        self.client.force_login(self.actor)
        for module, action in (
            ("paymenttransaction", "add"),
            ("paymenttransaction", "delete"),
            ("paymenttransaction", "*"),
            ("all_modules", "add"),
            ("membertransfer", "change"),
        ):
            with self.subTest(module=module, action=action):
                response = self.client.post(
                    reverse("approval_hierarchy_create"),
                    self.hierarchy_payload(module=module, action=action),
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(m.ApprovalHierarchy.objects.exists())
        response = self.client.post(
            reverse("approval_hierarchy_create"),
            self.hierarchy_payload(module="membertransfer", action="add"),
        )
        self.assertRedirects(response, reverse("approval_hierarchy_list"))
        self.assertEqual(m.ApprovalHierarchy.objects.get().module, "membertransfer")

    def test_configuration_rejects_missing_approvers(self):
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse("approval_hierarchy_create"),
            self.hierarchy_payload(**{"levels-0-approvers": []}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(m.ApprovalHierarchy.objects.exists())

    def test_advanced_admin_edits_immediately_with_configured_approval(self):
        self.hierarchy()
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse(
                "admin:members_paymenttransaction_change", args=[self.old_entry.pk]
            ),
            self.entry_payload(),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            m.PaymentTransaction.objects.filter(reference="PENDING-APPROVAL").exists()
        )
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def test_advanced_admin_creation_is_not_blocked_by_edit_hierarchy(self):
        self.hierarchy(module="all_modules")
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse("admin:members_paymenttransaction_add"), self.entry_payload()
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            m.PaymentTransaction.objects.filter(reference="PENDING-APPROVAL").exists()
        )
        self.assertFalse(m.ApprovalRequest.objects.exists())


class ConcurrentApprovalTests(TransactionTestCase):
    def test_two_final_approvers_apply_the_business_change_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections

        author = get_user_model().objects.create_user(
            "concurrent-author", password="test-only-pass"
        )
        author.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        users = [
            get_user_model().objects.create_user(f"concurrent-reviewer-{index}")
            for index in range(2)
        ]
        payment_type = m.PaymentType.objects.create(name="Concurrent contribution")
        hierarchy = m.ApprovalHierarchy.objects.create(
            name="Concurrent approval", module="paymentplantemplate", action="change"
        )
        level = m.ApprovalLevel.objects.create(
            hierarchy=hierarchy, position=1, name="Final approval"
        )
        level.approvers.set(users)
        client = Client()
        client.force_login(author)
        template = m.PaymentPlanTemplate.objects.create(name="Original concurrent plan")
        response = client.post(
            reverse("payment_plan_template_edit", args=[template.pk]),
            {
                "name": "Apply once",
                "is_active": "on",
                f"amount_{payment_type.pk}": "20",
            },
        )
        self.assertEqual(response.status_code, 302)
        record = m.ApprovalRequest.objects.get()
        barrier = Barrier(2)

        def approve(user_id):
            connections.close_all()
            try:
                actor = get_user_model().objects.get(pk=user_id)
                barrier.wait(timeout=10)
                try:
                    return decide(actor, record.pk, "approve").status
                except PermissionDenied:
                    return "already_decided"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(approve, [user.pk for user in users]))
        self.assertCountEqual(outcomes, ["approved", "already_decided"])
        self.assertEqual(
            m.PaymentPlanTemplate.objects.filter(name="Apply once").count(), 1
        )
        self.assertEqual(record.audit_events.filter(action="approve").count(), 1)
        self.assertEqual(record.audit_events.filter(action="applied").count(), 1)
