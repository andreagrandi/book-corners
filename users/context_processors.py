from django.conf import settings


def social_auth(request):
    """Expose social auth availability flags to all templates.
    Returns False for each provider when its env vars are missing."""
    return {
        "google_oauth_enabled": settings.GOOGLE_OAUTH_ENABLED,
        "apple_oauth_enabled": settings.APPLE_OAUTH_ENABLED,
    }
