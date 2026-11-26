from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import models as m
from .audit_views import visible_logs
from .deposit_interest import interest_report, post_interest_year
from .test_contributions import setup_records
from .transaction_balances import account_statement, balance_report, member_balances
from .year_end_services import close_financial_year, closing_preview


@override_settings(FINANCIAL_YEAR_START_MONTH=1)
class FinancialYearEndTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        setup_records(cls)
        cls.start = date(2026, 1, 1)
        cls.after_end = date(2027, 1, 1)
        fourth, _ = m.PaymentType.objects.get_or_create(name="Danajaya Account")
        cls.accounts = [
            cls.types["Fix Deposit"],
            cls.types["Special Deposit"],
            cls.types["Share"],
            fourth,
        ]
        cls.amounts = [
            Decimal("15000"),
            Decimal("8000"),
            Decimal("5000"),
            Decimal("3000"),
        ]
        cls.opening = m.OpeningBalance.objects.create(
            member=cls.member, balance_date=cls.start
        )
        for account, amount in zip(cls.accounts, cls.amounts):
            account.interest_rate = Decimal("0")
            account.save()
            m.OpeningBalanceAmount.objects.create(
                opening_balance=cls.opening, payment_type=account, amount=amount
            )

    def setUp(self):
        self.client.force_login(self.admin)
        self.manual_before = self.manual_state()

    def manual_state(self):
        return list(m.OpeningBalance.objects.values()), list(
            m.OpeningBalanceAmount.objects.values()
        )

    def close(self, start=None, today=None):
        start = start or self.start
        today = today or self.after_end
        preview = closing_preview(start, today=today)
        return close_financial_year(
            start,
            actor=self.admin,
            expected_fingerprint=preview["fingerprint"],
            reason="Administrator verified all account balances",
            today=today,
        )

    def test_existing_zero_interest_accounts_close_without_principal_journal_entries(
        self,
    ):
        preview = closing_preview(self.start, today=self.after_end)
        self.assertEqual(len(preview["rows"]), 4)
        self.assertFalse(m.DepositInterestYear.objects.exists())
        closing = self.close()
        self.assertEqual(closing.account_count, 4)
        self.assertEqual(closing.newly_posted_count, 4)
        self.assertEqual(closing.total_closing, Decimal("31000"))
        records = m.DepositInterestYear.objects.filter(posted_at__isnull=False)
        self.assertEqual(records.count(), 4)
        for account, amount in zip(self.accounts, self.amounts):
            record = records.get(payment_type=account)
            self.assertEqual(record.year_opening_balance, amount)
            self.assertEqual(record.final_closing_balance, amount)
            self.assertEqual(record.next_opening_balance, amount)
            self.assertEqual(record.period_credit, Decimal("0"))
            self.assertEqual(record.period_debit, Decimal("0"))
        self.assertFalse(m.PaymentTransaction.objects.exists())
        self.assertEqual(self.manual_state(), self.manual_before)

    def test_repeat_closing_is_idempotent_in_account_snapshots_and_approval_history(
        self,
    ):
        original = self.close()
        again = self.close()
        self.assertEqual(original.pk, again.pk)
        self.assertEqual(m.FinancialYearClosing.objects.count(), 1)
        self.assertEqual(m.DepositInterestYear.objects.count(), 4)
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_account_renaming_does_not_duplicate_a_closing(self):
        original = self.close()
        self.accounts[0].name = "Renamed fixed deposit"
        self.accounts[0].save()
        self.assertEqual(self.close().pk, original.pk)

    def test_additional_imported_account_needs_a_new_review_and_only_that_account_posts(
        self,
    ):
        self.close()
        account = m.PaymentType.objects.create(
            name="Later imported account", interest_rate=Decimal("0")
        )
        m.OpeningBalanceAmount.objects.create(
            opening_balance=self.opening, payment_type=account, amount=Decimal("100")
        )
        manual = self.manual_state()
        second = self.close()
        self.assertEqual(second.account_count, 5)
        self.assertEqual(second.newly_posted_count, 1)
        self.assertEqual(m.DepositInterestYear.objects.count(), 5)
        self.assertEqual(self.manual_state(), manual)
        self.assertFalse(m.PaymentTransaction.objects.exists())

    def test_inactive_accounts_and_members_are_included(self):
        self.accounts[0].is_active = False
        self.accounts[0].save()
        from .status_models import status_write

        self.member.status = "inactive"
        token = status_write.set(True)
        try:
            self.member.save()
        finally:
            status_write.reset(token)
        self.assertEqual(self.close().account_count, 4)

    def test_interest_is_credited_once_and_all_balance_readers_agree_next_year(self):
        account = self.accounts[1]
        account.interest_rate = Decimal("8")
        account.save()
        original_entries = list(m.PaymentTransaction.objects.values())
        self.close()
        self.close()
        year = m.DepositInterestYear.objects.get(payment_type=account)
        self.assertEqual(year.annual_interest, Decimal("640"))
        self.assertEqual(year.final_closing_balance, Decimal("8640"))
        self.assertEqual(
            m.PaymentTransaction.objects.count(), len(original_entries) + 1
        )
        self.assertEqual(
            account_statement(
                self.member, account, date(2027, 1, 1), date(2027, 12, 31)
            )["brought_forward"],
            Decimal("8640"),
        )
        self.assertEqual(
            member_balances(self.member, date(2027, 1, 1))["total"], "31640.00"
        )
        row = next(
            row
            for row in balance_report(
                m.Member.objects.filter(pk=self.member.pk),
                date(2027, 1, 1),
                date(2027, 12, 31),
            )
            if row["payment_type_id"] == account.pk
        )
        self.assertEqual(row["brought_forward"], Decimal("8640"))
        next_record = interest_report(
            m.Member.objects.filter(pk=self.member.pk),
            date(2027, 1, 1),
            payment_type=account,
            as_of=date(2027, 6, 30),
        )[0]["record"]
        self.assertEqual(next_record.year_opening_balance, Decimal("8640"))
        self.assertEqual(self.manual_state(), self.manual_before)

    def test_existing_automatic_processor_saves_distinct_next_year_openings(self):
        with patch("django.utils.timezone.localdate", return_value=self.after_end):
            for _ in range(2):
                call_command(
                    "process_deposit_interest",
                    from_year=2026,
                    post_ended=True,
                    stdout=StringIO(),
                )
        self.assertEqual(
            m.DepositInterestYear.objects.filter(
                year_start=self.start, posted_at__isnull=False
            ).count(),
            4,
        )
        self.assertEqual(
            m.DepositInterestYear.objects.filter(year_start=date(2027, 1, 1)).count(), 4
        )
        for account in self.accounts:
            previous = m.DepositInterestYear.objects.get(
                payment_type=account, year_start=self.start
            )
            following = m.DepositInterestYear.objects.get(
                payment_type=account, year_start=date(2027, 1, 1)
            )
            self.assertEqual(
                previous.final_closing_balance, following.year_opening_balance
            )
        self.assertEqual(self.manual_state(), self.manual_before)

    def test_negative_closing_is_retained_without_overwriting_nonnegative_manual_opening(
        self,
    ):
        account = self.accounts[1]
        entry = m.PaymentTransaction.objects.create(
            member=self.member,
            payment_type=account,
            transaction_date=date(2026, 2, 1),
            debit=Decimal("9000"),
        )
        before = list(m.PaymentTransaction.objects.filter(pk=entry.pk).values())
        self.close()
        year = m.DepositInterestYear.objects.get(payment_type=account)
        self.assertEqual(year.final_closing_balance, Decimal("-1000"))
        self.assertEqual(year.next_opening_balance, Decimal("-1000"))
        self.assertEqual(year.period_debit, Decimal("9000"))
        self.assertEqual(
            list(m.PaymentTransaction.objects.filter(pk=entry.pk).values()), before
        )
        self.assertEqual(self.manual_state(), self.manual_before)

    def test_midyear_original_opening_retains_its_effective_date(self):
        self.opening.balance_date = date(2026, 7, 1)
        self.opening.save()
        manual = self.manual_state()
        self.close()
        for record in m.DepositInterestYear.objects.all():
            self.assertEqual(record.opening_effective_on, date(2026, 7, 1))
            self.assertEqual(record.year_opening_balance, record.closing_balance)
        self.assertEqual(self.manual_state(), manual)

    def test_child_accounts_carry_separately_and_preserve_original_deposit_opening(
        self,
    ):
        child = m.Child.objects.create(
            member=self.member, name="Year end child", date_of_birth=date(2010, 1, 1)
        )
        typ = m.ChildDepositType.objects.create(
            name="Year end child type",
            rate_below_threshold=Decimal("0"),
            rate_from_threshold=Decimal("0"),
        )
        deposit = m.ChildDeposit.objects.create(
            child=child,
            deposit_type=typ,
            opened_on=date(2026, 7, 1),
            opening_amount=Decimal("500"),
        )
        before = list(m.ChildDeposit.objects.filter(pk=deposit.pk).values())
        closing = self.close()
        self.assertEqual(closing.account_count, 5)
        year = m.ChildDepositInterestYear.objects.get(deposit=deposit)
        self.assertEqual(year.year_opening_balance, Decimal("500"))
        self.assertEqual(year.next_opening_balance, Decimal("500"))
        self.assertEqual(year.opening_effective_on, date(2026, 7, 1))
        self.assertFalse(m.ChildDepositTransaction.objects.exists())
        self.assertEqual(
            list(m.ChildDeposit.objects.filter(pk=deposit.pk).values()), before
        )

    def test_skipped_year_is_rejected_then_contiguous_years_reconcile(self):
        self.close()
        with self.assertRaisesMessage(ValidationError, "intervening"):
            closing_preview(date(2028, 1, 1), today=date(2029, 1, 1))
        self.close(start=date(2027, 1, 1), today=date(2028, 1, 1))
        for account in self.accounts:
            earlier = m.DepositInterestYear.objects.get(
                payment_type=account, year_start=self.start
            )
            following = m.DepositInterestYear.objects.get(
                payment_type=account, year_start=date(2027, 1, 1)
            )
            self.assertEqual(
                earlier.final_closing_balance, following.year_opening_balance
            )

    def test_inconsistent_historical_carry_forward_is_detected_without_repairing_data(
        self,
    ):
        self.close()
        record = m.DepositInterestYear.objects.get(payment_type=self.accounts[0])
        # Simulate a legacy/import inconsistency outside normal immutable model saves.
        m.DepositInterestYear._base_manager.filter(pk=record.pk).update(
            next_opening_balance=Decimal("14999")
        )
        with self.assertRaisesMessage(ValidationError, "differs from the previous"):
            closing_preview(date(2027, 1, 1), today=date(2028, 1, 1))
        record.refresh_from_db()
        self.assertEqual(record.next_opening_balance, Decimal("14999"))
        self.assertFalse(
            m.DepositInterestYear.objects.filter(year_start=date(2027, 1, 1)).exists()
        )
        self.assertEqual(self.manual_state(), self.manual_before)

    def test_offsetting_transaction_changes_invalidate_review_even_if_balance_is_equal(
        self,
    ):
        preview = closing_preview(self.start, today=self.after_end)
        for side in ("debit", "credit"):
            m.PaymentTransaction.objects.create(
                member=self.member,
                payment_type=self.accounts[2],
                transaction_date=date(2026, 6, 1),
                **{side: Decimal("100")},
            )
        with self.assertRaisesMessage(ValidationError, "changed after review"):
            close_financial_year(
                self.start,
                actor=self.admin,
                expected_fingerprint=preview["fingerprint"],
                reason="Reviewed",
                today=self.after_end,
            )
        self.assertFalse(m.DepositInterestYear.objects.exists())
        self.assertFalse(m.FinancialYearClosing.objects.exists())

    def test_batch_failure_rolls_back_interest_entries_snapshots_and_audits(self):
        self.accounts[3].interest_rate = Decimal("8")
        self.accounts[3].save()
        original = post_interest_year
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValidationError("Second account failed")
            return original(*args, **kwargs)

        count = m.AuditLog.objects.count()
        with patch(
            "members.deposit_interest.post_interest_year", side_effect=fail_second
        ):
            with self.assertRaisesMessage(ValidationError, "Second account"):
                self.close()
        self.assertFalse(m.PaymentTransaction.objects.exists())
        self.assertFalse(m.DepositInterestYear.objects.exists())
        self.assertFalse(m.FinancialYearClosing.objects.exists())
        self.assertEqual(m.AuditLog.objects.count(), count)
        self.assertEqual(self.manual_state(), self.manual_before)

    def test_future_closing_and_unauthorized_operator_are_rejected(self):
        with self.assertRaisesMessage(ValidationError, "ended financial year"):
            self.close(today=date(2026, 12, 31))
        self.client.force_login(self.operator)
        self.assertEqual(
            self.client.get(reverse("financial_year_closing_list")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("financial_year_closing_list"),
                {"year": 2026, "intent": "close"},
            ).status_code,
            403,
        )
        preview = closing_preview(self.start, today=self.after_end)
        with self.assertRaises(PermissionDenied):
            close_financial_year(
                self.start,
                actor=self.operator,
                expected_fingerprint=preview["fingerprint"],
                reason="Reviewed",
                today=self.after_end,
            )

    def test_admin_approval_requires_confirmation_reason_and_review_then_audits(self):
        url = reverse("financial_year_closing_list")
        with patch("django.utils.timezone.localdate", return_value=self.after_end):
            preview = self.client.get(url, {"year": 2026})
            fingerprint = preview.context["form"]["review_hash"].value()
            self.assertContains(preview, "Approve &amp; close financial year")
            self.assertFalse(m.DepositInterestYear.objects.exists())
            for extra in (
                {"approve": "on"},
                {"reason": "Reviewed"},
                {"approve": "on", "reason": "Reviewed", "review_hash": ""},
            ):
                payload = {
                    "year": 2026,
                    "review_hash": fingerprint,
                    "intent": "close",
                    **extra,
                }
                response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(m.FinancialYearClosing.objects.exists())
            response = self.client.post(
                url,
                {
                    "year": 2026,
                    "review_hash": fingerprint,
                    "intent": "close",
                    "approve": "on",
                    "reason": "Year-end accounts approved",
                },
            )
            self.assertEqual(response.status_code, 302)
        closing = m.FinancialYearClosing.objects.get()
        self.assertEqual(closing.approved_by, self.admin)
        self.assertEqual(closing.reason, "Year-end accounts approved")
        self.assertTrue(
            m.AuditLog.objects.filter(
                source_model="members.financialyearclosing",
                source_id=closing.pk,
                actor_id=self.admin.pk,
            ).exists()
        )
        self.assertContains(self.client.get(response.url), "31,000.00")

    def test_closing_approvals_and_posted_account_years_are_immutable(self):
        closing = self.close()
        with self.assertRaises(ValidationError):
            closing.save()
        with self.assertRaises(ProtectedError):
            closing.delete()
        with self.assertRaises(ProtectedError):
            m.FinancialYearClosing.objects.filter(pk=closing.pk).delete()
        self.operator.user_permissions.add(
            *Permission.objects.filter(
                codename__in=("view_auditlog", "view_depositinterestyear")
            )
        )
        self.assertFalse(
            visible_logs(self.operator)
            .filter(source_model="members.financialyearclosing")
            .exists()
        )
        year = m.DepositInterestYear.objects.first()
        year.year_opening_balance += Decimal("1")
        with self.assertRaises(ValidationError):
            year.save()

    @override_settings(FINANCIAL_YEAR_START_MONTH=4)
    def test_non_january_years_reconcile_across_the_correct_dates(self):
        closing = self.close(start=date(2026, 4, 1), today=date(2027, 4, 1))
        self.assertEqual(closing.year_end, date(2027, 3, 31))
        self.assertEqual(closing.total_closing, Decimal("31000"))
        for record in m.DepositInterestYear.objects.all():
            self.assertEqual(record.opening_effective_on, date(2026, 4, 1))
            self.assertEqual(record.year_opening_balance, record.final_closing_balance)
