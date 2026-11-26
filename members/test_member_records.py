from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from . import models as m
from .forms import MemberForm
from .ledger_tracking import ledger_actor
from . import test_contributions
from .test_contributions import setup_records
from .transaction_balances import member_balances


class MemberRecordTests(TestCase):
    payload = test_contributions.ContributionTests.payload
    preview = test_contributions.ContributionTests.preview
    post = test_contributions.ContributionTests.post

    @classmethod
    def setUpTestData(cls):
        setup_records(cls)
        cls.member.computer_no = "COMP-001"
        cls.member.title = "Ms."
        cls.member.date_of_birth = date(1990, 1, 1)
        cls.member.save()

    def setUp(self):
        self.client.force_login(self.admin)

    def member_payload(self, **overrides):
        member = self.member
        return {
            "district": member.district_id,
            "lager": member.lager_id,
            "office_location": member.office_location_id,
            "school": member.school_id,
            "member_no": member.member_no,
            "computer_no": member.computer_no,
            "title": member.title,
            "full_name": member.full_name,
            "date_of_birth": "1990-01-01",
            "salary": "",
            "office_address": member.office_address,
            "private_address": member.private_address,
            "phone_mobile": member.phone_mobile,
            "admit_date": "2020-01-01",
            "gender": member.gender,
            "nic_no": member.nic_no,
            "status": member.status,
            **overrides,
        }

    def remainder_payload(self, **kwargs):
        data = self.payload(
            "2000", remaining_payment_type=self.types["Share"].pk, **kwargs
        )
        data[f"allocated_{self.types['Special Deposit'].pk}"] = "500"
        return data

    def test_remaining_credit_is_saved_once_and_visible_in_entry_form(self):
        data = self.preview(self.remainder_payload())
        for _ in range(2):
            self.assertEqual(
                self.client.post(reverse("contribution_create"), data).status_code, 302
            )
        receipt = m.PaymentReceipt.objects.get()
        self.assertEqual(receipt.plan_snapshot["remaining_amount"], "500.00")
        self.assertEqual(
            m.PaymentTransaction.objects.get(payment_type=self.types["Share"]).credit,
            Decimal("500"),
        )
        self.assertEqual(
            m.PaymentTransaction.objects.aggregate(total=Sum("credit"))["total"],
            Decimal("2000"),
        )
        summary = member_balances(self.member, self.today)
        self.assertEqual(summary["total"], "2000.00")
        self.assertEqual(summary["remaining_contribution_total"], "500.00")
        response = self.client.get(
            reverse("payment_transaction_create"), {"member": self.member.pk}
        )
        self.assertContains(response, "Remaining contribution amounts credited")
        self.assertContains(response, "500.00")
        response = self.client.get(
            reverse("payment_transaction_balances"),
            {"member": self.member.pk, "date": self.today},
        )
        self.assertEqual(response.json()["remaining_contribution_total"], "500.00")
        self.assertEqual(
            self.client.get(reverse("member_detail", args=[self.member.pk])).context[
                "contribution_total"
            ],
            Decimal("2000"),
        )

    def test_remaining_credit_merges_with_same_plan_category(self):
        data = self.remainder_payload()
        data["remaining_payment_type"] = self.types["Special Deposit"].pk
        self.assertEqual(self.post(data).status_code, 302)
        self.assertEqual(
            m.PaymentTransaction.objects.filter(
                payment_type=self.types["Special Deposit"]
            ).count(),
            1,
        )
        self.assertEqual(
            m.PaymentTransaction.objects.get(
                payment_type=self.types["Special Deposit"]
            ).credit,
            Decimal("1000"),
        )

    def test_remaining_credit_waits_with_below_plan_receipt_until_approval(self):
        test_contributions.ContributionTests.policy(self)
        self.client.force_login(self.operator)
        data = self.payload("2000", remaining_payment_type=self.types["Share"].pk)
        for row in self.plan.amounts.filter(amount__gt=0):
            data[f"allocated_{row.payment_type_id}"] = str(row.amount / 2)
        self.assertEqual(self.post(data).status_code, 302)
        self.assertFalse(m.PaymentReceipt.objects.exists())
        self.assertFalse(m.PaymentTransaction.objects.exists())
        record = m.ApprovalRequest.objects.get()
        result = test_contributions.ContributionTests.approve(self, record)
        self.assertEqual(result.status, "approved")
        self.assertEqual(m.PaymentReceipt.objects.count(), 1)
        self.assertEqual(
            m.PaymentTransaction.objects.get(payment_type=self.types["Share"]).credit,
            Decimal("1250"),
        )

    def test_remaining_credit_failure_rolls_back_entire_receipt(self):
        save = m.PaymentTransaction.save

        def failing(entry, *args, **kwargs):
            if entry.payment_type_id == self.types["Share"].pk:
                raise ValidationError("Remaining credit cannot be posted")
            return save(entry, *args, **kwargs)

        with patch.object(m.PaymentTransaction, "save", failing):
            self.assertContains(
                self.post(self.remainder_payload()), "Remaining credit cannot be posted"
            )
        self.assertFalse(m.PaymentReceipt.objects.exists())
        self.assertFalse(m.PaymentReceiptAllocation.objects.exists())
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_remainder_selection_change_requires_preview_and_cannot_reuse_receipt(self):
        data = self.preview(self.remainder_payload())
        changed = {**data, "remaining_payment_type": self.types["Special Deposit"].pk}
        self.assertContains(
            self.client.post(reverse("contribution_create"), changed),
            "Preview the allocation again",
        )
        self.assertFalse(m.PaymentReceipt.objects.exists())
        self.assertEqual(
            self.client.post(reverse("contribution_create"), data).status_code, 302
        )
        self.assertContains(
            self.client.post(reverse("contribution_create"), changed), "already used"
        )

    def test_overallocation_and_inactive_remaining_type_cannot_post(self):
        data = self.remainder_payload()
        data[f"allocated_{self.types['Special Deposit'].pk}"] = "2000"
        self.assertTrue(
            self.client.post(reverse("contribution_create"), data)
            .context["form"]
            .errors
        )
        self.types["Share"].is_active = False
        self.types["Share"].save()
        self.assertTrue(
            self.client.post(reverse("contribution_create"), self.remainder_payload())
            .context["form"]
            .errors
        )
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_optional_salary_and_required_identity_fields(self):
        form = MemberForm(self.member_payload(), instance=self.member, user=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        member = form.save()
        self.assertIsNone(member.salary)
        for name in ("computer_no", "title", "date_of_birth"):
            form = MemberForm(
                self.member_payload(**{name: ""}), instance=member, user=self.admin
            )
            self.assertFalse(form.is_valid())
            self.assertIn(name, form.errors)
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_gender_master_drives_member_choices_and_protects_used_codes(self):
        custom = m.Gender.objects.create(code="custom", name="Custom gender")
        form = MemberForm(
            self.member_payload(gender=custom.code),
            instance=self.member,
            user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        member = form.save()
        self.assertEqual(member.get_gender_display(), "Custom gender")
        with transaction.atomic(), self.assertRaises(ProtectedError):
            custom.delete()
        custom.is_active = False
        custom.save()
        self.assertIn(
            (custom.code, custom.name),
            MemberForm(instance=member).fields["gender"].choices,
        )
        self.assertNotIn(
            (custom.code, custom.name), MemberForm().fields["gender"].choices
        )
        self.assertEqual(self.client.get(reverse("gender_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("gender_create")).status_code, 200)

    def test_audit_records_actor_and_before_after_for_form_and_bulk_update(self):
        response = self.client.post(
            reverse("member_edit", args=[self.member.pk]),
            self.member_payload(full_name="Updated Member"),
        )
        self.assertEqual(response.status_code, 302)
        event = m.MemberAuditEvent.objects.filter(member_pk=self.member.pk).first()
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.before["full_name"]["value"], "Allocation Member")
        self.assertEqual(event.after["full_name"]["value"], "Updated Member")
        token = ledger_actor.set(self.admin)
        try:
            m.Member.objects.filter(pk=self.member.pk).update(
                private_address="New home"
            )
        finally:
            ledger_actor.reset(token)
        event = m.MemberAuditEvent.objects.first()
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.after["private_address"]["value"], "New home")
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            m.MemberAuditEvent.objects.filter(pk=event.pk).update(actor_name="Changed")
        with self.assertRaises(ProtectedError):
            m.MemberAuditEvent.objects.filter(pk=event.pk).delete()

    def test_audit_survives_member_deletion_and_failed_changes_roll_back(self):
        member = m.Member.objects.get(pk=self.member.pk)
        member.pk = None
        member.status = "pending"
        member.member_no = "DELETABLE"
        member.computer_no = "DELETE-COMP"
        member.nic_no = "199012345679"
        token = ledger_actor.set(self.admin)
        try:
            with patch("members.transfer_services.initialize_member_ledger"):
                member.save()
            pk = member.pk
            member.delete()
        finally:
            ledger_actor.reset(token)
        events = m.MemberAuditEvent.objects.filter(member_pk=pk)
        self.assertEqual(
            set(events.values_list("action", flat=True)), {"created", "deleted"}
        )
        self.assertEqual(events.first().actor, self.admin)
        self.assertContains(
            self.client.get(reverse("member_audit"), {"member": pk}), "DELETABLE"
        )
        count = m.MemberAuditEvent.objects.count()
        with self.assertRaises(RuntimeError), transaction.atomic():
            m.Member.objects.filter(pk=self.member.pk).update(
                private_address="Rolled back"
            )
            raise RuntimeError("rollback")
        self.assertEqual(m.MemberAuditEvent.objects.count(), count)

    def test_audit_access_follows_member_scope(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse("member_audit")).status_code, 403)
        from django.contrib.auth.models import Permission

        self.operator.user_permissions.add(
            Permission.objects.get(codename="view_member")
        )
        self.assertContains(
            self.client.get(reverse("member_audit")), self.member.member_no
        )
        self.operator.assigned_districts.clear()
        self.assertNotContains(
            self.client.get(reverse("member_audit")), self.member.member_no
        )
        self.assertEqual(
            self.client.get(
                reverse("member_audit"), {"member": self.member.pk}
            ).status_code,
            404,
        )
