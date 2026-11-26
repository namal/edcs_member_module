from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from . import models as m, test_child_contribution_allocations
from .child_services import deposit_balance


class ChildAccountEntryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_child_contribution_allocations.ChildContributionAllocationTests.setUpTestData.__func__(
            cls
        )

    def setUp(self):
        self.client.force_login(self.operator)

    def payload(self, **changes):
        return {
            "member": self.member.pk,
            "payment_type": f"child-type:{self.child_type.pk}",
            "child_deposit": self.deposits[0].pk,
            "transaction_date": self.today.isoformat(),
            "debit": "",
            "credit": "250.00",
            "reference": "CHILD-DIRECT-1",
            "description": "Child savings deposit",
            **changes,
        }

    def post(self, **changes):
        return self.client.post(
            reverse("payment_transaction_create"), self.payload(**changes)
        )

    def balances(self, **changes):
        return self.client.get(
            reverse("payment_transaction_balances"),
            {
                "member": self.member.pk,
                "child_deposit": self.deposits[0].pk,
                "date": self.today.isoformat(),
                **changes,
            },
        )

    def revoke(self, *names):
        self.operator.user_permissions.remove(
            *Permission.objects.filter(
                content_type__app_label="members", codename__in=names
            )
        )

    def test_entry_form_has_child_types_accounts_and_saved_balances(self):
        page = self.client.get(
            reverse("payment_transaction_create"),
            {
                "member": self.member.pk,
                "payment_type": f"child-type:{self.child_type.pk}",
                "child_deposit": self.deposits[0].pk,
            },
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.child_type.name)
        self.assertContains(page, self.deposits[0].account_no)
        self.assertContains(page, "Child deposit balances and transactions")
        summary = page.context["balance_summary"]
        self.assertEqual(summary["selected_child_account"]["balance"], "100.00")
        self.assertEqual(summary["child_total"], "200.00")

    def test_direct_child_credit_posts_only_to_child_ledger(self):
        response = self.post()
        self.assertRedirects(
            response, reverse("child_deposit_detail", args=[self.deposits[0].pk])
        )
        entry = m.ChildDepositTransaction.objects.get()
        self.assertEqual(entry.credit, Decimal("250"))
        self.assertEqual(entry.debit, Decimal("0"))
        self.assertEqual(entry.interest_rate, self.deposits[0].rate_on(self.today))
        self.assertEqual(entry.created_by, self.operator)
        self.assertEqual(deposit_balance(self.deposits[0], self.today), Decimal("350"))
        self.assertFalse(m.PaymentTransaction.objects.exists())
        self.assertFalse(m.PaymentReceipt.objects.exists())
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

    def test_direct_child_debit_reduces_balance_and_preserves_member_balance(self):
        self.assertEqual(self.post(credit="", debit="25").status_code, 302)
        self.assertEqual(deposit_balance(self.deposits[0], self.today), Decimal("75"))
        summary = self.balances().json()
        self.assertEqual(summary["selected_child_account"]["balance"], "75.00")
        self.assertEqual(summary["total"], "0.00")
        self.assertEqual(
            summary["selected_child_account"]["transactions"][0]["debit"], "25.00"
        )

    def test_received_contributions_and_direct_entries_share_the_same_child_balance(
        self,
    ):
        receipt_case = (
            test_child_contribution_allocations.ChildContributionAllocationTests
        )
        data = receipt_case.preview(self, receipt_case.payload(self))
        self.assertEqual(
            self.client.post(reverse("contribution_create"), data).status_code, 302
        )
        self.assertEqual(self.post(credit="", debit="50").status_code, 302)
        account = self.balances().json()["selected_child_account"]
        self.assertEqual(account["balance"], "550.00")
        self.assertEqual(account["total_credit"], "500.00")
        self.assertEqual(account["total_debit"], "50.00")
        self.assertEqual(len(account["transactions"]), 2)
        receipt = m.PaymentReceipt.objects.get()
        self.assertIn(receipt.receipt_number, account["transactions"][1]["description"])
        self.assertEqual(
            m.PaymentTransaction.objects.aggregate(total=Sum("credit"))["total"],
            Decimal("2500"),
        )

    def test_child_statement_in_debit_credit_uses_opening_and_historical_running_balances(
        self,
    ):
        self.post()
        self.post(credit="", debit="25", reference="WITHDRAWAL")
        response = self.client.get(
            reverse("payment_transaction_list"),
            {
                "member": self.member.pk,
                "payment_type": f"child-type:{self.child_type.pk}",
                "child_deposit": self.deposits[0].pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["brought_forward"], Decimal("100"))
        self.assertEqual(response.context["total_credit"], Decimal("250"))
        self.assertEqual(response.context["total_debit"], Decimal("25"))
        self.assertEqual(response.context["final_balance"], Decimal("325"))
        self.assertEqual(
            [entry.running_balance for entry in response.context["page_obj"]],
            [Decimal("350"), Decimal("325")],
        )
        self.assertContains(response, self.deposits[0].account_no)
        self.assertContains(
            response,
            reverse(
                "child_entry_edit", args=[m.ChildDepositTransaction.objects.first().pk]
            ),
        )

    def test_recent_transactions_respect_as_of_and_keep_balance_before_display_limit(
        self,
    ):
        for index in range(1, 14):
            m.ChildDepositTransaction.objects.create(
                deposit=self.deposits[0],
                transaction_date=date(self.today.year, 2, index),
                credit=Decimal("10"),
            )
        response = self.balances(date=f"{self.today.year}-02-12")
        self.assertEqual(response.status_code, 200)
        account = response.json()["selected_child_account"]
        self.assertEqual(account["balance"], "220.00")
        self.assertEqual(len(account["transactions"]), 10)
        self.assertEqual(account["transactions"][0]["balance"], "220.00")
        self.assertEqual(account["transactions"][-1]["balance"], "130.00")
        self.assertEqual(account["total_credit"], "120.00")

    def test_invalid_entries_and_mismatched_targets_do_not_post(self):
        for changes in (
            {"child_deposit": ""},
            {"child_deposit": self.other_deposit.pk},
            {"credit": "0"},
            {"credit": "250", "debit": "1"},
            {"credit": "-1"},
            {"credit": "1.001"},
            {
                "transaction_date": (
                    self.deposits[0].opened_on - timedelta(days=1)
                ).isoformat()
            },
            {"payment_type": self.types["Special Deposit"].pk},
        ):
            with self.subTest(changes=changes):
                page = self.post(**changes)
                self.assertEqual(page.status_code, 200)
                self.assertTrue(page.context["form"].errors)
        self.assertFalse(m.ChildDepositTransaction.objects.exists())
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_inactive_child_type_or_account_cannot_receive_entries(self):
        for obj in (self.children[0], self.child_type, self.deposits[0]):
            obj.is_active = False
            obj.save()
            response = self.post()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            obj.is_active = True
            obj.save()
        self.assertFalse(m.ChildDepositTransaction.objects.exists())

    def test_posting_requires_child_add_and_transaction_add_permissions(self):
        self.revoke("add_childdeposit")
        response = self.post()
        self.assertTrue(response.context["form"].errors)
        self.revoke("add_paymenttransaction")
        self.assertEqual(self.post().status_code, 403)
        self.assertFalse(m.ChildDepositTransaction.objects.exists())

    def test_add_only_child_operator_sees_balance_but_not_transactions(self):
        self.revoke("view_childdeposit", "change_childdeposit")
        self.assertEqual(self.post().status_code, 302)
        response = self.balances()
        self.assertEqual(response.status_code, 200)
        account = response.json()["selected_child_account"]
        self.assertEqual(account["balance"], "350.00")
        self.assertNotIn("transactions", account)
        self.assertIsNone(account["statement_url"])
        response = self.client.get(
            reverse("payment_transaction_list"),
            {
                "member": self.member.pk,
                "payment_type": f"child-type:{self.child_type.pk}",
                "child_deposit": self.deposits[0].pk,
            },
        )
        self.assertTrue(response.context["form"].errors)

    def test_member_only_viewer_does_not_receive_child_financial_data(self):
        self.revoke("view_childdeposit", "add_childdeposit", "change_childdeposit")
        response = self.balances()
        self.assertEqual(response.status_code, 403)
        response = self.client.get(
            reverse("payment_transaction_balances"), {"member": self.member.pk}
        )
        self.assertNotIn("child_accounts", response.json())

    def test_member_balance_inquiry_lists_child_balances_and_full_statements(self):
        self.post()
        response = self.client.get(
            reverse("member_balance"), {"member": self.member.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["child_balance_summary"]["child_total"], "450.00"
        )
        self.assertEqual(response.context["totals"]["final_balance"], Decimal("0"))
        self.assertContains(
            response, reverse("child_deposit_detail", args=[self.deposits[0].pk])
        )

    def test_child_account_cannot_be_used_for_another_member(self):
        other = m.Member.objects.create(
            district=self.member.district,
            office_location=self.member.office_location,
            school=self.member.school,
            lager=self.member.lager,
            member_no="OTHER-ENTRY-PARENT",
            full_name="Other entry parent",
            salary="1000",
            office_address="Office",
            private_address="Home",
            phone_mobile="0771234567",
            gender="female",
            nic_no="199012345679",
        )
        self.assertTrue(self.post(member=other.pk).context["form"].errors)
        self.assertEqual(self.balances(member=other.pk).status_code, 404)
        self.assertFalse(m.ChildDepositTransaction.objects.exists())

    def test_closed_interest_period_cannot_be_changed_by_unified_entry_form(self):
        from .child_interest import save_child_interest_year, post_child_interest_year

        prior = date(self.today.year - 1, 1, 1)
        self.deposits[0].opened_on = prior
        self.deposits[0].save()
        record = save_child_interest_year(self.deposits[0], prior, today=self.today)
        post_child_interest_year(record.pk, actor=self.admin, today=self.today)
        response = self.post(transaction_date=f"{prior.year}-06-01")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(m.ChildDepositTransaction.objects.count(), 1)
