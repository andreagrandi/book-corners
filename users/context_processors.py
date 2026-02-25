from django.conf import settings


def google_oauth(request):
    """Expose Google OAuth availability to all templates.
    Returns False when either OAuth env var is missing."""
    return {"google_oauth_enabled": settings.GOOGLE_OAUTH_ENABLED}
