"""Generate immutable GeoJSON exports of approved libraries.
Publishes only validated changed data through an atomic manifest swap.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import structlog
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from libraries.models import Library

logger = structlog.get_logger(__name__)

EXPORT_DIRECTORY_NAME = "library_exports"
EXPORT_SCHEMA_VERSION = 1
EXPORT_MANIFEST_VERSION = 1
EXPORT_ITERATOR_CHUNK_SIZE = 1000
EXPORT_RETAIN_PREVIOUS = 7
EXPORT_ADVISORY_LOCK_ID = 6_824_601_389_155_425_722
EXPORT_METADATA_VERSION = 1
GEOJSON_MEDIA_TYPE = "application/geo+json"

EXPORTED_PROPERTY_NAMES = (
    "id",
    "slug",
    "name",
    "description",
    "photo_url",
    "address",
    "city",
    "country",
    "postal_code",
    "wheelchair_accessible",
    "capacity",
    "is_indoor",
    "is_lit",
    "website",
    "contact",
    "source",
    "operator",
    "brand",
    "external_id",
    "created_at",
    "updated_at",
)
EXPORTED_LIBRARY_FIELDS = (
    "id",
    "slug",
    "name",
    "description",
    "photo",
    "location",
    "address",
    "city",
    "country",
    "postal_code",
    "wheelchair_accessible",
    "capacity",
    "is_indoor",
    "is_lit",
    "website",
    "contact",
    "source",
    "operator",
    "brand",
    "external_id",
    "created_at",
    "updated_at",
)
TEXT_PROPERTY_NAMES = frozenset(EXPORTED_PROPERTY_NAMES) - {
    "id",
    "capacity",
    "is_indoor",
    "is_lit",
    "source",
    "external_id",
    "created_at",
    "updated_at",
}
NULLABLE_BOOLEAN_PROPERTY_NAMES = frozenset({"is_indoor", "is_lit"})

EXPORT_SCHEMA: dict[str, object] = {
    "geojson_type": "FeatureCollection",
    "schema_version": EXPORT_SCHEMA_VERSION,
    "geometry": {
        "type": "Point",
        "coordinates": ["longitude", "latitude"],
    },
    "properties": {
        "id": {"type": "integer"},
        "slug": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "photo_url": {"type": "string", "format": "uri"},
        "address": {"type": "string"},
        "city": {"type": "string"},
        "country": {"type": "string"},
        "postal_code": {"type": "string"},
        "wheelchair_accessible": {"type": "string"},
        "capacity": {"type": ["integer", "null"]},
        "is_indoor": {"type": ["boolean", "null"]},
        "is_lit": {"type": ["boolean", "null"]},
        "website": {"type": "string", "format": "uri"},
        "contact": {"type": "string"},
        "source": {"type": ["string", "null"]},
        "operator": {"type": "string"},
        "brand": {"type": "string"},
        "external_id": {"type": ["string", "null"]},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
    },
}
GEOJSON_HEADER = (
    b'{"type":"FeatureCollection","book_corners_schema_version":1,"features":[\n'
)
GEOJSON_TRAILER = b"]}\n"


class LibraryExportError(Exception):
    """Describe a failure while preparing an export artifact.
    Keeps command failures distinct from an overlapping scheduled run.
    """


class LibraryExportValidationError(LibraryExportError):
    """Describe malformed generated data before it becomes public.
    Prevents incomplete or unsafe artifacts from reaching the manifest.
    """


@dataclass(frozen=True)
class CandidateGeoJSON:
    """Hold the validated temporary GeoJSON artifact details.
    Supplies publication metadata without retaining any library rows.
    """

    path: Path
    record_count: int
    byte_size: int
    data_checksum: str


@dataclass(frozen=True)
class WrittenArtifact:
    """Describe a fully written JSON file ready for publication.
    Carries immutable-file integrity metadata for the manifest.
    """

    path: Path
    byte_size: int
    checksum: str


@dataclass(frozen=True)
class LibraryExportResult:
    """Report the outcome of one export-generation invocation.
    Lets the management command provide clear scheduled-job output.
    """

    outcome: Literal["published", "unchanged", "locked"]
    record_count: int | None
    geojson_filename: str | None


def generate_library_export() -> LibraryExportResult:
    """Generate and conditionally publish the approved-library export.
    Uses one database snapshot and one manifest swap per successful run.
    """
    started_at = time.monotonic()
    checked_at = timezone.now()
    export_directory = _export_directory()
    export_directory.mkdir(parents=True, exist_ok=True)

    with transaction.atomic():
        _set_repeatable_read_isolation()
        if not _try_acquire_export_lock():
            logger.info(
                "library_export_skipped_locked",
                duration_seconds=round(time.monotonic() - started_at, 3),
            )
            return LibraryExportResult(
                outcome="locked",
                record_count=None,
                geojson_filename=None,
            )

        _cleanup_temporary_files(export_directory=export_directory)
        active_manifest = _load_valid_active_manifest(export_directory=export_directory)
        candidate_path: Path | None = None
        metadata_path: Path | None = None
        published_paths: list[Path] = []

        try:
            candidate_path = _temporary_path(
                export_directory=export_directory,
                suffix=".geojson.tmp",
            )
            candidate = _write_geojson_candidate(path=candidate_path)
            schema_checksum = _schema_checksum()

            if _matches_active_export(
                active_manifest=active_manifest,
                schema_checksum=schema_checksum,
                data_checksum=candidate.data_checksum,
            ):
                geojson_filename = _active_geojson_filename(
                    manifest=active_manifest
                )
                _replace_manifest(
                    export_directory=export_directory,
                    manifest=_manifest_with_checked_at(
                        manifest=active_manifest,
                        checked_at=checked_at,
                    ),
                )
                _cleanup_retained_artifacts(
                    export_directory=export_directory,
                    active_manifest=active_manifest,
                )
                logger.info(
                    "library_export_unchanged",
                    data_checksum=candidate.data_checksum,
                    geojson_filename=geojson_filename,
                    record_count=candidate.record_count,
                    duration_seconds=round(time.monotonic() - started_at, 3),
                )
                return LibraryExportResult(
                    outcome="unchanged",
                    record_count=candidate.record_count,
                    geojson_filename=geojson_filename,
                )

            generated_at = timezone.now()
            version = _artifact_version(
                generated_at=generated_at,
                schema_checksum=schema_checksum,
                data_checksum=candidate.data_checksum,
            )
            geojson_filename = f"libraries-{version}.geojson"
            metadata_filename = f"libraries-{version}.metadata.json"
            metadata_path = _temporary_path(
                export_directory=export_directory,
                suffix=".metadata.json.tmp",
            )
            metadata_artifact = _write_metadata_candidate(
                path=metadata_path,
                generated_at=generated_at,
                candidate=candidate,
                geojson_filename=geojson_filename,
                schema_checksum=schema_checksum,
            )

            geojson_path = export_directory / geojson_filename
            final_metadata_path = export_directory / metadata_filename
            _install_immutable_file(source=candidate.path, destination=geojson_path)
            published_paths.append(geojson_path)
            _install_immutable_file(
                source=metadata_artifact.path,
                destination=final_metadata_path,
            )
            published_paths.append(final_metadata_path)

            manifest = _build_manifest(
                checked_at=checked_at,
                generated_at=generated_at,
                candidate=candidate,
                geojson_filename=geojson_filename,
                metadata_artifact=metadata_artifact,
                metadata_filename=metadata_filename,
                schema_checksum=schema_checksum,
            )
            _replace_manifest(
                export_directory=export_directory,
                manifest=manifest,
            )
            _cleanup_retained_artifacts(
                export_directory=export_directory,
                active_manifest=manifest,
            )
            logger.info(
                "library_export_published",
                data_checksum=candidate.data_checksum,
                geojson_filename=geojson_filename,
                record_count=candidate.record_count,
                schema_checksum=schema_checksum,
                duration_seconds=round(time.monotonic() - started_at, 3),
            )
            return LibraryExportResult(
                outcome="published",
                record_count=candidate.record_count,
                geojson_filename=geojson_filename,
            )
        except Exception:
            for path in published_paths:
                _remove_file(path=path)
            raise
        finally:
            if candidate_path is not None:
                _remove_file(path=candidate_path)
            if metadata_path is not None:
                _remove_file(path=metadata_path)


def _export_directory() -> Path:
    """Return the persistent directory reserved for export artifacts.
    Resolves from MEDIA_ROOT so tests and deployment share one convention.
    """
    return Path(settings.MEDIA_ROOT) / EXPORT_DIRECTORY_NAME


def _set_repeatable_read_isolation() -> None:
    """Set the export transaction to one stable database snapshot.
    Must run before the command performs its first database query.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")


