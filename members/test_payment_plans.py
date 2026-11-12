from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    District,
    Lager,
    Member,
    OfficeLocation,
    PaymentPlan,
    PaymentPlanAmount,
    PaymentType,
    School,
)
from .payment_forms import PaymentPlanForm


class PaymentPlanTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "plan-operator", password="test-only-pass"
        )
        cls.user.user_permissions.set(
            Permission.objects.filter(content_type__app_label="members")
        )
        district = District.objects.get(code="COL")
        cls.user.assigned_districts.add(district)
        office = OfficeLocation.objects.create(
            district=district, name="Plan Zone", code="PLAN-Z"
        )
        department = School.objects.create(
            district=district,
            zonal_office=office,
            name="Plan Department",
            code="PLAN-D",
        )
        lager = Lager.objects.create(
            district=district, name="Plan Lager", code="PLAN-L"
        )
        cls.member = Member.objects.create(
            district=district,
            office_location=office,
            school=department,
            lager=lager,
            member_no="M-PLAN-1",
            full_name="Monthly Member",
            salary="1000.00",
            office_address="Office Road",
            private_address="Home Road",
            phone_mobile="0771234567",
            gender="female",
            nic_no="199012345678",
        )
        cls.other = Member.objects.create(
            district=district,
            office_location=office,
            school=department,
            lager=lager,
            member_no="M-PLAN-2",
            full_name="Another Member",
            salary="1000.00",
            office_address="Office Road",
            private_address="Home Road",
            phone_mobile="0771234567",
            gender="female",
            nic_no="199012345679",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def payload(self, **overrides):
        data = {
            "member": self.member.pk,
            "agreement_date": timezone.localdate().isoformat(),
        }
        data.update(
            {
                f"amount_{item.pk}": "0.00"
                for item in PaymentType.objects.filter(is_active=True)
            }
        )
        data.update(overrides)
        return data

    def create_plan(self, **overrides):
        form = PaymentPlanForm(self.payload(**overrides), member=self.member)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def grant(self, *codenames):
        self.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members", codename__in=codenames
            )
        )

    def test_default_types_include_both_scholar_fields_and_new_contributions(self):
        self.assertEqual(
            list(PaymentType.objects.values_list("name", flat=True)),
            [
                "Special Deposit",
                "Fix Deposit",
                "Share",
                "Pension",
                "Scholarship",
                "Scholar",
                "Funeral",
                "Danaayojana",
            ],
        )

    def test_member_master_shows_all_registered_members_and_searches_identity(self):
        for query in ("", "PLAN-1", "Monthly"):
            response = self.client.get(reverse("payment_plan_list"), {"q": query})
            self.assertContains(response, self.member.member_no)
            self.assertContains(response, self.member.full_name)
            if query:
                self.assertNotContains(response, self.other.full_name)
        self.assertFalse(PaymentPlan.objects.exists())

    def test_registration_continues_with_saved_member_identity(self):
        payload = {
            "district": self.member.district_id,
            "office_location": self.member.office_location_id,
            "school": self.member.school_id,
            "lager": self.member.lager_id,
            "member_no": "M-NEXT",
            "full_name": "New Plan Member",
            "salary": "1000",
            "office_address": "Office",
            "private_address": "Home",
            "phone_mobile": "0771234567",
            "admit_date": timezone.localdate().isoformat(),
            "gender": "female",
            "nic_no": "199012345680",
            "status": "active",
            "next_step": "payment_plan",
        }
        response = self.client.post(reverse("member_create"), payload)
        member = Member.objects.get(member_no="M-NEXT")
        self.assertRedirects(
            response, f'{reverse("payment_plan_create")}?member={member.pk}'
        )
        response = self.client.get(response.url)
        self.assertContains(response, "M-NEXT")
        self.assertContains(response, "New Plan Member")
        self.assertFalse(PaymentPlan.objects.filter(member=member).exists())

    def test_plan_form_carries_identity_and_dynamic_fields(self):
        response = self.client.get(
            reverse("payment_plan_create"), {"member": self.member.pk}
        )
        self.assertEqual(response.context["member"], self.member)
        self.assertNotIn("member_no", response.context["form"].fields)
        for payment_type in PaymentType.objects.all():
            self.assertContains(response, payment_type.name)
            self.assertIn(f"amount_{payment_type.pk}", response.context["form"].fields)

    def test_new_type_becomes_field_and_saves_without_schema_changes(self):
        administrator = get_user_model().objects.create_superuser(
            "type-admin", password="test-only-pass"
        )
        self.client.force_login(administrator)
        response = self.client.post(
            reverse("payment_type_create"),
            {"name": "Welfare", "sort_order": 9, "is_active": "on"},
        )
        self.assertRedirects(response, reverse("payment_type_list"))
        self.client.force_login(self.user)
        payment_type = PaymentType.objects.get(name="Welfare")
        field = f"amount_{payment_type.pk}"
        response = self.client.get(
            reverse("payment_plan_create"), {"member": self.member.pk}
        )
        self.assertIn(field, response.context["form"].fields)
        response = self.client.post(
            reverse("payment_plan_create"), self.payload(**{field: "1250.75"})
        )
        plan = PaymentPlan.objects.get(member=self.member)
        self.assertRedirects(response, reverse("payment_plan_detail", args=[plan.pk]))
        self.assertEqual(
            plan.amounts.get(payment_type=payment_type).amount, Decimal("1250.75")
        )
        self.assertEqual(plan.monthly_total, Decimal("1250.75"))

    def test_exact_decimal_total_and_blank_amounts_default_to_zero(self):
        special = PaymentType.objects.get(name="Special Deposit")
        fix = PaymentType.objects.get(name="Fix Deposit")
        plan = self.create_plan(
            **{f"amount_{special.pk}": "0.10", f"amount_{fix.pk}": "0.20"}
        )
        self.assertEqual(plan.monthly_total, Decimal("0.30"))
        self.assertEqual(plan.amounts.count(), 8)
        form = PaymentPlanForm(
            {"agreement_date": timezone.localdate().isoformat()},
            member=self.member,
            instance=plan,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().monthly_total, Decimal("0.00"))

    def test_invalid_amounts_do_not_save_partial_agreements(self):
        payment_type = PaymentType.objects.first()
        for value in ("-1", "1.001", "10000000000.00", "NaN", "abc"):
            field = f"amount_{payment_type.pk}"
            with self.subTest(value=value):
                response = self.client.post(
                    reverse("payment_plan_create"), self.payload(**{field: value})
                )
                self.assertIn(field, response.context["form"].errors)
                self.assertFalse(PaymentPlan.objects.exists())
                self.assertFalse(PaymentPlanAmount.objects.exists())

    def test_database_rejects_negative_amounts_and_duplicate_member_plans(self):
        plan = self.create_plan()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentPlan.objects.create(member=self.member)
        with self.assertRaises(IntegrityError), transaction.atomic():
            plan.amounts.update(amount=Decimal("-1.00"))

    def test_duplicate_submission_cannot_overwrite_plan(self):
        payment_type = PaymentType.objects.first()
        field = f"amount_{payment_type.pk}"
        plan = self.create_plan(**{field: "700"})
        response = self.client.post(
            reverse("payment_plan_create"), self.payload(**{field: "900"})
        )
        self.assertContains(response, "already has a payment plan")
        self.assertEqual(PaymentPlan.objects.count(), 1)
        self.assertEqual(
            plan.amounts.get(payment_type=payment_type).amount, Decimal("700.00")
        )

    def test_edit_updates_amounts_and_cannot_reassign_member(self):
        plan = self.create_plan()
        payment_type = PaymentType.objects.first()
        response = self.client.post(
            reverse("payment_plan_edit", args=[plan.pk]),
            self.payload(
                member=self.other.pk, **{f"amount_{payment_type.pk}": "840.30"}
            ),
        )
        self.assertRedirects(response, reverse("payment_plan_detail", args=[plan.pk]))
        plan.refresh_from_db()
        self.assertEqual(plan.member, self.member)
        self.assertEqual(plan.monthly_total, Decimal("840.30"))

    def test_member_name_and_number_stay_current_in_linked_plan(self):
        plan = self.create_plan()
        self.member.full_name = "Renamed Member"
        self.member.member_no = "M-UPDATED"
        self.member.save()
        for url in (
            reverse("payment_plan_list"),
            reverse("payment_plan_detail", args=[plan.pk]),
        ):
            response = self.client.get(url)
            self.assertContains(response, "Renamed Member")
            self.assertContains(response, "M-UPDATED")

    def test_inactive_type_preserves_existing_amount_but_is_hidden_on_new_plan(self):
        payment_type = PaymentType.objects.first()
        field = f"amount_{payment_type.pk}"
        plan = self.create_plan(**{field: "300.50"})
        payment_type.is_active = False
        payment_type.save()
        fresh = PaymentPlanForm(member=self.other)
        self.assertNotIn(field, fresh.fields)
        edit = PaymentPlanForm(member=self.member, instance=plan)
        self.assertEqual(edit[field].value(), Decimal("300.50"))
        self.assertIn("inactive", edit.fields[field].label)
        data = self.payload(**{field: "300.50"})
        edit = PaymentPlanForm(data, member=self.member, instance=plan)
        self.assertTrue(edit.is_valid(), edit.errors)
        self.assertEqual(
            edit.save().amounts.get(payment_type=payment_type).amount, Decimal("300.50")
        )
        with self.assertRaises(ProtectedError):
            payment_type.delete()

    def test_existing_plan_gets_new_active_fields_when_edited(self):
        plan = self.create_plan()
        payment_type = PaymentType.objects.create(name="New Benefit")
        field = f"amount_{payment_type.pk}"
        form = PaymentPlanForm(
            self.payload(**{field: "500.00"}), instance=plan, member=self.member
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.save().amounts.get(payment_type=payment_type).amount, Decimal("500.00")
        )

    def test_failed_amount_save_rolls_back_the_entire_plan(self):
        form = PaymentPlanForm(self.payload(), member=self.member)
        self.assertTrue(form.is_valid(), form.errors)
        with patch(
            "members.payment_forms.PaymentPlanAmount.objects.update_or_create",
            side_effect=RuntimeError("failure"),
        ):
            with self.assertRaises(RuntimeError):
                form.save()
        self.assertFalse(PaymentPlan.objects.exists())

    def test_delete_requires_post_and_preserves_member(self):
        plan = self.create_plan()
        url = reverse("payment_plan_delete", args=[plan.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(PaymentPlan.objects.filter(pk=plan.pk).exists())
        self.assertRedirects(self.client.post(url), reverse("payment_plan_list"))
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())
        self.assertFalse(PaymentPlanAmount.objects.exists())

    def test_member_with_plan_is_protected_from_deletion(self):
        self.create_plan()
        with self.assertRaises(ProtectedError):
            self.member.delete()

    def test_filters_and_selection_exclude_members_with_saved_plans(self):
        self.create_plan()
        response = self.client.get(reverse("payment_plan_list"), {"status": "pending"})
        self.assertNotContains(response, self.member.full_name)
        self.assertContains(response, self.other.full_name)
        response = self.client.get(reverse("payment_plan_list"), {"status": "ready"})
        self.assertContains(response, self.member.full_name)
        self.assertNotContains(response, self.other.full_name)
        response = self.client.get(reverse("payment_plan_create"))
        self.assertNotContains(response, self.member.full_name)

    def test_add_only_user_can_select_identity_without_member_or_view_access(self):
        self.grant("add_paymentplan")
        response = self.client.get(reverse("payment_plan_create"))
        self.assertContains(response, self.member.member_no)
        self.assertNotContains(response, self.member.private_address)
        response = self.client.post(reverse("payment_plan_create"), self.payload())
        self.assertRedirects(response, reverse("payment_plan_create"))
        self.assertEqual(self.client.get(reverse("payment_plan_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("member_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("payment_type_list")).status_code, 403)

    def test_member_permissions_alone_do_not_grant_plan_access(self):
        self.grant("view_member", "add_member")
        for name in (
            "payment_plan_list",
            "payment_plan_create",
            "payment_type_list",
            "payment_type_create",
        ):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
            self.assertEqual(
                self.client.post(reverse(name), self.payload()).status_code, 403
            )

    def test_csrf_and_anonymous_access(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(
            csrf.post(reverse("payment_plan_create"), self.payload()).status_code, 403
        )
        anonymous = Client()
        for name in ("payment_plan_list", "payment_plan_create", "payment_type_list"):
            self.assertEqual(anonymous.get(reverse(name)).status_code, 302)
