from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .access_forms import WorkspaceRoleForm, WorkspaceUserForm
from .fix_deposit_forms import FixPolicyForm
from .fix_deposit_services import (
    account_status,
    add_months,
    issue_loan,
    loan_balances,
    record_loan_entry,
    request_withdrawal,
    review_withdrawal,
    settle_loan,
)
from .models import (
    District,
    FixDepositAccount,
    FixDepositLoan,
    FixDepositPolicy,
    FixDepositWithdrawal,
    FixLoanEntry,
    Member,
    OfficeLocation,
    OpeningBalance,
    OpeningBalanceAmount,
    PaymentTransaction,
    PaymentType,
)
from .opening_balance_forms import OpeningBalanceForm
from .transaction_balances import account_statement
from .transaction_forms import PaymentTransactionForm


class FixDepositTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "fix-operator", password="test-only-pass"
        )
        cls.admin = get_user_model().objects.create_superuser(
            "fix-admin", password="test-only-pass"
        )
        cls.user.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        cls.district = District.objects.get(code="COL")
        cls.user.assigned_districts.add(cls.district)
        cls.members = []
        for index, district in enumerate(
            (
                cls.district,
                cls.district,
                District.objects.exclude(pk=cls.district.pk).first(),
            )
        ):
            office = OfficeLocation.objects.create(
                district=district, name=f"Fix office {index}", code=f"FIX-Z-{index}"
            )
            cls.members.append(
                Member.objects.create(
                    district=district,
                    office_location=office,
                    member_no=f"FIX-M-{index}",
                    full_name=f"Fix member {index}",
                    salary="1000",
                    office_address="Office",
                    private_address="Private member address",
                    phone_mobile="0771234567",
                    gender="female",
                    nic_no=f"19801234567{index}",
                )
            )
        cls.member, cls.other, cls.foreign = cls.members
        cls.policy = FixDepositPolicy.objects.get(name="Fix Deposit Rules")
        cls.policy.loan_interest_rate = Decimal("12.00")
        cls.policy.save()
        cls.payment_type = cls.policy.payment_type
        cls.opening = OpeningBalance.objects.create(
            member=cls.member, balance_date=date(2020, 1, 1)
        )
        cls.opening_amount = OpeningBalanceAmount.objects.create(
            opening_balance=cls.opening,
            payment_type=cls.payment_type,
            amount=Decimal("100000.00"),
        )
        cls.account = FixDepositAccount.objects.create(
            member=cls.member, policy=cls.policy, cycle_started_on=date(2020, 1, 1)
        )
        cls.foreign_account = FixDepositAccount.objects.create(
            member=cls.foreign, policy=cls.policy, cycle_started_on=date(2020, 1, 1)
        )

    def setUp(self):
        self.client.force_login(self.user)

    def grant(self, *names):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members", codename__in=names
            )
        )

    def status(self, day=date(2023, 1, 1)):
        return account_status(self.account, day)

    def loan(self, amount="90000.00", day=date(2021, 1, 1)):
        return issue_loan(self.user, self.account.pk, day, Decimal(amount))

    def request(self, amount="75000.00", day=date(2023, 1, 1)):
        return request_withdrawal(self.user, self.account.pk, day, Decimal(amount))

    def top_up(self, amount="1000.00", day=date(2022, 1, 1)):
        return PaymentTransaction.objects.create(
            member=self.member,
            payment_type=self.payment_type,
            transaction_date=day,
            credit=Decimal(amount),
        )

    def test_default_rules_and_monthly_topups_use_existing_member_balance(self):
        self.assertEqual(
            (self.policy.maturity_months, self.policy.loan_interval_months), (36, 12)
        )
        self.assertEqual(self.policy.loan_percent, Decimal("90.00"))
        self.assertEqual(self.policy.withdrawal_percent, Decimal("75.00"))
        self.top_up("1000", date(2022, 11, 1))
        self.top_up("2000", date(2022, 12, 1))
        status = self.status()
        self.assertEqual(status["deposit_balance"], Decimal("103000.00"))
        self.assertEqual(status["loan_limit"], Decimal("92700.00"))
        self.assertEqual(status["withdrawal_limit"], Decimal("77250.00"))
        self.assertEqual(status["minimum_held"], Decimal("25750.00"))
        self.assertEqual(status["maturity_date"], date(2023, 1, 1))

    def test_withdrawal_requires_exact_three_year_maturity(self):
        self.assertFalse(self.status(date(2022, 12, 31))["can_withdraw"])
        with self.assertRaises(ValidationError):
            self.request(day=date(2022, 12, 31))
        self.assertFalse(FixDepositWithdrawal.objects.exists())
        self.assertTrue(self.status(date(2023, 1, 1))["can_withdraw"])
        self.assertEqual(self.request().status, FixDepositWithdrawal.Status.PENDING)

    def test_withdrawal_is_capped_at_75_percent_and_retains_25_percent(self):
        with self.assertRaises(ValidationError):
            self.request("75000.01")
        withdrawal = self.request()
        self.assertFalse(PaymentTransaction.objects.filter(debit__gt=0).exists())
        released = review_withdrawal(self.user, withdrawal.pk, date(2023, 1, 1), True)
        self.assertEqual(released.balance_before, Decimal("100000.00"))
        self.assertEqual(released.balance_after, Decimal("25000.00"))
        self.assertEqual(released.maximum_at_release, Decimal("75000.00"))
        self.assertEqual(released.ledger_transaction.debit, Decimal("75000.00"))
        self.assertEqual(
            account_statement(self.member, self.payment_type, None, date(2023, 1, 1))[
                "final_balance"
            ],
            Decimal("25000.00"),
        )

    def test_each_release_restarts_the_three_year_account_cycle(self):
        withdrawal = self.request("50000")
        review_withdrawal(self.user, withdrawal.pk, date(2023, 1, 1), True)
        status = self.status(date(2025, 12, 31))
        self.assertEqual(status["cycle_start"], date(2023, 1, 1))
        self.assertEqual(status["maturity_date"], date(2026, 1, 1))
        self.assertFalse(status["can_withdraw"])
        with self.assertRaises(ValidationError):
            self.request("100", date(2025, 12, 31))
        self.assertTrue(self.status(date(2026, 1, 1))["can_withdraw"])
        self.assertEqual(
            self.request("37500", date(2026, 1, 1)).amount, Decimal("37500")
        )

    def test_recent_topups_share_the_accounts_three_year_cycle(self):
        self.top_up("10000", date(2022, 12, 31))
        withdrawal = self.request("82500")
        review_withdrawal(self.user, withdrawal.pk, date(2023, 1, 1), True)
        self.assertEqual(self.status()["deposit_balance"], Decimal("27500.00"))

    def test_loan_is_capped_at_90_percent_and_does_not_reduce_collateral(self):
        with self.assertRaises(ValidationError):
            self.loan("90000.01")
        loan = self.loan()
        self.assertEqual(loan.principal, Decimal("90000.00"))
        self.assertEqual(loan.deposit_balance_at_issue, Decimal("100000.00"))
        self.assertEqual(loan.interest_rate, Decimal("12.00"))
        self.assertEqual(
            self.status(date(2021, 1, 1))["deposit_balance"], Decimal("100000.00")
        )
        self.assertEqual(
            self.status(date(2021, 1, 1))["principal_due"], Decimal("90000.00")
        )
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_annual_loan_interval_and_combined_outstanding_limit(self):
        self.loan("50000")
        with self.assertRaises(ValidationError):
            self.loan("100", date(2021, 12, 31))
        self.assertEqual(
            self.status(date(2022, 1, 1))["loan_limit"], Decimal("40000.00")
        )
        with self.assertRaises(ValidationError):
            self.loan("40000.01", date(2022, 1, 1))
        self.loan("40000", date(2022, 1, 1))
        self.assertEqual(self.status()["principal_due"], Decimal("90000.00"))
        self.assertEqual(self.status()["loan_limit"], Decimal("0.00"))

    def test_bank_must_configure_interest_and_existing_loans_keep_their_rate(self):
        self.policy.loan_interest_rate = None
        self.policy.save()
        with self.assertRaises(ValidationError):
            self.loan()
        self.policy.loan_interest_rate = Decimal("12.00")
        self.policy.save()
        loan = self.loan("100")
        self.policy.loan_interest_rate = Decimal("15.00")
        self.policy.save()
        loan.refresh_from_db()
        self.assertEqual(loan.interest_rate, Decimal("12.00"))

    def test_unsettled_principal_and_interest_both_block_withdrawals(self):
        loan = self.loan()
        with self.assertRaises(ValidationError):
            self.request()
        record_loan_entry(
            self.user,
            loan.pk,
            date(2021, 1, 2),
            FixLoanEntry.Kind.INTEREST,
            interest=Decimal("100"),
        )
        record_loan_entry(
            self.user,
            loan.pk,
            date(2021, 1, 3),
            FixLoanEntry.Kind.REPAYMENT,
            principal=Decimal("90000"),
            interest=Decimal("99.99"),
        )
        with self.assertRaises(ValidationError):
            settle_loan(self.user, loan.pk, date(2021, 1, 3), True)
        self.assertFalse(self.status()["can_withdraw"])
        record_loan_entry(
            self.user,
            loan.pk,
            date(2021, 1, 4),
            FixLoanEntry.Kind.REPAYMENT,
            interest=Decimal("0.01"),
        )
        self.assertFalse(self.status()["can_withdraw"])
        with self.assertRaises(ValidationError):
            settle_loan(self.user, loan.pk, date(2021, 1, 4), False)
        settle_loan(self.user, loan.pk, date(2021, 1, 4), True)
        self.assertTrue(self.status()["can_withdraw"])
        self.assertEqual(self.request().status, "pending")

    def test_principal_paid_without_final_settlement_still_blocks_release(self):
        loan = self.loan()
        record_loan_entry(
            self.user,
            loan.pk,
            date(2021, 1, 2),
            FixLoanEntry.Kind.REPAYMENT,
            principal=loan.principal,
        )
        self.assertEqual(loan_balances(loan, date(2023, 1, 1))["principal_due"], 0)
        with self.assertRaises(ValidationError):
            self.request()
        settle_loan(self.user, loan.pk, date(2021, 1, 2), True)
        self.assertTrue(self.status()["can_withdraw"])

    def test_repayments_cannot_exceed_principal_or_assessed_interest(self):
        loan = self.loan("500")
        for principal, interest in (
            ("500.01", "0"),
            ("0", "1"),
            ("-1", "0"),
            ("0.001", "0"),
        ):
            with self.assertRaises(ValidationError):
                record_loan_entry(
                    self.user,
                    loan.pk,
                    date(2021, 1, 2),
                    FixLoanEntry.Kind.REPAYMENT,
                    principal=principal,
                    interest=interest,
                )
        self.assertFalse(FixLoanEntry.objects.exists())

    def test_duplicate_repayment_token_posts_once_and_changed_replay_is_rejected(self):
        loan = self.loan()
        key = uuid4()
        first = record_loan_entry(
            self.user,
            loan.pk,
            date(2021, 1, 2),
            FixLoanEntry.Kind.REPAYMENT,
            principal="100",
            operation_key=key,
        )
        second = record_loan_entry(
            self.user,
            loan.pk,
            date(2021, 1, 2),
            FixLoanEntry.Kind.REPAYMENT,
            principal="100",
            operation_key=key,
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(FixLoanEntry.objects.count(), 1)
        self.assertEqual(
            loan_balances(loan, date(2021, 1, 2))["principal_due"], Decimal("89900")
        )
        with self.assertRaises(ValidationError):
            record_loan_entry(
                self.user,
                loan.pk,
                date(2021, 1, 2),
                FixLoanEntry.Kind.REPAYMENT,
                principal="200",
                operation_key=key,
            )

    def test_withdrawal_release_rechecks_loans_and_cannot_post_twice(self):
        withdrawal = self.request()
        loan = self.loan("100", date(2023, 1, 2))
        with self.assertRaises(ValidationError):
            review_withdrawal(self.user, withdrawal.pk, date(2023, 1, 2), True)
        self.assertFalse(PaymentTransaction.objects.exists())
        record_loan_entry(
            self.user,
            loan.pk,
            date(2023, 1, 2),
            FixLoanEntry.Kind.REPAYMENT,
            principal="100",
        )
        settle_loan(self.user, loan.pk, date(2023, 1, 2), True)
        released = review_withdrawal(self.user, withdrawal.pk, date(2023, 1, 2), True)
        with self.assertRaises(ValidationError):
            review_withdrawal(self.user, withdrawal.pk, date(2023, 1, 2), True)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertEqual(released.balance_after, Decimal("25000.00"))

    def test_failed_release_rolls_back_ledger_and_request_update_together(self):
        withdrawal = self.request()
        with patch(
            "members.fix_deposit_services.FixDepositWithdrawal.save",
            side_effect=RuntimeError("posting failed"),
        ):
            with self.assertRaises(RuntimeError):
                review_withdrawal(self.user, withdrawal.pk, date(2023, 1, 1), True)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, "pending")
        self.assertIsNone(withdrawal.ledger_transaction_id)
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_pending_request_does_not_debit_and_rejection_does_not_restart_cycle(self):
        withdrawal = self.request()
        with self.assertRaises(ValidationError):
            self.request("100")
        rejected = review_withdrawal(
            self.user, withdrawal.pk, date(2023, 1, 2), False, "Member cancelled"
        )
        self.assertEqual(rejected.status, "rejected")
        self.assertFalse(PaymentTransaction.objects.exists())
        self.assertEqual(
            self.status(date(2023, 1, 2))["maturity_date"], date(2023, 1, 1)
        )
        self.assertEqual(self.request("100", date(2023, 1, 2)).status, "pending")

    def test_rounding_never_exceeds_percentage_limits(self):
        self.opening_amount.amount = Decimal("100000.01")
        self.opening_amount.save()
        status = self.status()
        self.assertEqual(status["loan_limit"], Decimal("90000.00"))
        self.assertEqual(status["withdrawal_limit"], Decimal("75000.00"))
        self.assertEqual(status["minimum_held"], Decimal("25000.01"))

    def test_calendar_years_handle_leap_dates(self):
        self.assertEqual(add_months(date(2024, 2, 29), 36), date(2027, 2, 28))
        self.assertEqual(add_months(date(2024, 2, 29), 12), date(2025, 2, 28))

    def test_generic_debit_cannot_bypass_maturity_or_loan_rules(self):
        data = {
            "member": self.member.pk,
            "payment_type": self.payment_type.pk,
            "transaction_date": "2021-01-01",
            "debit": "100",
            "credit": "0",
        }
        form = PaymentTransactionForm(data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("Fix Deposit", str(form.errors))
        response = self.client.post(reverse("payment_transaction_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PaymentTransaction.objects.exists())
        with self.assertRaises(ValidationError):
            PaymentTransaction.objects.create(
                member=self.member,
                payment_type=self.payment_type,
                transaction_date=date(2021, 1, 1),
                debit=Decimal("100"),
            )

    def test_monthly_credit_remains_allowed_but_old_collateral_cannot_be_edited_or_deleted(
        self,
    ):
        credit = self.top_up("1000", date(2020, 12, 1))
        self.loan("500")
        self.top_up("2000", date(2021, 2, 1))
        credit.credit = Decimal("1")
        with self.assertRaises(ValidationError):
            credit.save()
        credit.refresh_from_db()
        with self.assertRaises(ProtectedError):
            credit.delete()
        credit.reference = "Corrected receipt"
        credit.save()
        self.assertEqual(self.status()["deposit_balance"], Decimal("103000"))

    def test_released_journal_and_opening_balance_are_protected(self):
        withdrawal = self.request()
        released = review_withdrawal(self.user, withdrawal.pk, date(2023, 1, 1), True)
        entry = PaymentTransaction.objects.get(pk=released.ledger_transaction_id)
        with self.assertRaises(ProtectedError):
            entry.delete()
        entry.debit = Decimal("1")
        with self.assertRaises(ValidationError):
            entry.save()
        self.opening_amount.amount = Decimal("1")
        with self.assertRaises(ValidationError):
            self.opening_amount.save()
        self.opening_amount.refresh_from_db()
        with self.assertRaises(ProtectedError):
            self.opening_amount.delete()
        with self.assertRaises(ProtectedError):
            self.opening.delete()
        self.opening.balance_date = date(2022, 1, 1)
        with self.assertRaises(ValidationError):
            self.opening.save()
        form = OpeningBalanceForm(
            member=self.member,
            instance=OpeningBalance.objects.get(pk=self.opening.pk),
            user=self.user,
        )
        self.assertTrue(form.fields["balance_date"].disabled)
        self.assertTrue(form.fields[f"amount_{self.payment_type.pk}"].disabled)

    def test_future_and_backdated_financial_actions_are_rejected(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        with self.assertRaises(ValidationError):
            self.loan("100", tomorrow)
        loan = self.loan()
        record_loan_entry(
            self.user,
            loan.pk,
            date(2021, 1, 3),
            FixLoanEntry.Kind.REPAYMENT,
            principal="100",
        )
        with self.assertRaises(ValidationError):
            record_loan_entry(
                self.user,
                loan.pk,
                date(2021, 1, 2),
                FixLoanEntry.Kind.REPAYMENT,
                principal="100",
            )

    def test_account_list_detail_loan_and_policy_pages_render(self):
        loan = self.loan()
        for name, args in (
            ("fix_deposit_list", []),
            ("fix_deposit_detail", [self.account.pk]),
            ("fix_loan_detail", [loan.pk]),
            ("fix_loan_entry", [loan.pk]),
            ("fix_loan_settle", [loan.pk]),
            ("fix_deposit_policy_list", []),
        ):
            self.assertEqual(
                self.client.get(reverse(name, args=args)).status_code, 200, name
            )
        response = self.client.get(
            reverse("fix_deposit_detail", args=[self.account.pk]),
            {"as_of": "2023-01-01"},
        )
        self.assertContains(response, "90000.00")
        self.assertContains(response, "fully paid and marked settled")
        self.assertNotContains(
            self.client.get(reverse("fix_deposit_list")), self.foreign.full_name
        )

    def test_form_posts_issue_repay_settle_and_release(self):
        response = self.client.post(
            reverse("fix_loan_create", args=[self.account.pk]),
            {"day": "2021-01-01", "amount": "100", "notes": "Loan test"},
        )
        self.assertEqual(response.status_code, 302)
        loan = FixDepositLoan.objects.get()
        response = self.client.post(
            reverse("fix_loan_entry", args=[loan.pk]),
            {
                "day": "2021-01-02",
                "kind": "repayment",
                "principal": "100",
                "interest": "0",
                "operation_key": str(uuid4()),
            },
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            reverse("fix_loan_settle", args=[loan.pk]),
            {"day": "2021-01-02", "interest_confirmed": "on"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            reverse("fix_withdrawal_create", args=[self.account.pk]),
            {"day": "2023-01-01", "amount": "75000"},
        )
        self.assertEqual(response.status_code, 302)
        withdrawal = FixDepositWithdrawal.objects.get()
        response = self.client.post(
            reverse("fix_withdrawal_review", args=[withdrawal.pk]),
            {"day": "2023-01-01", "decision": "release"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.status()["deposit_balance"], Decimal("25000.00"))

    def test_status_endpoint_filters_dates_and_district_access(self):
        url = reverse("fix_deposit_status", args=[self.account.pk])
        data = self.client.get(url, {"date": "2022-12-31"}).json()
        self.assertEqual(data["deposit_balance"], "100000.00")
        self.assertFalse(data["can_withdraw"])
        self.assertEqual(data["maturity_date"], "2023-01-01")
        self.assertEqual(self.client.get(url, {"date": "bad"}).status_code, 400)
        self.assertIn("no-store", self.client.get(url).headers["Cache-Control"])
        self.assertEqual(
            self.client.get(
                reverse("fix_deposit_status", args=[self.foreign_account.pk])
            ).status_code,
            404,
        )
        self.assertEqual(self.client.post(url, {}).status_code, 405)

    def test_foreign_actions_and_forged_filters_are_inaccessible(self):
        for name in (
            "fix_deposit_detail",
            "fix_deposit_edit",
            "fix_deposit_delete",
            "fix_loan_create",
            "fix_withdrawal_create",
        ):
            self.assertEqual(
                self.client.get(
                    reverse(name, args=[self.foreign_account.pk])
                ).status_code,
                404,
            )
        with self.assertRaises(Exception) as error:
            issue_loan(
                self.user, self.foreign_account.pk, date(2021, 1, 1), Decimal("100")
            )
        self.assertEqual(error.exception.__class__.__name__, "Http404")
        response = self.client.get(
            reverse("fix_deposit_list"), {"member": self.foreign.pk}
        )
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.user.assigned_districts.clear()
        self.assertEqual(
            self.client.get(
                reverse("fix_deposit_detail", args=[self.account.pk])
            ).status_code,
            404,
        )

    def test_permissions_are_independent_and_new_entries_need_csrf(self):
        self.grant("view_member", "view_paymenttransaction", "add_paymenttransaction")
        self.assertEqual(self.client.get(reverse("fix_deposit_list")).status_code, 403)
        with self.assertRaises(PermissionDenied):
            self.loan()
        self.grant("view_fixdepositaccount")
        self.assertEqual(self.client.get(reverse("fix_deposit_list")).status_code, 200)
        self.assertEqual(
            self.client.post(
                reverse("fix_loan_create", args=[self.account.pk]),
                {"day": "2021-01-01", "amount": "100"},
            ).status_code,
            403,
        )
        self.assertEqual(Client().get(reverse("fix_deposit_list")).status_code, 302)
        self.grant("add_fixdepositaccount")
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        for name in ("fix_loan_create", "fix_withdrawal_create"):
            self.assertEqual(
                csrf.post(reverse(name, args=[self.account.pk]), {}).status_code, 403
            )

    def test_bank_rules_cannot_weaken_confirmed_limits_or_change_source(self):
        base = {
            "name": "Rule test",
            "payment_type": PaymentType.objects.get(name="Special Deposit").pk,
            "maturity_months": "36",
            "withdrawal_percent": "75",
            "loan_percent": "90",
            "loan_interval_months": "12",
            "loan_interest_rate": "12",
        }
        for changes in (
            {"maturity_months": "35"},
            {"withdrawal_percent": "75.01"},
            {"loan_percent": "90.01"},
            {"loan_interval_months": "11"},
            {"loan_interest_rate": "0"},
        ):
            form = FixPolicyForm({**base, **changes})
            self.assertFalse(form.is_valid(), changes)
        with self.assertRaises(ProtectedError):
            self.policy.delete()
        self.policy.payment_type = PaymentType.objects.get(name="Special Deposit")
        with self.assertRaises(ValidationError):
            self.policy.full_clean()

    def test_role_editors_and_read_only_financial_admin_are_available(self):
        for form in (WorkspaceRoleForm(), WorkspaceUserForm(actor=self.admin)):
            rows = {row["label"]: row for row in form.permission_rows()}
            self.assertEqual(len(rows["Fix Deposit Loans & Withdrawals"]["cells"]), 4)
            self.assertEqual(len(rows["Fix Deposit Rules"]["cells"]), 4)
        loan = self.loan()
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(
                reverse("admin:members_fixdepositloan_changelist")
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("admin:members_fixdepositloan_change", args=[loan.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                reverse("admin:members_fixdepositloan_add"), {}
            ).status_code,
            403,
        )
