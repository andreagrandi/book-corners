"""GeoJSON import logic for creating Library records from enriched exports.

Parses FeatureCollection data, maps OSM properties to Library fields,
and creates records with duplicate detection via external_id.
"""

from __future__ import annotations

import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D

from libraries.image_processing import build_library_photo_files
from libraries.management.commands.find_duplicates import _extract_street
from libraries.models import Library

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
IMAGE_FETCH_TIMEOUT = 10


@dataclass
class ImportCandidate:
    """A parsed GeoJSON feature ready for Library creation."""

    external_id: str
    name: str
    description: str
    longitude: float
    latitude: float
    address: str
    city: str
    country: str
    postal_code: str
    wheelchair_accessible: str
    capacity: int | None
    is_indoor: bool | None
    is_lit: bool | None
    website: str
    contact: str
    operator: str
    brand: str
    image_url: str


@dataclass
class ImportError:
    """A single import failure with its reason."""

    external_id: str
    reason: str


@dataclass
class ImportResult:
    """Summary of a GeoJSON import operation."""

    created: int = 0
    skipped_duplicate: int = 0
    skipped_duplicate_address: int = 0
    skipped_duplicate_location: int = 0
    skipped_missing_address: int = 0
    errors: list[ImportError] = field(default_factory=list)

    @property
    def total_skipped(self) -> int:
        """Return the total number of skipped features.
        Combines all skip reasons into a single count."""
        return (
            self.skipped_duplicate
            + self.skipped_duplicate_address
            + self.skipped_duplicate_location
            + self.skipped_missing_address
        )

    @property
    def total_errors(self) -> int:
        """Return the number of features that failed during import.
        Counts items in the errors list."""
        return len(self.errors)


def _normalize_for_dedup(value: str) -> str:
    """Normalize a string for duplicate comparison.
    Strips whitespace and lowercases to ignore trivial differences."""
    return value.strip().lower()


def _parse_bool(value: str | None) -> bool | None:
    """Convert OSM yes/no strings to Python booleans.
    Returns None for unrecognised or missing values."""
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def _parse_int(value: str | None) -> int | None:
    """Convert a numeric string to int, returning None on failure.
    Handles missing or non-numeric OSM property values."""
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _build_address(properties: dict[str, Any]) -> str:
    """Build a street address from OSM addr:* properties.
    Combines street and house number when both are present."""
    street = str(properties.get("addr:street") or "").strip()
    house_number = str(properties.get("addr:housenumber") or "").strip()
    if street and house_number:
        return f"{street} {house_number}"
    return street


def parse_geojson(geojson_data: dict[str, Any]) -> list[ImportCandidate]:
    """Parse a GeoJSON FeatureCollection into ImportCandidate objects.
    Extracts and maps OSM properties to Library-compatible fields."""
    features = geojson_data.get("features", [])
    candidates = []

    for feature in features:
        geometry = feature.get("geometry", {})
        coords = geometry.get("coordinates", [])
        if len(coords) < 2:
            continue

        properties = feature.get("properties", {})
        longitude, latitude = float(coords[0]), float(coords[1])

        website = (
            str(properties.get("website") or "").strip()
            or str(properties.get("contact:website") or "").strip()
        )

        contact_parts = []
        phone = str(properties.get("phone") or "").strip()
        email = str(properties.get("email") or "").strip()
        if phone:
            contact_parts.append(phone)
        if email:
            contact_parts.append(email)

        country_raw = str(properties.get("addr:country") or "").strip()
        country = country_raw.upper()[:2] if country_raw else ""

        external_id_raw = (
            properties.get("@id")
            or feature.get("id")
            or properties.get("id")
            or ""
        )

        candidate = ImportCandidate(
            external_id=str(external_id_raw).strip(),
            name=str(properties.get("name") or "").strip(),
            description=str(properties.get("description") or "").strip(),
            longitude=longitude,
            latitude=latitude,
            address=_build_address(properties),
            city=str(properties.get("addr:city") or "").strip(),
            country=country,
            postal_code=str(properties.get("addr:postcode") or "").strip(),
            wheelchair_accessible=str(properties.get("wheelchair") or "").strip().lower(),
            capacity=_parse_int(properties.get("capacity")),
            is_indoor=_parse_bool(properties.get("indoor")),
            is_lit=_parse_bool(properties.get("lit")),
            website=website,
            contact=", ".join(contact_parts),
            operator=str(properties.get("operator") or "").strip(),
            brand=str(properties.get("brand") or "").strip(),
            image_url=str(properties.get("image") or "").strip(),
        )
        candidates.append(candidate)

    return candidates


