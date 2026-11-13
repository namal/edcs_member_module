from django.contrib.auth.views import LoginView
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST


@never_cache
@require_POST
def session_activity(request):
    # SessionMiddleware refreshes the server expiry only for a live session.
    return HttpResponse(status=204 if request.user.is_authenticated else 401)


class WorkspaceLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        if self.request.user.is_superuser:
            return reverse("administration")
        return super().get_default_redirect_url()