def _try_acquire_export_lock() -> bool:
    """Attempt to acquire the transaction-scoped export advisory lock.
    Avoids competing cron jobs publishing incompatible manifest generations.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_xact_lock(%s)",
            [EXPORT_ADVISORY_LOCK_ID],
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def _temporary_path(*, export_directory: Path, suffix: str) -> Path:
    """Create an empty temporary path beside its eventual artifact.
    Keeps atomic file operations on the persistent media filesystem.
    """
    descriptor, raw_path = tempfile.mkstemp(
        prefix=".library-export-",
        suffix=suffix,
        dir=export_directory,
    )
    os.close(descriptor)
    return Path(raw_path)


def _write_geojson_candidate(*, path: Path) -> CandidateGeoJSON:
    """Stream the approved libraries into a validated temporary GeoJSON file.
    Avoids materializing the catalogue while calculating the data checksum.
    """
    digest = hashlib.sha256()
    record_count = 0
    first_feature = True

    with path.open("wb") as handle:
        _write_bytes(handle=handle, digest=digest, value=GEOJSON_HEADER)
        queryset = (
            Library.objects.filter(status=Library.Status.APPROVED)
            .order_by("id")
            .values(*EXPORTED_LIBRARY_FIELDS)
        )
        for row in queryset.iterator(chunk_size=EXPORT_ITERATOR_CHUNK_SIZE):
            feature = _feature_from_row(row=row)
            _validate_feature(feature=feature, previous_id=None)
            encoded_feature = json.dumps(
                feature,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if not first_feature:
                _write_bytes(handle=handle, digest=digest, value=b",\n")
            _write_bytes(handle=handle, digest=digest, value=encoded_feature)
            first_feature = False
            record_count += 1

        if not first_feature:
            _write_bytes(handle=handle, digest=digest, value=b"\n")
        _write_bytes(handle=handle, digest=digest, value=GEOJSON_TRAILER)
        handle.flush()
        os.fsync(handle.fileno())

    byte_size = path.stat().st_size
    checksum = digest.hexdigest()
    _validate_geojson_candidate(
        path=path,
        expected_checksum=checksum,
        expected_record_count=record_count,
    )
    return CandidateGeoJSON(
        path=path,
        record_count=record_count,
        byte_size=byte_size,
        data_checksum=checksum,
    )


def _write_bytes(*, handle: Any, digest: Any, value: bytes) -> None:
    """Write bytes and include them in the deterministic data checksum.
    Keeps the checksum tied exactly to the published GeoJSON representation.
    """
    handle.write(value)
    digest.update(value)


def _feature_from_row(*, row: dict[str, Any]) -> dict[str, object]:
    """Build one public GeoJSON feature from an approved-library row.
    Keeps every property confined to the documented export allowlist.
    """
    location = row["location"]
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [location.x, location.y],
        },
        "properties": {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "photo_url": _absolute_photo_url(photo_name=row["photo"]),
            "address": row["address"],
            "city": row["city"],
            "country": row["country"],
            "postal_code": row["postal_code"],
            "wheelchair_accessible": row["wheelchair_accessible"],
            "capacity": row["capacity"],
            "is_indoor": row["is_indoor"],
            "is_lit": row["is_lit"],
            "website": row["website"],
            "contact": row["contact"],
            "source": _nullable_provenance(value=row["source"]),
            "operator": row["operator"],
            "brand": row["brand"],
            "external_id": _nullable_provenance(value=row["external_id"]),
            "created_at": _format_timestamp(value=row["created_at"]),
            "updated_at": _format_timestamp(value=row["updated_at"]),
        },
    }


def _absolute_photo_url(*, photo_name: str) -> str:
    """Return an absolute primary-photo URL or an empty string.
    Preserves already absolute storage URLs and normalizes relative media paths.
    """
    if not photo_name:
        return ""

    try:
        photo_url = Library._meta.get_field("photo").storage.url(photo_name)
    except (OSError, ValueError):
        return ""

    parsed_url = urlsplit(photo_url)
    if parsed_url.scheme and parsed_url.netloc:
        return photo_url

    site_url = settings.SITE_URL.rstrip("/")
    parsed_site_url = urlsplit(site_url)
    if not parsed_site_url.scheme or not parsed_site_url.netloc:
        raise LibraryExportValidationError("SITE_URL must be an absolute URL.")
    return f"{site_url}/{photo_url.lstrip('/')}"


def _nullable_provenance(*, value: Any) -> str | None:
    """Normalize absent provenance values to explicit JSON nulls.
    Keeps source and external identifiers present with a stable meaning.
    """
    if not value:
        return None
    if not isinstance(value, str):
        raise LibraryExportValidationError("Provenance values must be strings.")
    return value


def _format_timestamp(*, value: Any) -> str:
    """Format a model timestamp as an explicit UTC RFC 3339 value.
    Rejects unexpected database values before they enter a public artifact.
    """
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LibraryExportValidationError("Export timestamps must be timezone-aware datetimes.")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_feature(*, feature: object, previous_id: int | None) -> int:
    """Validate one feature against the export schema and ordering rules.
    Returns its identifier so streaming validation can enforce stable order.
    """
    if not isinstance(feature, dict):
        raise LibraryExportValidationError("GeoJSON feature must be an object.")
    if set(feature) != {"type", "geometry", "properties"}:
        raise LibraryExportValidationError("GeoJSON feature contains unexpected fields.")
    if feature["type"] != "Feature":
        raise LibraryExportValidationError("GeoJSON feature type must be Feature.")

    geometry = feature["geometry"]
    if not isinstance(geometry, dict) or set(geometry) != {"type", "coordinates"}:
        raise LibraryExportValidationError("GeoJSON feature geometry is invalid.")
    if geometry["type"] != "Point":
        raise LibraryExportValidationError("GeoJSON feature geometry must be a Point.")
    coordinates = geometry["coordinates"]
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise LibraryExportValidationError(
            "GeoJSON coordinates must contain longitude and latitude."
        )
    longitude, latitude = coordinates
    if (
        isinstance(longitude, bool)
        or isinstance(latitude, bool)
        or not isinstance(longitude, (int, float))
        or not isinstance(latitude, (int, float))
        or not math.isfinite(longitude)
        or not math.isfinite(latitude)
        or not -180 <= longitude <= 180
        or not -90 <= latitude <= 90
    ):
        raise LibraryExportValidationError("GeoJSON coordinates are outside WGS 84 bounds.")

    properties = feature["properties"]
    if not isinstance(properties, dict) or set(properties) != set(EXPORTED_PROPERTY_NAMES):
        raise LibraryExportValidationError("GeoJSON properties do not match the export allowlist.")
    identifier = properties["id"]
    if isinstance(identifier, bool) or not isinstance(identifier, int):
        raise LibraryExportValidationError("Exported library IDs must be integers.")
    if previous_id is not None and identifier <= previous_id:
        raise LibraryExportValidationError("Exported library IDs must be strictly increasing.")

    for property_name in TEXT_PROPERTY_NAMES:
        if not isinstance(properties[property_name], str):
            raise LibraryExportValidationError(
                f"Export property {property_name} must be a string."
            )
    for property_name in ("source", "external_id"):
        if properties[property_name] is not None and not isinstance(
            properties[property_name], str
        ):
            raise LibraryExportValidationError(
                f"Export property {property_name} must be a string or null."
            )
    capacity = properties["capacity"]
    if capacity is not None and (isinstance(capacity, bool) or not isinstance(capacity, int)):
        raise LibraryExportValidationError("Export property capacity must be an integer or null.")
    for property_name in NULLABLE_BOOLEAN_PROPERTY_NAMES:
        if properties[property_name] is not None and not isinstance(
            properties[property_name], bool
        ):
            raise LibraryExportValidationError(
                f"Export property {property_name} must be a boolean or null."
            )
    for property_name in ("created_at", "updated_at"):
        value = properties[property_name]
        if not isinstance(value, str) or not value.endswith("Z"):
            raise LibraryExportValidationError(
                f"Export property {property_name} must be a UTC timestamp."
            )
    photo_url = properties["photo_url"]
    if photo_url and (
        not isinstance(photo_url, str)
        or not urlsplit(photo_url).scheme
        or not urlsplit(photo_url).netloc
    ):
        raise LibraryExportValidationError("Exported photo URLs must be absolute.")
    return identifier


def _validate_geojson_candidate(
    *,
    path: Path,
    expected_checksum: str,
    expected_record_count: int,
) -> None:
    """Re-read a candidate GeoJSON file with bounded validation memory.
    Verifies framing, ordering, count, and checksum before publication.
    """
    digest = hashlib.sha256()
    previous_id: int | None = None
    record_count = 0

    with path.open("rb") as handle:
        header = handle.readline()
        digest.update(header)
        if header != GEOJSON_HEADER:
            raise LibraryExportValidationError("GeoJSON candidate has an invalid header.")

        while True:
            line = handle.readline()
            if not line:
                raise LibraryExportValidationError("GeoJSON candidate has no closing trailer.")
            digest.update(line)
            if line == GEOJSON_TRAILER:
                break

            serialized_feature = line.rstrip(b"\n")
            if serialized_feature.endswith(b","):
                serialized_feature = serialized_feature[:-1]
            try:
                feature = json.loads(serialized_feature)
            except json.JSONDecodeError as exc:
                raise LibraryExportValidationError(
                    "GeoJSON candidate contains invalid JSON."
                ) from exc
            previous_id = _validate_feature(
                feature=feature,
                previous_id=previous_id,
            )
            record_count += 1

        trailing_data = handle.read(1)
        if trailing_data:
            raise LibraryExportValidationError("GeoJSON candidate contains trailing data.")

    if digest.hexdigest() != expected_checksum:
        raise LibraryExportValidationError("GeoJSON candidate checksum changed during validation.")
    if record_count != expected_record_count:
        raise LibraryExportValidationError("GeoJSON candidate record count is inconsistent.")


def _schema_checksum() -> str:
    """Return the checksum of the canonical public schema descriptor.
    Makes field or semantic changes trigger a new immutable export version.
    """
    serialized_schema = json.dumps(
        EXPORT_SCHEMA,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized_schema).hexdigest()


def _artifact_version(
    *,
    generated_at: datetime,
    schema_checksum: str,
    data_checksum: str,
) -> str:
    """Build a unique, readable immutable artifact version token.
    Combines UTC generation time with schema and data checksum prefixes.
    """
    timestamp = generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{schema_checksum[:12]}-{data_checksum[:12]}"


def _write_metadata_candidate(
    *,
    path: Path,
    generated_at: datetime,
    candidate: CandidateGeoJSON,
    geojson_filename: str,
    schema_checksum: str,
) -> WrittenArtifact:
    """Write and validate metadata describing one immutable GeoJSON file.
    Preserves schema, licensing, and artifact-integrity information beside data.
    """
    metadata = {
        "metadata_version": EXPORT_METADATA_VERSION,
        "title": "Book Corners approved library export",
        "scope": "Complete catalogue of approved Book Corners libraries.",
        "format": "GeoJSON",
        "media_type": GEOJSON_MEDIA_TYPE,
        "generated_at": _format_timestamp(value=generated_at),
        "record_count": candidate.record_count,
        "schema": {
            "version": EXPORT_SCHEMA_VERSION,
            "sha256": schema_checksum,
            "definition": EXPORT_SCHEMA,
        },
        "data": {
            "filename": geojson_filename,
            "byte_size": candidate.byte_size,
            "sha256": candidate.data_checksum,
        },
        "license": {
            "name": "Open Data Commons Open Database License (ODbL) v1.0",
            "url": "https://opendatacommons.org/licenses/odbl/1-0/",
        },
        "attribution": [
            {
                "name": "Book Corners",
                "url": settings.SITE_URL.rstrip("/"),
            },
            {
                "name": "OpenStreetMap contributors",
                "url": "https://www.openstreetmap.org/copyright",
            },
        ],
        "photo_notice": (
            "Photo URLs identify publicly displayed media and do not independently "
            "grant permission to redistribute or relicense image files."
        ),
    }
    _write_json_file(path=path, payload=metadata)
    _validate_metadata_file(
        path=path,
        expected_geojson_filename=geojson_filename,
        expected_checksum=candidate.data_checksum,
        expected_record_count=candidate.record_count,
        expected_schema_checksum=schema_checksum,
    )
    checksum, byte_size = _hash_file(path=path)
    return WrittenArtifact(path=path, byte_size=byte_size, checksum=checksum)


def _write_json_file(*, path: Path, payload: dict[str, object]) -> None:
    """Write a small JSON artifact with durable UTF-8 bytes.
    Uses canonical formatting so metadata and manifests remain reproducible.
    """
    serialized_payload = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    with path.open("wb") as handle:
        handle.write(serialized_payload)
        handle.flush()
        os.fsync(handle.fileno())


def _validate_metadata_file(
    *,
    path: Path,
    expected_geojson_filename: str,
    expected_checksum: str,
    expected_record_count: int,
    expected_schema_checksum: str,
) -> None:
    """Validate the small metadata document before it can be published.
    Ensures it refers exactly to the candidate GeoJSON and schema.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryExportValidationError("Export metadata is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise LibraryExportValidationError("Export metadata must be a JSON object.")
    data = payload.get("data")
    schema = payload.get("schema")
    if not isinstance(data, dict) or not isinstance(schema, dict):
        raise LibraryExportValidationError("Export metadata is missing data or schema details.")
    if (
        data.get("filename") != expected_geojson_filename
        or data.get("sha256") != expected_checksum
        or payload.get("record_count") != expected_record_count
        or schema.get("sha256") != expected_schema_checksum
    ):
        raise LibraryExportValidationError("Export metadata does not match its candidate.")


def _hash_file(*, path: Path) -> tuple[str, int]:
    """Return the SHA-256 checksum and byte size of a file.
    Reads fixed-size chunks so integrity checks remain memory bounded.
    """
    digest = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            byte_size += len(chunk)
    return digest.hexdigest(), byte_size


def _install_immutable_file(*, source: Path, destination: Path) -> None:
    """Publish a completed artifact without permitting file replacement.
    Uses a hard link in the same directory so existing versions cannot change.
    """
    if destination.exists():
        raise LibraryExportError(f"Refusing to replace immutable artifact {destination.name}.")
    try:
        os.link(source, destination)
    except OSError as exc:
        raise LibraryExportError(
            f"Could not publish immutable artifact {destination.name}."
        ) from exc
    _remove_file(path=source)


def _build_manifest(
    *,
    checked_at: datetime,
    generated_at: datetime,
    candidate: CandidateGeoJSON,
    geojson_filename: str,
    metadata_artifact: WrittenArtifact,
    metadata_filename: str,
    schema_checksum: str,
) -> dict[str, object]:
    """Build the current export manifest after immutable files exist.
    Keeps latest delivery independent of filesystem directory discovery.
    """
    return {
        "manifest_version": EXPORT_MANIFEST_VERSION,
        "checked_at": _format_timestamp(value=checked_at),
        "export": {
            "generated_at": _format_timestamp(value=generated_at),
            "record_count": candidate.record_count,
            "schema_version": EXPORT_SCHEMA_VERSION,
            "schema_checksum": schema_checksum,
            "data_checksum": candidate.data_checksum,
            "geojson": {
                "filename": geojson_filename,
                "byte_size": candidate.byte_size,
                "sha256": candidate.data_checksum,
            },
            "metadata": {
                "filename": metadata_filename,
                "byte_size": metadata_artifact.byte_size,
                "sha256": metadata_artifact.checksum,
            },
        },
    }


def _replace_manifest(*, export_directory: Path, manifest: dict[str, object]) -> None:
    """Atomically replace latest.json after validating its referenced artifacts.
    Makes the manifest swap the sole commit point for a new export version.
    """
    _validate_manifest(manifest=manifest, export_directory=export_directory)
    temporary_path = _temporary_path(
        export_directory=export_directory,
        suffix=".manifest.json.tmp",
    )
    try:
        _write_json_file(path=temporary_path, payload=manifest)
        os.replace(temporary_path, export_directory / "latest.json")
    finally:
        _remove_file(path=temporary_path)


def _load_valid_active_manifest(*, export_directory: Path) -> dict[str, object] | None:
    """Load the active manifest only when its files still pass integrity checks.
    Allows a fresh valid candidate to recover from missing or damaged artifacts.
    """
    manifest_path = export_directory / "latest.json"
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise LibraryExportValidationError("Active export manifest must be an object.")
        _validate_manifest(manifest=payload, export_directory=export_directory)
    except (LibraryExportValidationError, OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "library_export_active_manifest_invalid",
            reason=str(exc),
        )
        return None
    return payload


def _validate_manifest(*, manifest: dict[str, object], export_directory: Path) -> None:
    """Validate a manifest and the immutable artifacts it references.
    Prevents unchanged checks from trusting corrupted or unsafe file paths.
    """
    if manifest.get("manifest_version") != EXPORT_MANIFEST_VERSION:
        raise LibraryExportValidationError("Export manifest version is unsupported.")
    if not isinstance(manifest.get("checked_at"), str):
        raise LibraryExportValidationError("Export manifest checked_at is missing.")
    export = manifest.get("export")
    if not isinstance(export, dict):
        raise LibraryExportValidationError("Export manifest has no export descriptor.")
    required_fields = {
        "generated_at",
        "record_count",
        "schema_version",
        "schema_checksum",
        "data_checksum",
        "geojson",
        "metadata",
    }
    if set(export) != required_fields:
        raise LibraryExportValidationError("Export manifest has unexpected descriptor fields.")
    if (
        not isinstance(export["generated_at"], str)
        or not isinstance(export["record_count"], int)
        or isinstance(export["record_count"], bool)
        or export["schema_version"] != EXPORT_SCHEMA_VERSION
        or not isinstance(export["schema_checksum"], str)
        or not isinstance(export["data_checksum"], str)
    ):
        raise LibraryExportValidationError("Export manifest descriptor values are invalid.")

    geojson = export["geojson"]
    metadata = export["metadata"]
    if not isinstance(geojson, dict) or not isinstance(metadata, dict):
        raise LibraryExportValidationError("Export manifest artifact descriptors are invalid.")
    _validate_artifact_descriptor(
        descriptor=geojson,
        export_directory=export_directory,
        required_suffix=".geojson",
    )
    _validate_artifact_descriptor(
        descriptor=metadata,
        export_directory=export_directory,
        required_suffix=".metadata.json",
    )
    if geojson["sha256"] != export["data_checksum"]:
        raise LibraryExportValidationError("Export manifest data checksum is inconsistent.")

    metadata_path = export_directory / str(metadata["filename"])
    try:
        metadata_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryExportValidationError("Published metadata is not valid JSON.") from exc
    if not isinstance(metadata_payload, dict):
        raise LibraryExportValidationError("Published metadata must be a JSON object.")
    data = metadata_payload.get("data")
    schema = metadata_payload.get("schema")
    if not isinstance(data, dict) or not isinstance(schema, dict):
        raise LibraryExportValidationError("Published metadata has no data or schema details.")
    if (
        data.get("filename") != geojson["filename"]
        or data.get("sha256") != geojson["sha256"]
        or metadata_payload.get("record_count") != export["record_count"]
        or schema.get("sha256") != export["schema_checksum"]
    ):
        raise LibraryExportValidationError("Published metadata does not match its manifest.")


def _validate_artifact_descriptor(
    *,
    descriptor: dict[str, object],
    export_directory: Path,
    required_suffix: str,
) -> None:
    """Validate one manifest artifact descriptor and its on-disk checksum.
    Restricts future file serving to regular export-directory filenames only.
    """
    if set(descriptor) != {"filename", "byte_size", "sha256"}:
        raise LibraryExportValidationError("Export artifact descriptor has unexpected fields.")
    filename = descriptor["filename"]
    byte_size = descriptor["byte_size"]
    checksum = descriptor["sha256"]
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not filename.startswith("libraries-")
        or not filename.endswith(required_suffix)
        or not isinstance(byte_size, int)
        or isinstance(byte_size, bool)
        or byte_size < 0
        or not isinstance(checksum, str)
        or len(checksum) != 64
    ):
        raise LibraryExportValidationError("Export artifact descriptor is invalid.")
    artifact_path = export_directory / filename
    try:
        actual_checksum, actual_byte_size = _hash_file(path=artifact_path)
    except OSError as exc:
        raise LibraryExportValidationError(
            f"Export artifact {filename} is unavailable."
        ) from exc
    if actual_checksum != checksum or actual_byte_size != byte_size:
        raise LibraryExportValidationError(
            f"Export artifact {filename} failed integrity validation."
        )


