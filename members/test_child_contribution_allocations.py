from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from . import models as m, test_contributions, test_remaining_allocations
from .child_services import deposit_balance
from .contribution_forms import ContributionForm
from .contribution_services import allocation_preview, post_receipt
from .transaction_balances import member_balances


class ChildContributionAllocationTests(TestCase):
    preview = test_contributions.ContributionTests.preview
    post = test_contributions.ContributionTests.post

    @classmethod
    def setUpTestData(cls):
        test_remaining_allocations.MultipleRemainingAllocationTests.setUpTestData.__func__(
            cls
        )
        cls.operator.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="members",
                codename__in=[
                    "add_childdeposit",
                    "view_childdeposit",
                    "change_childdeposit",
                ],
            )
        )
        cls.child_type = m.ChildDepositType.objects.create(
            name="Contribution child savings"
        )
        cls.other_type = m.ChildDepositType.objects.create(name="Other child savings")
        cls.children = [
            m.Child.objects.create(
                member=cls.member, name=name, date_of_birth=date(2015, 1, 1)
            )
            for name in ("First child", "Second child")
        ]
        cls.deposits = [
            m.ChildDeposit.objects.create(
                child=child,
                deposit_type=cls.child_type,
                opened_on=date(cls.today.year, 1, 1),
                opening_amount="100",
            )
            for child in cls.children
        ]
        cls.other_deposit = m.ChildDeposit.objects.create(
            child=cls.children[0],
            deposit_type=cls.other_type,
            opened_on=date(cls.today.year, 1, 1),
        )

    def setUp(self):
        self.client.force_login(self.operator)

    def payload(self, deposit=None):
        data = test_remaining_allocations.MultipleRemainingAllocationTests.payload(
            self, [(self.accounts[0], "800"), (None, "500")]
        )
        deposit = deposit or self.deposits[0]
        data["remaining-1-payment_type"] = f"child-type:{deposit.deposit_type_id}"
        data["remaining-1-child_deposit"] = deposit.pk
        return data

    def test_account_options_include_child_types_and_only_selected_members_accounts(
        self,
    ):
        response = self.client.get(
            reverse("contribution_create"), {"member": self.member.pk}
        )
        self.assertContains(response, "Child deposit types")
        self.assertContains(response, self.child_type.name)
        self.assertContains(response, self.deposits[0].account_no)
        self.assertContains(response, self.deposits[1].account_no)
        self.assertContains(response, f'data-deposit-type="{self.child_type.pk}"')

    def test_child_credit_posts_to_child_module_without_duplicating_member_credit(self):
        self.assertEqual(self.post(self.payload()).status_code, 302)
        receipt = m.PaymentReceipt.objects.get()
        allocated = receipt.allocations.get(child_transaction__isnull=False)
        self.assertEqual(allocated.kind, "remaining")
        self.assertIsNone(allocated.transaction_id)
        self.assertIsNone(allocated.payment_type_id)
        entry = allocated.child_transaction
        self.assertEqual(entry.deposit, self.deposits[0])
        self.assertEqual(entry.credit, Decimal("500"))
        self.assertEqual(entry.interest_rate, self.deposits[0].rate_on(self.today))
        self.assertEqual(entry.created_by, self.operator)
        self.assertEqual(deposit_balance(self.deposits[0], self.today), Decimal("600"))
        self.assertEqual(
            m.PaymentTransaction.objects.aggregate(total=Sum("credit"))["total"],
            Decimal("2500"),
        )
        balances = member_balances(self.member, self.today)
        self.assertEqual(balances["total"], "2500.00")
        self.assertEqual(balances["remaining_contribution_total"], "800.00")
        page = self.client.get(reverse("child_deposit_detail", args=[entry.deposit_id]))
        self.assertContains(page, receipt.receipt_number)
        self.assertEqual(page.context["final_balance"], Decimal("600"))
        self.assertTrue(page.context["page_obj"][0].ledger_readonly)
        receipt_page = self.client.get(
            reverse("contribution_detail", args=[receipt.pk])
        )
        self.assertContains(receipt_page, self.children[0].name)
        self.assertContains(
            receipt_page, reverse("child_deposit_detail", args=[entry.deposit_id])
        )
        self.assertTrue(
            m.LedgerRecord.objects.filter(
                source_model="members.childdeposittransaction", source_id=entry.pk
            ).exists()
        )
        self.assertTrue(
            m.AuditLog.objects.filter(
                source_model="members.childdeposittransaction", source_id=entry.pk
            ).exists()
        )

    def test_child_account_lookup_is_scoped_and_requires_posting_permissions(self):
        url = reverse("contribution_child_accounts")
        response = self.client.get(url, {"member": self.member.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["id"] for row in response.json()["accounts"]},
            {account.pk for account in [*self.deposits, self.other_deposit]},
        )
        self.assertEqual(self.client.get(url, {"member": "invalid"}).status_code, 400)
        self.assertEqual(self.client.get(url, {"member": "999999"}).status_code, 404)
        self.deposits[0].is_active = False
        self.deposits[0].save()
        response = self.client.get(url, {"member": self.member.pk})
        self.assertNotIn(
            self.deposits[0].pk, {row["id"] for row in response.json()["accounts"]}
        )
        self.operator.user_permissions.remove(
            Permission.objects.get(
                content_type__app_label="members", codename="add_childdeposit"
            )
        )
        self.assertEqual(
            self.client.get(url, {"member": self.member.pk}).status_code, 403
        )

    def test_multiple_children_and_repeated_child_account_get_distinct_credits(self):
        data = self.payload()
        data["remaining-TOTAL_FORMS"] = "3"
        for index, (deposit, amount) in enumerate(
            zip(
                (self.deposits[0], self.deposits[1], self.deposits[0]),
                ("500", "600", "200"),
            )
        ):
            data[f"remaining-{index}-payment_type"] = f"child-type:{self.child_type.pk}"
            data[f"remaining-{index}-child_deposit"] = deposit.pk
            data[f"remaining-{index}-amount"] = amount
        self.assertEqual(self.post(data).status_code, 302)
        self.assertEqual(m.ChildDepositTransaction.objects.count(), 3)
        self.assertEqual(deposit_balance(self.deposits[0], self.today), Decimal("800"))
        self.assertEqual(deposit_balance(self.deposits[1], self.today), Decimal("700"))
        self.assertEqual(member_balances(self.member, self.today)["total"], "1700.00")

    def test_missing_child_account_mismatched_type_and_member_account_are_rejected(
        self,
    ):
        for updates in (
            {"remaining-1-child_deposit": ""},
            {"remaining-1-child_deposit": self.other_deposit.pk},
            {"remaining-1-payment_type": self.accounts[0].pk},
            {"remaining-1-child_deposit": "999999"},
        ):
            with self.subTest(updates=updates):
                form = ContributionForm(
                    {**self.payload(), **updates}, user=self.operator
                )
                self.assertFalse(form.is_valid())
        self.assertFalse(m.PaymentReceipt.objects.exists())

    def test_another_members_child_cannot_be_selected_or_posted_directly(self):
        other_member = m.Member.objects.create(
            district=self.member.district,
            office_location=self.member.office_location,
            school=self.member.school,
            lager=self.member.lager,
            member_no="OTHER-CHILD-PARENT",
            full_name="Other parent",
            salary="1000",
            office_address="Office",
            private_address="Home",
            phone_mobile="0771234567",
            gender="female",
            nic_no="199012345679",
        )
        child = m.Child.objects.create(
            member=other_member,
            name="Other parent's child",
            date_of_birth=date(2015, 1, 1),
        )
        deposit = m.ChildDeposit.objects.create(
            child=child,
            deposit_type=self.child_type,
            opened_on=date(self.today.year, 1, 1),
        )
        self.assertFalse(
            ContributionForm(self.payload(deposit), user=self.operator).is_valid()
        )
        with self.assertRaises(ValidationError):
            allocation_preview(
                self.member,
                self.today,
                "3000",
                remaining_allocations=[
                    {
                        "child_deposit_id": deposit.pk,
                        "child_deposit_type_id": self.child_type.pk,
                        "amount": "1300",
                    }
                ],
            )
        response = self.client.get(
            reverse("contribution_create"), {"member": self.member.pk}
        )
        self.assertNotContains(response, deposit.account_no)
        own_lookup = self.client.get(
            reverse("contribution_child_accounts"), {"member": self.member.pk}
        )
        self.assertNotIn(
            deposit.pk, {row["id"] for row in own_lookup.json()["accounts"]}
        )

    def test_inactive_child_type_account_and_receipt_before_opening_are_rejected(self):
        for target in (self.children[0], self.child_type, self.deposits[0]):
            target.is_active = False
            target.save()
            self.assertFalse(
                ContributionForm(self.payload(), user=self.operator).is_valid()
            )
            target.is_active = True
            target.save()
        data = self.payload()
        data["received_on"] = (
            self.deposits[0].opened_on - timedelta(days=1)
        ).isoformat()
        self.assertFalse(ContributionForm(data, user=self.operator).is_valid())

    def test_child_deposit_add_permission_is_required_in_form_and_service(self):
        form = ContributionForm(self.payload(), user=self.operator)
        self.assertTrue(form.is_valid(), form.errors)
        values, fingerprint = form.cleaned_data, form.preview["fingerprint"]
        self.operator.user_permissions.remove(
            Permission.objects.get(
                content_type__app_label="members", codename="add_childdeposit"
            )
        )
        for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            self.operator.__dict__.pop(attr, None)
        self.assertFalse(
            ContributionForm(self.payload(), user=self.operator).is_valid()
        )
        with self.assertRaises(PermissionDenied):
            post_receipt(self.operator, values, fingerprint)

    def test_preview_target_changes_require_review_and_duplicate_submission_is_safe(
        self,
    ):
        data = self.preview(self.payload())
        changed = {**data, "remaining-1-child_deposit": self.deposits[1].pk}
        self.assertContains(
            self.client.post(reverse("contribution_create"), changed),
            "Preview the allocation again",
        )
        self.assertFalse(m.PaymentReceipt.objects.exists())
        for _ in range(2):
            self.assertEqual(
                self.client.post(reverse("contribution_create"), data).status_code, 302
            )
        self.assertEqual(m.PaymentReceipt.objects.count(), 1)
        self.assertEqual(m.ChildDepositTransaction.objects.count(), 1)
        self.assertContains(
            self.client.post(reverse("contribution_create"), changed), "already used"
        )

    def test_failure_rolls_back_member_and_child_credits_and_receipt(self):
        with patch.object(
            m.ChildDepositTransaction,
            "save",
            side_effect=ValidationError("Child account unavailable"),
        ):
            self.assertContains(self.post(self.payload()), "Child account unavailable")
        self.assertFalse(m.PaymentTransaction.objects.exists())
        self.assertFalse(m.ChildDepositTransaction.objects.exists())
        self.assertFalse(m.PaymentReceipt.objects.exists())
        self.assertFalse(m.PaymentReceiptAllocation.objects.exists())

    def test_linked_child_credit_cannot_be_edited_or_deleted(self):
        self.post(self.payload())
        entry = m.ChildDepositTransaction.objects.get()
        entry.credit = Decimal("1")
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ProtectedError):
            entry.delete()
        self.assertEqual(
            self.client.get(reverse("child_entry_edit", args=[entry.pk])).status_code,
            403,
        )

    def test_below_plan_receipt_waits_for_approval_before_child_is_credited(self):
        test_contributions.ContributionTests.policy(self)
        data = self.payload()
        data[f"allocated_{self.types['Fix Deposit'].pk}"] = "100"
        data["remaining-0-amount"] = "900"
        self.assertEqual(self.post(data).status_code, 302)
        self.assertFalse(m.ChildDepositTransaction.objects.exists())
        self.assertFalse(m.PaymentReceipt.objects.exists())
        record = m.ApprovalRequest.objects.get()
        self.assertEqual(
            test_contributions.ContributionTests.approve(self, record).status,
            "approved",
        )
        self.assertEqual(m.ChildDepositTransaction.objects.get().credit, Decimal("500"))
