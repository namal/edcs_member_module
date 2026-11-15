from decimal import Decimal

from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    PaymentPlan,
    PaymentPlanTemplate,
    PaymentPlanTemplateAmount,
    PaymentType,
)
from .payment_forms import PaymentPlanForm, PaymentPlanTemplateForm


class PaymentTemplateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_payment_plans import PaymentPlanTests

        PaymentPlanTests.setUpTestData.__func__(cls)
        cls.type = PaymentType.objects.get(name="Special Deposit")
        cls.other_type = PaymentType.objects.get(name="Share")
        cls.template = PaymentPlanTemplate.objects.create(name="Standard monthly plan")
        PaymentPlanTemplateAmount.objects.create(
            template=cls.template, payment_type=cls.type, amount="500"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def assign(self, member=None, **data):
        form = PaymentPlanForm(
            {
                "agreement_date": "2026-01-01",
                "apply_template": self.template.pk,
                **data,
            },
            member=member or self.member,
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_template_can_be_created_from_existing_types_and_amounts(self):
        response = self.client.post(
            reverse("payment_plan_template_create"),
            {
                "name": "Custom template",
                "is_active": "on",
                f"amount_{self.type.pk}": "200",
                f"amount_{self.other_type.pk}": "50",
            },
        )
        self.assertRedirects(response, reverse("payment_plan_template_list"))
        template = PaymentPlanTemplate.objects.get(name="Custom template")
        self.assertEqual(template.monthly_total, Decimal("250"))
        self.assertEqual(template.amounts.count(), 2)

    def test_assignment_copies_and_customizes_without_changing_template_or_other_member(
        self,
    ):
        first = self.assign()
        second = self.assign(self.other)
        edit = PaymentPlanForm(
            {
                "agreement_date": "2026-01-02",
                f"amount_{self.type.pk}": "750",
                f"amount_{self.other_type.pk}": "100",
            },
            member=self.member,
            instance=first,
            user=self.user,
        )
        self.assertTrue(edit.is_valid(), edit.errors)
        first = edit.save()
        self.assertEqual(first.monthly_total, Decimal("850"))
        self.assertEqual(second.monthly_total, Decimal("500"))
        self.assertEqual(self.template.monthly_total, Decimal("500"))
        self.assertEqual(first.source_template_name, self.template.name)

    def test_selected_template_allows_amount_overrides_and_zero_excludes_a_type(self):
        plan = self.assign(
            **{f"amount_{self.type.pk}": "0", f"amount_{self.other_type.pk}": "125"}
        )
        self.assertEqual(plan.monthly_total, Decimal("125"))
        self.assertEqual(self.template.monthly_total, Decimal("500"))

    def test_template_edits_and_deletion_do_not_change_assigned_amounts(self):
        plan = self.assign()
        form = PaymentPlanTemplateForm(
            {
                "name": "Updated template",
                "is_active": "on",
                f"amount_{self.type.pk}": "900",
            },
            instance=self.template,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        plan.refresh_from_db()
        self.assertEqual(plan.monthly_total, Decimal("500"))
        self.assertEqual(plan.source_template_name, "Standard monthly plan")
        self.template.delete()
        plan.refresh_from_db()
        self.assertIsNone(plan.source_template_id)
        self.assertEqual(plan.source_template_name, "Standard monthly plan")
        self.assertEqual(plan.monthly_total, Decimal("500"))

    def test_editing_existing_plan_does_not_reapply_changed_template(self):
        plan = self.assign()
        self.template.amounts.update(amount="999")
        form = PaymentPlanForm(instance=plan, member=self.member)
        self.assertEqual(form[f"amount_{self.type.pk}"].value(), Decimal("500"))
        self.assertFalse(form["apply_template"].value())

    def test_inactive_templates_and_forged_ids_are_rejected(self):
        self.template.is_active = False
        self.template.save()
        form = PaymentPlanForm(
            {"agreement_date": "2026-01-01", "apply_template": self.template.pk},
            member=self.member,
        )
        self.assertIn("apply_template", form.errors)
        response = self.client.get(
            reverse("payment_plan_template_options"), {"template": self.template.pk}
        )
        self.assertEqual(response.status_code, 404)

    def test_inactive_payment_types_are_not_copied_to_new_members(self):
        self.type.is_active = False
        self.type.save()
        PaymentPlanTemplateAmount.objects.create(
            template=self.template, payment_type=self.other_type, amount="25"
        )
        plan = self.assign()
        self.assertFalse(plan.amounts.filter(payment_type=self.type).exists())
        self.assertEqual(plan.monthly_total, Decimal("25"))

    def test_template_validates_name_amounts_and_requires_a_positive_contribution(self):
        for data in (
            {"name": "Blank"},
            {"name": "Negative", f"amount_{self.type.pk}": "-1"},
            {"name": "standard MONTHLY plan", f"amount_{self.type.pk}": "1"},
        ):
            self.assertFalse(PaymentPlanTemplateForm(data).is_valid())

    def test_template_actions_are_independently_protected_and_assignment_needs_plan_permission(
        self,
    ):
        url = reverse("payment_plan_template_options")
        self.assertEqual(Client().get(url).status_code, 302)
        self.user.user_permissions.set(
            Permission.objects.filter(codename="view_paymentplantemplate")
        )
        self.assertEqual(
            self.client.get(url, {"template": self.template.pk}).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("payment_plan_template_create"), {"name": "Forbidden"}
            ).status_code,
            403,
        )
        self.user.user_permissions.set(
            Permission.objects.filter(codename="add_paymentplan")
        )
        response = self.client.get(url, {"template": self.template.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["amounts"][0]["amount"], "500.00")
        self.assertEqual(
            self.client.get(reverse("payment_plan_template_list")).status_code, 403
        )

    def test_assignment_preserves_district_access_and_one_plan_per_member(self):
        self.user.assigned_districts.clear()
        response = self.client.post(
            reverse("payment_plan_create"),
            {
                "member": self.member.pk,
                "agreement_date": "2026-01-01",
                "apply_template": self.template.pk,
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PaymentPlan.objects.exists())

    def test_member_plan_create_route_accepts_template_and_records_source(self):
        response = self.client.post(
            reverse("payment_plan_create"),
            {
                "member": self.member.pk,
                "agreement_date": "2026-01-01",
                "apply_template": self.template.pk,
            },
        )
        plan = PaymentPlan.objects.get(member=self.member)
        self.assertRedirects(response, reverse("payment_plan_detail", args=[plan.pk]))
        self.assertEqual(plan.monthly_total, Decimal("500"))
        self.assertEqual(plan.source_template_id, self.template.pk)
        self.assertContains(
            self.client.get(reverse("payment_plan_edit", args=[plan.pk])),
            self.template.name,
        )

    def test_assign_from_template_list_preserves_template_through_member_selection(
        self,
    ):
        self.assertContains(
            self.client.get(reverse("payment_plan_template_list")),
            f"?template={self.template.pk}",
        )
        response = self.client.get(
            reverse("payment_plan_create"),
            {"template": self.template.pk, "q": self.member.member_no},
        )
        self.assertContains(
            response, f"member={self.member.pk}&amp;template={self.template.pk}"
        )
        response = self.client.get(
            reverse("payment_plan_create"),
            {"member": self.member.pk, "template": self.template.pk},
        )
        self.assertEqual(
            str(response.context["form"]["apply_template"].value()),
            str(self.template.pk),
        )
        self.assertEqual(
            response.context["form"][f"amount_{self.type.pk}"].value(), Decimal("500")
        )

    def test_template_with_only_inactive_types_cannot_be_applied_as_an_empty_plan(self):
        self.type.is_active = False
        self.type.save()
        form = PaymentPlanForm(
            {"agreement_date": "2026-01-01", "apply_template": self.template.pk},
            member=self.member,
        )
        self.assertIn("apply_template", form.errors)