def _matches_active_export(
    *,
    active_manifest: dict[str, object] | None,
    schema_checksum: str,
    data_checksum: str,
) -> bool:
    """Return whether a valid active export has identical schema and data.
    Keeps daily checks from publishing duplicate immutable artifact pairs.
    """
    if active_manifest is None:
        return False
    export = active_manifest["export"]
    if not isinstance(export, dict):
        return False
    return (
        export["schema_checksum"] == schema_checksum
        and export["data_checksum"] == data_checksum
    )


def _active_geojson_filename(*, manifest: dict[str, object]) -> str:
    """Return the current GeoJSON filename from a validated manifest.
    Keeps command output tied to the active artifact after an unchanged run.
    """
    export = manifest["export"]
    if not isinstance(export, dict):
        raise LibraryExportValidationError("Export manifest has no export descriptor.")
    geojson = export["geojson"]
    if not isinstance(geojson, dict) or not isinstance(geojson.get("filename"), str):
        raise LibraryExportValidationError("Export manifest has no GeoJSON filename.")
    return geojson["filename"]


def _manifest_with_checked_at(
    *, manifest: dict[str, object], checked_at: datetime
) -> dict[str, object]:
    """Return an unchanged-export manifest with a fresh check timestamp.
    Preserves generated_at and immutable descriptors for delivery health checks.
    """
    updated_manifest = dict(manifest)
    updated_manifest["checked_at"] = _format_timestamp(value=checked_at)
    return updated_manifest


