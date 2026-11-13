from datetime import timedelta

from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone


@override_settings(SESSION_COOKIE_AGE=900)
class SessionSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            "session-admin", password="test-session-password"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def expire_session(self):
        Session.objects.filter(session_key=self.client.session.session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1)
        )

    def test_login_uses_browser_session_cookie_and_short_server_expiry(self):
        self.client.logout()
        before = timezone.now()
        response = self.client.post(
            reverse("login"),
            {"username": self.user.username, "password": "test-session-password"},
        )
        self.assertRedirects(response, reverse("administration"))
        cookie = response.cookies[settings.SESSION_COOKIE_NAME]
        self.assertEqual(cookie["max-age"], "")
        self.assertEqual(cookie["expires"], "")
        self.assertTrue(cookie["httponly"])
        session = Session.objects.get(session_key=cookie.value)
        self.assertGreaterEqual(session.expire_date, before + timedelta(seconds=900))
        self.assertLessEqual(
            session.expire_date, timezone.now() + timedelta(seconds=900)
        )

    def test_activity_refreshes_live_session(self):
        key = self.client.session.session_key
        Session.objects.filter(session_key=key).update(
            expire_date=timezone.now() + timedelta(seconds=30)
        )
        before = timezone.now()
        response = self.client.post(reverse("session_activity"))
        self.assertEqual(response.status_code, 204)
        session = Session.objects.get(session_key=key)
        self.assertGreaterEqual(session.expire_date, before + timedelta(seconds=900))
        self.assertEqual(session.get_decoded()[SESSION_KEY], str(self.user.pk))

    def test_expired_session_cannot_be_refreshed(self):
        key = self.client.session.session_key
        self.expire_session()
        response = self.client.post(reverse("session_activity"))
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertLess(
            Session.objects.get(session_key=key).expire_date, timezone.now()
        )

    def test_expired_session_is_rejected_across_workspace_and_admin(self):
        for name in ("dashboard", "administration", "admin:index"):
            with self.subTest(name=name):
                self.client.force_login(self.user)
                self.expire_session()
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response.url)

    def test_activity_requires_post_authentication_and_csrf(self):
        url = reverse("session_activity")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client().post(url).status_code, 401)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        response = csrf_client.get(reverse("administration"))
        token = response.context["session_policy"]["csrfToken"]
        self.assertEqual(csrf_client.post(url, HTTP_X_CSRFTOKEN=token).status_code, 204)

    def test_workspace_and_advanced_admin_have_idle_monitor_and_no_cache(self):
        scope = None
        for name in ("dashboard", "administration", "admin:index"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="session-policy"')
                self.assertContains(response, "js/session.js")
                self.assertIn("no-store", response.headers["Cache-Control"])
                self.assertIn("private", response.headers["Cache-Control"])
                policy = response.context["session_policy"]
                self.assertEqual(policy["timeout"], 900)
                self.assertNotEqual(policy["scope"], self.client.session.session_key)
                if scope is not None:
                    self.assertEqual(policy["scope"], scope)
                scope = policy["scope"]

    def test_logout_invalidates_session_and_next_login_uses_new_tab_scope(self):
        response = self.client.get(reverse("administration"))
        scope = response.context["session_policy"]["scope"]
        key = self.client.session.session_key
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.client.force_login(self.user)
        response = self.client.get(reverse("administration"))
        self.assertNotEqual(response.context["session_policy"]["scope"], scope)

    def test_expiry_notice_on_login_has_no_idle_monitor(self):
        self.client.logout()
        response = self.client.get(reverse("login"), {"expired": "1"})
        self.assertContains(response, "Your session has expired.")
        self.assertNotContains(response, "js/session.js")

    @override_settings(SESSION_COOKIE_AGE=120)
    def test_configured_timeout_reaches_the_browser(self):
        response = self.client.get(reverse("administration"))
        self.assertEqual(response.context["session_policy"]["timeout"], 120)
