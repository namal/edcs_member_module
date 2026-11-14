from datetime import date
from decimal import Decimal
from io import StringIO
from subprocess import CompletedProcess
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import test_transactions
from .deposit_interest import (
    calculation_fingerprint,
    current_financial_year,
    interest_report,
    post_interest_year,
    save_interest_year,
)
from .models import (
    DepositInterestYear,
    Member,
    OpeningBalance,
    OpeningBalanceAmount,
    PaymentTransaction,
    PaymentType,
)
from .opening_balance_forms import OpeningBalanceForm
from .transaction_balances import account_statement, member_balances


@override_settings(FINANCIAL_YEAR_START_MONTH=1)
class DepositInterestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_transactions.TransactionTests.setUpTestData.__func__(cls)
        cls.payment_type.interest_rate = Decimal("8.00")
        cls.payment_type.save()
        cls.other_type.interest_rate = Decimal("4.00")
        cls.other_type.save()
        cls.user.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )

    def setUp(self):
        self.client.force_login(self.user)

    def opening(
        self, amount="2000.00", member=None, payment_type=None, day=date(2026, 1, 1)
    ):
        parent, _ = OpeningBalance.objects.get_or_create(
            member=member or self.member, defaults={"balance_date": day}
        )
        return OpeningBalanceAmount.objects.create(
            opening_balance=parent,
            payment_type=payment_type or self.payment_type,
            amount=amount,
        )

    def entry(self, day, credit="0", debit="0", member=None, payment_type=None):
        return PaymentTransaction.objects.create(
            member=member or self.member,
            payment_type=payment_type or self.payment_type,
            transaction_date=day,
            credit=credit,
            debit=debit,
        )

    def example(self):
        self.opening()
        self.entry(date(2026, 2, 10), credit="1000")
        self.entry(date(2026, 3, 15), debit="1500")
        self.entry(date(2026, 4, 5), debit="200")
        self.entry(date(2026, 5, 10), credit="1000")
        self.entry(date(2026, 6, 15), debit="500")

    def report(self, as_of=date(2026, 6, 30), **kwargs):
        return interest_report(
            Member.objects.filter(pk=self.member.pk),
            date(2026, 1, 1),
            as_of=as_of,
            **kwargs,
        )

    def test_user_example_30_plus_26_equals_56_without_quarterly_compounding(self):
        self.example()
        record = self.report()[0]["record"]
        self.assertEqual(
            [item["balance"] for item in record.quarters[0]["months"]],
            ["2000.00", "3000.00", "1500.00"],
        )
        self.assertEqual(record.quarters[0]["minimum"], "1500.00")
        self.assertEqual(record.quarters[0]["interest"], "30.00")
        self.assertEqual(record.quarters[1]["minimum"], "1300.00")
        self.assertEqual(record.quarters[1]["interest"], "26.00")
        self.assertEqual(record.annual_interest, Decimal("56.00"))
        self.assertFalse(record.quarters[2]["complete"])
        self.assertIsNone(record.quarters[2]["interest"])
        self.assertEqual(PaymentTransaction.objects.count(), 5)
        self.assertFalse(DepositInterestYear.objects.exists())

    def test_month_end_not_daily_minimum_and_includes_last_day_transactions(self):
        self.opening()
        self.entry(date(2026, 1, 15), debit="1900")
        self.entry(date(2026, 1, 31), credit="1900")
        record = self.report()[0]["record"]
        self.assertEqual(record.quarters[0]["minimum"], "2000.00")
        self.assertEqual(record.quarters[0]["interest"], "40.00")

    def test_pension_date_excludes_its_quarter_and_keeps_other_completed_quarters(self):
        self.opening()
        self.member.pension_date = date(2026, 5, 15)
        self.member.save()
        record = self.report(as_of=date(2026, 12, 31))[0]["record"]
        self.assertEqual(
            [q["interest"] for q in record.quarters],
            ["40.00", "0.00", "40.00", "40.00"],
        )
        self.assertEqual(
            [q["excluded"] for q in record.quarters], [False, True, False, False]
        )
        self.assertIn("2026-05-15", record.quarters[1]["exclusion_reason"])
        self.assertEqual(record.annual_interest, Decimal("120.00"))

    def test_pension_boundary_dates_exclude_exactly_one_calendar_quarter(self):
        self.opening()
        for day, index in (
            (date(2026, 1, 1), 0),
            (date(2026, 3, 31), 0),
            (date(2026, 4, 1), 1),
            (date(2026, 12, 31), 3),
        ):
            with self.subTest(day=day):
                self.member.pension_date = day
                self.member.save()
                quarters = self.report(as_of=date(2026, 12, 31))[0]["record"].quarters
                self.assertEqual(
                    [q["number"] for q in quarters if q["excluded"]], [index + 1]
                )

    def test_pension_exclusion_applies_to_all_own_types_and_not_other_members(self):
        self.opening()
        self.opening("1000", payment_type=self.other_type)
        self.opening("2000", member=self.other)
        self.member.pension_date = date(2026, 5, 15)
        self.member.save()
        rows = interest_report(
            Member.objects.filter(pk__in=[self.member.pk, self.other.pk]),
            date(2026, 1, 1),
            as_of=date(2026, 6, 30),
        )
        own = [row for row in rows if row["member_id"] == self.member.pk]
        self.assertTrue(
            all(row["record"].quarters[1]["interest"] == "0.00" for row in own)
        )
        other = next(row for row in rows if row["member_id"] == self.other.pk)
        self.assertEqual(other["record"].quarters[1]["interest"], "40.00")

    def test_pension_year_posts_only_eligible_interest_and_carries_forward(self):
        self.opening()
        self.member.pension_date = date(2026, 5, 15)
        self.member.save()
        record = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        posted = post_interest_year(record.pk, today=date(2027, 1, 1))
        self.assertEqual(posted.annual_interest, Decimal("120.00"))
        self.assertEqual(posted.next_opening_balance, Decimal("2120.00"))
        self.assertEqual(posted.ledger_transaction.credit, Decimal("120.00"))
        next_year = interest_report(
            Member.objects.filter(pk=self.member.pk),
            date(2027, 1, 1),
            as_of=date(2027, 12, 31),
        )[0]["record"]
        self.assertFalse(any(q["excluded"] for q in next_year.quarters))

    def test_pension_date_cannot_rewrite_posted_year_but_can_be_recorded_for_future(
        self,
    ):
        self.posted_year()
        self.member.pension_date = date(2026, 5, 15)
        with self.assertRaises(ValidationError):
            self.member.save()
        self.member.pension_date = date(2027, 5, 15)
        self.member.save()
        self.assertEqual(
            Member.objects.get(pk=self.member.pk).pension_date, date(2027, 5, 15)
        )

    def test_pension_date_used_in_posted_year_cannot_be_removed_or_moved(self):
        self.opening()
        self.member.pension_date = date(2026, 5, 15)
        self.member.save()
        record = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        post_interest_year(record.pk, today=date(2027, 1, 1))
        for value in (None, date(2027, 5, 15)):
            self.member.pension_date = value
            with self.assertRaises(ValidationError):
                self.member.save()

    def test_unfinished_pension_quarter_does_not_contribute_to_annual_interest(self):
        self.opening()
        self.member.pension_date = date(2026, 11, 15)
        self.member.save()
        record = self.report(as_of=date(2026, 11, 30))[0]["record"]
        self.assertEqual(record.annual_interest, Decimal("120.00"))
        self.assertTrue(record.quarters[3]["excluded"])
        self.assertFalse(record.quarters[3]["complete"])
        self.assertIsNone(record.quarters[3]["interest"])

    def test_member_and_payment_type_rates_are_calculated_separately(self):
        self.opening()
        self.opening("1000", payment_type=self.other_type)
        self.opening("3000", member=self.other)
        rows = interest_report(
            Member.objects.filter(pk__in=[self.member.pk, self.other.pk]),
            date(2026, 1, 1),
            as_of=date(2026, 3, 31),
        )
        totals = {
            (row["member_id"], row["payment_type_id"]): row["record"].annual_interest
            for row in rows
        }
        self.assertEqual(
            totals[(self.member.pk, self.payment_type.pk)], Decimal("40.00")
        )
        self.assertEqual(totals[(self.member.pk, self.other_type.pk)], Decimal("10.00"))
        self.assertEqual(
            totals[(self.other.pk, self.payment_type.pk)], Decimal("60.00")
        )

    def test_before_account_opening_zero_month_reduces_quarter_minimum_to_zero(self):
        self.opening("1000", day=date(2026, 2, 15))
        self.assertEqual(self.report()[0]["record"].quarters[0]["interest"], "0.00")

    def test_negative_minimum_does_not_charge_negative_interest(self):
        self.opening("100")
        self.entry(date(2026, 2, 1), debit="200")
        record = self.report()[0]["record"]
        self.assertEqual(record.quarters[0]["minimum"], "-100.00")
        self.assertEqual(record.quarters[0]["interest"], "0.00")

    def test_quarters_remain_pending_until_all_three_month_ends_complete(self):
        self.opening()
        record = self.report(as_of=date(2026, 3, 30))[0]["record"]
        self.assertIsNone(record.quarters[0]["months"][2]["balance"])
        self.assertEqual(record.annual_interest, Decimal("0.00"))
        self.assertFalse(self.report(as_of=date(2025, 12, 31)))

    def test_inactive_payment_type_with_history_still_earns_interest(self):
        self.opening()
        self.payment_type.is_active = False
        self.payment_type.save()
        self.assertEqual(self.report()[0]["record"].annual_interest, Decimal("80.00"))

    def test_rounding_uses_two_decimal_places_per_quarter(self):
        self.opening("0.25")
        record = self.report(as_of=date(2026, 12, 31))[0]["record"]
        self.assertEqual(record.quarters[0]["interest"], "0.01")
        self.assertEqual(record.annual_interest, Decimal("0.04"))

    def test_leap_year_and_non_january_financial_year(self):
        self.opening(day=date(2024, 1, 1))
        record = interest_report(
            Member.objects.filter(pk=self.member.pk),
            date(2024, 1, 1),
            as_of=date(2024, 3, 31),
        )[0]["record"]
        self.assertEqual(record.quarters[0]["months"][1]["date"], "2024-02-29")
        with override_settings(FINANCIAL_YEAR_START_MONTH=4):
            self.assertEqual(current_financial_year(date(2026, 1, 1)), 2025)
            record = interest_report(
                Member.objects.filter(pk=self.member.pk),
                date(2025, 4, 1),
                as_of=date(2026, 3, 31),
            )[0]["record"]
            self.assertEqual(record.year_end, date(2026, 3, 31))
            self.assertEqual(record.quarters[3]["months"][0]["date"], "2026-01-31")

    def test_save_updates_one_draft_without_posting_interest(self):
        self.example()
        first = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 6, 30)
        )
        self.entry(date(2026, 6, 30), debit="1000")
        second = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 6, 30)
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(DepositInterestYear.objects.count(), 1)
        self.assertEqual(second.annual_interest, Decimal("46.00"))
        self.assertIsNone(second.ledger_transaction_id)

    def test_year_end_posts_once_and_carries_1928_into_next_year(self):
        self.example()
        record = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        self.assertEqual(record.annual_interest, Decimal("128.00"))
        with self.assertRaises(ValidationError):
            post_interest_year(record.pk, today=date(2026, 12, 31))
        posted = post_interest_year(record.pk, actor=self.admin, today=date(2027, 1, 1))
        self.assertEqual(posted.next_opening_balance, Decimal("1928.00"))
        self.assertEqual(posted.ledger_transaction.credit, Decimal("128.00"))
        self.assertEqual(posted.ledger_transaction.transaction_date, date(2026, 12, 31))
        self.assertEqual(
            OpeningBalanceAmount.objects.get(payment_type=self.payment_type).amount,
            Decimal("2000.00"),
        )
        self.assertEqual(
            account_statement(
                self.member, self.payment_type, date(2027, 1, 1), date(2027, 1, 1)
            )["brought_forward"],
            Decimal("1928.00"),
        )
        self.assertEqual(
            member_balances(self.member, date(2027, 1, 1))["balances"][0]["balance"],
            "1928.00",
        )
        self.assertEqual(
            post_interest_year(record.pk, today=date(2027, 1, 2)).ledger_transaction_id,
            posted.ledger_transaction_id,
        )
        self.assertEqual(PaymentTransaction.objects.count(), 6)

    def posted_year(self):
        self.opening()
        record = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        return post_interest_year(record.pk, today=date(2027, 1, 1))

    def test_posted_snapshot_and_rate_do_not_change_with_new_rate(self):
        record = self.posted_year()
        self.payment_type.interest_rate = Decimal("10.00")
        self.payment_type.save()
        saved = self.report(as_of=date(2027, 1, 1))[0]["record"]
        self.assertEqual(saved.annual_rate, Decimal("8.00"))
        self.assertEqual(saved.annual_interest, Decimal("160.00"))
        self.assertEqual(saved.closing_balance, Decimal("2000.00"))
        next_year = interest_report(
            Member.objects.filter(pk=self.member.pk),
            date(2027, 1, 1),
            as_of=date(2027, 3, 31),
        )[0]["record"]
        self.assertEqual(next_year.quarters[0]["minimum"], "2160.00")
        self.assertEqual(next_year.quarters[0]["interest"], "54.00")

    def test_posted_year_blocks_backdated_transactions_but_allows_next_year(self):
        record = self.posted_year()
        with self.assertRaises(ValidationError):
            self.entry(date(2026, 1, 1), credit="100")
        self.entry(date(2027, 1, 1), credit="100")
        with self.assertRaises(ValidationError):
            record.ledger_transaction.credit = Decimal("200.00")
            record.ledger_transaction.save()
        with self.assertRaises(ProtectedError):
            record.ledger_transaction.delete()
        with self.assertRaises(ProtectedError):
            record.delete()
        record.annual_rate = Decimal("10")
        with self.assertRaises(ValidationError):
            record.save()

    def test_posted_year_protects_imported_opening_values_and_dates(self):
        self.posted_year()
        amount = OpeningBalanceAmount.objects.get(payment_type=self.payment_type)
        amount.amount = Decimal("3000")
        with self.assertRaises(ValidationError):
            amount.save()
        with self.assertRaises(ProtectedError):
            amount.delete()
        parent = amount.opening_balance
        parent.balance_date = date(2026, 2, 1)
        with self.assertRaises(ValidationError):
            parent.save()
        with self.assertRaises(ProtectedError):
            parent.delete()
        form = OpeningBalanceForm(
            member=self.member, instance=OpeningBalance.objects.get(pk=parent.pk)
        )
        self.assertTrue(form.fields["balance_date"].disabled)
        self.assertTrue(form.fields[f"amount_{self.payment_type.pk}"].disabled)

    def test_changed_balances_after_review_require_another_review(self):
        self.opening()
        record = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        fingerprint = calculation_fingerprint(record)
        self.entry(date(2026, 2, 1), credit="100")
        with self.assertRaises(ValidationError):
            post_interest_year(
                record.pk, today=date(2027, 1, 1), expected_fingerprint=fingerprint
            )
        record.refresh_from_db()
        self.assertIsNone(record.posted_at)

    def test_zero_rate_year_has_no_zero_value_ledger_entry(self):
        self.opening()
        self.payment_type.interest_rate = Decimal("0")
        self.payment_type.save()
        record = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        record = post_interest_year(record.pk, today=date(2027, 1, 1))
        self.assertIsNotNone(record.posted_at)
        self.assertIsNone(record.ledger_transaction_id)
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_new_year_rate_does_not_revalue_last_year_saved_rate(self):
        self.opening()
        saved = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        self.payment_type.interest_rate = Decimal("10.00")
        self.payment_type.save()
        posted = post_interest_year(saved.pk, today=date(2027, 1, 1))
        self.assertEqual(posted.annual_rate, Decimal("8.00"))
        self.assertEqual(posted.annual_interest, Decimal("160.00"))

    def test_closed_opening_cannot_be_moved_to_another_payment_type(self):
        self.posted_year()
        amount = OpeningBalanceAmount.objects.get(payment_type=self.payment_type)
        amount.payment_type = self.other_type
        with self.assertRaises(ValidationError):
            amount.save()

    def test_post_route_requires_review_and_is_idempotent(self):
        self.opening(day=date(2025, 1, 1))
        saved = save_interest_year(self.member, self.payment_type, date(2025, 1, 1))
        url = reverse("deposit_interest_edit", args=[saved.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        fingerprint = response.context["calculation_hash"]
        self.client.post(url, {})
        saved.refresh_from_db()
        self.assertIsNone(saved.posted_at)
        self.client.post(url, {"calculation_hash": fingerprint})
        self.client.post(url, {"calculation_hash": fingerprint})
        saved.refresh_from_db()
        self.assertIsNotNone(saved.posted_at)
        self.assertEqual(
            PaymentTransaction.objects.filter(interest_posting__isnull=False).count(), 1
        )

    def test_opening_balance_screen_shows_annual_carry_forward(self):
        record = self.posted_year()
        response = self.client.get(
            reverse("opening_balance_list"), {"member": self.member.pk}
        )
        self.assertContains(response, "Annual opening balances carried forward")
        self.assertContains(response, "2160.00")
        self.assertContains(
            response, reverse("deposit_interest_detail", args=[record.pk])
        )

    def test_failure_to_save_posting_rolls_back_ledger_and_snapshot(self):
        self.opening()
        record = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        original_save = DepositInterestYear.save

        def fail_post(instance, *args, **kwargs):
            if instance.posted_at:
                raise RuntimeError("posting failed")
            return original_save(instance, *args, **kwargs)

        with patch.object(DepositInterestYear, "save", fail_post):
            with self.assertRaises(RuntimeError):
                post_interest_year(record.pk, today=date(2027, 1, 1))
        self.assertFalse(PaymentTransaction.objects.exists())
        record.refresh_from_db()
        self.assertIsNone(record.posted_at)

    def test_report_and_posting_routes_are_district_scoped(self):
        self.opening(member=self.foreign)
        record = save_interest_year(
            self.foreign, self.payment_type, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        response = self.client.get(reverse("deposit_interest_list"), {"year": 2026})
        self.assertNotContains(response, self.foreign.full_name)
        for action in ("detail", "edit", "delete"):
            for method in ("get", "post"):
                self.assertEqual(
                    getattr(self.client, method)(
                        reverse(f"deposit_interest_{action}", args=[record.pk])
                    ).status_code,
                    404,
                )
        response = self.client.get(
            reverse("deposit_interest_list"), {"year": 2026, "member": self.foreign.pk}
        )
        self.assertTrue(response.context["form"].errors)

    def test_ledger_permission_does_not_grant_interest_permissions(self):
        self.user.user_permissions.set(
            Permission.objects.filter(codename="view_paymenttransaction")
        )
        for name in ("deposit_interest_list", "deposit_interest_create"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_preview_get_is_read_only_and_create_post_saves_only(self):
        self.example()
        self.assertEqual(
            self.client.get(
                reverse("deposit_interest_list"), {"year": 2026}
            ).status_code,
            200,
        )
        self.assertFalse(DepositInterestYear.objects.exists())
        response = self.client.post(reverse("deposit_interest_create"), {"year": 2026})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DepositInterestYear.objects.count(), 1)
        self.assertIsNone(DepositInterestYear.objects.get().posted_at)

    def test_csrf_and_anonymous_access(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(
            csrf.post(reverse("deposit_interest_create"), {"year": 2026}).status_code,
            403,
        )
        self.assertEqual(
            Client().get(reverse("deposit_interest_list")).status_code, 302
        )

    def test_unique_year_and_nonnegative_interest_database_constraints(self):
        self.opening()
        record = save_interest_year(
            self.member, self.payment_type, date(2026, 1, 1), today=date(2026, 12, 31)
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DepositInterestYear.objects.create(
                member=self.member,
                payment_type=self.payment_type,
                year_start=record.year_start,
                year_end=record.year_end,
                annual_rate="8",
                calculated_through=record.year_end,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DepositInterestYear.objects.filter(pk=record.pk).update(
                annual_interest="-1"
            )

    def test_automatic_command_calculates_and_posts_ended_years_idempotently(self):
        self.example()
        output = StringIO()
        with patch("django.utils.timezone.localdate", return_value=date(2027, 1, 1)):
            call_command(
                "process_deposit_interest",
                from_year=2026,
                post_ended=True,
                stdout=output,
            )
            call_command(
                "process_deposit_interest",
                from_year=2026,
                post_ended=True,
                stdout=output,
            )
        self.assertEqual(
            DepositInterestYear.objects.filter(posted_at__isnull=False).count(), 1
        )
        self.assertEqual(
            PaymentTransaction.objects.filter(interest_posting__isnull=False).count(), 1
        )


class InterestSchedulerInstallerTests(SimpleTestCase):
    def test_show_schedule_does_not_install_it(self):
        output = StringIO()
        with patch(
            "members.management.commands.install_interest_schedule.subprocess.run"
        ) as run:
            call_command("install_interest_schedule", stdout=output)
        run.assert_not_called()
        self.assertIn("15 * * * *", output.getvalue())
        self.assertIn("process_deposit_interest --post-ended", output.getvalue())

    def test_install_preserves_other_jobs_and_replaces_its_own_entry(self):
        old = "0 4 * * * existing-backup\n15 * * * * old-job # member-module-quarterly-interest\n"
        with patch(
            "members.management.commands.install_interest_schedule.subprocess.run",
            side_effect=[
                CompletedProcess([], 0, old, ""),
                CompletedProcess([], 0, "", ""),
            ],
        ) as run:
            call_command("install_interest_schedule", install=True, stdout=StringIO())
        saved = run.call_args_list[1].kwargs["input"]
        self.assertIn("0 4 * * * existing-backup\n", saved)
        self.assertNotIn("old-job", saved)
        self.assertEqual(saved.count("# member-module-quarterly-interest"), 1)

    def test_read_failure_does_not_replace_crontab(self):
        with patch(
            "members.management.commands.install_interest_schedule.subprocess.run",
            return_value=CompletedProcess([], 1, "", "permission denied"),
        ) as run:
            with self.assertRaises(CommandError):
                call_command(
                    "install_interest_schedule", install=True, stdout=StringIO()
                )
        self.assertEqual(run.call_count, 1)
