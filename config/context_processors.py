from uuid import uuid4

from django.conf import settings
from django.middleware.csrf import get_token
from django.urls import reverse


def session_policy(request):
    if not request.user.is_authenticated:
        return {}
    # A public identifier for tab coordination, separate from the session cookie.
    scope = request.session.get("idle_scope")
    if not scope:
        scope = request.session["idle_scope"] = uuid4().hex
    return {
        "session_policy": {
            "timeout": settings.SESSION_COOKIE_AGE,
            "scope": scope,
            "activityUrl": reverse("session_activity"),
            "logoutUrl": reverse("logout"),
            "loginUrl": reverse("login"),
            "csrfToken": get_token(request),
        }
    }
