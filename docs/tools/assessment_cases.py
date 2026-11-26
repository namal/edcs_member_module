"""Bounded local verification probes for the EDCS vulnerability assessment.

Run only through run_vulnerability_assessment.py, which creates an isolated
PostgreSQL test database. A passing exposure probe confirms the documented
behavior; it does not mean the application has remediated that behavior.
"""

import json
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse

EVIDENCE = Path(__file__).resolve().parents[1] / "assessment_evidence/probes.jsonl"


def record(identifier, **evidence):
    with EVIDENCE.open("a") as handle:
        handle.write(json.dumps({"probe": identifier, **evidence}) + "\n")


class AssessmentAuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "va-synthetic-user", password="VaSynthetic-Only-7824!"
        )
        cls.user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="members",
                codename__in=["view_member", "add_paymenttransaction"],
            )
        )

    def test_bounded_login_attempts_have_no_application_throttle(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("login"))
        token = client.cookies["csrftoken"].value
        statuses = []
        for _ in range(8):
            response = client.post(
                reverse("login"),
                {
                    "username": self.user.username,
                    "password": "Intentionally-Wrong-Assessment-Password",
                    "csrfmiddlewaretoken": token,
                },
            )
            statuses.append(response.status_code)
        response = client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "VaSynthetic-Only-7824!",
                "csrfmiddlewaretoken": token,
            },
        )
        self.assertEqual(statuses, [200] * 8)
        self.assertEqual(response.status_code, 302)
        record(
            "P-01",
            wrong_attempt_count=8,
            wrong_statuses=statuses,
            valid_attempt_status=response.status_code,
            throttled_response_seen=False,
        )

    def test_unicode_digit_filter_causes_500_instead_of_validation_error(self):
        client = Client(raise_request_exception=False)
        client.force_login(self.user)
        rows = []
        for debug in (False, True):
            for name, parameter in [
                ("office_options", "district"),
                ("location_options", "district"),
                ("payment_transaction_balances", "member"),
            ]:
                with (
                    override_settings(DEBUG=debug),
                    patch("django.core.handlers.exception.log_response"),
                ):
                    response = client.get(reverse(name), {parameter: "²"})
                self.assertEqual(response.status_code, 500)
                exception = response.exc_info[0].__name__
                technical_page = b"Traceback" in response.content
                rows.append(
                    {
                        "route": reverse(name),
                        "parameter": parameter,
                        "input": "U+00B2",
                        "debug": debug,
                        "status": response.status_code,
                        "exception": exception,
                        "technical_traceback": technical_page,
                    }
                )
                self.assertEqual(exception, "ValueError")
                if debug:
                    self.assertTrue(technical_page)
        record("P-02", results=rows)

    def test_debug_false_still_does_not_enforce_https_or_csp(self):
        with override_settings(DEBUG=False):
            response = self.client.get(reverse("login"), secure=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Strict-Transport-Security", response.headers)
        self.assertNotIn("Content-Security-Policy", response.headers)
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        record(
            "P-03",
            http_login_status=response.status_code,
            hsts_present=False,
            csp_present=False,
            frame_options=response.headers.get("X-Frame-Options"),
            nosniff=response.headers.get("X-Content-Type-Options"),
        )

    def test_anonymous_and_csrf_requests_are_rejected(self):
        anonymous = Client(enforce_csrf_checks=True)
        statuses = {
            "members": anonymous.get(reverse("member_list")).status_code,
            "admin": anonymous.get(reverse("administration")).status_code,
            "login_without_csrf": anonymous.post(
                reverse("login"), {"username": "va-only", "password": "invalid"}
            ).status_code,
        }
        self.assertEqual(
            statuses, {"members": 302, "admin": 302, "login_without_csrf": 403}
        )
        record("P-04", statuses=statuses)

    def test_external_login_next_is_not_followed(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "VaSynthetic-Only-7824!",
                "next": "https://outside.invalid/assessment-only",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")
        record("P-05", status=response.status_code, redirect=response.url)


class AssessmentJournalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from members.test_contributions import setup_records

        setup_records(cls)

    def test_repeated_plain_journal_post_creates_two_credits(self):
        from members.models import PaymentTransaction

        self.client.force_login(self.operator)
        payload = {
            "member": self.member.pk,
            "payment_type": self.types["Share"].pk,
            "transaction_date": self.today.isoformat(),
            "debit": "0",
            "credit": "500.00",
            "reference": "VA-SYNTHETIC-RETRY",
            "description": "Synthetic local retry assessment",
        }
        statuses = [
            self.client.post(reverse("payment_transaction_create"), payload).status_code
            for _ in range(2)
        ]
        entries = PaymentTransaction.objects.filter(reference="VA-SYNTHETIC-RETRY")
        self.assertEqual(statuses, [302, 302])
        self.assertEqual(entries.count(), 2)
        record(
            "P-06",
            statuses=statuses,
            identical_submissions=2,
            saved_entries=entries.count(),
            resulting_credit_total=str(sum(entry.credit for entry in entries)),
        )
