from django.contrib.auth.views import LoginView
from django.urls import reverse


class WorkspaceLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        if self.request.user.is_superuser:
            return reverse("administration")
        return super().get_default_redirect_url()