def _cleanup_temporary_files(*, export_directory: Path) -> None:
    """Remove stale candidate files once this invocation owns the lock.
    Clears interrupted writes without inspecting or altering published artifacts.
    """
    for path in export_directory.glob(".library-export-*.tmp"):
        _remove_file(path=path)


def _cleanup_retained_artifacts(
    *, export_directory: Path,
    active_manifest: dict[str, object],
) -> None:
    """Keep the active export and seven previous complete artifact pairs.
    Removes only recognized stale files after a successful manifest operation.
    """
    try:
        active_filename = _active_geojson_filename(manifest=active_manifest)
        active_version = active_filename.removesuffix(".geojson")
        versions: dict[str, set[Path]] = {}
        for path in export_directory.glob("libraries-*.geojson"):
            versions.setdefault(path.name.removesuffix(".geojson"), set()).add(path)
        for path in export_directory.glob("libraries-*.metadata.json"):
            versions.setdefault(path.name.removesuffix(".metadata.json"), set()).add(path)

        complete_versions = [
            version
            for version, paths in versions.items()
            if {
                export_directory / f"{version}.geojson",
                export_directory / f"{version}.metadata.json",
            }.issubset(paths)
        ]
        retained_versions = {active_version}
        retained_versions.update(
            version
            for version in sorted(complete_versions, reverse=True)
            if version != active_version
        )
        retained_versions = {active_version} | set(
            sorted(retained_versions - {active_version}, reverse=True)[
                :EXPORT_RETAIN_PREVIOUS
            ]
        )

        for version, paths in versions.items():
            if version in retained_versions and len(paths) == 2:
                continue
            for path in paths:
                _remove_file(path=path)
    except Exception:
        logger.warning("library_export_cleanup_warning", exc_info=True)


def _remove_file(*, path: Path) -> None:
    """Delete one known temporary or superseded export file when present.
    Limits cleanup to paths constructed inside the dedicated export directory.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("library_export_cleanup_warning", path=str(path), exc_info=True)
