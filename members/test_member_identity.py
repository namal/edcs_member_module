import csv
import io
from datetime import date, timedelta

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import MemberForm
from .member_identity import retirement_date, short_name
from .models import DepositInterestYear, Member, PaymentType


class MemberIdentityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .tests import MemberWorkflowTests

        MemberWorkflowTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client.force_login(self.user)

    def payload(self, **overrides):
        from .tests import MemberWorkflowTests

        return MemberWorkflowTests.payload(self, **overrides)

    def member(self, **overrides):
        form = MemberForm(self.payload(**overrides))
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_examples_and_titles_generate_short_names(self):
        for full, expected in (
            ("Krishantha Namal Spencer", "K. N. Spencer"),
            ("Nimal Perera", "N. Perera"),
            ("Amila Kumara Silva", "A. K. Silva"),
            ("Sunil", "Sunil"),
            ("  Nimal   Perera  ", "N. Perera"),
        ):
            self.assertEqual(short_name(full), expected)
            for title in Member.Title.values:
                self.assertEqual(short_name(full, title), f"{title} {expected}")

    def test_short_name_updates_on_name_and_title_changes_including_partial_save(self):
        member = self.member(full_name="Krishantha Namal Spencer", title="Mr.")
        self.assertEqual(member.short_name, "Mr. K. N. Spencer")
        member.full_name = "Nimal Perera"
        member.save(update_fields=["full_name"])
        member.refresh_from_db()
        self.assertEqual(member.short_name, "Mr. N. Perera")
        member.title = "Rev."
        member.save(update_fields=["title"])
        member.refresh_from_db()
        self.assertEqual(member.short_name, "Rev. N. Perera")

    def test_retirement_age_is_60_and_birth_changes_recalculate_expected_date(self):
        member = self.member(date_of_birth="1990-05-14")
        self.assertEqual(member.retirement_age, 60)
        self.assertEqual(member.expected_retirement_date, date(2050, 5, 14))
        self.assertIsNone(member.pension_date)
        member.date_of_birth = date(1980, 6, 20)
        member.save(update_fields=["date_of_birth"])
        member.refresh_from_db()
        self.assertEqual(member.expected_retirement_date, date(2040, 6, 20))
        member.date_of_birth = None
        member.save(update_fields=["date_of_birth"])
        member.refresh_from_db()
        self.assertIsNone(member.expected_retirement_date)

    def test_leap_day_retirement_dates_are_valid(self):
        self.assertEqual(retirement_date(date(2000, 2, 29)), date(2060, 2, 29))
        self.assertEqual(retirement_date(date(2040, 2, 29)), date(2100, 2, 28))

    def test_form_ignores_forged_generated_fields_and_rejects_future_birth_or_invalid_title(
        self,
    ):
        form = MemberForm(
            self.payload(
                title="Mr.",
                full_name="Nimal Perera",
                date_of_birth="1990-01-01",
                retirement_age="25",
                expected_retirement_date="2000-01-01",
                short_name="Forged",
            )
        )
        self.assertTrue(form.is_valid(), form.errors)
        member = form.save()
        self.assertEqual(member.short_name, "Mr. N. Perera")
        self.assertEqual(member.retirement_age, 60)
        self.assertEqual(member.expected_retirement_date, date(2050, 1, 1))
        self.assertIn(
            "date_of_birth",
            MemberForm(
                self.payload(
                    date_of_birth=(timezone.localdate() + timedelta(days=1)).isoformat()
                )
            ).errors,
        )
        self.assertIn("title", MemberForm(self.payload(title="Invalid")).errors)

    def test_invalid_form_keeps_generated_previews_without_javascript(self):
        form = MemberForm(
            self.payload(
                title="Ms.",
                full_name="Amila Kumara Silva",
                date_of_birth="1980-02-29",
                nic_no="bad",
            )
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(form["short_name"].value(), "Ms. A. K. Silva")
        self.assertEqual(form["expected_retirement_date"].value(), date(2040, 2, 29))

    def test_early_retirement_records_actual_date_retains_expected_date_and_history(
        self,
    ):
        member = self.member(date_of_birth="1990-01-01")
        response = self.client.post(
            reverse("member_retire", args=[member.pk]),
            {"retirement_date": "2025-05-15"},
        )
        self.assertRedirects(response, reverse("member_detail", args=[member.pk]))
        member.refresh_from_db()
        self.assertEqual(member.pension_date, date(2025, 5, 15))
        self.assertEqual(member.status, Member.Status.RETIRED)
        self.assertEqual(member.expected_retirement_date, date(2050, 1, 1))
        self.assertIsNotNone(member.lager_id)

    def test_retirement_cannot_rewrite_a_posted_interest_year(self):
        member = self.member(date_of_birth="1990-01-01")
        DepositInterestYear.objects.create(
            member=member,
            payment_type=PaymentType.objects.first(),
            year_start=date(2025, 1, 1),
            year_end=date(2025, 12, 31),
            annual_rate="8",
            calculated_through=date(2025, 12, 31),
            posted_at=timezone.now(),
        )
        response = self.client.post(
            reverse("member_retire", args=[member.pk]),
            {"retirement_date": "2025-05-15"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "posted interest year")
        member.refresh_from_db()
        self.assertEqual(member.status, "active")
        self.assertIsNone(member.pension_date)

    def test_retirement_access_and_date_validation(self):
        member = self.member(date_of_birth="1990-01-01")
        url = reverse("member_retire", args=[member.pk])
        self.assertEqual(Client().get(url).status_code, 302)
        for value in (
            "1980-01-01",
            (timezone.localdate() + timedelta(days=1)).isoformat(),
            "invalid",
        ):
            response = self.client.post(url, {"retirement_date": value})
            self.assertTrue(response.context["form"].errors)
        self.user.is_superuser = False
        self.user.save()
        self.user.user_permissions.set(
            Permission.objects.filter(codename="view_member")
        )
        self.assertEqual(
            self.client.post(url, {"retirement_date": "2025-05-15"}).status_code, 403
        )
        self.user.user_permissions.set(
            Permission.objects.filter(codename="change_member")
        )
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_retired_status_requires_date_and_birth_is_checked_on_direct_save(self):
        member = self.member()
        member.status = Member.Status.RETIRED
        with self.assertRaises(ValidationError):
            member.save()
        member.status = Member.Status.ACTIVE
        member.date_of_birth = timezone.localdate() + timedelta(days=1)
        with self.assertRaises(ValidationError):
            member.save()

    def test_profile_search_and_csv_include_generated_identity_and_retirement(self):
        member = self.member(
            title="Mrs.", date_of_birth="1990-01-01", full_name="Nimal Perera"
        )
        profile = self.client.get(reverse("member_detail", args=[member.pk]))
        self.assertContains(profile, "Mrs. N. Perera")
        self.assertContains(profile, "2050")
        self.assertContains(
            self.client.get(reverse("member_list"), {"q": "N. Perera"}),
            member.member_no,
        )
        rows = list(
            csv.DictReader(
                io.StringIO(
                    self.client.get(reverse("member_export")).content.decode(
                        "utf-8-sig"
                    )
                )
            )
        )
        self.assertEqual(rows[0]["Short Name"], "Mrs. N. Perera")
        self.assertEqual(rows[0]["Expected Retirement Date"], "2050-01-01")
