from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse

from .access_forms import WorkspaceRoleForm, WorkspaceUserForm
from .models import (
    District,
    Lager,
    Member,
    OfficeLocation,
    OpeningBalance,
    OpeningBalanceAmount,
    PaymentPlan,
    PaymentType,
    School,
)
from .opening_balance_forms import OpeningBalanceForm


class OpeningBalanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "balance-operator", password="test-only-pass"
        )
        cls.user.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        cls.members = []
        for index, district_code in enumerate(("COL", "COL", "KAN")):
            district = District.objects.get(code=district_code)
            office = OfficeLocation.objects.create(
                district=district, name=f"Balance Zone {index}", code=f"BAL-Z{index}"
            )
            school = School.objects.create(
                district=district,
                zonal_office=office,
                name=f"Balance Department {index}",
                code=f"BAL-D{index}",
            )
            lager = Lager.objects.create(
                zonal_office=office, name=f"Balance Lager {index}", code=f"BAL-L{index}"
            )
            cls.members.append(
                Member.objects.create(
                    district=district,
                    office_location=office,
                    school=school,
                    lager=lager,
                    member_no=f"M-BAL-{index}",
                    full_name=f"Balance Member {index}",
                    salary="1000.00",
                    office_address="Office Road",
                    private_address="Private balance member address",
                    phone_mobile="0771234567",
                    gender="female",
                    nic_no=f"19901234567{index}",
                )
            )
        cls.member, cls.other, cls.outside = cls.members
        cls.user.assigned_districts.add(cls.member.district)
        cls.deposit = PaymentType.objects.get(name="Special Deposit")
        from .test_support import assign_member_ledgers

        assign_member_ledgers(cls.user, cls.members)

    def setUp(self):
        self.client.force_login(self.user)

    def grant(self, *codenames):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members", codename__in=codenames
            )
        )

    def payload(self, member=None, **overrides):
        data = {
            "member": (member or self.member).pk,
            "balance_date": "2026-01-01",
            **{
                f"amount_{item.pk}": "0.00"
                for item in PaymentType.objects.filter(is_active=True)
            },
        }
        data.update(overrides)
        return data

    def create_balance(self, member=None, **amounts):
        member = member or self.member
        form = OpeningBalanceForm(self.payload(member, **amounts), member=member)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_member_selection_search_and_status(self):
        balance = self.create_balance()
        for query in (self.member.member_no, self.member.full_name):
            response = self.client.get(reverse("opening_balance_list"), {"q": query})
            self.assertEqual(
                [member.pk for member in response.context["page_obj"]], [self.member.pk]
            )
        response = self.client.get(
            reverse("opening_balance_list"), {"status": "pending"}
        )
        self.assertEqual(
            [member.pk for member in response.context["page_obj"]], [self.other.pk]
        )
        response = self.client.get(reverse("opening_balance_list"), {"status": "ready"})
        self.assertEqual(
            [member.pk for member in response.context["page_obj"]], [self.member.pk]
        )
        response = self.client.get(reverse("opening_balance_create"))
        self.assertContains(response, balance.member.full_name)
        self.assertContains(response, self.other.full_name)

    def test_select_member_displays_saved_balances_and_edit_action(self):
        balance = self.create_balance(**{f"amount_{self.deposit.pk}": "12345.67"})
        response = self.client.get(
            reverse("opening_balance_list"), {"member": self.member.pk}
        )
        self.assertContains(response, "12,345.67")
        self.assertContains(response, "Opening balances by payment type")
        self.assertContains(
            response, reverse("opening_balance_edit", args=[balance.pk])
        )

    def test_select_member_without_record_shows_each_type_and_create_action(self):
        dividend = PaymentType.objects.create(name="Dividends")
        response = self.client.get(
            reverse("opening_balance_list"), {"member": self.member.pk}
        )
        self.assertContains(response, dividend.name)
        self.assertContains(response, "No opening balances recorded")
        self.assertContains(response, "Not entered")
        self.assertContains(
            response, f'{reverse("opening_balance_create")}?member={self.member.pk}'
        )
        self.assertFalse(OpeningBalance.objects.exists())

    def test_member_selector_rejects_other_districts_and_invalid_ids(self):
        for value in (self.outside.pk, "invalid", "9999999"):
            response = self.client.get(
                reverse("opening_balance_list"), {"member": value}
            )
            self.assertTrue(response.context["selection_form"].errors)
            self.assertIsNone(response.context["selected_balance"])
            self.assertNotContains(response, self.outside.full_name)

    def test_add_only_selector_cannot_disclose_saved_amounts(self):
        self.create_balance(**{f"amount_{self.deposit.pk}": "76543.21"})
        self.grant("add_openingbalance")
        response = self.client.get(reverse("opening_balance_create"))
        self.assertContains(response, "Already recorded")
        self.assertNotContains(response, "76,543.21")
        self.assertNotContains(response, "Opening balances by payment type")

    def test_save_member_balances_and_exact_total(self):
        dividend = PaymentType.objects.create(name="Dividends")
        response = self.client.post(
            reverse("opening_balance_create"),
            self.payload(
                **{
                    f"amount_{self.deposit.pk}": "123456.78",
                    f"amount_{dividend.pk}": "250.25",
                }
            ),
        )
        balance = OpeningBalance.objects.get(member=self.member)
        self.assertRedirects(
            response, reverse("opening_balance_detail", args=[balance.pk])
        )
        self.assertEqual(balance.total, Decimal("123707.03"))
        self.assertEqual(balance.balance_date.isoformat(), "2026-01-01")
        self.assertEqual(
            balance.amounts.count(), PaymentType.objects.filter(is_active=True).count()
        )
        self.assertEqual(
            balance.amounts.get(payment_type=self.deposit).amount, Decimal("123456.78")
        )
        self.assertFalse(PaymentPlan.objects.exists())

    def test_new_payment_type_appears_in_new_and_existing_balances(self):
        balance = self.create_balance()
        dividend = PaymentType.objects.create(name="Dividends")
        field = f"amount_{dividend.pk}"
        self.assertIn(field, OpeningBalanceForm(member=self.other).fields)
        edit = OpeningBalanceForm(instance=balance, member=self.member)
        self.assertIn(field, edit.fields)
        self.assertEqual(edit[field].value(), Decimal("0.00"))
        response = self.client.get(reverse("opening_balance_detail", args=[balance.pk]))
        self.assertContains(response, "Dividends")
        self.assertContains(response, "Not entered")
        response = self.client.post(
            reverse("opening_balance_edit", args=[balance.pk]),
            self.payload(**{field: "4000.50"}),
        )
        self.assertRedirects(
            response, reverse("opening_balance_detail", args=[balance.pk])
        )
        self.assertEqual(
            balance.amounts.get(payment_type=dividend).amount, Decimal("4000.50")
        )

    def test_inactive_type_preserves_history_and_is_not_offered_for_new_members(self):
        field = f"amount_{self.deposit.pk}"
        balance = self.create_balance(**{field: "700.25"})
        self.deposit.is_active = False
        self.deposit.save()
        self.assertNotIn(field, OpeningBalanceForm(member=self.other).fields)
        form = OpeningBalanceForm(
            self.payload(**{field: "700.25"}), instance=balance, member=self.member
        )
        self.assertIn("inactive", form.fields[field].label)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(
            balance.amounts.get(payment_type=self.deposit).amount, Decimal("700.25")
        )
        response = self.client.get(reverse("opening_balance_detail", args=[balance.pk]))
        self.assertContains(response, "Special Deposit")
        self.assertContains(response, "700.25")
        with self.assertRaises(ProtectedError):
            self.deposit.delete()

    def test_invalid_amounts_and_dates_do_not_create_partial_records(self):
        field = f"amount_{self.deposit.pk}"
        for value in ("-0.01", "1.001", "10000000000.00", "NaN", "Infinity", "abc"):
            with self.subTest(value=value):
                response = self.client.post(
                    reverse("opening_balance_create"), self.payload(**{field: value})
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertFalse(OpeningBalance.objects.exists())
                self.assertFalse(OpeningBalanceAmount.objects.exists())
        for date in ("", "2026-02-30", "invalid"):
            response = self.client.post(
                reverse("opening_balance_create"), self.payload(balance_date=date)
            )
            self.assertIn("balance_date", response.context["form"].errors)
            self.assertFalse(OpeningBalance.objects.exists())

    def test_blank_balances_are_saved_as_zero(self):
        balance = self.create_balance(**{f"amount_{self.deposit.pk}": ""})
        self.assertEqual(
            balance.amounts.get(payment_type=self.deposit).amount, Decimal("0.00")
        )

    def test_duplicate_and_database_constraints(self):
        balance = self.create_balance()
        response = self.client.post(reverse("opening_balance_create"), self.payload())
        self.assertContains(response, "already has opening balances")
        self.assertEqual(OpeningBalance.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OpeningBalance.objects.create(member=self.member)
        for amount in ("1.00", "-1.00"):
            with self.assertRaises(IntegrityError), transaction.atomic():
                OpeningBalanceAmount.objects.create(
                    opening_balance=balance, payment_type=self.deposit, amount=amount
                )
        with self.assertRaises(IntegrityError), transaction.atomic():
            OpeningBalanceAmount.objects.filter(opening_balance=balance).update(
                amount="-0.01"
            )

    def test_edit_keeps_member_identity_and_payment_plan_independent(self):
        balance = self.create_balance()
        plan = PaymentPlan.objects.create(member=self.member)
        response = self.client.post(
            reverse("opening_balance_edit", args=[balance.pk]),
            self.payload(self.other, **{f"amount_{self.deposit.pk}": "9876.54"}),
        )
        self.assertRedirects(
            response, reverse("opening_balance_detail", args=[balance.pk])
        )
        balance.refresh_from_db()
        self.assertEqual(balance.member_id, self.member.pk)
        self.assertEqual(balance.total, Decimal("9876.54"))
        self.assertEqual(
            PaymentPlan.objects.get(pk=plan.pk).monthly_total, Decimal("0.00")
        )

    def test_failed_amount_save_rolls_back_the_record(self):
        form = OpeningBalanceForm(self.payload(), member=self.member)
        self.assertTrue(form.is_valid(), form.errors)
        with patch(
            "members.opening_balance_forms.OpeningBalanceAmount.objects.update_or_create",
            side_effect=RuntimeError("failure"),
        ):
            with self.assertRaises(RuntimeError):
                form.save()
        self.assertFalse(OpeningBalance.objects.exists())

    def test_delete_requires_post_and_preserves_member_and_payment_plan(self):
        balance = self.create_balance()
        plan = PaymentPlan.objects.create(member=self.member)
        url = reverse("opening_balance_delete", args=[balance.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(OpeningBalance.objects.filter(pk=balance.pk).exists())
        self.assertRedirects(self.client.post(url), reverse("opening_balance_list"))
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())
        self.assertTrue(PaymentPlan.objects.filter(pk=plan.pk).exists())
        self.assertFalse(OpeningBalanceAmount.objects.exists())

    def test_member_with_opening_balances_is_protected_from_deletion(self):
        self.create_balance()
        with self.assertRaises(ProtectedError):
            self.member.delete()

    def test_district_limits_list_selection_direct_urls_and_forged_submission(self):
        outside_balance = self.create_balance(self.outside)
        for name in ("opening_balance_list", "opening_balance_create"):
            response = self.client.get(reverse(name))
            self.assertNotContains(response, self.outside.full_name)
        for action in ("detail", "edit", "delete"):
            url = reverse(f"opening_balance_{action}", args=[outside_balance.pk])
            for method in ("get", "post"):
                self.assertEqual(
                    getattr(self.client, method)(url, self.payload()).status_code, 404
                )
        self.assertEqual(
            self.client.get(
                reverse("opening_balance_create"), {"member": self.outside.pk}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("opening_balance_create"), self.payload(self.outside)
            ).status_code,
            404,
        )
        form = OpeningBalanceForm(
            self.payload(self.outside), member=self.outside, user=self.user
        )
        self.assertFalse(form.is_valid())

    def test_district_revocation_applies_to_existing_session(self):
        balance = self.create_balance()
        self.user.assigned_districts.clear()
        self.assertEqual(
            self.client.get(
                reverse("opening_balance_detail", args=[balance.pk])
            ).status_code,
            404,
        )
        self.assertNotContains(
            self.client.get(reverse("opening_balance_list")), self.member.full_name
        )

    def test_add_only_user_can_select_and_save_without_viewing_private_member_data(
        self,
    ):
        self.grant("add_openingbalance")
        response = self.client.get(reverse("opening_balance_create"))
        self.assertContains(response, self.member.member_no)
        self.assertNotContains(response, self.member.private_address)
        self.assertRedirects(
            self.client.post(reverse("opening_balance_create"), self.payload()),
            reverse("opening_balance_create"),
        )
        for name in (
            "opening_balance_list",
            "member_list",
            "payment_plan_list",
            "payment_type_list",
        ):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_existing_record_redirects_and_add_only_cannot_overwrite(self):
        balance = self.create_balance()
        self.assertRedirects(
            self.client.get(
                reverse("opening_balance_create"), {"member": self.member.pk}
            ),
            reverse("opening_balance_edit", args=[balance.pk]),
        )
        self.grant("add_openingbalance", "view_openingbalance")
        self.assertRedirects(
            self.client.get(
                reverse("opening_balance_create"), {"member": self.member.pk}
            ),
            reverse("opening_balance_detail", args=[balance.pk]),
        )
        self.grant("add_openingbalance")
        response = self.client.post(
            reverse("opening_balance_create"),
            self.payload(**{f"amount_{self.deposit.pk}": "999"}),
        )
        self.assertContains(response, "already has opening balances")
        self.assertEqual(balance.total, Decimal("0.00"))

    def test_change_and_delete_only_users_return_to_allowed_pages(self):
        balance = self.create_balance()
        self.grant("change_openingbalance")
        url = reverse("opening_balance_edit", args=[balance.pk])
        self.assertRedirects(self.client.post(url, self.payload()), url)
        self.grant("delete_openingbalance")
        self.assertRedirects(
            self.client.post(reverse("opening_balance_delete", args=[balance.pk])),
            reverse("dashboard"),
        )

    def test_member_and_payment_plan_permissions_do_not_grant_balance_access(self):
        balance = self.create_balance()
        self.grant("view_member", "add_member", "view_paymentplan", "add_paymentplan")
        for name, args in (
            ("opening_balance_list", []),
            ("opening_balance_create", []),
            ("opening_balance_detail", [balance.pk]),
            ("opening_balance_edit", [balance.pk]),
            ("opening_balance_delete", [balance.pk]),
        ):
            for method in ("get", "post"):
                self.assertEqual(
                    getattr(self.client, method)(
                        reverse(name, args=args), self.payload()
                    ).status_code,
                    403,
                )

    def test_integration_displays_balances_only_with_view_permission(self):
        balance = self.create_balance(**{f"amount_{self.deposit.pk}": "8123.45"})
        plan = PaymentPlan.objects.create(member=self.member)
        urls = (
            reverse("member_detail", args=[self.member.pk]),
            reverse("payment_plan_detail", args=[plan.pk]),
            reverse("payment_plan_edit", args=[plan.pk]),
            f'{reverse("payment_plan_create")}?member={self.other.pk}',
        )
        for url in urls[:3]:
            response = self.client.get(url)
            self.assertContains(response, "Opening balances by payment type")
            self.assertContains(response, "8,123.45")
            self.assertContains(
                response, reverse("opening_balance_detail", args=[balance.pk])
            )
        self.grant(
            "view_member", "view_paymentplan", "change_paymentplan", "add_paymentplan"
        )
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "8,123.45")
            self.assertNotContains(response, "Opening balances by payment type")

    def test_selecting_member_for_new_payment_plan_shows_their_balances(self):
        self.create_balance(**{f"amount_{self.deposit.pk}": "8123.45"})
        response = self.client.get(
            reverse("payment_plan_create"), {"member": self.member.pk}
        )
        self.assertContains(response, "8,123.45")

    def test_member_renaming_updates_balances_and_details(self):
        balance = self.create_balance()
        Member.objects.filter(pk=self.member.pk).update(
            member_no="M-RENAMED", full_name="Renamed balance member"
        )
        for url in (
            reverse("opening_balance_list"),
            reverse("opening_balance_detail", args=[balance.pk]),
        ):
            response = self.client.get(url)
            self.assertContains(response, "M-RENAMED")
            self.assertContains(response, "Renamed balance member")

    def test_empty_payment_catalog_and_invalid_member_selection(self):
        PaymentType.objects.update(is_active=False)
        form = OpeningBalanceForm(self.payload(), member=self.member)
        self.assertFalse(form.is_valid())
        for data in ({}, {"member": "abc"}):
            self.assertEqual(
                self.client.post(reverse("opening_balance_create"), data).status_code,
                400,
            )

    def test_permissions_appear_in_user_and_role_editors(self):
        admin = get_user_model().objects.create_superuser(
            "balance-admin", password="test-only-pass"
        )
        for form in (WorkspaceRoleForm(), WorkspaceUserForm(actor=admin)):
            row = next(
                row
                for row in form.permission_rows()
                if row["label"] == "Opening Balances"
            )
            self.assertEqual(len(row["cells"]), 4)
        self.client.force_login(admin)
        self.assertEqual(
            self.client.get(
                reverse("admin:members_openingbalance_changelist")
            ).status_code,
            200,
        )
        self.assertContains(
            self.client.get(reverse("administration")), "Opening Balances"
        )

    def test_csrf_and_anonymous_access(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        balance = self.create_balance()
        for name, args in (
            ("opening_balance_create", []),
            ("opening_balance_edit", [balance.pk]),
            ("opening_balance_delete", [balance.pk]),
        ):
            self.assertEqual(
                csrf.post(reverse(name, args=args), self.payload()).status_code, 403
            )
        anonymous = Client()
        for name, args in (
            ("opening_balance_list", []),
            ("opening_balance_create", []),
            ("opening_balance_detail", [balance.pk]),
            ("opening_balance_edit", [balance.pk]),
            ("opening_balance_delete", [balance.pk]),
        ):
            self.assertEqual(anonymous.get(reverse(name, args=args)).status_code, 302)
