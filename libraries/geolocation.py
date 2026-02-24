from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.core.files.uploadedfile import UploadedFile
from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim
from PIL import ExifTags, Image, UnidentifiedImageError


def _normalize_gps_reference(value: Any) -> str:
    """Normalize EXIF GPS reference values to uppercase strings.
    Ensures hemisphere markers are comparable during coordinate parsing."""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8").strip().upper()
        except UnicodeDecodeError:
            return ""

    return str(value).strip().upper()


def _dms_to_decimal(values: Sequence[Any], reference: str) -> float | None:
    """Convert EXIF DMS coordinate tuples into decimal degrees.
    Returns None when values are malformed or the GPS reference is unknown."""
    if len(values) != 3:
        return None

    try:
        degrees = float(values[0])
        minutes = float(values[1])
        seconds = float(values[2])
    except (TypeError, ValueError):
        return None

    decimal = degrees + (minutes / 60) + (seconds / 3600)
    if reference in ("S", "W"):
        return -decimal
    if reference in ("N", "E"):
        return decimal
    return None


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
