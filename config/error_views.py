from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def page_not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    """Render a friendly custom 404 page.
    Guides users back to key navigation routes after unknown URLs."""
    return render(request, "404.html", status=404)


def server_error(request: HttpRequest) -> HttpResponse:
    """Render a friendly custom 500 page.
    Gives users a stable fallback when an unexpected error occurs."""
    return render(request, "500.html", status=500)