def fetch_image_from_url(url: str, *, timeout: int = IMAGE_FETCH_TIMEOUT, max_bytes: int = MAX_IMAGE_BYTES) -> bytes | None:
    """Download an image from a URL with size guard.
    Returns None on any failure to keep imports resilient."""
    if not url:
        return None

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "book-corners/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                logger.warning("Image too large (>%d bytes): %s", max_bytes, url)
                return None
            return data
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        logger.warning("Failed to fetch image %s: %s", url, exc)
        return None


class GeoJSONImporter:
    """Orchestrates creating Library records from parsed GeoJSON candidates."""

    PROXIMITY_METERS = 100

    def __init__(self, *, source: str, status: str, created_by):
        """Initialise the importer with shared field values.
        All created libraries share the same source, status, and creator."""
        self.source = source
        self.status = status
        self.created_by = created_by

    def _load_existing_address_pairs(self) -> set[tuple[str, str]]:
        """Load all normalized (city, address) pairs from the database.
        Used for fast address-based duplicate detection."""
        return {
            (_normalize_for_dedup(city), _normalize_for_dedup(address))
            for city, address in Library.objects.values_list("city", "address")
        }

    def _has_nearby_library(self, point: Point, address: str) -> bool:
        """Check whether a library on the same street exists within proximity.
        Compares street names so nearby libraries on different streets are not flagged."""
        candidate_street = _extract_street(address)
        nearby_addresses = Library.objects.filter(
            location__distance_lte=(point, D(m=self.PROXIMITY_METERS))
        ).values_list("address", flat=True)
        return any(
            _extract_street(addr) == candidate_street for addr in nearby_addresses
        )

    def run(self, candidates: list[ImportCandidate]) -> ImportResult:
        """Process all candidates and return a structured import result.
        Creates Library records, skipping duplicates and incomplete features."""
        result = ImportResult()

        existing_external_ids = set(
            Library.objects.filter(
                external_id__in=[c.external_id for c in candidates if c.external_id],
            ).values_list("external_id", flat=True)
        )

        existing_address_pairs = self._load_existing_address_pairs()

        for candidate in candidates:
            if candidate.external_id and candidate.external_id in existing_external_ids:
                result.skipped_duplicate += 1
                continue

            if not candidate.address or not candidate.city or not candidate.country:
                result.skipped_missing_address += 1
                continue

            address_pair = (
                _normalize_for_dedup(candidate.city),
                _normalize_for_dedup(candidate.address),
            )
            if address_pair in existing_address_pairs:
                result.skipped_duplicate_address += 1
                continue

            point = Point(x=candidate.longitude, y=candidate.latitude, srid=4326)
            if self._has_nearby_library(point, address=candidate.address):
                result.skipped_duplicate_location += 1
                continue

            try:
                self._create_library(candidate=candidate)
                result.created += 1
                existing_address_pairs.add(address_pair)
            except Exception as exc:
                logger.exception("Failed to create library for %s", candidate.external_id)
                result.errors.append(
                    ImportError(external_id=candidate.external_id, reason=str(exc))
                )

        return result

    def _create_library(self, *, candidate: ImportCandidate) -> Library:
        """Create a single Library record from an ImportCandidate.
        Fetches and attaches the photo when an image URL is available."""
        wheelchair = candidate.wheelchair_accessible
        if wheelchair not in ("yes", "no", "limited"):
            wheelchair = ""

        library = Library(
            name=candidate.name,
            description=candidate.description,
            location=Point(x=candidate.longitude, y=candidate.latitude, srid=4326),
            address=candidate.address,
            city=candidate.city,
            country=candidate.country,
            postal_code=candidate.postal_code,
            wheelchair_accessible=wheelchair,
            capacity=candidate.capacity,
            is_indoor=candidate.is_indoor,
            is_lit=candidate.is_lit,
            website=candidate.website,
            contact=candidate.contact,
            source=self.source,
            operator=candidate.operator,
            brand=candidate.brand,
            external_id=candidate.external_id,
            status=self.status,
            created_by=self.created_by,
        )

        if candidate.image_url:
            image_data = fetch_image_from_url(candidate.image_url)
            if image_data:
                try:
                    image_file = BytesIO(image_data)
                    main_image, thumbnail_image = build_library_photo_files(
                        image_file=image_file,
                        original_name=candidate.image_url.rsplit("/", maxsplit=1)[-1] or "import.jpg",
                    )
                    main_filename, main_content = main_image
                    thumbnail_filename, thumbnail_content = thumbnail_image
                    library.photo.save(main_filename, main_content, save=False)
                    library.photo_thumbnail.save(thumbnail_filename, thumbnail_content, save=False)
                except (ValueError, OSError) as exc:
                    logger.warning("Image processing failed for %s: %s", candidate.external_id, exc)

        library.save()
        return library
