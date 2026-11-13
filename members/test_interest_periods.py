from datetime import date

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import PaymentType
from .payment_forms import PaymentTypeForm


class InterestPeriodTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            "period-admin", password="test-only-pass"
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def payload(self, **changes):
        return {
            "name": "Custom period deposit",
            "interest_rate": "7.25",
            "interest_frequency": "custom",
            "interest_period_type": "repeating",
            "interest_interval_count": "6",
            "interest_interval_unit": "months",
            "sort_order": "0",
            "is_active": "on",
            **changes,
        }

    def save_form(self, **changes):
        form = PaymentTypeForm(self.payload(**changes))
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_new_and_previously_unset_types_default_to_quarterly(self):
        payment_type = PaymentType.objects.create(name="Default period")
        self.assertEqual(payment_type.interest_frequency, "quarterly")
        self.assertEqual(
            payment_type.interest_period_display, "Quarterly (every 3 months)"
        )
        self.assertFalse(PaymentType.objects.filter(interest_frequency="").exists())
        response = self.client.get(reverse("payment_type_create"))
        self.assertContains(response, 'value="quarterly" selected')
        for label in (
            "Daily (every day)",
            "Monthly (every month)",
            "Quarterly (every 3 months)",
            "Yearly (every year)",
            "Custom period",
        ):
            self.assertContains(response, label)

    def test_custom_repeating_days_months_and_years(self):
        for amount, unit, display in (
            (45, "days", "45 days"),
            (6, "months", "6 months"),
            (2, "years", "2 years"),
            (1, "months", "1 month"),
        ):
            payment_type = self.save_form(
                name=f"Custom {amount} {unit}",
                interest_interval_count=str(amount),
                interest_interval_unit=unit,
            )
            payment_type.refresh_from_db()
            self.assertEqual(payment_type.interest_interval_count, amount)
            self.assertEqual(payment_type.interest_interval_unit, unit)
            self.assertEqual(
                payment_type.interest_period_display, f"Custom: every {display}"
            )

    def test_custom_specific_dates_save_including_a_one_day_period(self):
        for end in ("2026-07-09", "2026-01-10"):
            payment_type = self.save_form(
                name=f"Dates ending {end}",
                interest_period_type="date_range",
                interest_start_date="2026-01-10",
                interest_end_date=end,
            )
            self.assertEqual(payment_type.interest_start_date, date(2026, 1, 10))
            self.assertEqual(payment_type.interest_end_date.isoformat(), end)
            self.assertIsNone(payment_type.interest_interval_count)
            self.assertEqual(payment_type.interest_interval_unit, "")
            self.assertEqual(
                payment_type.interest_period_display, f"Custom: 2026-01-10 to {end}"
            )

    def test_invalid_custom_intervals_are_rejected_without_saving(self):
        cases = [
            ({"interest_period_type": ""}, "interest_period_type"),
            ({"interest_period_type": "invalid"}, "interest_period_type"),
            ({"interest_interval_count": ""}, "interest_interval_count"),
            ({"interest_interval_count": "0"}, "interest_interval_count"),
            ({"interest_interval_count": "-1"}, "interest_interval_count"),
            ({"interest_interval_count": "1.5"}, "interest_interval_count"),
            ({"interest_interval_count": "abc"}, "interest_interval_count"),
            ({"interest_interval_count": "32768"}, "interest_interval_count"),
            ({"interest_interval_unit": ""}, "interest_interval_unit"),
            ({"interest_interval_unit": "weeks"}, "interest_interval_unit"),
        ]
        for values, field in cases:
            with self.subTest(values=values):
                form = PaymentTypeForm(self.payload(**values))
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)
        self.assertFalse(
            PaymentType.objects.filter(name="Custom period deposit").exists()
        )

    def test_date_ranges_require_valid_dates_in_order(self):
        for changes, field in (
            ({"interest_start_date": ""}, "interest_start_date"),
            ({"interest_end_date": ""}, "interest_end_date"),
            ({"interest_start_date": "bad"}, "interest_start_date"),
            ({"interest_end_date": "bad"}, "interest_end_date"),
            ({"interest_end_date": "2026-01-09"}, "interest_end_date"),
        ):
            data = self.payload(
                interest_period_type="date_range",
                interest_start_date="2026-01-10",
                interest_end_date="2026-06-30",
            )
            data.update(changes)
            form = PaymentTypeForm(data)
            self.assertFalse(form.is_valid())
            self.assertIn(field, form.errors)

    def test_switching_between_period_types_clears_unused_settings(self):
        payment_type = self.save_form()
        data = self.payload(
            interest_period_type="date_range",
            interest_start_date="2026-01-10",
            interest_end_date="2026-06-30",
            interest_interval_count="bad",
            interest_interval_unit="invalid",
        )
        form = PaymentTypeForm(data, instance=payment_type)
        self.assertTrue(form.is_valid(), form.errors)
        payment_type = form.save()
        self.assertIsNone(payment_type.interest_interval_count)
        self.assertEqual(payment_type.interest_interval_unit, "")
        data = self.payload(
            interest_frequency="quarterly",
            interest_period_type="invalid",
            interest_interval_count="bad",
            interest_start_date="bad",
            interest_end_date="bad",
        )
        form = PaymentTypeForm(data, instance=payment_type)
        self.assertTrue(form.is_valid(), form.errors)
        payment_type = form.save()
        payment_type.refresh_from_db()
        self.assertEqual(payment_type.interest_frequency, "quarterly")
        self.assertEqual(payment_type.interest_period_type, "")
        self.assertIsNone(payment_type.interest_interval_count)
        self.assertEqual(payment_type.interest_interval_unit, "")
        self.assertIsNone(payment_type.interest_start_date)
        self.assertIsNone(payment_type.interest_end_date)

    def test_omitted_frequency_preserves_existing_bank_choice(self):
        payment_type = PaymentType.objects.create(
            name="Daily choice", interest_frequency="daily"
        )
        form = PaymentTypeForm(
            {"name": payment_type.name, "sort_order": "0"}, instance=payment_type
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().interest_frequency, "daily")
        form = PaymentTypeForm({"name": "Missing frequency", "sort_order": "0"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().interest_frequency, "quarterly")

    def test_custom_period_visible_in_master_and_transaction_forms(self):
        self.client.post(reverse("payment_type_create"), self.payload())
        payment_type = PaymentType.objects.get(name="Custom period deposit")
        self.assertContains(
            self.client.get(reverse("payment_type_list")), "Custom: every 6 months"
        )
        response = self.client.get(reverse("payment_transaction_create"))
        self.assertEqual(
            response.context["payment_type_frequencies"][str(payment_type.pk)],
            "Custom: every 6 months",
        )
        self.assertContains(
            self.client.get(reverse("payment_type_edit", args=[payment_type.pk])),
            'value="6"',
        )
        self.client.post(
            reverse("payment_type_edit", args=[payment_type.pk]),
            self.payload(
                interest_period_type="date_range",
                interest_start_date="2026-01-10",
                interest_end_date="2026-06-30",
            ),
        )
        self.assertContains(
            self.client.get(reverse("payment_type_list")),
            "Custom: 2026-01-10 to 2026-06-30",
        )

    def test_database_rejects_incomplete_or_mixed_period_settings(self):
        cases = (
            {"interest_frequency": ""},
            {"interest_frequency": "custom"},
            {
                "interest_frequency": "custom",
                "interest_period_type": "repeating",
                "interest_interval_unit": "days",
            },
            {
                "interest_frequency": "custom",
                "interest_period_type": "repeating",
                "interest_interval_count": 0,
                "interest_interval_unit": "days",
            },
            {
                "interest_frequency": "custom",
                "interest_period_type": "repeating",
                "interest_interval_count": 3,
                "interest_interval_unit": "weeks",
            },
            {
                "interest_frequency": "custom",
                "interest_period_type": "date_range",
                "interest_start_date": date(2026, 1, 10),
            },
            {
                "interest_frequency": "custom",
                "interest_period_type": "date_range",
                "interest_start_date": date(2026, 1, 10),
                "interest_end_date": date(2026, 1, 9),
            },
            {"interest_frequency": "quarterly", "interest_interval_count": 3},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(
                IntegrityError
            ), transaction.atomic():
                PaymentType.objects.create(name="Invalid database period", **values)

    def test_non_administrator_cannot_change_period_settings(self):
        user = get_user_model().objects.create_user(
            "period-operator", password="test-only-pass"
        )
        self.client.force_login(user)
        response = self.client.post(reverse("payment_type_create"), self.payload())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            PaymentType.objects.filter(name="Custom period deposit").exists()
        )
