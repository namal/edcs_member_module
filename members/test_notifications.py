from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .notifications import notify_user_created


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="EDCS <accounts@example.com>",
)
class UserWelcomeEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            "welcome-admin", password="test-only-pass"
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def payload(self, **overrides):
        data = {
            "username": "new-operator",
            "first_name": "New",
            "last_name": "Operator",
            "email": "operator@example.com",
            "is_active": "on",
            "password1": "EDCS-new-operator-strong-83",
            "password2": "EDCS-new-operator-strong-83",
        }
        data.update(overrides)
        return data

    def test_new_account_emails_only_after_commit_without_exposing_password(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(reverse("access_user_create"), self.payload())
            self.assertTrue(
                get_user_model().objects.filter(username="new-operator").exists()
            )
            self.assertEqual(len(mail.outbox), 0)
        self.assertRedirects(response, reverse("access_users"))
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["operator@example.com"])
        self.assertEqual(message.from_email, "EDCS <accounts@example.com>")
        for value in ("New Operator", "new-operator", "http://testserver/login/"):
            self.assertIn(value, message.body)
        self.assertNotIn(self.payload()["password1"], message.body)

    def test_blank_email_saves_user_without_scheduling_email(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(
                reverse("access_user_create"), self.payload(email="  ")
            )
        self.assertRedirects(response, reverse("access_users"))
        self.assertTrue(
            get_user_model().objects.filter(username="new-operator").exists()
        )
        self.assertEqual(callbacks, [])
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_creation_neither_saves_user_nor_sends_email(self):
        for overrides in ({"email": "invalid"}, {"password2": "mismatch"}):
            with self.subTest(overrides=overrides):
                with self.captureOnCommitCallbacks(execute=True) as callbacks:
                    response = self.client.post(
                        reverse("access_user_create"), self.payload(**overrides)
                    )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(
                    get_user_model().objects.filter(username="new-operator").exists()
                )
                self.assertEqual(callbacks, [])
                self.assertEqual(len(mail.outbox), 0)

    def test_editing_existing_user_does_not_send_welcome_email(self):
        user = get_user_model().objects.create_user("existing-operator")
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(
                reverse("access_user_edit", args=[user.pk]),
                self.payload(username=user.username),
            )
        self.assertRedirects(response, reverse("access_users"))
        user.refresh_from_db()
        self.assertEqual(user.email, "operator@example.com")
        self.assertEqual(callbacks, [])
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_account_email_explains_activation_is_required(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("access_user_create"), self.payload(is_active="")
            )
        self.assertRedirects(response, reverse("access_users"))
        self.assertIn("currently inactive", mail.outbox[0].body)

    def test_advanced_admin_creation_also_sends_one_welcome_email(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(
                reverse("admin:auth_user_add"),
                {**self.payload(), "usable_password": "true", "_save": "Save"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["operator@example.com"])

    def test_advanced_admin_without_email_does_not_send(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(
                reverse("admin:auth_user_add"),
                {
                    **self.payload(email=""),
                    "usable_password": "true",
                    "_save": "Save",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(callbacks, [])
        self.assertEqual(len(mail.outbox), 0)

    @patch("members.notifications.send_mail", side_effect=OSError("SMTP unavailable"))
    def test_delivery_failure_keeps_saved_user_and_shows_warning(self, send):
        with self.assertLogs("members.notifications", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("access_user_create"), self.payload()
                )
        self.assertRedirects(response, reverse("access_users"))
        self.assertTrue(
            get_user_model().objects.filter(username="new-operator").exists()
        )
        self.assertTrue(
            any(
                "welcome email could not be sent" in str(message)
                for message in get_messages(response.wsgi_request)
            )
        )
        send.assert_called_once()

    @patch("members.notifications.send_mail", return_value=0)
    def test_backend_sending_zero_messages_shows_warning(self, send):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("access_user_create"), self.payload())
        self.assertTrue(
            any(
                "welcome email could not be sent" in str(message)
                for message in get_messages(response.wsgi_request)
            )
        )
        send.assert_called_once()

    def test_rolled_back_account_does_not_send_email(self):
        response = self.client.get(reverse("access_user_create"))
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaisesMessage(ValueError, "rollback"):
                with transaction.atomic():
                    user = get_user_model().objects.create_user(
                        "rolled-back", email="operator@example.com"
                    )
                    notify_user_created(response.wsgi_request, user)
                    raise ValueError("rollback")
        self.assertFalse(
            get_user_model().objects.filter(username="rolled-back").exists()
        )
        self.assertEqual(callbacks, [])
        self.assertEqual(len(mail.outbox), 0)
