from django.http import HttpRequest
from django.utils import translation


class UserLanguageMiddleware:
    """Activate the preferred language for authenticated users.
    Falls back to Django's LocaleMiddleware for anonymous visitors."""

    def __init__(self, get_response):
        """Store the next middleware or view callable."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        """Set the active language from user preference or force English for admin."""
        if request.path.startswith("/admin/"):
            translation.activate("en")
            request.LANGUAGE_CODE = "en"
        elif hasattr(request, "user") and request.user.is_authenticated:
            language = getattr(request.user, "language", "en") or "en"
            translation.activate(language)
            request.LANGUAGE_CODE = language

        return self.get_response(request)
