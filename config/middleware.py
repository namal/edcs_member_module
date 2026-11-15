from django.utils.cache import add_never_cache_headers


class WorkspaceNotFoundMiddleware:
    """Use the branded page for HTML 404s, including Django's DEBUG responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            response.status_code != 404
            or response.get("Content-Type", "").split(";", 1)[0] != "text/html"
            or getattr(response, "_workspace_not_found", False)
        ):
            return response
        from .views import page_not_found

        branded = page_not_found(request)
        for name, value in response.items():
            if name.lower() not in ("content-type", "content-length"):
                branded[name] = value
        branded.cookies.update(response.cookies)
        return branded


class PrivateSessionMiddleware:
    """Keep authenticated pages out of browser and shared caches."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        authenticated = request.user.is_authenticated
        response = self.get_response(request)
        if authenticated or request.user.is_authenticated:
            add_never_cache_headers(response)
        return response
