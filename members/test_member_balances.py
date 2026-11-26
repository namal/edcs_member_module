from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from . import test_transactions
from .balance_services import member_balance_inquiry
from .contribution_services import allocation_preview, post_receipt
from .deposit_interest import post_interest_year, save_interest_year
from .models import (
    DepositInterestYear,
    Lager,
    OpeningBalance,
    OpeningBalanceAmount,
    PaymentPlan,
    PaymentPlanAmount,
    PaymentReceipt,
    PaymentTransaction,
)


@override_settings(FINANCIAL_YEAR_START_MONTH=1)
class MemberBalanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_transactions.TransactionTests.setUpTestData.__func__(cls)
        cls.payment_type.interest_rate = Decimal("8.00")
        cls.payment_type.save()
        cls.plan = PaymentPlan.objects.create(
            member=cls.member, agreement_date=date(2025, 1, 1)
        )
        PaymentPlanAmount.objects.create(
            plan=cls.plan, payment_type=cls.payment_type, amount="500"
        )

    def setUp(self):
        self.today = patch(
            "django.utils.timezone.localdate", return_value=date(2026, 11, 25)
        )
        self.today.start()
        self.addCleanup(self.today.stop)
        self.client.force_login(self.user)

    def opening(self, amount="1000", day=date(2026, 1, 1)):
        parent = OpeningBalance.objects.create(member=self.member, balance_date=day)
        return OpeningBalanceAmount.objects.create(
            opening_balance=parent, payment_type=self.payment_type, amount=amount
        )

    def entry(self, day, credit="0", debit="0", payment_type=None):
        return PaymentTransaction.objects.create(
            member=self.member,
            payment_type=payment_type or self.payment_type,
            transaction_date=day,
            credit=Decimal(credit),
            debit=Decimal(debit),
        )

    def receipt(self):
        day, amount = date(2026, 11, 5), Decimal("500")
        preview = allocation_preview(self.member, day, amount)
        return post_receipt(
            self.user,
            {
                "member": self.member,
                "received_on": day,
                "amount": amount,
                "payment_type": None,
                "reference": "PAID-001",
                "description": "November contribution",
                "operation_key": uuid4(),
            },
            preview["fingerprint"],
        )

    def inquiry(self, **filters):
        return self.client.get(
            reverse("member_balance"), {"member": self.member.pk, **filters}
        )

    def test_monthly_balance_counts_paid_contributions_once_and_is_read_only(self):
        self.opening()
        self.entry(date(2026, 10, 1), credit="200")
        self.receipt()
        self.entry(date(2026, 11, 6), credit="100")
        self.entry(date(2026, 11, 7), debit="50")
        self.entry(date(2026, 11, 26), credit="9999")
        counts = (
            PaymentTransaction.objects.count(),
            PaymentReceipt.objects.count(),
            DepositInterestYear.objects.count(),
        )
        response = self.inquiry()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["as_of"], date(2026, 11, 25))
        self.assertEqual(
            response.context["totals"],
            {
                "brought_forward": Decimal("1200"),
                "contribution": Decimal("500"),
                "interest": Decimal("0"),
                "other_credit": Decimal("100"),
                "total_debit": Decimal("50"),
                "final_balance": Decimal("1750"),
            },
        )
        self.assertContains(response, "data-member-balance>1,750.00</output>")
        self.assertEqual(response.context["page_obj"][-1]["balance"], Decimal("1750"))
        self.assertEqual(
            counts,
            (
                PaymentTransaction.objects.count(),
                PaymentReceipt.objects.count(),
                DepositInterestYear.objects.count(),
            ),
        )

    def test_unpaid_plan_is_reference_only(self):
        self.opening()
        response = self.inquiry()
        self.assertEqual(response.context["plan"].monthly_total, Decimal("500"))
        self.assertEqual(response.context["totals"]["contribution"], Decimal("0"))
        self.assertEqual(response.context["totals"]["final_balance"], Decimal("1000"))

    def test_history_filter_and_pagination_keep_earlier_running_balance(self):
        self.opening()
        self.entry(date(2026, 10, 5), credit="200")
        for day in range(1, 13):
            self.entry(date(2026, 11, day), credit="10")
        response = self.inquiry(history="month", page=2)
        self.assertEqual(response.context["history_opening"], Decimal("1200"))
        self.assertEqual(response.context["page_obj"].paginator.count, 12)
        self.assertEqual(response.context["page_obj"][0]["balance"], Decimal("1310"))
        self.assertEqual(response.context["page_obj"][-1]["balance"], Decimal("1320"))
        self.assertEqual(response.context["totals"]["final_balance"], Decimal("1320"))

    def test_opening_date_does_not_discard_other_account_history(self):
        self.opening(day=date(2026, 11, 10))
        self.entry(date(2026, 11, 1), credit="9999")
        self.entry(date(2026, 11, 1), credit="75", payment_type=self.other_type)
        self.entry(date(2026, 11, 10), credit="25")
        response = self.inquiry()
        events = list(response.context["page_obj"])
        self.assertEqual(
            [row["balance"] for row in events],
            [Decimal("75"), Decimal("1075"), Decimal("1100")],
        )
        self.assertEqual(response.context["totals"]["final_balance"], Decimal("1100"))

    def test_historical_month_excludes_later_opening_and_transactions(self):
        self.opening(day=date(2026, 11, 1))
        self.entry(date(2026, 10, 31), credit="75")
        self.entry(date(2026, 11, 1), credit="25")
        response = self.inquiry(month="2026-10")
        self.assertEqual(response.context["as_of"], date(2026, 10, 31))
        self.assertEqual(response.context["totals"]["final_balance"], Decimal("75"))

    def test_completed_quarter_interest_is_separate_from_posted_bank_balance(self):
        self.opening()
        response = self.inquiry(month="2026-04")
        self.assertEqual(response.context["accrued_interest"], Decimal("20"))
        self.assertEqual(response.context["totals"]["final_balance"], Decimal("1000"))
        self.assertEqual(
            response.context["balance_with_accrued_interest"], Decimal("1020")
        )
        self.assertEqual(len(response.context["quarter_rows"]), 1)
        self.assertEqual(
            self.inquiry(month="2026-02").context["accrued_interest"], Decimal("0")
        )

    def test_posted_interest_is_included_once_and_past_quarters_obey_as_of(self):
        self.opening(day=date(2025, 1, 1))
        record = save_interest_year(
            self.member, self.payment_type, date(2025, 1, 1), today=date(2026, 1, 1)
        )
        post_interest_year(record.pk, actor=self.admin, today=date(2026, 1, 1))
        response = self.inquiry(month="2025-12")
        self.assertEqual(response.context["totals"]["interest"], Decimal("80"))
        self.assertEqual(response.context["totals"]["other_credit"], Decimal("0"))
        self.assertEqual(response.context["totals"]["final_balance"], Decimal("1080"))
        self.assertEqual(response.context["accrued_interest"], Decimal("0"))
        historical = self.inquiry(month="2025-03")
        self.assertEqual(len(historical.context["quarter_rows"]), 1)
        self.assertEqual(historical.context["accrued_interest"], Decimal("20"))
        self.assertEqual(historical.context["totals"]["final_balance"], Decimal("1000"))

    def test_interest_uses_minimum_month_end_and_pension_exclusion(self):
        self.opening()
        self.entry(date(2026, 2, 1), debit="400")
        self.entry(date(2026, 3, 1), credit="200")
        response = self.inquiry(month="2026-03")
        self.assertEqual(response.context["accrued_interest"], Decimal("12"))
        self.member.pension_date = date(2026, 3, 2)
        self.member.save()
        excluded = self.inquiry(month="2026-03")
        self.assertEqual(excluded.context["accrued_interest"], Decimal("0"))

    @override_settings(FINANCIAL_YEAR_START_MONTH=4)
    def test_non_calendar_financial_year_is_used(self):
        self.opening(day=date(2025, 4, 1))
        response = self.inquiry(month="2026-02")
        self.assertEqual(response.context["interest_year_start"], date(2025, 4, 1))
        self.assertEqual(response.context["accrued_interest"], Decimal("60"))

    def test_invalid_selection_never_displays_financial_data(self):
        for filters in (
            {"month": "2026-12"},
            {"month": "invalid"},
            {"member": self.foreign.pk},
            {"member": "invalid"},
            {"history": "invalid"},
        ):
            with self.subTest(filters=filters):
                response = self.inquiry(**filters)
                self.assertTrue(response.context["form"].errors)
                self.assertNotIn("totals", response.context)
        Lager.objects.filter(pk=self.other.lager_id).update(
            maintenance_officer=self.admin
        )
        response = self.inquiry(member=self.other.pk)
        self.assertTrue(response.context["form"].errors)

    def test_permission_and_login_are_required(self):
        self.client.logout()
        self.assertEqual(self.inquiry().status_code, 302)
        self.user.user_permissions.set(
            Permission.objects.filter(codename="add_paymenttransaction")
        )
        self.client.force_login(self.user)
        self.assertEqual(self.inquiry().status_code, 403)

    def test_empty_member_has_zero_balance_and_initial_page_is_usable(self):
        response = self.client.get(reverse("member_balance"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a member")
        result = member_balance_inquiry(
            self.other, date(2026, 11, 1), date(2026, 11, 25)
        )
        self.assertEqual(result["totals"]["final_balance"], Decimal("0"))
        self.assertEqual(result["events"], [])
