from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import models as m
from .approval_services import decide, decision_rights, visible_requests
from .ledger_tracking import ledger_actor
from .status_models import status_write
from .status_services import (
    submit_status_request,
    apply_due_status_requests,
    visible_status_requests,
)
from .test_support import assign_lagers


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MembershipStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.operator = User.objects.create_user(
            "status.operator", first_name="Requesting officer"
        )
        cls.reviewer = User.objects.create_user(
            "status.reviewer", first_name="Senior reviewer"
        )
        cls.second = User.objects.create_user(
            "status.second", first_name="Final reviewer"
        )
        cls.admin = User.objects.create_superuser("status.admin")
        cls.other = User.objects.create_user("status.other")
        cls.branch = m.District.objects.get(code="COL")
        cls.office = m.OfficeLocation.objects.create(
            district=cls.branch, name="Status office", code="STATUS-Z1"
        )
        cls.school = m.School.objects.create(
            district=cls.branch,
            zonal_office=cls.office,
            name="Status department",
            code="STATUS-D1",
        )
        cls.lager = m.Lager.objects.create(
            zonal_office=cls.office, name="Status assigned Lager", code="STATUS-L1"
        )
        cls.foreign_lager = m.Lager.objects.create(
            zonal_office=cls.office, name="Other assigned Lager", code="STATUS-L2"
        )
        for user, lager in ((cls.operator, cls.lager), (cls.other, cls.foreign_lager)):
            user.assigned_districts.add(cls.branch)
            assign_lagers(user, [lager])
        cls.operator.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members",
                codename__in=[
                    "view_member",
                    "add_member",
                    "change_member",
                    "view_memberledger",
                    "view_memberstatusrequest",
                    "add_memberstatusrequest",
                    "view_paymenttransaction",
                    "add_paymenttransaction",
                ],
            )
        )
        cls.reviewer.user_permissions.set(
            Permission.objects.filter(codename="change_memberstatusrequest")
        )
        cls.second.user_permissions.set(
            Permission.objects.filter(codename="change_memberstatusrequest")
        )
        cls.other.user_permissions.set(
            Permission.objects.filter(
                codename__in=["view_memberstatusrequest", "add_memberstatusrequest"]
            )
        )
        cls.member = m.Member.objects.create(
            district=cls.branch,
            office_location=cls.office,
            school=cls.school,
            lager=cls.lager,
            member_no="STATUS-001",
            computer_no="STATUS-C001",
            full_name="Status Test Member",
            title="Mr.",
            date_of_birth=date(1985, 1, 1),
            gender="male",
            nic_no="198512345678",
            phone_mobile="0771234567",
            admit_date=date(2020, 1, 1),
            office_address="Training office",
            private_address="Training private address",
        )
        cls.ledger = cls.member.ledgers.get()
        cls.policy = m.ApprovalHierarchy.objects.create(
            name="Higher membership approval",
            module="memberstatusrequest",
            action="change",
        )
        cls.level = m.ApprovalLevel.objects.create(
            hierarchy=cls.policy, position=1, name="Senior approval"
        )
        cls.level.approvers.add(cls.reviewer)
        cls.payment_type = m.PaymentType.objects.get(name="Special Deposit")
        m.PaymentTransaction.objects.create(
            member=cls.member,
            payment_type=cls.payment_type,
            transaction_date=date(2020, 1, 1),
            credit=Decimal("2500"),
        )

    def setUp(self):
        self.client.force_login(self.operator)

    def submit(self, kind="inactivate", reason="resignation", **kwargs):
        return submit_status_request(
            self.operator,
            self.member.pk,
            kind,
            reason,
            kwargs.pop("description", "Verified supporting membership status request."),
            kwargs.pop("effective_date", timezone.localdate()),
            kwargs.pop("files", []),
            **kwargs,
        )

    def payload(self, **changes):
        return {
            "member": self.member.pk,
            "kind": "inactivate",
            "reason": "resignation",
            "description": "Member resignation was confirmed against the signed notice.",
            "requested_effective_date": timezone.localdate().isoformat(),
            **changes,
        }

    def approve(self, request, actor=None):
        return decide(
            actor or self.reviewer,
            request.approval_id,
            "approve",
            "Source records and reason verified by higher-level reviewer.",
        )

    def test_request_captures_identity_reason_requester_and_preserves_old_state(self):
        request = self.submit()
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")
        self.assertEqual(request.requested_by, self.operator)
        self.assertEqual(request.snapshot["ledger_code"], self.lager.code)
        self.assertEqual(request.snapshot["member_no"], self.member.member_no)
        self.assertEqual(request.snapshot["district"], self.branch.name)
        self.assertEqual(request.snapshot["zonal_office"], self.office.name)
        self.assertEqual(request.state, "pending")
        self.assertIsNotNone(request.requested_at)

    def test_inactivate_then_reactivate_uses_same_ledger_balances_and_new_active_period(
        self,
    ):
        initial_ledger = self.ledger.pk
        initial_entries = m.PaymentTransaction.objects.count()
        request = self.submit()
        self.approve(request)
        self.member.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(self.member.status, "inactive")
        self.assertEqual(request.state, "applied")
        period = self.member.active_periods.get()
        self.assertIsNotNone(period.ended_at)
        second = self.submit(kind="reactivate", reason="eligibility_restored")
        self.approve(second)
        self.member.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.member.status, "active")
        self.assertEqual(self.member.ledgers.count(), 1)
        self.assertEqual(self.member.ledgers.get().pk, initial_ledger)
        self.assertEqual(m.PaymentTransaction.objects.count(), initial_entries)
        self.assertEqual(self.member.payment_transactions.get().credit, Decimal("2500"))
        self.assertEqual(self.member.active_periods.count(), 2)
        current = self.member.active_periods.get(ended_at__isnull=True)
        self.assertEqual(current.started_at, second.applied_at)
        self.assertEqual(self.member.status_events.count(), 2)
        self.assertTrue(
            m.LedgerEvent.objects.filter(
                record__source_model="members.memberstatusevent"
            ).exists()
        )
        self.assertTrue(
            m.AuditLog.objects.filter(source_model="members.memberstatusevent").exists()
        )

    def test_all_levels_are_required_and_requester_cannot_self_approve(self):
        self.level.approvers.add(self.operator)
        level = m.ApprovalLevel.objects.create(
            hierarchy=self.policy, position=2, name="Final approval"
        )
        level.approvers.add(self.second)
        request = self.submit()
        self.assertFalse(decision_rights(self.operator, request.approval)["approve"])
        with self.assertRaises(PermissionDenied):
            self.approve(request, self.operator)
        self.approve(request)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")
        self.approve(request, self.second)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "inactive")

    def test_administrator_requests_also_require_another_approver(self):
        request = submit_status_request(
            self.admin,
            self.member.pk,
            "inactivate",
            "administrative",
            "Administrator requests a documented membership review.",
            timezone.localdate(),
        )
        self.assertFalse(decision_rights(self.admin, request.approval)["override"])
        with self.assertRaises(PermissionDenied):
            decide(
                self.admin,
                request.approval_id,
                "override_approve",
                "Cannot approve my own request",
            )
        self.approve(request)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "inactive")

    def test_missing_or_self_only_policy_cannot_submit(self):
        self.policy.is_active = False
        self.policy.save()
        with self.assertRaises(ValidationError):
            self.submit()
        self.policy.is_active = True
        self.policy.save()
        self.level.approvers.set([self.operator])
        with self.assertRaises(ValidationError):
            self.submit()
        self.assertFalse(m.MemberStatusRequest.objects.exists())

    def test_invalid_blank_or_short_reason_cannot_be_submitted(self):
        for reason, description in [
            ("", "A valid detailed reason"),
            ("forged", "A valid detailed reason"),
            ("resignation", "  "),
            ("other", "test"),
        ]:
            with (
                self.subTest(reason=reason, description=description),
                self.assertRaises(ValidationError),
            ):
                self.submit(reason=reason, description=description)
        response = self.client.post(
            reverse("member_status_create"), self.payload(reason="")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(m.MemberStatusRequest.objects.exists())

    def test_foreign_lager_cannot_be_requested_even_with_forged_identity(self):
        self.client.force_login(self.other)
        response = self.client.post(
            reverse("member_status_create"),
            self.payload(member_name="Forged", lager=self.foreign_lager.pk),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(m.MemberStatusRequest.objects.exists())
        with self.assertRaises(m.Member.DoesNotExist):
            submit_status_request(
                self.other,
                self.member.pk,
                "inactivate",
                "resignation",
                "Attempt for a foreign assigned Lager.",
                timezone.localdate(),
            )

    def test_only_one_open_request_and_repeated_approval_cannot_duplicate_events(self):
        request = self.submit()
        with self.assertRaises(ValidationError):
            self.submit()
        self.approve(request)
        with self.assertRaises(PermissionDenied):
            self.approve(request)
        self.assertEqual(self.member.status_events.count(), 1)

    def test_rejection_and_cancellation_preserve_status_and_keep_requests(self):
        request = self.submit()
        decide(
            self.reviewer,
            request.approval_id,
            "reject",
            "The evidence does not justify inactivation.",
        )
        request.refresh_from_db()
        self.assertEqual(request.state, "rejected")
        second = self.submit()
        decide(
            self.operator,
            second.approval_id,
            "cancel",
            "Withdraw the mistaken request.",
        )
        second.refresh_from_db()
        self.assertEqual(second.state, "cancelled")
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")
        self.assertEqual(self.member.status_requests.count(), 2)

    def test_revoked_lager_access_is_rechecked_at_final_approval(self):
        request = self.submit()
        m.Lager.objects.filter(pk=self.lager.pk).update(maintenance_officer=self.other)
        result = self.approve(request)
        self.assertEqual(result.status, "failed")
        request.refresh_from_db()
        self.assertEqual(request.state, "failed")
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")

    def test_assigned_reviewer_outside_lager_can_review_only_assigned_requests(self):
        request = self.submit()
        self.assertTrue(
            visible_requests(self.reviewer).filter(pk=request.approval_id).exists()
        )
        self.assertFalse(
            visible_status_requests(self.second).filter(pk=request.pk).exists()
        )
        self.client.force_login(self.reviewer)
        self.assertEqual(
            self.client.get(
                reverse("member_status_detail", args=[request.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("approval_request_detail", args=[request.approval_id])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                reverse("member_status_create"), self.payload()
            ).status_code,
            403,
        )

    def test_supporting_document_is_private_and_not_stored_in_audit_json(self):
        file = SimpleUploadedFile(
            "resignation.pdf",
            b"%PDF-1.4\nTraining private document",
            content_type="application/pdf",
        )
        request = self.submit(files=[file])
        document = request.documents.get()
        self.assertTrue(document.sha256)
        self.assertEqual(document.size, file.size)
        self.assertNotIn(
            "content",
            m.AuditLog.objects.filter(source_model="members.memberstatusdocument")
            .first()
            .after,
        )
        self.client.force_login(self.reviewer)
        response = self.client.get(
            reverse("member_status_document", args=[document.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("member_status_document", args=[document.pk])
            ).status_code,
            404,
        )
        self.client.logout()
        self.assertEqual(
            self.client.get(
                reverse("member_status_document", args=[document.pk])
            ).status_code,
            302,
        )

    def test_invalid_or_large_attachments_are_rejected(self):
        for file in [
            SimpleUploadedFile("bad.exe", b"bad"),
            SimpleUploadedFile("pretend.pdf", b"<script>bad</script>"),
            SimpleUploadedFile("large.pdf", b"%PDF-" + b"x" * (5 * 1024 * 1024)),
        ]:
            with self.assertRaises(ValidationError):
                self.submit(files=[file])
        self.assertFalse(m.MemberStatusRequest.objects.exists())

    def test_future_approval_waits_for_date_then_applies_once(self):
        day = timezone.localdate() + timedelta(days=2)
        request = self.submit(effective_date=day)
        self.approve(request)
        request.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(request.state, "scheduled")
        self.assertEqual(self.member.status, "active")
        self.assertEqual(apply_due_status_requests()["applied"], 0)
        with (
            patch("django.utils.timezone.localdate", return_value=day),
            patch(
                "django.utils.timezone.now",
                return_value=datetime.combine(
                    day, datetime.min.time(), tzinfo=dt_timezone.utc
                ),
            ),
        ):
            self.assertEqual(apply_due_status_requests()["applied"], 1)
            self.assertEqual(apply_due_status_requests()["applied"], 0)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "inactive")

    def test_general_edit_and_queryset_cannot_bypass_status_approval(self):
        token = ledger_actor.set(self.operator)
        try:
            self.member.status = "inactive"
            with self.assertRaises(ValidationError):
                self.member.save()
            with self.assertRaises(ValidationError):
                m.Member.objects.filter(pk=self.member.pk).update(status="inactive")
        finally:
            ledger_actor.reset(token)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")

    def test_new_registration_is_pending_and_queues_activation(self):
        data = {
            "district": self.branch.pk,
            "office_location": self.office.pk,
            "lager": self.lager.pk,
            "school": self.school.pk,
            "member_no": "STATUS-NEW",
            "computer_no": "STATUS-NEW-C",
            "title": "Ms.",
            "full_name": "New Registration",
            "date_of_birth": "1990-02-02",
            "gender": "female",
            "nic_no": "199012345678",
            "phone_mobile": "0771234567",
            "admit_date": timezone.localdate().isoformat(),
            "office_address": "Training office",
            "private_address": "Training address",
            "status": "active",
            "maintenance_officer": self.operator.pk,
        }
        response = self.client.post(reverse("member_create"), data)
        self.assertEqual(response.status_code, 302)
        new = m.Member.objects.get(member_no="STATUS-NEW")
        self.assertEqual(new.status, "pending")
        request = new.status_requests.get()
        self.assertEqual(request.kind, "activate")
        self.approve(request)
        new.refresh_from_db()
        self.assertEqual(new.status, "active")
        self.assertEqual(new.ledgers.count(), 1)
        self.assertEqual(new.active_periods.count(), 1)

    def test_new_registration_is_rolled_back_if_approval_not_configured(self):
        self.policy.is_active = False
        self.policy.save()
        response = self.client.post(
            reverse("member_create"),
            {
                "district": self.branch.pk,
                "office_location": self.office.pk,
                "lager": self.lager.pk,
                "school": self.school.pk,
                "member_no": "NO-POLICY",
                "computer_no": "NO-POLICY-C",
                "title": "Ms.",
                "full_name": "No policy member",
                "date_of_birth": "1990-02-02",
                "gender": "female",
                "nic_no": "199012345678",
                "phone_mobile": "0771234567",
                "admit_date": timezone.localdate().isoformat(),
                "office_address": "Office",
                "private_address": "Home",
                "status": "active",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(m.Member.objects.filter(member_no="NO-POLICY").exists())

    def test_state_machine_and_backdated_dates_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.submit(kind="reactivate", reason="other")
        with self.assertRaises(ValidationError):
            self.submit(effective_date=timezone.localdate() - timedelta(days=1))

    def test_status_history_cannot_be_rewritten_or_deleted(self):
        request = self.submit()
        request.description = "Rewritten"
        with self.assertRaises(ValidationError):
            request.save()
        with self.assertRaises(ValidationError):
            m.MemberStatusRequest.objects.filter(pk=request.pk).update(
                description="Rewritten"
            )
        from django.db.models.deletion import ProtectedError

        with self.assertRaises(ProtectedError):
            request.delete()

    def test_missing_permission_cannot_submit_or_make_review_decisions(self):
        self.client.force_login(self.reviewer)
        self.assertEqual(
            self.client.post(
                reverse("member_status_create"), self.payload()
            ).status_code,
            403,
        )
        request = self.submit()
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("member_status_detail", args=[request.pk])
            ).status_code,
            404,
        )
        with self.assertRaises(PermissionDenied):
            self.approve(request, self.other)

    def test_unscoped_and_anonymous_requests_do_not_reveal_status_data(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("member_status_list")).status_code, 302
        )
        self.client.force_login(self.second)
        response = self.client.get(reverse("member_status_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.member.member_no)

    def test_pending_member_cannot_post_financial_entries_or_bypass_via_retirement(
        self,
    ):
        pending = m.Member.objects.create(
            district=self.branch,
            office_location=self.office,
            school=self.school,
            lager=self.lager,
            member_no="STATUS-PENDING",
            computer_no="STATUS-PENDING-C",
            full_name="Pending status member",
            nic_no="199912345678",
            status="pending",
        )
        with self.assertRaises(ValidationError):
            m.PaymentTransaction.objects.create(
                member=pending,
                payment_type=self.payment_type,
                transaction_date=timezone.localdate(),
                credit=Decimal("100"),
            )
        pending.status = "retired"
        pending.pension_date = timezone.localdate()
        with self.assertRaises(ValidationError):
            pending.save()

    def test_status_changes_are_protected_without_an_http_actor(self):
        self.member.status = "inactive"
        with self.assertRaises(ValidationError):
            self.member.save()
        with self.assertRaises(ValidationError):
            m.Member.objects.filter(pk=self.member.pk).update(status="inactive")
        with self.assertRaises(ValidationError):
            m.Member.objects.bulk_update([self.member], ["status"])

    def test_applied_status_change_is_visible_in_lager_activity(self):
        from .lager_activity import activity_query, decorate_event

        request = self.submit()
        self.approve(request)
        rows = list(
            activity_query(
                m.Member.objects.filter(pk=self.member.pk),
                {"membership_status": "Membership status"},
                None,
                timezone.localdate(),
            )
        )
        self.assertEqual(len(rows), 1)
        row = decorate_event(rows[0])
        self.assertEqual(row["event_details"], request.description)
        self.assertEqual(row["url"], reverse("member_status_detail", args=[request.pk]))

    def test_approval_decision_requires_a_reason(self):
        request = self.submit()
        with self.assertRaises(ValidationError):
            decide(self.reviewer, request.approval_id, "approve", "")
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")

    def test_scheduled_request_does_not_apply_after_ownership_is_revoked(self):
        day = timezone.localdate() + timedelta(days=1)
        request = self.submit(effective_date=day)
        self.approve(request)
        m.Lager.objects.filter(pk=self.lager.pk).update(maintenance_officer=self.other)
        with patch("django.utils.timezone.localdate", return_value=day):
            result = apply_due_status_requests()
        self.assertEqual(result["failed"], 1)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")
        request.refresh_from_db()
        self.assertEqual(request.state, "failed")

    def test_direct_status_application_cannot_bypass_approval_levels(self):
        from .status_services import approve_status_request

        request = self.submit()
        with self.assertRaises(PermissionDenied):
            approve_status_request(request.approval, self.reviewer)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")

    def test_requester_can_approve_when_administrator_explicitly_allows_it(self):
        self.operator.user_permissions.add(
            Permission.objects.get(
                codename="change_memberstatusrequest", content_type__app_label="members"
            )
        )
        self.level.allow_self_approval = True
        self.level.save()
        self.level.approvers.set([self.operator])
        request = self.submit()
        self.assertTrue(request.approval.steps[0]["allow_self_approval"])
        self.assertTrue(decision_rights(self.operator, request.approval)["approve"])
        self.approve(request, self.operator)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "inactive")

    def test_workflow_self_approval_does_not_skip_a_later_manager_level(self):
        self.operator.user_permissions.add(
            Permission.objects.get(
                codename="change_memberstatusrequest", content_type__app_label="members"
            )
        )
        self.level.allow_self_approval = True
        self.level.save()
        self.level.approvers.set([self.operator])
        manager = m.ApprovalLevel.objects.create(
            hierarchy=self.policy,
            position=2,
            name="Manager approval",
            allow_self_approval=False,
        )
        manager.approvers.add(self.reviewer)
        request = self.submit()
        self.approve(request, self.operator)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")
        self.assertFalse(
            decision_rights(
                self.operator, m.ApprovalRequest.objects.get(pk=request.approval_id)
            )["approve"]
        )
        self.approve(request, self.reviewer)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "inactive")

    def test_admin_requester_cannot_override_a_later_level_that_disallows_self_approval(
        self,
    ):
        self.level.allow_self_approval = True
        self.level.save()
        self.level.approvers.set([self.admin])
        manager = m.ApprovalLevel.objects.create(
            hierarchy=self.policy, position=2, name="Another manager required"
        )
        manager.approvers.add(self.reviewer)
        request = submit_status_request(
            self.admin,
            self.member.pk,
            "inactivate",
            "administrative",
            "Documented administrative membership request.",
            timezone.localdate(),
        )
        rights = decision_rights(self.admin, request.approval)
        self.assertTrue(rights["approve"])
        self.assertFalse(rights["override"])
        with self.assertRaises(PermissionDenied):
            decide(
                self.admin,
                request.approval_id,
                "override_approve",
                "Cannot skip required manager approval.",
            )
        self.approve(request, self.admin)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")
        self.approve(request, self.reviewer)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "inactive")

    def test_pending_request_keeps_captured_self_approval_rules_after_policy_edit(self):
        self.operator.user_permissions.add(
            Permission.objects.get(
                codename="change_memberstatusrequest", content_type__app_label="members"
            )
        )
        self.level.approvers.add(self.operator)
        request = self.submit()
        self.level.allow_self_approval = True
        self.level.save()
        self.assertFalse(decision_rights(self.operator, request.approval)["approve"])

    def test_request_payload_cannot_enable_self_approval(self):
        self.operator.user_permissions.add(
            Permission.objects.get(
                codename="change_memberstatusrequest", content_type__app_label="members"
            )
        )
        self.level.approvers.add(self.operator)
        response = self.client.post(
            reverse("member_status_create"), self.payload(allow_self_approval="on")
        )
        self.assertEqual(response.status_code, 302)
        request = m.MemberStatusRequest.objects.get()
        self.assertFalse(request.approval.steps[0]["allow_self_approval"])
        self.assertFalse(decision_rights(self.operator, request.approval)["approve"])
