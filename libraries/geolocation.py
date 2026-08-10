from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Sequence
from functools import partial
from typing import Any

from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
from geopy.adapters import RequestsAdapter
from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim
from PIL import ExifTags, Image, UnidentifiedImageError

FORWARD_GEOCODE_CACHE_TIMEOUT_SECONDS = 60 * 60 * 6
FORWARD_GEOCODE_FAILURE_CACHE_TIMEOUT_SECONDS = 60
FORWARD_GEOCODE_LOCK_TIMEOUT_PADDING_SECONDS = 1
FORWARD_GEOCODE_WAIT_INTERVAL_SECONDS = 0.25
_FORWARD_GEOCODE_FAILURE_CACHE_VALUE = "unresolved"


def _normalize_gps_reference(value: Any) -> str:
    """Normalize EXIF GPS reference values to uppercase strings.
    Ensures hemisphere markers are comparable during coordinate parsing."""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8").strip().upper()
        except UnicodeDecodeError:
            return ""

    return str(value).strip().upper()


def _build_forward_geocode_cache_key(*, place_query: str, country_code: str | None) -> str:
    """Build a stable cache key for forward geocoding lookups.
    Keeps repeated place searches fast while respecting rate limits."""
    normalized_query = " ".join(place_query.split()).casefold()
    normalized_country = (country_code or "").strip().casefold()
    lookup_identity = f"{normalized_country}:{normalized_query}"
    lookup_digest = hashlib.sha256(lookup_identity.encode("utf-8")).hexdigest()
    return f"forward-geocode:{lookup_digest}"


def _get_cached_forward_geocode_result(
    *, cache_key: str
) -> tuple[bool, tuple[float, float] | None]:
    """Read a successful or failed forward-geocode cache entry.
    Distinguishes a cached failure from a cache miss for single-flight callers."""
    cached_value = cache.get(cache_key)
    if cached_value == _FORWARD_GEOCODE_FAILURE_CACHE_VALUE:
        return True, None
    if (
        isinstance(cached_value, tuple)
        and len(cached_value) == 2
        and all(isinstance(value, float) for value in cached_value)
    ):
        return True, cached_value
    return False, None


def _cache_forward_geocode_failure(*, cache_key: str) -> None:
    """Cache an unresolved forward-geocode result for a short period.
    Prevents immediate retries while preserving the caller's keyword fallback."""
    cache.set(
        cache_key,
        _FORWARD_GEOCODE_FAILURE_CACHE_VALUE,
        FORWARD_GEOCODE_FAILURE_CACHE_TIMEOUT_SECONDS,
    )


def _wait_for_forward_geocode_result(
    *, cache_key: str, timeout_seconds: int
) -> tuple[float, float] | None:
    """Wait for the active forward-geocode caller to publish its result.
    Returns None if the single-flight window ends without a cached outcome."""
    wait_timeout_seconds = max(
        timeout_seconds + FORWARD_GEOCODE_LOCK_TIMEOUT_PADDING_SECONDS,
        FORWARD_GEOCODE_LOCK_TIMEOUT_PADDING_SECONDS,
    )
    deadline = time.monotonic() + wait_timeout_seconds

    while time.monotonic() < deadline:
        cache_hit, cached_result = _get_cached_forward_geocode_result(
            cache_key=cache_key
        )
        if cache_hit:
            return cached_result
        time.sleep(FORWARD_GEOCODE_WAIT_INTERVAL_SECONDS)

    cache_hit, cached_result = _get_cached_forward_geocode_result(
        cache_key=cache_key
    )
    return cached_result if cache_hit else None


def _dms_to_decimal(values: Sequence[Any], reference: str) -> float | None:
    """Convert EXIF DMS coordinate tuples into decimal degrees.
    Returns None when values are malformed or the GPS reference is unknown."""
    if len(values) != 3:
        return None

    try:
        degrees = float(values[0])
        minutes = float(values[1])
        seconds = float(values[2])
    except (TypeError, ValueError, ZeroDivisionError):
        return None

    decimal = degrees + (minutes / 60) + (seconds / 3600)
    if not math.isfinite(decimal):
        return None

    if reference in ("S", "W"):
        return -decimal
    if reference in ("N", "E"):
        return decimal
    return None


