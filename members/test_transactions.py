from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .access_forms import WorkspaceRoleForm, WorkspaceUserForm
from .models import (
    District,
    Member,
    OfficeLocation,
    OpeningBalance,
    OpeningBalanceAmount,
    PaymentTransaction,
    PaymentType,
)
from .payment_forms import PaymentTypeForm
from .transaction_forms import PaymentTransactionForm


class TransactionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "ledger-operator", password="test-only-pass"
        )
        cls.admin = get_user_model().objects.create_superuser(
            "ledger-admin", password="test-only-pass"
        )
        cls.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members",
                codename__in=[
                    "view_paymenttransaction",
                    "add_paymenttransaction",
                    "change_paymenttransaction",
                    "delete_paymenttransaction",
                ],
            )
        )
        cls.district = District.objects.get(code="COL")
        cls.user.assigned_districts.add(cls.district)
        foreign_district = District.objects.exclude(pk=cls.district.pk).first()
        cls.members = []
        for index, district in enumerate(
            (cls.district, cls.district, foreign_district)
        ):
            office = OfficeLocation.objects.create(
                district=district, name=f"Ledger office {index}", code=f"LEDGER-{index}"
            )
            cls.members.append(
                Member.objects.create(
                    district=district,
                    office_location=office,
                    member_no=f"LEDGER-{index}",
                    full_name=f"Ledger member {index}",
                    salary="1000.00",
                    office_address="Office Road",
                    private_address="Home Road",
                    phone_mobile="0771234567",
                    gender="female",
                    nic_no=f"19901234567{index}",
                )
            )
        cls.member, cls.other, cls.foreign = cls.members
        cls.payment_type = PaymentType.objects.get(name="Special Deposit")
        cls.payment_type.interest_rate = Decimal("7.25")
        cls.payment_type.save()
        cls.other_type = PaymentType.objects.get(name="Fix Deposit")
        from .test_support import assign_member_ledgers

        assign_member_ledgers(cls.user, cls.members)

    def setUp(self):
        self.client.force_login(self.user)

    def payload(self, **overrides):
        return {
            "member": self.member.pk,
            "payment_type": self.payment_type.pk,
            "transaction_date": "2026-01-15",
            "debit": "",
            "credit": "100.00",
            "reference": "RECEIPT-1",
            "description": "Member deposit",
            **overrides,
        }

    def entry(self, **overrides):
        return PaymentTransaction.objects.create(
            **{
                "member": self.member,
                "payment_type": self.payment_type,
                "transaction_date": date(2026, 1, 15),
                "credit": Decimal("100.00"),
                **overrides,
            }
        )

    def ledger(self, **overrides):
        return self.client.get(
            reverse("payment_transaction_list"),
            {
                "member": self.member.pk,
                "payment_type": self.payment_type.pk,
                **overrides,
            },
        )

    def grant(self, *codenames):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members", codename__in=codenames
            )
        )

    def balances(self, **overrides):
        return self.client.get(
            reverse("payment_transaction_balances"),
            {
                "member": self.member.pk,
                "date": "2026-01-15",
                **overrides,
            },
        )

    def report(self, **filters):
        return self.client.get(reverse("payment_transaction_report"), filters)

    def test_statement_final_amount_includes_opening_plus_2000_credit(self):
        self.opening(amount="10000.00")
        self.entry(credit="2000.00")
        response = self.ledger(date="2026-01-15")
        self.assertEqual(response.context["brought_forward"], Decimal("10000.00"))
        self.assertEqual(response.context["total_credit"], Decimal("2000.00"))
        self.assertEqual(response.context["final_balance"], Decimal("12000.00"))
        self.assertEqual(
            response.context["page_obj"][0].running_balance, Decimal("12000.00")
        )
        self.assertContains(response, "data-final-balance>12000.00</output>")
        self.assertContains(response, reverse("payment_transaction_report"))

    def test_final_balance_keeps_earlier_movements_when_date_range_changes(self):
        self.opening()
        self.entry(transaction_date=date(2026, 1, 5), credit="500")
        self.entry(transaction_date=date(2026, 1, 10), credit="200")
        self.entry(transaction_date=date(2026, 1, 20), credit="0", debit="50")
        self.entry(transaction_date=date(2026, 1, 21), credit="9999")
        response = self.ledger(date_from="2026-01-10", date_to="2026-01-20")
        self.assertEqual(response.context["brought_forward"], Decimal("1500.00"))
        self.assertEqual(response.context["total_credit"], Decimal("200.00"))
        self.assertEqual(response.context["total_debit"], Decimal("50.00"))
        self.assertEqual(response.context["final_balance"], Decimal("1650.00"))
        self.assertEqual(
            [entry.running_balance for entry in response.context["page_obj"]],
            [Decimal("1700.00"), Decimal("1650.00")],
        )
        all_history = self.ledger(date_to="2026-01-20")
        self.assertEqual(
            all_history.context["final_balance"], response.context["final_balance"]
        )
        report = self.report(
            member=self.member.pk,
            payment_type=self.payment_type.pk,
            date_from="2026-01-10",
            date_to="2026-01-20",
        )
        row = report.context["page_obj"][0]
        self.assertEqual(row["brought_forward"], Decimal("1500.00"))
        self.assertEqual(row["final_balance"], Decimal("1650.00"))
        self.assertIn("date_from=2026-01-10", row["statement_url"])
        self.assertIn("date_to=2026-01-20", row["statement_url"])

    def test_statement_and_report_use_the_same_opening_date_rules_as_form(self):
        self.opening()
        self.entry(transaction_date=date(2025, 12, 31), credit="900")
        self.entry(transaction_date=date(2026, 1, 1), credit="2000")
        self.entry(
            transaction_date=date(2025, 12, 31),
            payment_type=self.other_type,
            credit="25",
        )
        response = self.ledger(date_from="2025-12-01", date_to="2026-01-15")
        self.assertEqual(response.context["final_balance"], Decimal("3000.00"))
        self.assertEqual(response.context["effective_date_from"], date(2026, 1, 1))
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        report = self.report(member=self.member.pk, date="2026-01-15")
        self.assertEqual(report.context["final_balance"], Decimal("3025.00"))
        self.assertEqual(
            str(report.context["final_balance"]), self.balances().json()["total"]
        )
        historical = self.ledger(date="2025-12-31")
        self.assertIsNone(historical.context["opening_date"])
        self.assertEqual(historical.context["final_balance"], Decimal("900.00"))
        self.assertEqual(
            self.report(member=self.member.pk, date="2025-12-31").context[
                "final_balance"
            ],
            Decimal("925.00"),
        )

    def test_opening_only_and_days_without_transactions_still_show_final_balance(self):
        self.opening(amount="10000")
        response = self.ledger(date="2026-01-15")
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertEqual(response.context["final_balance"], Decimal("10000.00"))
        self.assertContains(response, "data-final-balance>10000.00</output>")
        response = self.report(
            member=self.member.pk, payment_type=self.payment_type.pk, date="2026-01-15"
        )
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertEqual(response.context["final_balance"], Decimal("10000.00"))

    def test_report_separates_members_and_payment_types_and_filters_each(self):
        self.opening()
        self.entry(credit="2000")
        self.entry(payment_type=self.other_type, credit="50")
        self.entry(member=self.other, credit="70")
        self.entry(member=self.foreign, credit="999999")
        report = self.report(date="2026-01-15")
        self.assertEqual(report.context["member_count"], 2)
        self.assertEqual(report.context["page_obj"].paginator.count, 3)
        amounts = {
            (row["member_id"], row["payment_type_id"]): row["final_balance"]
            for row in report.context["page_obj"]
        }
        self.assertEqual(
            amounts[(self.member.pk, self.payment_type.pk)], Decimal("3000.00")
        )
        self.assertEqual(
            amounts[(self.member.pk, self.other_type.pk)], Decimal("50.00")
        )
        self.assertEqual(
            amounts[(self.other.pk, self.payment_type.pk)], Decimal("70.00")
        )
        self.assertEqual(report.context["final_balance"], Decimal("3120.00"))
        self.assertNotContains(report, self.foreign.full_name)
        self.assertEqual(
            self.report(member=self.member.pk).context["final_balance"],
            Decimal("3050.00"),
        )
        self.assertEqual(
            self.report(payment_type=self.payment_type.pk).context["final_balance"],
            Decimal("3070.00"),
        )

    def test_report_and_statement_balances_remain_correct_across_pages(self):
        self.opening()
        for _ in range(12):
            self.entry(credit="0.10")
        response = self.ledger(date="2026-01-15", page="2")
        self.assertEqual(response.context["final_balance"], Decimal("1001.20"))
        self.assertEqual(
            [entry.running_balance for entry in response.context["page_obj"]],
            [Decimal("1001.10"), Decimal("1001.20")],
        )
        for index in range(12):
            payment_type = PaymentType.objects.create(name=f"Report type {index}")
            self.entry(payment_type=payment_type, credit="10.00")
        report = self.report(date="2026-01-15", page="2")
        self.assertEqual(report.context["page_obj"].paginator.count, 13)
        self.assertEqual(report.context["final_balance"], Decimal("1121.20"))
        self.assertEqual(report.context["total_credit"], Decimal("121.20"))
        self.assertEqual(len(report.context["page_obj"]), 3)
        self.assertIn("date=2026-01-15", report.context["query_string"])
        self.assertNotIn("page=", report.context["query_string"])

    def test_report_includes_inactive_balances_and_negative_accounts(self):
        self.entry(credit="0", debit="20.25")
        self.payment_type.is_active = False
        self.payment_type.save()
        report = self.report()
        self.assertEqual(report.context["final_balance"], Decimal("-20.25"))
        self.assertContains(report, "(inactive)")
        self.assertEqual(
            report.context["page_obj"][0]["payment_type_id"], self.payment_type.pk
        )

    def test_final_balances_default_to_today_and_ignore_future_transactions(self):
        self.entry(credit="100")
        tomorrow = timezone.localdate() + timedelta(days=1)
        self.entry(transaction_date=tomorrow, credit="2000")
        self.assertEqual(self.report().context["final_balance"], Decimal("100.00"))
        self.assertEqual(self.ledger().context["final_balance"], Decimal("100.00"))
        self.assertEqual(
            self.report(date=tomorrow.isoformat()).context["final_balance"],
            Decimal("2100.00"),
        )
        self.assertTrue(
            self.report(date_from=tomorrow.isoformat()).context["form"].errors
        )

    def test_report_invalid_filters_do_not_reveal_unfiltered_balances(self):
        self.opening()
        for filters in (
            {"member": self.foreign.pk},
            {"member": "abc"},
            {"payment_type": "abc"},
            {"date": "bad"},
            {"date_from": "2026-02-01", "date_to": "2026-01-01"},
            {"date": "2026-01-15", "date_from": "2026-01-01"},
        ):
            report = self.report(**filters)
            self.assertTrue(report.context["form"].errors)
            self.assertNotIn("final_balance", report.context)
            self.assertEqual(report.context["page_obj"].paginator.count, 0)

    def test_report_requires_view_permission_and_respects_district_changes(self):
        self.entry(member=self.foreign)
        for grants in (
            ("add_paymenttransaction",),
            ("view_member", "view_openingbalance"),
        ):
            self.grant(*grants)
            self.assertEqual(self.report().status_code, 403)
        self.grant("view_paymenttransaction")
        self.assertEqual(self.report().context["member_count"], 0)
        self.user.assigned_districts.add(self.foreign.district)
        self.assertEqual(self.report().context["member_count"], 1)
        self.user.assigned_districts.clear()
        self.assertEqual(self.report().context["member_count"], 0)
        self.assertEqual(
            Client().get(reverse("payment_transaction_report")).status_code, 302
        )

    def opening(self, amount="1000.00", **overrides):
        opening = OpeningBalance.objects.create(
            member=self.member, balance_date=date(2026, 1, 1), **overrides
        )
        OpeningBalanceAmount.objects.create(
            opening_balance=opening, payment_type=self.payment_type, amount=amount
        )
        return opening

    def test_interest_frequency_is_configurable_for_each_payment_type(self):
        self.client.force_login(self.admin)
        for value, label in PaymentType.InterestFrequency.choices:
            if value == PaymentType.InterestFrequency.CUSTOM:
                continue
            response = self.client.post(
                reverse("payment_type_edit", args=[self.payment_type.pk]),
                {
                    "name": self.payment_type.name,
                    "interest_rate": "7.25",
                    "interest_frequency": value,
                    "sort_order": "0",
                    "is_active": "on",
                },
            )
            self.assertRedirects(response, reverse("payment_type_list"))
            self.payment_type.refresh_from_db()
            self.assertEqual(self.payment_type.interest_frequency, value)
            self.assertContains(self.client.get(reverse("payment_type_list")), label)
            response = self.client.get(reverse("payment_transaction_create"))
            self.assertEqual(
                response.context["payment_type_frequencies"][str(self.payment_type.pk)],
                label,
            )
            self.assertContains(self.ledger(), f"Calculation frequency: {label}")
        self.assertEqual(self.other_type.interest_frequency, "quarterly")
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_interest_frequency_rejects_unknown_values_and_defaults_to_quarterly(self):
        form = PaymentTypeForm(
            {
                "name": "Invalid frequency",
                "interest_frequency": "weekly",
                "sort_order": "0",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("interest_frequency", form.errors)
        form = PaymentTypeForm(
            {
                "name": "Frequency not decided",
                "interest_rate": "2.50",
                "sort_order": "0",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().interest_frequency, "quarterly")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentType.objects.filter(pk=self.payment_type.pk).update(
                interest_frequency="weekly"
            )

    def test_member_balance_includes_opening_credit_debit_and_all_types(self):
        self.opening()
        self.entry(credit="250.25")
        self.entry(debit="50.10", credit="0")
        self.entry(payment_type=self.other_type, credit="20.05")
        self.entry(member=self.other, credit="99999")
        self.entry(transaction_date=date(2026, 1, 16), credit="88888")
        response = self.balances()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], "1220.20")
        self.assertEqual(data["as_of"], "2026-01-15")
        self.assertEqual(data["opening_date"], "2026-01-01")
        amounts = {row["payment_type_id"]: row["balance"] for row in data["balances"]}
        self.assertEqual(amounts[self.payment_type.pk], "1200.15")
        self.assertEqual(amounts[self.other_type.pk], "20.05")
        self.assertNotContains(response, self.member.private_address)
        self.assertNotContains(response, self.member.nic_no)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_opening_balance_is_start_of_day_and_prevents_double_counting_history(self):
        self.opening()
        self.entry(transaction_date=date(2025, 12, 31), credit="900")
        self.entry(transaction_date=date(2026, 1, 1), credit="50")
        self.entry(
            transaction_date=date(2025, 12, 31),
            payment_type=self.other_type,
            credit="25",
        )
        self.assertEqual(self.balances().json()["total"], "1075.00")
        # A type without an opening amount keeps its full transaction history.
        self.assertEqual(self.balances(date="2026-01-01").json()["total"], "1075.00")
        historical = self.balances(date="2025-12-31").json()
        self.assertEqual(historical["total"], "925.00")
        self.assertIsNone(historical["opening_date"])

    def test_member_balance_works_without_openings_and_can_be_negative(self):
        self.entry(credit="0.10")
        self.entry(credit="0.20")
        self.entry(credit="0", debit="1.00")
        data = self.balances().json()
        self.assertEqual(data["total"], "-0.70")
        self.assertTrue(data["has_records"])
        self.assertIsNone(data["opening_date"])

    def test_member_balance_without_records_returns_zero_and_inactive_history_survives(
        self,
    ):
        data = self.balances().json()
        self.assertFalse(data["has_records"])
        self.assertEqual(data["total"], "0.00")
        self.opening(amount="0.00")
        self.payment_type.is_active = False
        self.payment_type.save()
        data = self.balances().json()
        self.assertTrue(data["has_records"])
        row = next(
            row
            for row in data["balances"]
            if row["payment_type_id"] == self.payment_type.pk
        )
        self.assertFalse(row["is_active"])
        self.assertEqual(row["balance"], "0.00")

    def test_balance_form_renders_preselected_member_and_excludes_entry_on_edit(self):
        self.opening()
        entry = self.entry(credit="100")
        response = self.client.get(
            reverse("payment_transaction_create"),
            {"member": self.member.pk, "payment_type": self.payment_type.pk},
        )
        self.assertEqual(response.context["balance_summary"]["total"], "1100.00")
        self.assertEqual(response.context["selected_type_balance"], "1100.00")
        self.assertContains(response, "data-member-balance>1100.00</output>")
        response = self.client.get(reverse("payment_transaction_edit", args=[entry.pk]))
        self.assertEqual(response.context["balance_summary"]["total"], "1000.00")
        self.assertEqual(self.balances(entry=entry.pk).json()["total"], "1000.00")
        # Unsaved values on a failed POST must never alter the displayed saved balance.
        response = self.client.post(
            reverse("payment_transaction_create"),
            self.payload(credit="9999", debit="1"),
        )
        self.assertEqual(response.context["balance_summary"]["total"], "1100.00")

    def test_balance_endpoint_is_scoped_and_requires_transaction_permissions(self):
        self.entry(member=self.foreign, credit="9999")
        self.assertEqual(self.balances(member=self.foreign.pk).status_code, 404)
        self.grant("view_member", "view_openingbalance", "view_paymentplan")
        self.assertEqual(self.balances().status_code, 403)
        self.grant("delete_paymenttransaction")
        self.assertEqual(self.balances().status_code, 403)
        for action in ("add", "view", "change"):
            self.grant(f"{action}_paymenttransaction")
            self.assertEqual(self.balances().status_code, 200)
        self.user.assigned_districts.clear()
        self.assertEqual(self.balances().status_code, 404)

    def test_balance_lookup_validation_and_edit_exclusion_are_authorized(self):
        for params in (
            {"member": "bad"},
            {"date": "bad"},
            {"date": ""},
            {"entry": "bad"},
        ):
            self.assertEqual(self.balances(**params).status_code, 400)
        entry = self.entry(member=self.other)
        self.assertEqual(self.balances(entry=entry.pk).status_code, 404)
        self.assertEqual(self.balances(entry="999999").status_code, 404)
        entry = self.entry()
        self.grant("add_paymenttransaction")
        self.assertEqual(self.balances(entry=entry.pk).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("payment_transaction_balances"), {"member": self.member.pk}
            ).status_code,
            405,
        )
        self.assertEqual(
            Client()
            .get(reverse("payment_transaction_balances"), {"member": self.member.pk})
            .status_code,
            302,
        )
        # Ordinary balance lookups use today's date if a date was not supplied.
        self.assertEqual(
            self.client.get(
                reverse("payment_transaction_balances"), {"member": self.member.pk}
            ).status_code,
            200,
        )

    def test_interest_rate_master_saves_and_displays_percentage(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("payment_type_create"),
            {
                "name": "Interest-bearing deposit",
                "interest_rate": "8.75",
                "sort_order": "9",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("payment_type_list"))
        payment_type = PaymentType.objects.get(name="Interest-bearing deposit")
        self.assertEqual(payment_type.interest_rate, Decimal("8.75"))
        self.assertContains(self.client.get(reverse("payment_type_list")), "8.75%")
        self.assertContains(self.ledger(), "7.25%")

    def test_invalid_interest_rates_and_database_constraint(self):
        for value in ("-0.01", "100.01", "1.001", "NaN", "Infinity", "abc"):
            form = PaymentTypeForm(
                {"name": "Invalid rate", "sort_order": "0", "interest_rate": value}
            )
            self.assertFalse(form.is_valid(), value)
            self.assertIn("interest_rate", form.errors)
        for value in ("0", "100", ""):
            form = PaymentTypeForm(
                {"name": "Valid rate", "sort_order": "0", "interest_rate": value}
            )
            self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentType.objects.filter(pk=self.payment_type.pk).update(interest_rate=-1)

    def test_create_debit_and_credit_with_blank_other_side_and_creator(self):
        for values in (
            {"debit": "25.75", "credit": ""},
            {"credit": "0.10", "debit": ""},
        ):
            response = self.client.post(
                reverse("payment_transaction_create"), self.payload(**values)
            )
            self.assertRedirects(
                response,
                reverse("payment_transaction_list")
                + f"?member={self.member.pk}&payment_type={self.payment_type.pk}",
            )
        entries = list(PaymentTransaction.objects.all())
        self.assertEqual(
            [(item.debit, item.credit) for item in entries],
            [(Decimal("25.75"), Decimal("0.00")), (Decimal("0.00"), Decimal("0.10"))],
        )
        self.assertTrue(all(item.created_by == self.user for item in entries))

    def test_invalid_entries_never_save(self):
        invalid = [
            {"debit": "0", "credit": "0"},
            {"debit": "", "credit": ""},
            {"debit": "1", "credit": "1"},
            {"debit": "-1"},
            {"credit": "-1"},
            {"credit": "NaN"},
            {"credit": "1.001"},
            {"credit": "10000000000.00"},
            {"credit": "abc"},
            {"transaction_date": "not-a-date"},
            {"member": self.foreign.pk},
            {"payment_type": "999999"},
        ]
        for values in invalid:
            with self.subTest(values=values):
                response = self.client.post(
                    reverse("payment_transaction_create"), self.payload(**values)
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertFalse(PaymentTransaction.objects.exists())

    def test_model_and_database_require_exactly_one_positive_side(self):
        for debit, credit in ((0, 0), (1, 1), (-1, 0), (0, -1)):
            with self.assertRaises(ValidationError):
                PaymentTransaction(
                    member=self.member,
                    payment_type=self.payment_type,
                    debit=debit,
                    credit=credit,
                ).full_clean()
            with self.assertRaises(IntegrityError), transaction.atomic():
                self.entry(debit=debit, credit=credit)

    def test_single_date_is_inclusive_and_separates_member_and_type(self):
        expected = self.entry(credit="0.10")
        self.entry(credit="0.20")
        self.entry(transaction_date=date(2026, 1, 14))
        self.entry(transaction_date=date(2026, 1, 16))
        self.entry(member=self.other)
        self.entry(payment_type=self.other_type)
        response = self.ledger(date="2026-01-15")
        self.assertEqual(response.context["page_obj"].paginator.count, 2)
        self.assertEqual(response.context["total_credit"], Decimal("0.30"))
        self.assertEqual(response.context["total_debit"], Decimal("0.00"))
        self.assertEqual(response.context["net_movement"], Decimal("0.30"))
        self.assertEqual(response.context["page_obj"][0].pk, expected.pk)

    def test_date_range_totals_and_running_net_include_both_boundaries(self):
        self.entry(transaction_date=date(2026, 1, 1), credit="50")
        self.entry(transaction_date=date(2026, 1, 10), credit="200")
        self.entry(transaction_date=date(2026, 1, 20), debit="40", credit="0")
        self.entry(transaction_date=date(2026, 1, 21), credit="300")
        response = self.ledger(date_from="2026-01-10", date_to="2026-01-20")
        self.assertEqual(response.context["total_credit"], Decimal("200.00"))
        self.assertEqual(response.context["total_debit"], Decimal("40.00"))
        self.assertEqual(response.context["net_movement"], Decimal("160.00"))
        self.assertEqual(
            [item.running_net for item in response.context["page_obj"]],
            [Decimal("200.00"), Decimal("160.00")],
        )

    def test_all_dates_and_open_ended_ranges(self):
        self.entry(transaction_date=date(2026, 1, 1))
        self.entry(transaction_date=date(2026, 1, 31), credit="50")
        self.assertEqual(self.ledger().context["total_credit"], Decimal("150.00"))
        self.assertEqual(
            self.ledger(date_from="2026-01-31").context["total_credit"],
            Decimal("50.00"),
        )
        self.assertEqual(
            self.ledger(date_to="2026-01-01").context["total_credit"], Decimal("100.00")
        )

    def test_invalid_filters_do_not_fall_back_to_unfiltered_financial_data(self):
        self.entry()
        for filters in (
            {"date": "bad"},
            {"date_from": "bad"},
            {"date_to": "bad"},
            {"date_from": "2026-02-01", "date_to": "2026-01-01"},
            {"date": "2026-01-15", "date_from": "2026-01-01"},
            {"date": "2026-01-15", "date_to": "2026-01-31"},
            {"member": self.foreign.pk},
            {"member": "abc"},
            {"payment_type": "bad"},
        ):
            with self.subTest(filters=filters):
                response = self.ledger(**filters)
                self.assertTrue(response.context["form"].errors)
                self.assertNotIn("total_credit", response.context)
                self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_pagination_preserves_filters_totals_and_running_net(self):
        for _ in range(12):
            self.entry(credit="0.10")
        response = self.ledger(date="2026-01-15", page="2")
        self.assertEqual(response.context["total_credit"], Decimal("1.20"))
        self.assertEqual(len(response.context["page_obj"]), 2)
        self.assertEqual(
            [item.running_net for item in response.context["page_obj"]],
            [Decimal("1.10"), Decimal("1.20")],
        )
        self.assertIn("date=2026-01-15", response.context["query_string"])
        self.assertNotIn("page=", response.context["query_string"])

    def test_empty_selection_and_period_show_no_unrelated_entries(self):
        self.entry(member=self.other)
        response = self.ledger()
        self.assertEqual(response.context["total_credit"], Decimal("0.00"))
        self.assertContains(response, "No debit / credit entries for this selection.")
        response = self.client.get(reverse("payment_transaction_list"))
        self.assertNotIn("selected_member", response.context)
        self.assertContains(response, "Choose a member and payment type above")

    def test_inactive_types_remain_in_history_and_existing_editor(self):
        entry = self.entry()
        self.payment_type.is_active = False
        self.payment_type.save()
        self.assertContains(self.ledger(), "(inactive)")
        fresh = PaymentTransactionForm(self.payload(), user=self.user)
        self.assertFalse(fresh.is_valid())
        self.assertIn("payment_type", fresh.errors)
        response = self.client.post(
            reverse("payment_transaction_edit", args=[entry.pk]),
            self.payload(credit="150"),
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.credit, Decimal("150.00"))

    def test_edit_cannot_move_entry_with_forged_post_or_initial_query(self):
        entry = self.entry(created_by=self.user)
        url = (
            reverse("payment_transaction_edit", args=[entry.pk])
            + f"?member={self.other.pk}&payment_type={self.other_type.pk}"
        )
        response = self.client.post(
            url,
            self.payload(
                member=self.foreign.pk, payment_type=self.other_type.pk, credit="125"
            ),
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.member, self.member)
        self.assertEqual(entry.payment_type, self.payment_type)
        self.assertEqual(entry.credit, Decimal("125.00"))
        self.assertEqual(entry.created_by, self.user)

    def test_foreign_district_entries_and_member_choices_are_inaccessible(self):
        entry = self.entry(member=self.foreign)
        for name in ("payment_transaction_edit", "payment_transaction_delete"):
            self.assertEqual(
                self.client.get(reverse(name, args=[entry.pk])).status_code, 404
            )
            self.assertEqual(
                self.client.post(
                    reverse(name, args=[entry.pk]), self.payload()
                ).status_code,
                404,
            )
        for name in ("payment_transaction_create", "payment_transaction_list"):
            response = self.client.get(reverse(name))
            self.assertNotContains(response, self.foreign.full_name)
            self.assertContains(response, self.member.full_name)
        self.user.assigned_districts.add(self.foreign.district)
        self.assertEqual(
            self.client.get(
                reverse("payment_transaction_edit", args=[entry.pk])
            ).status_code,
            200,
        )
        self.user.assigned_districts.clear()
        self.assertTrue(self.ledger().context["form"].errors)

    def test_add_only_operator_can_record_without_reading_ledger(self):
        self.grant("add_paymenttransaction")
        response = self.client.get(reverse("payment_transaction_create"))
        self.assertContains(response, self.member.full_name)
        self.assertNotContains(response, self.member.private_address)
        response = self.client.post(
            reverse("payment_transaction_create"), self.payload()
        )
        self.assertRedirects(response, reverse("payment_transaction_create"))
        self.assertEqual(self.ledger().status_code, 403)

    def test_member_and_plan_permissions_do_not_grant_transaction_access(self):
        self.grant(
            "view_member", "add_member", "view_paymentplan", "view_openingbalance"
        )
        entry = self.entry()
        for name, args in (
            ("payment_transaction_list", []),
            ("payment_transaction_create", []),
            ("payment_transaction_edit", [entry.pk]),
            ("payment_transaction_delete", [entry.pk]),
        ):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
            self.assertEqual(
                self.client.post(reverse(name, args=args), self.payload()).status_code,
                403,
            )

    def test_delete_requires_post_and_preserves_member(self):
        entry = self.entry()
        url = reverse("payment_transaction_delete", args=[entry.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(PaymentTransaction.objects.filter(pk=entry.pk).exists())
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(PaymentTransaction.objects.filter(pk=entry.pk).exists())
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())

    def test_recorded_entries_protect_member_and_payment_type(self):
        self.entry()
        for obj in (self.member, self.payment_type):
            with self.assertRaises(ProtectedError):
                obj.delete()

    def test_module_permissions_navigation_and_advanced_admin(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, reverse("payment_transaction_list"))
        self.assertNotIn(
            "payment_transactions",
            [item["section"] for item in response.context["master_modules"]],
        )
        for form in (WorkspaceRoleForm(), WorkspaceUserForm(actor=self.admin)):
            row = next(
                row
                for row in form.permission_rows()
                if row["label"] == "Debit / Credit"
            )
            self.assertEqual(len(row["cells"]), 4)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(
                reverse("admin:members_paymenttransaction_changelist")
            ).status_code,
            200,
        )

    def test_anonymous_access_and_csrf(self):
        entry = self.entry()
        anonymous = Client()
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        for name, args in (
            ("payment_transaction_list", []),
            ("payment_transaction_create", []),
            ("payment_transaction_edit", [entry.pk]),
            ("payment_transaction_delete", [entry.pk]),
        ):
            self.assertEqual(anonymous.get(reverse(name, args=args)).status_code, 302)
            if name != "payment_transaction_list":
                self.assertEqual(
                    csrf.post(reverse(name, args=args), self.payload()).status_code, 403
                )
