from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse

from .models import PaymentType
from .payment_forms import PaymentPlanForm, PaymentPlanTemplateForm, PaymentTypeForm


class FixedPaymentAmountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_payment_plans import PaymentPlanTests

        PaymentPlanTests.setUpTestData.__func__(cls)
        cls.fixed_type = PaymentType.objects.get(name="Scholarship")
        cls.variable_type = PaymentType.objects.get(name="Special Deposit")

    def plan_form(self, *, member=None, instance=None, **data):
        return PaymentPlanForm(
            {"agreement_date": "2026-01-01", **data},
            member=member or self.member,
            instance=instance,
            user=self.user,
        )

    def save_plan(self, **kwargs):
        form = self.plan_form(**kwargs)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def template(self, name="Fixed scholarship plan", amount="400"):
        form = PaymentPlanTemplateForm(
            {
                "name": name,
                "is_active": "on",
                f"amount_{self.fixed_type.pk}": amount,
                f"fixed_{self.fixed_type.pk}": "on",
                f"amount_{self.variable_type.pk}": "100",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_fixed_checkbox_only_exists_in_template_amount_rows(self):
        self.assertNotIn("is_fixed", PaymentTypeForm().fields)
        self.assertFalse(hasattr(PaymentType, "is_fixed"))
        self.assertIn(f"fixed_{self.fixed_type.pk}", PaymentPlanTemplateForm().fields)
        self.assertFalse(
            any(name.startswith("fixed_") for name in self.plan_form().fields)
        )

    def test_template_fixed_and_variable_rows_copy_independently(self):
        template = self.template()
        plan = self.save_plan(apply_template=template.pk)
        self.assertTrue(plan.amounts.get(payment_type=self.fixed_type).is_fixed)
        self.assertFalse(plan.amounts.get(payment_type=self.variable_type).is_fixed)
        plan = self.save_plan(
            instance=plan, **{f"amount_{self.variable_type.pk}": "250"}
        )
        self.assertEqual(plan.monthly_total, Decimal("650"))
        self.assertEqual(template.monthly_total, Decimal("500"))

    def test_different_templates_can_fix_different_amounts_for_same_type(self):
        first = self.template()
        second = self.template("Different scholarship amount", "600")
        one = self.save_plan(apply_template=first.pk)
        two = self.save_plan(member=self.other, apply_template=second.pk)
        self.assertEqual(
            one.amounts.get(payment_type=self.fixed_type).amount, Decimal("400")
        )
        self.assertEqual(
            two.amounts.get(payment_type=self.fixed_type).amount, Decimal("600")
        )

    def test_fixed_amount_rejects_edits_removal_and_unchecking_in_both_forms(self):
        template = self.template()
        plan = self.save_plan(apply_template=template.pk)
        for amount in ["401", "0", ""]:
            data = {
                f"amount_{self.fixed_type.pk}": amount,
                f"fixed_{self.fixed_type.pk}": "",
            }
            forms = [
                self.plan_form(instance=plan, **data),
                PaymentPlanTemplateForm(
                    {"name": template.name, **data}, instance=template
                ),
            ]
            for form in forms:
                self.assertFalse(form.is_valid())
                self.assertIn(f"amount_{self.fixed_type.pk}", form.errors)

    def test_new_template_assignment_cannot_override_existing_member_lock(self):
        first = self.template()
        second = self.template("New template", "600")
        plan = self.save_plan(apply_template=first.pk)
        plan = self.save_plan(instance=plan, apply_template=second.pk)
        self.assertEqual(
            plan.amounts.get(payment_type=self.fixed_type).amount, Decimal("400")
        )

        first.delete()
        self.assertFalse(
            self.plan_form(
                instance=plan, **{f"amount_{self.fixed_type.pk}": "600"}
            ).is_valid()
        )

    def test_custom_member_plan_cannot_add_a_fixed_checkbox_by_forging_input(self):
        plan = self.save_plan(
            **{
                f"amount_{self.fixed_type.pk}": "300",
                f"fixed_{self.fixed_type.pk}": "on",
            }
        )
        self.assertFalse(plan.amounts.get(payment_type=self.fixed_type).is_fixed)
        plan = self.save_plan(instance=plan, **{f"amount_{self.fixed_type.pk}": "500"})
        self.assertEqual(plan.monthly_total, Decimal("500"))

    def test_blank_template_amount_cannot_be_marked_fixed(self):
        form = PaymentPlanTemplateForm(
            {"name": "Empty fixed", f"fixed_{self.fixed_type.pk}": "on"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn(f"amount_{self.fixed_type.pk}", form.errors)

    def test_models_and_advanced_admin_cannot_change_or_remove_fixed_rows(self):
        template = self.template()
        plan = self.save_plan(apply_template=template.pk)
        for parent in [template, plan]:
            row = parent.amounts.get(payment_type=self.fixed_type)
            row.amount = Decimal("900")
            with self.assertRaises(ValidationError):
                row.full_clean()
            with self.assertRaises(ValidationError):
                row.save()
            row.refresh_from_db()
            row.is_fixed = False
            with self.assertRaises(ValidationError):
                row.full_clean()
            registered = admin.site._registry[type(parent)]
            self.assertFalse(
                registered.inlines[0](type(parent), admin.site).has_delete_permission(
                    None, parent
                )
            )

    def test_web_form_and_options_show_locks_without_member_checkboxes(self):
        self.client.force_login(self.user)
        template = self.template()
        response = self.client.get(
            reverse("payment_plan_template_options"), {"template": template.pk}
        )
        fixed = next(
            row
            for row in response.json()["amounts"]
            if row["payment_type_id"] == self.fixed_type.pk
        )
        self.assertTrue(fixed["is_fixed"])
        plan = self.save_plan(apply_template=template.pk)
        response = self.client.get(reverse("payment_plan_edit", args=[plan.pk]))
        self.assertContains(response, "Fixed amount")
        self.assertNotContains(response, f'name="fixed_{self.fixed_type.pk}"')
        self.assertTrue(
            response.context["form"]
            .fields[f"amount_{self.fixed_type.pk}"]
            .widget.attrs["readonly"]
        )
        response = self.client.post(
            reverse("payment_plan_edit", args=[plan.pk]),
            {"agreement_date": "2026-01-01", f"amount_{self.fixed_type.pk}": "999"},
        )
        self.assertContains(response, "This amount is fixed")
        self.assertEqual(
            plan.amounts.get(payment_type=self.fixed_type).amount, Decimal("400")
        )


class TemplateFixedMigrationTests(TransactionTestCase):
    def restore_latest(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes("members"))

    def test_removing_type_setting_preserves_existing_amounts_and_fixed_locks(self):
        from .test_contributions import setup_records
        from .models import PaymentPlanAmount, PaymentPlanTemplateAmount

        setup_records(self)
        self.addCleanup(self.restore_latest)
        before = [("members", "0026_fixed_payment_plan_amounts")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        old = executor.loader.project_state(before).apps
        old.get_model("members", "PaymentType").objects.filter(
            pk=self.types["Pension"].pk
        ).update(is_fixed=True)
        template = old.get_model("members", "PaymentPlanTemplate").objects.create(
            name="Preserve fixed amounts"
        )
        row = old.get_model("members", "PaymentPlanTemplateAmount").objects.create(
            template=template,
            payment_type_id=self.types["Pension"].pk,
            amount="400",
            is_fixed=False,
        )
        executor = MigrationExecutor(connection)
        executor.migrate([("members", "0027_template_only_fixed_amounts")])
        saved = PaymentPlanTemplateAmount.objects.get(pk=row.pk)
        self.assertTrue(saved.is_fixed)
        self.assertEqual(saved.amount, Decimal("400"))
        saved = PaymentPlanAmount.objects.get(
            plan=self.plan, payment_type=self.types["Pension"]
        )
        self.assertTrue(saved.is_fixed)
        self.assertEqual(saved.amount, Decimal("400"))
        self.assertFalse(
            PaymentPlanAmount.objects.get(
                plan=self.plan, payment_type=self.types["Special Deposit"]
            ).is_fixed
        )
