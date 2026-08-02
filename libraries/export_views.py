"""Render authenticated library export download pages and files.
Provides a browser-friendly companion to the JWT API endpoints.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe

from libraries.library_export_delivery import (
    IMMUTABLE_LIBRARY_EXPORT_CACHE_CONTROL,
    LATEST_LIBRARY_EXPORT_CACHE_CONTROL,
    LibraryExportArtifact,
    apply_library_export_response_headers,
    build_library_export_artifact_response,
    get_library_export_delivery,
    is_library_export_delivery_enabled,
)


@login_required(login_url="login")
@require_safe
def library_export_download(request: HttpRequest) -> HttpResponse:
    """Render the authenticated page for downloading the library export.
    Shows a clear unavailable state until the first artifact is published.
    """
    _require_library_export_delivery()
    export = get_library_export_delivery()
    status_code = 200 if export is not None else 503
    response = render(
        request,
        "libraries/library_export_download.html",
        {"export": export},
        status=status_code,
    )
    return apply_library_export_response_headers(
        response=response,
        cache_control="private, no-store",
        vary_header="Cookie",
    )


@login_required(login_url="login")
@require_safe
def library_export_latest_geojson(request: HttpRequest) -> HttpResponse:
    """Stream the current GeoJSON export to an authenticated browser user.
    Uses a private latest alias that supports conditional retrieval.
    """
    _require_library_export_delivery()
    export = get_library_export_delivery()
    if export is None:
        return _unavailable_response()
    return build_library_export_artifact_response(
        request=request,
        artifact=export.geojson,
        cache_control=LATEST_LIBRARY_EXPORT_CACHE_CONTROL,
        vary_header="Cookie",
        generated_at=export.generated_at,
    )


@login_required(login_url="login")
@require_safe
def library_export_latest_geojson_gzip(request: HttpRequest) -> HttpResponse:
    """Stream precompressed GeoJSON to an authenticated browser user.
    Makes the smaller gzip artifact the default human-facing download.
    """
    _require_library_export_delivery()
    export = get_library_export_delivery()
    if export is None:
        return _unavailable_response()
    return build_library_export_artifact_response(
        request=request,
        artifact=export.geojson_gzip,
        cache_control=LATEST_LIBRARY_EXPORT_CACHE_CONTROL,
        vary_header="Cookie",
        generated_at=export.generated_at,
    )


@login_required(login_url="login")
@require_safe
def library_export_metadata(request: HttpRequest) -> HttpResponse:
    """Stream current export metadata to an authenticated browser user.
    Keeps the latest alias private while preserving HTTP validators.
    """
    _require_library_export_delivery()
    export = get_library_export_delivery()
    if export is None:
        return _unavailable_response()
    return build_library_export_artifact_response(
        request=request,
        artifact=export.metadata,
        cache_control=LATEST_LIBRARY_EXPORT_CACHE_CONTROL,
        vary_header="Cookie",
        generated_at=export.generated_at,
    )


@login_required(login_url="login")
@require_safe
def library_export_artifact(request: HttpRequest, filename: str) -> HttpResponse:
    """Stream one current immutable artifact to an authenticated browser user.
    Refuses filenames not explicitly listed by the active manifest.
    """
    _require_library_export_delivery()
    export = get_library_export_delivery()
    if export is None:
        raise Http404
    artifact = _matching_artifact(
        export_artifacts=(export.geojson, export.geojson_gzip, export.metadata),
        filename=filename,
    )
    if artifact is None:
        raise Http404
    return build_library_export_artifact_response(
        request=request,
        artifact=artifact,
        cache_control=IMMUTABLE_LIBRARY_EXPORT_CACHE_CONTROL,
        vary_header="Cookie",
        generated_at=export.generated_at,
    )


def _require_library_export_delivery() -> None:
    """Raise a not-found response when export delivery is disabled.
    Keeps an emergency shutdown from advertising an available endpoint.
    """
    if not is_library_export_delivery_enabled():
        raise Http404


def _matching_artifact(
    *, export_artifacts: tuple[LibraryExportArtifact, ...], filename: str
) -> LibraryExportArtifact | None:
    """Return the current manifest artifact matching one exact requested name.
    Prevents retained files and arbitrary paths from becoming downloadable.
    """
    return next(
        (
            artifact
            for artifact in export_artifacts
            if artifact.filename == filename
        ),
        None,
    )


def _unavailable_response() -> HttpResponse:
    """Return a clear private response while no current artifact exists.
    Avoids exposing internal filesystem or manifest validation details.
    """
    response = HttpResponse(
        _("Library export is temporarily unavailable. Please try again later."),
        content_type="text/plain; charset=utf-8",
        status=503,
    )
    return apply_library_export_response_headers(
        response=response,
        cache_control="private, no-store",
        vary_header="Cookie",
    )
