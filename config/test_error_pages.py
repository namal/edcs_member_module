from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.test import SimpleTestCase, override_settings
from django.urls import include, path


def missing_record(request):
    raise Http404("Private record details must not be displayed.")


def missing_json(request):
    response = JsonResponse({"error": "Record not found"}, status=404)
    response["X-Request-ID"] = "json-check"
    return response


def denied(request):
    raise PermissionDenied


def redirect_to_workspace(request):
    return redirect("dashboard")


handler404 = "config.views.page_not_found"
urlpatterns = [
    path("error-check/missing-record/", missing_record),
    path("error-check/missing-json/", missing_json),
    path("error-check/denied/", denied),
    path("error-check/redirect/", redirect_to_workspace),
    path("", include("config.urls")),
]


@override_settings(ROOT_URLCONF=__name__)
class NotFoundPageTests(SimpleTestCase):
    def test_unknown_urls_and_missing_records_use_branded_page_in_both_debug_modes(
        self,
    ):
        for debug in (True, False):
            for url in ("/this-page-does-not-exist/", "/error-check/missing-record/"):
                with self.subTest(debug=debug, url=url), override_settings(DEBUG=debug):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 404)
                    self.assertTemplateUsed(response, "404.html")
                    self.assertContains(response, "off the map", status_code=404)
                    self.assertContains(response, "Back to sign in", status_code=404)
                    self.assertNotContains(
                        response, "Private record details", status_code=404
                    )
                    self.assertNotContains(
                        response, "No districts are assigned", status_code=404
                    )
                    self.assertNotContains(
                        response, 'aria-label="Sign out"', status_code=404
                    )
                    self.assertEqual(response["X-Frame-Options"], "DENY")
                    self.assertEqual(
                        int(response["Content-Length"]), len(response.content)
                    )

    def test_json_not_found_responses_are_preserved(self):
        response = self.client.get("/error-check/missing-json/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Record not found"})
        self.assertEqual(response["X-Request-ID"], "json-check")

    def test_permission_errors_and_redirects_keep_their_existing_behaviour(self):
        response = self.client.get("/error-check/denied/")
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "403.html")
        self.assertNotContains(response, "off the map", status_code=403)
        response = self.client.get("/error-check/redirect/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    def test_missing_trailing_slash_still_redirects_to_the_valid_page(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/login/")
