"""Resolve and stream authenticated library export artifacts.
Uses the published manifest instead of filesystem discovery or regeneration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from django.conf import settings
from django.http import FileResponse, HttpRequest, HttpResponseBase
from django.utils.cache import get_conditional_response, patch_vary_headers
from django.utils.dateparse import parse_datetime
from django.utils.http import http_date

from libraries.library_export import (
    GEOJSON_MEDIA_TYPE,
    library_export_directory,
    load_active_library_export_manifest,
)

IMMUTABLE_LIBRARY_EXPORT_CACHE_CONTROL = "private, max-age=31536000, immutable"
LATEST_LIBRARY_EXPORT_CACHE_CONTROL = "private, no-cache"
LIBRARY_EXPORT_ROBOTS_HEADER = "noindex, nofollow, noarchive"


@dataclass(frozen=True)
class LibraryExportArtifact:
    """Describe one manifest-listed immutable export file.
    Carries the response metadata required to stream it safely.
    """

    filename: str
    path: Path
    byte_size: int
    checksum: str
    content_type: str
    as_attachment: bool


@dataclass(frozen=True)
class LibraryExportDelivery:
    """Describe the current complete library export for authenticated delivery.
    Keeps page and API views aligned to one validated manifest snapshot.
    """

    checked_at: datetime
    generated_at: datetime
    record_count: int
    geojson: LibraryExportArtifact
    metadata: LibraryExportArtifact


def is_library_export_delivery_enabled() -> bool:
    """Return whether authenticated export delivery is enabled.
    Leaves scheduled artifact generation independent of the delivery switch.
    """
    return settings.LIBRARY_EXPORT_DELIVERY_ENABLED


def get_library_export_delivery() -> LibraryExportDelivery | None:
    """Return the current manifest-backed export when its files are usable.
    Avoids database work and full artifact hashes during download requests.
    """
    manifest = load_active_library_export_manifest(verify_checksums=False)
    if manifest is None:
        return None

    export = manifest.get("export")
    if not isinstance(export, dict):
        return None
    checked_at = _parse_manifest_timestamp(value=manifest.get("checked_at"))
    generated_at = _parse_manifest_timestamp(value=export.get("generated_at"))
    record_count = export.get("record_count")
    if (
        checked_at is None
        or generated_at is None
        or not isinstance(record_count, int)
        or isinstance(record_count, bool)
    ):
        return None

    export_directory = library_export_directory()
    geojson = _manifest_artifact(
        descriptor=export.get("geojson"),
        export_directory=export_directory,
        kind="geojson",
    )
    metadata = _manifest_artifact(
        descriptor=export.get("metadata"),
        export_directory=export_directory,
        kind="metadata",
    )
    if geojson is None or metadata is None:
        return None

    return LibraryExportDelivery(
        checked_at=checked_at,
        generated_at=generated_at,
        record_count=record_count,
        geojson=geojson,
        metadata=metadata,
    )


def is_library_export_delivery_available() -> bool:
    """Return whether authenticated delivery has a valid current artifact.
    Keeps unavailable downloads out of the user dashboard.
    """
    return is_library_export_delivery_enabled() and get_library_export_delivery() is not None


def build_library_export_artifact_response(
    *,
    request: HttpRequest,
    artifact: LibraryExportArtifact,
    cache_control: str,
    vary_header: str,
    generated_at: datetime,
) -> HttpResponseBase:
    """Build a conditional streaming response for one export artifact.
    Shares headers and validators between browser and JWT API delivery.
    """
    etag = f'"{artifact.checksum}"'
    last_modified = int(generated_at.timestamp())
    response = get_conditional_response(
        request,
        etag=etag,
        last_modified=last_modified,
    )
    if response is None:
        response = FileResponse(
            artifact.path.open("rb"),
            as_attachment=artifact.as_attachment,
            filename=artifact.filename,
            content_type=artifact.content_type,
        )

    response.headers["ETag"] = etag
    response.headers["Last-Modified"] = http_date(last_modified)
    return apply_library_export_response_headers(
        response=response,
        cache_control=cache_control,
        vary_header=vary_header,
    )


def apply_library_export_response_headers(
    *,
    response: HttpResponseBase,
    cache_control: str,
    vary_header: str,
) -> HttpResponseBase:
    """Apply private caching and crawler headers to an export response.
    Varies shared behavior by the authentication transport in use.
    """
    response.headers["Cache-Control"] = cache_control
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Robots-Tag"] = LIBRARY_EXPORT_ROBOTS_HEADER
    patch_vary_headers(response, [vary_header])
    return response


def _parse_manifest_timestamp(*, value: object) -> datetime | None:
    """Parse one manifest timestamp into an aware datetime.
    Rejects malformed or naive values before they become HTTP validators.
    """
    if not isinstance(value, str):
        return None
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def _manifest_artifact(
    *,
    descriptor: object,
    export_directory: Path,
    kind: Literal["geojson", "metadata"],
) -> LibraryExportArtifact | None:
    """Build one safe artifact descriptor from the active manifest.
    Restricts delivery to regular files named by the published export only.
    """
    if not isinstance(descriptor, dict):
        return None
    filename = descriptor.get("filename")
    byte_size = descriptor.get("byte_size")
    checksum = descriptor.get("sha256")
    expected_suffix = ".geojson" if kind == "geojson" else ".metadata.json"
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not filename.endswith(expected_suffix)
        or not isinstance(byte_size, int)
        or isinstance(byte_size, bool)
        or byte_size < 0
        or not isinstance(checksum, str)
        or len(checksum) != 64
    ):
        return None

    artifact_path = export_directory / filename
    try:
        if (
            artifact_path.parent != export_directory
            or artifact_path.is_symlink()
            or not artifact_path.is_file()
            or artifact_path.stat().st_size != byte_size
        ):
            return None
    except OSError:
        return None

    return LibraryExportArtifact(
        filename=filename,
        path=artifact_path,
        byte_size=byte_size,
        checksum=checksum,
        content_type=GEOJSON_MEDIA_TYPE if kind == "geojson" else "application/json",
        as_attachment=kind == "geojson",
    )
