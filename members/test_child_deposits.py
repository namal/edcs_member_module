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
from .child_forms import ChildDepositForm, ChildDepositTypeForm, ChildForm
from .child_services import deposit_balance
from .models import (
    Child,
    ChildDeposit,
    ChildDepositTransaction,
    ChildDepositType,
    District,
    Member,
    OfficeLocation,
)


class ChildDepositTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "child-operator", password="test-only-pass"
        )
        cls.admin = get_user_model().objects.create_superuser(
            "child-admin", password="test-only-pass"
        )
        cls.user.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        cls.district = District.objects.get(code="COL")
        cls.user.assigned_districts.add(cls.district)
        cls.members = []
        for index, district in enumerate(
            (cls.district, District.objects.exclude(pk=cls.district.pk).first())
        ):
            office = OfficeLocation.objects.create(
                district=district, name=f"Child zone {index}", code=f"CHILD-Z-{index}"
            )
            cls.members.append(
                Member.objects.create(
                    district=district,
                    office_location=office,
                    member_no=f"CHILD-M-{index}",
                    full_name=f"Parent member {index}",
                    salary="1000.00",
                    office_address="Office",
                    private_address="Private parent address",
                    phone_mobile="0771234567",
                    gender="female",
                    nic_no=f"19901234568{index}",
                )
            )
        cls.member, cls.foreign_member = cls.members
        cls.child = Child.objects.create(
            member=cls.member, name="Mia", date_of_birth=date(2015, 1, 10)
        )
        cls.sibling = Child.objects.create(
            member=cls.member, name="Sam", date_of_birth=date(2012, 5, 1)
        )
        cls.foreign_child = Child.objects.create(
            member=cls.foreign_member,
            name="Foreign child",
            date_of_birth=date(2010, 1, 1),
        )
        cls.deposit_type = ChildDepositType.objects.get(name="Child Savings")
        cls.deposit = ChildDeposit.objects.create(
            child=cls.child,
            deposit_type=cls.deposit_type,
            opened_on=date(2026, 1, 1),
            opening_amount="1000.00",
        )
        cls.sibling_deposit = ChildDeposit.objects.create(
            child=cls.sibling,
            deposit_type=cls.deposit_type,
            opened_on=date(2026, 1, 1),
            opening_amount="50.00",
        )
        cls.foreign_deposit = ChildDeposit.objects.create(
            child=cls.foreign_child,
            deposit_type=cls.deposit_type,
            opened_on=date(2026, 1, 1),
            opening_amount="999999.00",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def grant(self, *names):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members", codename__in=names
            )
        )

    def entry_payload(self, **changes):
        return {
            "transaction_date": "2026-01-15",
            "debit": "",
            "credit": "2000.00",
            "reference": "CHILD-RECEIPT",
            "description": "Child saving",
            **changes,
        }

    def add_entry(self, deposit=None, **changes):
        return self.client.post(
            reverse("child_entry_create", args=[(deposit or self.deposit).pk]),
            self.entry_payload(**changes),
        )

    def entry(self, **changes):
        return ChildDepositTransaction.objects.create(
            **{
                "deposit": self.deposit,
                "transaction_date": date(2026, 1, 15),
                "credit": "100.00",
                "interest_rate": "8.00",
                **changes,
            }
        )

    def statement(self, **filters):
        return self.client.get(
            reverse("child_deposit_detail", args=[self.deposit.pk]), filters
        )

    def test_member_can_have_any_number_of_children_and_child_multiple_deposits(self):
        for index in range(3):
            response = self.client.post(
                reverse("child_create"),
                {
                    "member": self.member.pk,
                    "name": f"Extra child {index}",
                    "date_of_birth": "2016-02-01",
                    "is_active": "on",
                },
            )
            self.assertEqual(response.status_code, 302)
        self.assertEqual(self.member.children.count(), 5)
        special = ChildDepositType.objects.create(
            name="Junior Plus",
            age_threshold=21,
            rate_below_threshold="9.50",
            rate_from_threshold="6.25",
        )
        for deposit_type in (self.deposit_type, special):
            response = self.client.post(
                reverse("child_deposit_create"),
                {
                    "child": self.child.pk,
                    "deposit_type": deposit_type.pk,
                    "opened_on": "2026-01-01",
                    "opening_amount": "250.25",
                    "is_active": "on",
                },
            )
            self.assertEqual(response.status_code, 302)
        self.assertEqual(self.child.deposits.count(), 3)
        self.assertEqual(self.child.deposits.values("account_no").distinct().count(), 3)
        response = self.client.get(reverse("child_list"), {"member": self.member.pk})
        self.assertContains(response, self.child.name)
        self.assertContains(response, self.sibling.name)
        self.assertNotContains(response, self.foreign_child.name)

    def test_child_form_validates_name_birth_date_and_member_scope(self):
        for changes in (
            {"name": " "},
            {"date_of_birth": (timezone.localdate() + timedelta(days=1)).isoformat()},
            {"date_of_birth": "bad"},
            {"member": self.foreign_member.pk},
        ):
            data = {
                "member": self.member.pk,
                "name": "Invalid child",
                "date_of_birth": "2016-01-01",
                **changes,
            }
            form = ChildForm(data, user=self.user)
            self.assertFalse(form.is_valid(), changes)
        self.assertEqual(
            self.client.get(
                reverse("child_create"), {"member": self.foreign_member.pk}
            ).status_code,
            404,
        )
        response = self.client.get(reverse("child_create"), {"member": self.member.pk})
        self.assertEqual(response.context["form"]["member"].value(), self.member.pk)

    def test_age_and_interest_change_on_exact_configured_birthday(self):
        child = Child(
            member=self.member, name="Birthday child", date_of_birth=date(2008, 6, 15)
        )
        self.assertEqual(child.age_on(date(2026, 6, 14)), 17)
        self.assertEqual(
            self.deposit_type.rate_for(child, date(2026, 6, 14)), Decimal("8.00")
        )
        self.assertEqual(
            self.deposit_type.rate_for(child, date(2026, 6, 15)), Decimal("5.00")
        )
        self.assertEqual(
            self.deposit_type.rate_for(child, date(2027, 6, 15)), Decimal("5.00")
        )
        self.assertIsNone(self.deposit_type.rate_for(child, date(2007, 1, 1)))
        self.deposit_type.age_threshold = 21
        self.deposit_type.rate_below_threshold = Decimal("9.50")
        self.deposit_type.rate_from_threshold = Decimal("6.25")
        self.assertEqual(
            self.deposit_type.rate_for(child, date(2026, 6, 15)), Decimal("9.50")
        )
        self.assertEqual(
            self.deposit_type.rate_for(child, date(2029, 6, 15)), Decimal("6.25")
        )

    def test_bank_can_configure_each_deposit_types_rates_and_threshold(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("child_deposit_type_create"),
            {
                "name": "Education deposit",
                "age_threshold": "21",
                "rate_below_threshold": "9.25",
                "rate_from_threshold": "6.50",
                "sort_order": "2",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("child_deposit_type_list"))
        saved = ChildDepositType.objects.get(name="Education deposit")
        self.assertEqual(saved.age_threshold, 21)
        self.assertEqual(saved.rate_below_threshold, Decimal("9.25"))
        self.assertEqual(saved.rate_from_threshold, Decimal("6.50"))
        self.assertContains(
            self.client.get(reverse("child_deposit_type_list")), "9.25%"
        )
        self.assertEqual(self.deposit_type.age_threshold, 18)
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.post(
                reverse("child_deposit_type_edit", args=[saved.pk]), {}
            ).status_code,
            403,
        )

    def test_invalid_bank_rate_rules_are_rejected_by_forms_and_database(self):
        for values in (
            {"age_threshold": "0"},
            {"age_threshold": "151"},
            {"rate_below_threshold": "-1"},
            {"rate_from_threshold": "100.01"},
            {"rate_below_threshold": "NaN"},
            {"rate_from_threshold": "1.001"},
        ):
            form = ChildDepositTypeForm(
                {
                    "name": "Invalid rule",
                    "age_threshold": "18",
                    "rate_below_threshold": "8",
                    "rate_from_threshold": "5",
                    "sort_order": "0",
                    **values,
                }
            )
            self.assertFalse(form.is_valid(), values)
        for values in (
            {"age_threshold": 0},
            {"rate_below_threshold": -1},
            {"rate_from_threshold": 101},
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                ChildDepositType.objects.create(name="Invalid database rule", **values)

    def test_dated_credit_debit_and_balance_include_opening_amount(self):
        self.assertEqual(self.add_entry().status_code, 302)
        self.assertEqual(
            self.add_entry(
                transaction_date="2026-01-20", credit="", debit="500"
            ).status_code,
            302,
        )
        response = self.statement(date_from="2026-01-20", date_to="2026-01-20")
        self.assertEqual(response.context["brought_forward"], Decimal("3000.00"))
        self.assertEqual(response.context["total_debit"], Decimal("500.00"))
        self.assertEqual(response.context["final_balance"], Decimal("2500.00"))
        self.assertEqual(
            response.context["page_obj"][0].running_balance, Decimal("2500.00")
        )
        self.assertContains(response, "data-final-balance>2500.00</output>")
        saved = self.deposit.transactions.first()
        self.assertEqual(saved.created_by, self.user)
        self.assertEqual(saved.interest_rate, Decimal("8.00"))
        self.assertEqual(
            deposit_balance(self.sibling_deposit, date(2026, 1, 20)), Decimal("50.00")
        )

    def test_snapshot_rate_is_preserved_when_rules_change_and_date_edit_refreshes_it(
        self,
    ):
        self.child.date_of_birth = date(2008, 6, 15)
        self.child.save()
        self.add_entry(transaction_date="2026-06-14")
        entry = self.deposit.transactions.get()
        self.assertEqual(entry.interest_rate, Decimal("8.00"))
        self.deposit_type.rate_below_threshold = Decimal("9.00")
        self.deposit_type.rate_from_threshold = Decimal("6.00")
        self.deposit_type.save()
        url = reverse("child_entry_edit", args=[entry.pk])
        self.assertEqual(
            self.client.post(
                url,
                self.entry_payload(
                    transaction_date="2026-06-14", description="Corrected note"
                ),
            ).status_code,
            302,
        )
        entry.refresh_from_db()
        self.assertEqual(entry.interest_rate, Decimal("8.00"))
        self.client.post(url, self.entry_payload(transaction_date="2026-06-15"))
        entry.refresh_from_db()
        self.assertEqual(entry.interest_rate, Decimal("6.00"))
        self.assertEqual(entry.created_by, self.user)

    def test_invalid_entries_never_save_and_database_enforces_one_side(self):
        for changes in (
            {"credit": "", "debit": ""},
            {"credit": "1", "debit": "1"},
            {"credit": "-1"},
            {"credit": "NaN"},
            {"credit": "1.001"},
            {"credit": "10000000000"},
            {"transaction_date": "2025-12-31"},
            {"transaction_date": "bad"},
        ):
            response = self.add_entry(**changes)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            self.assertFalse(self.deposit.transactions.exists())
        for debit, credit in ((0, 0), (1, 1), (-1, 0), (0, -1)):
            with self.assertRaises(IntegrityError), transaction.atomic():
                self.entry(debit=debit, credit=credit)

    def test_saved_child_and_deposit_relations_cannot_be_reassigned(self):
        self.client.post(
            reverse("child_edit", args=[self.child.pk])
            + f"?member={self.foreign_member.pk}",
            {
                "member": self.foreign_member.pk,
                "name": "Updated Mia",
                "date_of_birth": "2015-01-10",
                "is_active": "on",
            },
        )
        self.child.refresh_from_db()
        self.assertEqual(self.child.member_id, self.member.pk)
        self.assertEqual(self.child.name, "Updated Mia")
        self.entry()
        url = (
            reverse("child_deposit_edit", args=[self.deposit.pk])
            + f"?child={self.foreign_child.pk}"
        )
        self.client.post(
            url,
            {
                "child": self.foreign_child.pk,
                "deposit_type": "99999",
                "opened_on": "2026-02-01",
                "opening_amount": "9999",
                "is_active": "on",
            },
        )
        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.child_id, self.child.pk)
        self.assertEqual(self.deposit.opening_amount, Decimal("1000.00"))
        self.assertEqual(self.deposit.opened_on, date(2026, 1, 1))
        self.deposit.opening_amount = Decimal("9000")
        with self.assertRaises(ValidationError):
            self.deposit.full_clean()

    def test_deposit_validation_opening_dates_and_inactive_choices(self):
        for changes in (
            {"opened_on": "2010-01-01"},
            {"opened_on": (timezone.localdate() + timedelta(days=1)).isoformat()},
            {"opening_amount": "-1"},
            {"child": self.foreign_child.pk},
        ):
            form = ChildDepositForm(
                {
                    "child": self.child.pk,
                    "deposit_type": self.deposit_type.pk,
                    "opened_on": "2026-01-01",
                    "opening_amount": "0",
                    **changes,
                },
                user=self.user,
            )
            self.assertFalse(form.is_valid(), changes)
        self.child.is_active = False
        self.child.save()
        self.assertEqual(self.add_entry().status_code, 200)
        self.assertFalse(self.deposit.transactions.exists())
        form = ChildDepositForm(
            {
                "child": self.child.pk,
                "deposit_type": self.deposit_type.pk,
                "opened_on": "2026-01-01",
                "opening_amount": "0",
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())

    def test_deposit_list_filters_and_totals_are_per_member_child_type_and_date(self):
        self.entry(credit="2000")
        url = reverse("child_deposit_list")
        response = self.client.get(
            url, {"member": self.member.pk, "as_of": "2026-01-15"}
        )
        self.assertEqual(response.context["total_balance"], Decimal("3050.00"))
        self.assertEqual(response.context["page_obj"].paginator.count, 2)
        self.assertNotContains(response, self.foreign_child.name)
        self.assertEqual(
            self.client.get(
                url, {"child": self.child.pk, "as_of": "2026-01-15"}
            ).context["total_balance"],
            Decimal("3000.00"),
        )
        self.assertEqual(
            self.client.get(url, {"as_of": "2025-12-31"}).context["total_balance"],
            Decimal("0.00"),
        )
        self.assertEqual(
            self.client.get(url, {"as_of": "2026-01-14"}).context["total_balance"],
            Decimal("1050.00"),
        )
        for params in (
            {"member": self.foreign_member.pk},
            {"child": self.foreign_child.pk},
            {"as_of": "bad"},
            {"member": self.member.pk, "child": self.foreign_child.pk},
        ):
            response = self.client.get(url, params)
            self.assertTrue(response.context["form"].errors)
            self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_deposit_statement_empty_period_and_pagination_keep_balance(self):
        for _ in range(12):
            self.entry(credit="0.10")
        response = self.statement(date="2026-01-15", page="2")
        self.assertEqual(response.context["final_balance"], Decimal("1001.20"))
        self.assertEqual(
            [item.running_balance for item in response.context["page_obj"]],
            [Decimal("1001.10"), Decimal("1001.20")],
        )
        self.assertIn("date=2026-01-15", response.context["query_string"])
        response = self.statement(date="2026-01-16")
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertEqual(response.context["final_balance"], Decimal("1001.20"))
        self.assertEqual(
            self.statement(date="2025-12-31").context["final_balance"], Decimal("0.00")
        )
        response = self.statement(date_from="2026-02-01", date_to="2026-01-01")
        self.assertTrue(response.context["form"].errors)
        self.assertNotIn("final_balance", response.context)

    def test_foreign_district_pages_forms_entries_and_api_are_inaccessible(self):
        foreign_entry = self.entry(deposit=self.foreign_deposit)
        for name, pk in (
            ("child_detail", self.foreign_child.pk),
            ("child_edit", self.foreign_child.pk),
            ("child_delete", self.foreign_child.pk),
            ("child_deposit_detail", self.foreign_deposit.pk),
            ("child_deposit_edit", self.foreign_deposit.pk),
            ("child_deposit_delete", self.foreign_deposit.pk),
            ("child_entry_edit", foreign_entry.pk),
            ("child_entry_delete", foreign_entry.pk),
            ("child_deposit_status", self.foreign_deposit.pk),
        ):
            self.assertEqual(
                self.client.get(reverse(name, args=[pk])).status_code, 404, name
            )
        self.assertEqual(
            self.client.get(
                reverse("child_entry_create", args=[self.foreign_deposit.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("child_entry_create", args=[self.foreign_deposit.pk]),
                self.entry_payload(),
            ).status_code,
            404,
        )
        self.user.assigned_districts.add(self.foreign_member.district)
        self.assertEqual(
            self.client.get(
                reverse("child_deposit_detail", args=[self.foreign_deposit.pk])
            ).status_code,
            200,
        )
        self.user.assigned_districts.clear()
        self.assertEqual(
            self.client.get(reverse("child_deposit_list"))
            .context["page_obj"]
            .paginator.count,
            0,
        )

    def test_status_api_shows_date_based_balance_and_rate_and_excludes_edited_entry(
        self,
    ):
        entry = self.entry()
        url = reverse("child_deposit_status", args=[self.deposit.pk])
        response = self.client.get(url, {"date": "2026-01-15"})
        self.assertEqual(response.json()["balance"], "1100.00")
        self.assertEqual(response.json()["rate"], "8.00")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(
            self.client.get(url, {"date": "2026-01-15", "entry": entry.pk}).json()[
                "balance"
            ],
            "1000.00",
        )
        sibling_entry = self.entry(deposit=self.sibling_deposit)
        self.assertEqual(
            self.client.get(url, {"entry": sibling_entry.pk}).status_code, 404
        )
        self.assertEqual(self.client.get(url, {"entry": "bad"}).status_code, 400)
        self.assertEqual(self.client.get(url, {"date": "bad"}).status_code, 400)
        self.grant("add_childdeposit")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url, {"entry": entry.pk}).status_code, 403)

    def test_child_profile_exposes_balances_only_with_deposit_view_permission(self):
        response = self.client.get(reverse("child_detail", args=[self.child.pk]))
        self.assertEqual(response.context["total_balance"], Decimal("1000.00"))
        self.grant("view_child")
        response = self.client.get(reverse("child_detail", args=[self.child.pk]))
        self.assertNotContains(response, self.deposit.account_no)
        self.assertNotContains(response, "1000.00")

    def test_member_permissions_do_not_grant_child_deposit_access(self):
        self.grant("view_member", "add_member", "view_paymenttransaction")
        for name, args in (
            ("child_list", []),
            ("child_create", []),
            ("child_deposit_list", []),
            ("child_deposit_create", []),
            ("child_deposit_detail", [self.deposit.pk]),
            ("child_entry_create", [self.deposit.pk]),
            ("child_deposit_status", [self.deposit.pk]),
        ):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)

    def test_add_only_operator_can_register_child_open_deposit_and_record_entries(self):
        self.grant("add_child", "add_childdeposit")
        self.assertEqual(self.client.get(reverse("child_create")).status_code, 200)
        response = self.add_entry()
        self.assertRedirects(
            response, reverse("child_entry_create", args=[self.deposit.pk])
        )
        self.assertEqual(
            self.client.get(reverse("child_deposit_list")).status_code, 403
        )
        self.assertEqual(self.client.get(reverse("child_list")).status_code, 403)

    def test_used_records_are_protected_and_entry_deletion_requires_post(self):
        entry = self.entry()
        for obj in (self.member, self.child, self.deposit, self.deposit_type):
            with self.assertRaises(ProtectedError):
                obj.delete()
        response = self.client.post(
            reverse("child_deposit_delete", args=[self.deposit.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ChildDeposit.objects.filter(pk=self.deposit.pk).exists())
        url = reverse("child_entry_delete", args=[entry.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(ChildDepositTransaction.objects.filter(pk=entry.pk).exists())
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(ChildDepositTransaction.objects.filter(pk=entry.pk).exists())
        self.assertEqual(
            deposit_balance(self.deposit, date(2026, 1, 15)), Decimal("1000.00")
        )

    def test_permissions_navigation_and_admin_include_the_new_modules(self):
        for form in (WorkspaceRoleForm(), WorkspaceUserForm(actor=self.admin)):
            rows = {row["label"]: row for row in form.permission_rows()}
            for label in ("Children", "Child Deposits", "Child Deposit Types"):
                self.assertEqual(len(rows[label]["cells"]), 4)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, reverse("child_list"))
        self.assertContains(response, reverse("child_deposit_list"))
        self.assertNotIn(
            "children", [item["section"] for item in response.context["master_modules"]]
        )
        self.client.force_login(self.admin)
        for model in (Child, ChildDeposit, ChildDepositType, ChildDepositTransaction):
            self.assertEqual(
                self.client.get(
                    reverse(f"admin:members_{model._meta.model_name}_changelist")
                ).status_code,
                200,
            )

    def test_anonymous_and_csrf_protection_for_child_and_deposit_mutations(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        entry = self.entry()
        for name, args in (
            ("child_create", []),
            ("child_edit", [self.child.pk]),
            ("child_delete", [self.child.pk]),
            ("child_deposit_create", []),
            ("child_deposit_edit", [self.deposit.pk]),
            ("child_deposit_delete", [self.deposit.pk]),
            ("child_entry_create", [self.deposit.pk]),
            ("child_entry_edit", [entry.pk]),
            ("child_entry_delete", [entry.pk]),
        ):
            url = reverse(name, args=args)
            self.assertEqual(Client().get(url).status_code, 302)
            self.assertEqual(csrf.post(url, {}).status_code, 403)

    def test_as_of_rate_changes_at_birthday_and_type_rules_are_independent(self):
        self.child.date_of_birth = date(2008, 6, 15)
        self.child.save()
        special = ChildDepositType.objects.create(
            name="Age 21 Savings",
            age_threshold=21,
            rate_below_threshold=Decimal("9.50"),
            rate_from_threshold=Decimal("6.25"),
        )
        ChildDeposit.objects.create(
            child=self.child, deposit_type=special, opened_on=date(2026, 1, 1)
        )
        for as_of, rate in (("2026-06-14", "8.00"), ("2026-06-15", "5.00")):
            response = self.client.get(
                reverse("child_deposit_status", args=[self.deposit.pk]), {"date": as_of}
            )
            self.assertEqual(response.json()["rate"], rate)
            listing = self.client.get(
                reverse("child_deposit_list"), {"child": self.child.pk, "as_of": as_of}
            )
            rates = {
                item.deposit_type_id: item.configured_rate
                for item in listing.context["page_obj"]
            }
            self.assertEqual(rates[self.deposit_type.pk], Decimal(rate))
            self.assertEqual(rates[special.pk], Decimal("9.50"))

    def test_account_list_totals_span_pages_and_entries_remain_separate(self):
        for index in range(12):
            account = ChildDeposit.objects.create(
                child=self.child,
                deposit_type=self.deposit_type,
                opened_on=date(2026, 1, 1),
                opening_amount=Decimal("10.00"),
            )
            self.entry(deposit=account, credit="0.10")
        response = self.client.get(
            reverse("child_deposit_list"),
            {"child": self.child.pk, "as_of": "2026-01-15", "page": "2"},
        )
        self.assertEqual(response.context["page_obj"].paginator.count, 13)
        self.assertEqual(response.context["total_balance"], Decimal("1121.20"))
        self.assertEqual(len(response.context["page_obj"]), 3)
        self.assertIn("as_of=2026-01-15", response.context["query_string"])

    def test_inactive_history_remains_editable_but_new_entries_are_blocked(self):
        entry = self.entry()
        self.deposit_type.is_active = False
        self.deposit_type.save()
        self.assertEqual(self.add_entry().status_code, 200)
        self.assertEqual(self.deposit.transactions.count(), 1)
        response = self.client.post(
            reverse("child_entry_edit", args=[entry.pk]),
            self.entry_payload(credit="125"),
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.credit, Decimal("125.00"))
        response = self.client.get(reverse("child_deposit_list"))
        self.assertContains(response, self.deposit.account_no)
        self.assertContains(response, "(inactive)")

    def test_saved_entry_cannot_be_moved_and_birth_correction_cannot_break_history(
        self,
    ):
        entry = self.entry()
        entry.deposit = self.sibling_deposit
        with self.assertRaises(ValidationError):
            entry.full_clean()
        self.child.date_of_birth = date(2026, 1, 2)
        with self.assertRaises(ValidationError):
            self.child.full_clean()