def forward_geocode_place(
    *,
    place_query: str,
    user_agent: str,
    timeout_seconds: int,
    country_code: str | None = None,
) -> tuple[float, float] | None:
    """Forward geocode a place string into latitude and longitude.
    Returns None when no usable coordinates are resolved."""
    normalized_query = " ".join(place_query.split())
    if not normalized_query:
        return None

    cache_key = _build_forward_geocode_cache_key(
        place_query=normalized_query,
        country_code=country_code,
    )
    cache_hit, cached_result = _get_cached_forward_geocode_result(
        cache_key=cache_key
    )
    if cache_hit:
        return cached_result

    lock_timeout_seconds = max(
        timeout_seconds + FORWARD_GEOCODE_LOCK_TIMEOUT_PADDING_SECONDS,
        FORWARD_GEOCODE_LOCK_TIMEOUT_PADDING_SECONDS,
    )
    lock_key = f"{cache_key}:lock"
    if not cache.add(lock_key, True, lock_timeout_seconds):
        return _wait_for_forward_geocode_result(
            cache_key=cache_key,
            timeout_seconds=timeout_seconds,
        )

    geolocator = Nominatim(
        user_agent=user_agent,
        timeout=timeout_seconds,
        adapter_factory=partial(RequestsAdapter, max_retries=0),
    )
    geocode_kwargs: dict[str, Any] = {
        "exactly_one": True,
        "language": "en",
        "addressdetails": False,
    }

    normalized_country = (country_code or "").strip().lower()
    if normalized_country:
        geocode_kwargs["country_codes"] = normalized_country

    try:
        location = geolocator.geocode(normalized_query, **geocode_kwargs)
    except (GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable, ValueError):
        _cache_forward_geocode_failure(cache_key=cache_key)
        return None

    if location is None:
        _cache_forward_geocode_failure(cache_key=cache_key)
        return None

    latitude = getattr(location, "latitude", None)
    longitude = getattr(location, "longitude", None)
    if not isinstance(latitude, (float, int)) or not isinstance(longitude, (float, int)):
        _cache_forward_geocode_failure(cache_key=cache_key)
        return None

    coordinates = (float(latitude), float(longitude))
    cache.set(cache_key, coordinates, FORWARD_GEOCODE_CACHE_TIMEOUT_SECONDS)
    return coordinates


def extract_gps_coordinates(image_file: UploadedFile) -> tuple[float, float] | None:
    """Extract latitude and longitude from image EXIF GPS metadata.
    Returns None when GPS tags are missing or coordinates cannot be parsed."""
    start_position: int | None = None
    if hasattr(image_file, "tell"):
        try:
            start_position = image_file.tell()
        except (OSError, ValueError):
            start_position = None

    try:
        if hasattr(image_file, "seek"):
            image_file.seek(0)

        with Image.open(image_file) as image:
            exif_data = image.getexif()
            if not exif_data:
                return None

            try:
                gps_data = exif_data.get_ifd(ExifTags.IFD.GPSInfo)
            except KeyError:
                return None

            if not gps_data:
                return None

            latitude_values = gps_data.get(ExifTags.GPS.GPSLatitude)
            latitude_reference = _normalize_gps_reference(gps_data.get(ExifTags.GPS.GPSLatitudeRef))
            longitude_values = gps_data.get(ExifTags.GPS.GPSLongitude)
            longitude_reference = _normalize_gps_reference(gps_data.get(ExifTags.GPS.GPSLongitudeRef))

            if not latitude_values or not longitude_values:
                return None

            latitude = _dms_to_decimal(latitude_values, latitude_reference)
            longitude = _dms_to_decimal(longitude_values, longitude_reference)
            if latitude is None or longitude is None:
                return None

            return latitude, longitude
    except (UnidentifiedImageError, OSError):
        return None
    finally:
        if start_position is not None and hasattr(image_file, "seek"):
            try:
                image_file.seek(start_position)
            except (OSError, ValueError):
                pass


def _build_street_address(address: dict[str, Any]) -> str:
    """Build a street address from Nominatim address fragments.
    Prefers road plus house number and falls back to road-only values."""
    street_value = (
        address.get("road")
        or address.get("pedestrian")
        or address.get("residential")
        or address.get("footway")
        or address.get("path")
        or ""
    )
    house_number_value = address.get("house_number") or ""

    street = str(street_value).strip()
    house_number = str(house_number_value).strip()

    if street and house_number:
        return f"{street} {house_number}"
    return street


def _extract_city(address: dict[str, Any]) -> str:
    """Pick the best city-like locality from reverse-geocode payloads.
    Tries common locality keys in descending order of specificity."""
    for key in ("city", "town", "village", "hamlet", "municipality", "suburb", "county"):
        value = address.get(key)
        if value:
            return str(value).strip()
    return ""


def reverse_geocode_coordinates(
    *,
    latitude: float,
    longitude: float,
    user_agent: str,
    timeout_seconds: int,
) -> dict[str, str] | None:
    """Reverse geocode coordinates into submit-form address fields.
    Returns normalized address, city, country code, and postal code."""
    geolocator = Nominatim(user_agent=user_agent, timeout=timeout_seconds)

    try:
        location = geolocator.reverse(
            (latitude, longitude),
            exactly_one=True,
            language="en",
            addressdetails=True,
        )
    except (GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable, ValueError):
        return None

    if location is None:
        return None

    raw_data = getattr(location, "raw", None)
    if not isinstance(raw_data, dict):
        return None

    address_data = raw_data.get("address")
    if not isinstance(address_data, dict):
        return None

    street_address = _build_street_address(address_data)
    if not street_address:
        display_name = str(raw_data.get("display_name") or "").strip()
        if display_name:
            street_address = display_name.split(",", maxsplit=1)[0].strip()

    country_code = str(address_data.get("country_code") or "").strip().upper()

    return {
        "address": street_address,
        "city": _extract_city(address_data),
        "country": country_code,
        "postal_code": str(address_data.get("postcode") or "").strip(),
    }
