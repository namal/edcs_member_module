from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import test_child_deposits
from .child_interest import (
    child_calculation_fingerprint,
    child_interest_report,
    post_child_interest_year,
    quarter_rate,
    save_child_interest_year,
)
from .child_forms import ChildDepositForm
from .child_services import child_statement, deposit_balance
from .models import (
    Child,
    ChildDeposit,
    ChildDepositInterestYear,
    ChildDepositTransaction,
    DepositInterestYear,
    OpeningBalance,
    OpeningBalanceAmount,
    PaymentType,
)


@override_settings(FINANCIAL_YEAR_START_MONTH=1)
class ChildInterestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_child_deposits.ChildDepositTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client.force_login(self.user)

    def entry(self, day, credit="0", debit="0", deposit=None):
        account = deposit or self.deposit
        return ChildDepositTransaction.objects.create(
            deposit=account,
            transaction_date=day,
            credit=credit,
            debit=debit,
            interest_rate=account.rate_on(day),
        )

    def report(self, through=date(2026, 6, 30), deposit=None):
        return child_interest_report(
            ChildDeposit.objects.filter(pk=(deposit or self.deposit).pk),
            date(2026, 1, 1),
            as_of=through,
        )[0]["record"]

    def example(self):
        self.deposit.opening_amount = Decimal("2000.00")
        self.deposit.save()
        self.entry(date(2026, 2, 10), credit="1000")
        self.entry(date(2026, 3, 15), debit="1500")
        self.entry(date(2026, 4, 5), debit="200")
        self.entry(date(2026, 5, 10), credit="1000")
        self.entry(date(2026, 6, 15), debit="500")

    def save_and_post(self, deposit=None):
        account = deposit or self.deposit
        saved = save_child_interest_year(
            account, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        return post_child_interest_year(
            saved.pk, actor=self.admin, today=date(2027, 1, 1)
        )

    def test_same_quarterly_example_30_plus_26_equals_56_for_child(self):
        self.example()
        record = self.report()
        self.assertEqual(
            [m["balance"] for m in record.quarters[0]["months"]],
            ["2000.00", "3000.00", "1500.00"],
        )
        self.assertEqual(record.quarters[0]["interest"], "30.00")
        self.assertEqual(record.quarters[1]["interest"], "26.00")
        self.assertEqual(record.annual_interest, Decimal("56.00"))
        self.assertFalse(ChildDepositInterestYear.objects.exists())
        self.assertEqual(ChildDepositTransaction.objects.count(), 5)

    def test_daily_low_is_not_month_end_low_and_last_day_entries_count(self):
        self.entry(date(2026, 1, 15), debit="900")
        self.entry(date(2026, 1, 31), credit="900")
        self.assertEqual(self.report().quarters[0]["interest"], "20.00")

    def test_child_rates_and_sibling_accounts_remain_separate(self):
        older = Child.objects.create(
            member=self.member, name="Older Child", date_of_birth=date(2000, 1, 1)
        )
        adult = ChildDeposit.objects.create(
            child=older,
            deposit_type=self.deposit_type,
            opened_on=date(2026, 1, 1),
            opening_amount="1000",
        )
        self.assertEqual(self.report(deposit=adult).quarters[0]["interest"], "12.50")
        self.assertEqual(
            self.report(deposit=self.deposit).quarters[0]["interest"], "20.00"
        )
        self.assertEqual(
            self.report(deposit=self.sibling_deposit).quarters[0]["interest"], "1.00"
        )

    def test_birthday_mid_quarter_excludes_the_whole_quarter(self):
        self.child.date_of_birth = date(2008, 2, 15)
        self.child.save()
        quarter = self.report().quarters[0]
        self.assertIsNone(quarter["annual_rate"])
        self.assertEqual(quarter["interest"], "0.00")
        self.assertTrue(quarter["excluded"])
        self.assertIn("2026-02-15", quarter["exclusion_reason"])
        self.assertEqual(self.report().quarters[1]["interest"], "12.50")

    def test_birthday_on_last_day_excludes_only_that_quarter(self):
        self.child.date_of_birth = date(2008, 3, 31)
        self.child.save()
        record = self.report()
        self.assertEqual(record.quarters[0]["interest"], "0.00")
        self.assertEqual(record.quarters[1]["interest"], "12.50")

    def test_birthday_on_first_day_excludes_its_calendar_quarter(self):
        self.child.date_of_birth = date(2008, 1, 1)
        self.child.save()
        self.assertEqual(self.report().quarters[0]["interest"], "0.00")
        self.assertEqual(self.report().quarters[1]["interest"], "12.50")

    def test_equal_age_rates_do_not_reenable_the_excluded_quarter(self):
        self.deposit_type.rate_from_threshold = Decimal("8.00")
        self.deposit_type.save()
        self.child.date_of_birth = date(2008, 2, 15)
        self.child.save()
        self.assertEqual(self.report().quarters[0]["interest"], "0.00")

    def test_zero_balance_can_close_without_a_birthday_method_setting(self):
        self.deposit.opening_amount = Decimal("0.00")
        self.deposit.save()
        self.child.date_of_birth = date(2008, 2, 15)
        self.child.save()
        posted = self.save_and_post()
        self.assertEqual(posted.annual_interest, Decimal("0.00"))
        self.assertFalse(ChildDepositTransaction.objects.exists())

    def test_child_posting_route_and_carry_forward_history(self):
        self.deposit.opened_on = date(2025, 1, 1)
        self.deposit.save()
        saved = save_child_interest_year(self.deposit, date(2025, 1, 1))
        url = reverse("child_interest_edit", args=[saved.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        fingerprint = response.context["calculation_hash"]
        self.client.post(url, {"calculation_hash": fingerprint})
        self.client.post(url, {"calculation_hash": fingerprint})
        saved.refresh_from_db()
        self.assertIsNotNone(saved.posted_at)
        self.assertEqual(
            ChildDepositTransaction.objects.filter(
                interest_posting__isnull=False
            ).count(),
            1,
        )
        response = self.client.get(
            reverse("child_deposit_detail", args=[self.deposit.pk])
        )
        self.assertContains(
            response, "Annual interest and opening balances carried forward"
        )
        self.assertContains(response, "1,080.00")
        response = self.client.post(
            reverse("child_entry_delete", args=[saved.ledger_transaction_id])
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            ChildDepositTransaction.objects.filter(
                pk=saved.ledger_transaction_id
            ).exists()
        )

    def test_leap_day_rate_transition_matches_existing_child_age_rule(self):
        self.child.date_of_birth = date(2008, 2, 29)
        self.child.save()
        quarter = self.report().quarters[0]
        self.assertIn("2026-03-01", quarter["exclusion_reason"])
        self.assertEqual(quarter["interest"], "0.00")

    def test_annual_posting_adds_only_other_completed_quarters(self):
        self.child.date_of_birth = date(2008, 2, 15)
        self.child.save()
        self.assertTrue(self.report().quarters[0]["excluded"])
        saved = save_child_interest_year(
            self.deposit, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        posted = post_child_interest_year(saved.pk, today=date(2027, 1, 1))
        self.assertEqual(posted.annual_interest, Decimal("37.50"))
        self.assertEqual(posted.next_opening_balance, Decimal("1037.50"))
        self.assertEqual(posted.ledger_transaction.credit, Decimal("37.50"))

    def test_parent_pension_does_not_exclude_childs_quarter(self):
        self.member.pension_date = date(2026, 5, 15)
        self.member.save()
        record = self.report()
        self.assertFalse(record.quarters[1]["excluded"])
        self.assertEqual(record.quarters[1]["interest"], "20.00")

    def test_mid_quarter_opening_and_negative_balances_earn_zero(self):
        later = ChildDeposit.objects.create(
            child=self.child,
            deposit_type=self.deposit_type,
            opened_on=date(2026, 2, 15),
            opening_amount="1000",
        )
        self.assertEqual(self.report(deposit=later).quarters[0]["interest"], "0.00")
        self.entry(date(2026, 2, 1), debit="2000")
        self.assertEqual(self.report().quarters[0]["minimum"], "-1000.00")
        self.assertEqual(self.report().quarters[0]["interest"], "0.00")

    def test_incomplete_quarter_remains_pending(self):
        record = self.report(through=date(2026, 3, 30))
        self.assertFalse(record.quarters[0]["complete"])
        self.assertIsNone(record.quarters[0]["interest"])

    def test_year_end_posts_once_and_next_year_balance_includes_interest(self):
        self.example()
        saved = save_child_interest_year(
            self.deposit, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        with self.assertRaises(ValidationError):
            post_child_interest_year(saved.pk, today=date(2026, 12, 31))
        posted = post_child_interest_year(saved.pk, today=date(2027, 1, 1))
        self.assertEqual(posted.annual_interest, Decimal("128.00"))
        self.assertEqual(posted.next_opening_balance, Decimal("1928.00"))
        self.assertEqual(posted.ledger_transaction.transaction_date, date(2026, 12, 31))
        self.assertEqual(
            deposit_balance(self.deposit, date(2027, 1, 1)), Decimal("1928.00")
        )
        self.assertEqual(
            child_statement(self.deposit, date(2027, 1, 1), date(2027, 1, 1))[
                "brought_forward"
            ],
            Decimal("1928.00"),
        )
        self.assertEqual(
            post_child_interest_year(
                saved.pk, today=date(2027, 1, 2)
            ).ledger_transaction_id,
            posted.ledger_transaction_id,
        )
        self.assertEqual(ChildDepositTransaction.objects.count(), 6)
        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.opening_amount, Decimal("2000.00"))

    def test_posted_snapshot_and_rate_rules_are_immutable(self):
        posted = self.save_and_post()
        self.deposit_type.rate_below_threshold = Decimal("12")
        self.deposit_type.save()
        self.assertEqual(self.report().annual_interest, Decimal("80.00"))
        posted.annual_interest = Decimal("999")
        with self.assertRaises(ValidationError):
            posted.save()
        with self.assertRaises(ProtectedError):
            posted.delete()
        with self.assertRaises(ProtectedError):
            posted.ledger_transaction.delete()

    def test_next_year_applies_new_type_rates_to_the_carried_balance(self):
        self.save_and_post()
        self.deposit_type.rate_below_threshold = Decimal("10.00")
        self.deposit_type.save()
        record = child_interest_report(
            ChildDeposit.objects.filter(pk=self.deposit.pk),
            date(2027, 1, 1),
            as_of=date(2027, 3, 31),
        )[0]["record"]
        self.assertEqual(record.quarters[0]["minimum"], "1080.00")
        self.assertEqual(record.quarters[0]["interest"], "27.00")

    def test_closed_year_protects_birth_date_opening_and_historical_entries(self):
        entry = self.entry(date(2026, 2, 1), credit="100")
        self.save_and_post()
        self.child.date_of_birth = date(2014, 1, 1)
        with self.assertRaises(ValidationError):
            self.child.save()
        self.deposit.opening_amount = Decimal("2000")
        with self.assertRaises(ValidationError):
            self.deposit.save()
        with self.assertRaises(ValidationError):
            self.entry(date(2026, 3, 1), credit="100")
        with self.assertRaises(ProtectedError):
            entry.delete()
        entry.reference = "Corrected reference"
        entry.save()
        self.entry(date(2027, 1, 1), credit="100")

    def test_zero_interest_year_locks_opening_without_zero_ledger_entry(self):
        self.deposit_type.rate_below_threshold = Decimal("0")
        self.deposit_type.save()
        self.save_and_post()
        self.assertFalse(ChildDepositTransaction.objects.exists())
        form = ChildDepositForm(instance=self.deposit, user=self.user)
        self.assertTrue(form.fields["opening_amount"].disabled)

    def test_inactive_child_and_type_retain_interest_on_saved_funds(self):
        self.child.is_active = False
        self.child.save()
        self.deposit.is_active = False
        self.deposit.save()
        self.deposit_type.is_active = False
        self.deposit_type.save()
        posted = self.save_and_post()
        self.assertEqual(posted.annual_interest, Decimal("80.00"))
        self.assertEqual(posted.ledger_transaction.credit, Decimal("80.00"))

    def test_changed_ledger_after_review_cannot_post_stale_calculation(self):
        saved = save_child_interest_year(
            self.deposit, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        fingerprint = child_calculation_fingerprint(saved)
        self.entry(date(2026, 2, 1), credit="100")
        with self.assertRaises(ValidationError):
            post_child_interest_year(
                saved.pk, today=date(2027, 1, 1), expected_fingerprint=fingerprint
            )
        saved.refresh_from_db()
        self.assertIsNone(saved.posted_at)

    def test_posting_failure_rolls_back_ledger_credit(self):
        saved = save_child_interest_year(
            self.deposit, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        original = ChildDepositInterestYear.save

        def fail_post(record, *args, **kwargs):
            if record.posted_at:
                raise RuntimeError("posting failure")
            return original(record, *args, **kwargs)

        with patch.object(ChildDepositInterestYear, "save", fail_post):
            with self.assertRaises(RuntimeError):
                post_child_interest_year(saved.pk, today=date(2027, 1, 1))
        self.assertFalse(ChildDepositTransaction.objects.exists())

    def test_combined_report_and_account_group_filters(self):
        payment_type = PaymentType.objects.get(name="Special Deposit")
        payment_type.interest_rate = Decimal("8")
        payment_type.save()
        parent = OpeningBalance.objects.create(
            member=self.member, balance_date=date(2026, 1, 1)
        )
        OpeningBalanceAmount.objects.create(
            opening_balance=parent, payment_type=payment_type, amount="2000"
        )
        response = self.client.get(reverse("deposit_interest_list"), {"year": 2026})
        self.assertEqual(response.context["account_count"], 3)
        self.assertContains(response, self.child.name)
        self.assertNotContains(response, self.foreign_child.name)
        for group, count in (("members", 1), ("children", 2)):
            response = self.client.get(
                reverse("deposit_interest_list"), {"year": 2026, "account_group": group}
            )
            self.assertEqual(response.context["account_count"], count)

    def test_child_filter_and_detail_routes_enforce_district_access(self):
        saved = save_child_interest_year(
            self.foreign_deposit, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        for action in ("detail", "edit", "delete"):
            for method in ("get", "post"):
                self.assertEqual(
                    getattr(self.client, method)(
                        reverse(f"child_interest_{action}", args=[saved.pk])
                    ).status_code,
                    404,
                )
        for field, value in (
            ("child", self.foreign_child.pk),
            ("deposit", self.foreign_deposit.pk),
        ):
            response = self.client.get(
                reverse("deposit_interest_list"), {"year": 2026, field: value}
            )
            self.assertTrue(response.context["form"].errors)
        response = self.client.get(
            reverse("deposit_interest_list"),
            {
                "year": 2026,
                "account_group": "children",
                "payment_type": PaymentType.objects.first().pk,
            },
        )
        self.assertTrue(response.context["form"].errors)

    def test_valid_child_account_reference_filter_shows_only_that_account(self):
        for field, value in (("deposit", self.deposit.pk), ("child", self.child.pk)):
            response = self.client.get(
                reverse("deposit_interest_list"),
                {"year": 2026, "account_group": "children", field: value},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["account_count"], 1)
            self.assertEqual(
                response.context["page_obj"][0]["deposit_id"], self.deposit.pk
            )

    def test_child_deposit_permission_does_not_grant_interest_access(self):
        self.user.user_permissions.set(
            Permission.objects.filter(codename="view_childdeposit")
        )
        self.assertEqual(
            self.client.get(reverse("deposit_interest_list")).status_code, 403
        )

    def test_child_year_has_one_record_per_account_and_database_guards(self):
        saved = save_child_interest_year(
            self.deposit, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        self.assertEqual(
            save_child_interest_year(self.deposit, date(2026, 1, 1)).pk, saved.pk
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChildDepositInterestYear.objects.filter(pk=saved.pk).update(
                annual_interest="-1"
            )

    def test_scheduler_processes_member_and_child_accounts_and_posts_once(self):
        payment_type = PaymentType.objects.get(name="Special Deposit")
        payment_type.interest_rate = "8.00"
        payment_type.save()
        parent = OpeningBalance.objects.create(
            member=self.member, balance_date=date(2026, 1, 1)
        )
        OpeningBalanceAmount.objects.create(
            opening_balance=parent, payment_type=payment_type, amount="2000"
        )
        with patch("django.utils.timezone.localdate", return_value=date(2027, 1, 1)):
            for _ in range(2):
                call_command(
                    "process_deposit_interest",
                    from_year=2026,
                    post_ended=True,
                    stdout=StringIO(),
                )
        self.assertEqual(
            DepositInterestYear.objects.filter(posted_at__isnull=False).count(), 1
        )
        self.assertEqual(
            ChildDepositInterestYear.objects.filter(posted_at__isnull=False).count(), 3
        )
        self.assertEqual(
            ChildDepositTransaction.objects.filter(
                interest_posting__isnull=False
            ).count(),
            3,
        )
