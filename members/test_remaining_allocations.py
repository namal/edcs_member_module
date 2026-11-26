from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from . import models as m, test_contributions
from .contribution_forms import ContributionForm
from .contribution_services import allocation_preview, post_receipt
from .ledger_tracking import ledger_actor
from .transaction_balances import member_balances


class MultipleRemainingAllocationTests(TestCase):
    preview = test_contributions.ContributionTests.preview
    post = test_contributions.ContributionTests.post

    @classmethod
    def setUpTestData(cls):
        test_contributions.setup_records(cls)
        row = cls.plan.amounts.get(payment_type=cls.types["Scholarship"])
        row.amount = Decimal("500")
        row.save()
        cls.accounts = [
            m.PaymentType.objects.create(name=name)
            for name in ("Loan Account", "Danajaya Account", "Children's Account")
        ]

    def setUp(self):
        self.client.force_login(self.operator)

    def payload(self, rows=None, amount="3000"):
        data = test_contributions.ContributionTests.payload(self, amount)
        for row in self.plan.amounts.filter(amount__gt=0):
            data[f"allocated_{row.payment_type_id}"] = str(row.amount)
        rows = (
            rows
            if rows is not None
            else list(zip(self.accounts, ("600", "200", "500")))
        )
        data.update(
            {
                "remaining-TOTAL_FORMS": str(len(rows)),
                "remaining-INITIAL_FORMS": "0",
                "remaining-MIN_NUM_FORMS": "0",
                "remaining-MAX_NUM_FORMS": "100",
            }
        )
        for i, (account, allocated) in enumerate(rows):
            data[f"remaining-{i}-payment_type"] = account.pk if account else ""
            data[f"remaining-{i}-amount"] = allocated
        return data

    def test_example_posts_standard_and_each_remaining_allocation_separately(self):
        data = self.preview(self.payload())
        self.assertFalse(m.PaymentReceipt.objects.exists())
        self.assertEqual(
            self.client.post(reverse("contribution_create"), data).status_code, 302
        )
        receipt = m.PaymentReceipt.objects.get()
        self.assertEqual(
            receipt.allocations.filter(kind="standard").aggregate(total=Sum("amount"))[
                "total"
            ],
            Decimal("1700"),
        )
        allocations = list(
            receipt.allocations.filter(kind="remaining").order_by("position")
        )
        self.assertEqual(
            [row.amount for row in allocations],
            [Decimal("600"), Decimal("200"), Decimal("500")],
        )
        self.assertEqual(len({row.transaction_id for row in allocations}), 3)
        self.assertEqual(
            [row.transaction.credit for row in allocations],
            [Decimal("600"), Decimal("200"), Decimal("500")],
        )
        self.assertEqual(
            [
                row["remaining_balance"]
                for row in receipt.plan_snapshot["remaining_allocations"]
            ],
            ["700.00", "500.00", "0.00"],
        )
        self.assertEqual(
            m.PaymentTransaction.objects.aggregate(total=Sum("credit"))["total"],
            Decimal("3000"),
        )
        self.assertFalse(m.ApprovalRequest.objects.exists())
        summary = member_balances(self.member, self.today)
        self.assertEqual(summary["total"], "3000.00")
        self.assertEqual(summary["remaining_contribution_total"], "1300.00")
        self.assertEqual(len(summary["remaining_contribution_credits"]), 3)

    def test_same_account_as_standard_and_repeated_accounts_keep_separate_entries(self):
        account = self.types["Special Deposit"]
        self.assertEqual(
            self.post(
                self.payload(
                    [(account, "200"), (account, "100"), (self.accounts[0], "1000")]
                )
            ).status_code,
            302,
        )
        self.assertEqual(
            list(
                m.PaymentTransaction.objects.filter(payment_type=account)
                .order_by("pk")
                .values_list("credit", flat=True)
            ),
            [Decimal("500"), Decimal("200"), Decimal("100")],
        )
        self.assertEqual(
            m.PaymentReceiptAllocation.objects.filter(
                payment_type=account, kind="remaining"
            ).count(),
            2,
        )
        self.assertFalse(m.ApprovalRequest.objects.exists())

    def test_edit_remove_and_blank_rows_are_respected(self):
        data = self.payload(
            [
                (self.accounts[0], "1000"),
                (self.accounts[1], "200"),
                (self.accounts[2], "1300"),
                (None, ""),
            ]
        )
        data["remaining-0-DELETE"] = "on"
        data["remaining-1-DELETE"] = "on"
        data["remaining-2-amount"] = "1300.00"
        self.assertEqual(self.post(data).status_code, 302)
        rows = m.PaymentReceiptAllocation.objects.filter(kind="remaining")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().payment_type, self.accounts[2])

    def test_invalid_excess_partial_zero_negative_and_missing_fields_cannot_post(self):
        for amount in ("1300.01", "1299.99", "0", "-1", "NaN", "1.001", "10000000000"):
            with self.subTest(amount=amount):
                form = ContributionForm(
                    self.payload([(self.accounts[0], amount)]), user=self.operator
                )
                self.assertFalse(form.is_valid())
        for rows in ([(None, "1300")], [(self.accounts[0], "")]):
            form = ContributionForm(self.payload(rows), user=self.operator)
            self.assertFalse(form.is_valid())
            self.assertTrue(form.remaining_formset.errors)
        self.assertFalse(m.PaymentReceipt.objects.exists())

    def test_accounts_are_database_driven_and_inactive_accounts_are_rejected(self):
        page = self.client.get(
            reverse("contribution_create"), {"member": self.member.pk}
        )
        self.assertContains(page, "Add Another Account")
        self.assertContains(page, self.accounts[0].name)
        self.assertContains(page, "remaining-__prefix__-payment_type")
        self.accounts[0].is_active = False
        self.accounts[0].save()
        self.assertFalse(
            ContributionForm(self.payload(), user=self.operator).is_valid()
        )

    def test_management_tampering_and_mixed_legacy_selection_are_rejected(self):
        data = self.payload()
        del data["remaining-TOTAL_FORMS"]
        self.assertFalse(ContributionForm(data, user=self.operator).is_valid())
        for count in ("1", "101", "-1", "invalid"):
            data = self.payload()
            data["remaining-TOTAL_FORMS"] = count
            self.assertFalse(ContributionForm(data, user=self.operator).is_valid())
        data = self.payload()
        data["remaining_payment_type"] = self.accounts[0].pk
        self.assertFalse(ContributionForm(data, user=self.operator).is_valid())

    def test_preview_change_and_duplicate_submission_are_safe(self):
        data = self.preview(self.payload())
        changed = {**data, "remaining-0-amount": "500", "remaining-1-amount": "300"}
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
        self.assertEqual(m.PaymentTransaction.objects.count(), 8)
        self.assertContains(
            self.client.post(reverse("contribution_create"), changed), "already used"
        )

    def test_last_allocation_failure_rolls_back_every_posting(self):
        save = m.PaymentTransaction.save

        def failing(entry, *args, **kwargs):
            if entry.payment_type_id == self.accounts[-1].pk:
                raise ValidationError("Last account is unavailable")
            return save(entry, *args, **kwargs)

        with patch.object(m.PaymentTransaction, "save", failing):
            self.assertContains(
                self.post(self.payload()), "Last account is unavailable"
            )
        self.assertFalse(m.PaymentReceipt.objects.exists())
        self.assertFalse(m.PaymentReceiptAllocation.objects.exists())
        self.assertFalse(m.PaymentTransaction.objects.exists())
        self.assertFalse(
            m.LedgerEvent.objects.filter(
                record__source_model="members.paymenttransaction"
            ).exists()
        )

    def test_history_retains_complete_breakdown_and_entries_are_immutable(self):
        self.post(self.payload())
        receipt = m.PaymentReceipt.objects.get()
        page = self.client.get(reverse("contribution_detail", args=[receipt.pk]))
        for text in (
            "Loan Account",
            "Danajaya Account",
            "Children&#x27;s Account",
            "Remaining balance",
            "700.00",
            "500.00",
            "0.00",
        ):
            self.assertContains(page, text)
        self.accounts[0].name = "Renamed loan"
        self.accounts[0].save()
        self.assertContains(
            self.client.get(reverse("contribution_detail", args=[receipt.pk])),
            "Loan Account",
        )
        entry = receipt.allocations.filter(kind="remaining").first().transaction
        entry.credit = Decimal("1")
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

    def test_below_standard_allocation_still_requires_approval_for_whole_receipt(self):
        test_contributions.ContributionTests.policy(self)
        data = self.payload(
            [
                (self.accounts[0], "700"),
                (self.accounts[1], "200"),
                (self.accounts[2], "500"),
            ]
        )
        data[f"allocated_{self.types['Fix Deposit'].pk}"] = "100"
        self.assertEqual(self.post(data).status_code, 302)
        self.assertFalse(m.PaymentTransaction.objects.exists())
        record = m.ApprovalRequest.objects.get()
        self.assertEqual(
            test_contributions.ContributionTests.approve(self, record).status,
            "approved",
        )
        self.assertEqual(
            m.PaymentReceiptAllocation.objects.filter(kind="remaining").count(), 3
        )

    def test_direct_service_cannot_bypass_remaining_balance_validation(self):
        with self.assertRaises(ValidationError):
            allocation_preview(
                self.member,
                self.today,
                "3000",
                remaining_allocations=[
                    {"payment_type_id": self.accounts[0].pk, "amount": "1300.01"}
                ],
            )
        with self.assertRaises(ValidationError):
            allocation_preview(
                self.member,
                self.today,
                "3000",
                remaining_allocations=[{"payment_type_id": 9999999, "amount": "1300"}],
            )
