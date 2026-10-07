from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, HttpResponseNotFound

from config.security import is_probe_path


class BlockProbePathsMiddleware:
    """Answer secret-file scanner probes with a bare 404 before other middleware.
    Skips sessions, templates and database work for traffic no page serves."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in the middleware chain.
        Follows Django's standard middleware construction contract."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Return 404 for probe paths and pass every other request through.
        Keeps blocked probes cheap while leaving real routes untouched."""
        if is_probe_path(path=request.path_info):
            return HttpResponseNotFound()
        return self.get_response(request)
