from django.db import connection
from django.http import JsonResponse


def health(request):
    """Return 200 if the app and database are responsive.
    Used by Dokku zero-downtime checks and uptime monitoring."""
    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "error", "detail": "database unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
