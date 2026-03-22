from __future__ import annotations

from datetime import datetime
from enum import Enum

from ninja import Schema
from pydantic import Field

from libraries.models import Library, Report


class PaginationMeta(Schema):
    """Pagination metadata for paginated list responses.
    Provides page navigation context alongside result items."""

    page: int = Field(description="Current page number (1-indexed).", examples=[1])
    page_size: int = Field(description="Number of items per page.", examples=[20])
    total: int = Field(description="Total number of matching items.", examples=[142])
    total_pages: int = Field(description="Total number of pages.", examples=[8])
    has_next: bool = Field(description="Whether a next page exists.", examples=[True])
    has_previous: bool = Field(description="Whether a previous page exists.", examples=[False])


class LibraryOut(Schema):
    """Serialized representation of an approved library.
    Resolves geospatial and media fields into flat JSON values."""

    id: int = Field(description="Unique library identifier.", examples=[42])
    slug: str = Field(description="URL-friendly unique slug.", examples=["berlin-friedrichstr-12-corner-books"])
    name: str = Field(description="Display name of the library.", examples=["Corner Books"])
    description: str = Field(description="Free-text description of the library.", examples=["A cozy little free library near the park entrance."])
    photo_url: str = Field(description="Full-size photo URL, or empty string if unavailable.", examples=["/media/libraries/photos/corner-books.jpg"])
    thumbnail_url: str = Field(description="Thumbnail photo URL, or empty string if unavailable.", examples=["/media/libraries/thumbnails/corner-books.jpg"])
    lat: float = Field(description="Latitude of the library location (WGS 84).", examples=[52.5200])
    lng: float = Field(description="Longitude of the library location (WGS 84).", examples=[13.4050])
    address: str = Field(description="Street address.", examples=["Friedrichstr. 12"])
    city: str = Field(description="City name.", examples=["Berlin"])
    country: str = Field(description="ISO 3166-1 alpha-2 country code.", examples=["DE"])
    postal_code: str = Field(description="Postal or ZIP code.", examples=["10117"])
    wheelchair_accessible: str = Field(description="Wheelchair accessibility: yes, no, limited, or empty.", examples=["yes"])
    capacity: int | None = Field(description="Approximate book capacity.", examples=[50])
    is_indoor: bool | None = Field(description="Whether the library is inside a building.", examples=[False])
    is_lit: bool | None = Field(description="Whether the library is illuminated at night.", examples=[True])
    website: str = Field(description="External website link.", examples=["https://littlefreelibrary.org/charter/12345"])
    contact: str = Field(description="Contact information (email, phone, etc.).", examples=["info@example.org"])
    source: str = Field(description="Data origin, e.g. OpenStreetMap.", examples=["OpenStreetMap"])
    operator: str = Field(description="Organisation that maintains the library.", examples=["City Library Association"])
    brand: str = Field(description="Network or brand name.", examples=["Little Free Library"])
    created_at: datetime = Field(description="Timestamp when the library was created (UTC).", examples=["2025-06-15T14:30:00Z"])

    @staticmethod
    def resolve_photo_url(obj: Library) -> str:
        """Return the main photo URL or empty string.
        Handles missing uploads without raising errors."""
        if obj.photo:
            try:
                return obj.photo.url
            except ValueError:
                return ""
        return ""

    @staticmethod
    def resolve_thumbnail_url(obj: Library) -> str:
        """Return the thumbnail URL or empty string.
        Falls back gracefully for libraries without thumbnails."""
        if obj.photo_thumbnail:
            try:
                return obj.photo_thumbnail.url
            except ValueError:
                return ""
        return ""

    @staticmethod
    def resolve_lat(obj: Library) -> float:
        """Extract latitude from the PostGIS PointField.
        Returns the Y coordinate of the stored geometry."""
        return obj.location.y

    @staticmethod
    def resolve_lng(obj: Library) -> float:
        """Extract longitude from the PostGIS PointField.
        Returns the X coordinate of the stored geometry."""
        return obj.location.x


class LibraryListOut(Schema):
    """Paginated list of libraries with navigation metadata.
    Wraps items and pagination in a single response envelope."""

    items: list[LibraryOut] = Field(description="List of library objects for the current page.")
    pagination: PaginationMeta = Field(description="Pagination metadata for navigating the result set.")


class LatestLibrariesOut(Schema):
    """Flat list of latest approved libraries without pagination.
    Used by the /latest endpoint for lightweight newest-first results."""

    items: list[LibraryOut] = Field(description="List of the most recently approved libraries.")


class LibrarySearchParams(Schema):
    """Query parameters for searching and filtering libraries.
    Validates bounds and defaults for pagination and geospatial queries."""

    q: str | None = Field(default=None, max_length=200, description="Free-text search query matched against name and description.", json_schema_extra={"example": "corner books"})
    city: str | None = Field(default=None, max_length=100, description="Filter by exact city name (case-insensitive).", json_schema_extra={"example": "Berlin"})
    country: str | None = Field(default=None, max_length=2, description="Filter by ISO 3166-1 alpha-2 country code.", json_schema_extra={"example": "DE"})
    postal_code: str | None = Field(default=None, max_length=20, description="Filter by postal or ZIP code.", json_schema_extra={"example": "10117"})
    lat: float | None = Field(default=None, ge=-90, le=90, description="Latitude for proximity search (requires lng and radius_km).", json_schema_extra={"example": 52.52})
    lng: float | None = Field(default=None, ge=-180, le=180, description="Longitude for proximity search (requires lat and radius_km).", json_schema_extra={"example": 13.405})
    radius_km: int | None = Field(default=None, ge=1, le=100, description="Search radius in kilometres (requires lat and lng).", json_schema_extra={"example": 5})
    has_photo: bool | None = Field(default=None, description="Filter by photo presence: true for libraries with a photo, false for those without.")
    page: int = Field(default=1, ge=1, le=1000, description="Page number to retrieve (1-indexed).", json_schema_extra={"example": 1})
    page_size: int = Field(default=20, ge=1, le=50, description="Number of items per page.", json_schema_extra={"example": 20})


class LibrarySubmitIn(Schema):
    """Input schema for submitting a new library.
    Captures location and descriptive fields from authenticated users."""

    name: str = Field(default="", max_length=255, description="Display name of the library.", examples=["Corner Books"])
    description: str = Field(default="", max_length=2000, description="Free-text description of the library.", examples=["A cozy little free library near the park entrance."])
    address: str = Field(max_length=255, description="Street address of the library.", examples=["Friedrichstr. 12"])
    city: str = Field(max_length=100, description="City where the library is located.", examples=["Berlin"])
    country: str = Field(max_length=2, description="ISO 3166-1 alpha-2 country code.", examples=["DE"])
    postal_code: str = Field(default="", max_length=20, description="Postal or ZIP code.", examples=["10117"])
    wheelchair_accessible: str = Field(default="", max_length=10, description="Wheelchair accessibility: yes, no, or limited.", examples=["yes"])
    capacity: int | None = Field(default=None, ge=0, description="Approximate book capacity.", examples=[50])
    is_indoor: bool | None = Field(default=None, description="Whether the library is inside a building.", examples=[False])
    is_lit: bool | None = Field(default=None, description="Whether the library is illuminated at night.", examples=[True])
    website: str = Field(default="", max_length=500, description="External website link.", examples=["https://littlefreelibrary.org/charter/12345"])
    contact: str = Field(default="", max_length=255, description="Contact information (email, phone, etc.).", examples=["info@example.org"])
    operator: str = Field(default="", max_length=255, description="Organisation that maintains the library.", examples=["City Library Association"])
    brand: str = Field(default="", max_length=255, description="Network or brand name.", examples=["Little Free Library"])
    latitude: float = Field(ge=-90, le=90, description="Latitude of the library (WGS 84).", examples=[52.5200])
    longitude: float = Field(ge=-180, le=180, description="Longitude of the library (WGS 84).", examples=[13.4050])


class ReportReasonEnum(str, Enum):
    """Enumeration of valid report reasons matching model choices.
    Keeps API and database reason values in sync."""

    DAMAGED = Report.Reason.DAMAGED
    MISSING = Report.Reason.MISSING
    INCORRECT_INFO = Report.Reason.INCORRECT_INFO
    INAPPROPRIATE = Report.Reason.INAPPROPRIATE
    OTHER = Report.Reason.OTHER


class ReportIn(Schema):
    """Input schema for submitting a report about a library.
    Requires a reason category and optional narrative details."""

    reason: ReportReasonEnum = Field(description="Category of the issue being reported.", examples=["damaged"])
    details: str = Field(default="", max_length=2000, description="Optional free-text details about the issue.", examples=["The door hinge is broken and books are getting wet."])


class ReportOut(Schema):
    """Serialized representation of a submitted report.
    Returns confirmation data after successful report creation."""

    id: int = Field(description="Unique report identifier.", examples=[7])
    reason: str = Field(description="Reason category of the report.", examples=["damaged"])
    created_at: datetime = Field(description="Timestamp when the report was created (UTC).", examples=["2025-06-15T14:30:00Z"])


class LibraryPhotoIn(Schema):
    """Input schema for submitting a community photo to a library.
    Captures an optional caption alongside the uploaded image file."""

    caption: str = Field(default="", max_length=200, description="Optional caption for the photo.", examples=["A sunny day at the library."])


class LibraryPhotoOut(Schema):
    """Serialized representation of a submitted community photo.
    Returns confirmation data after successful photo submission."""

    id: int = Field(description="Unique photo identifier.", examples=[12])
    caption: str = Field(description="Caption for the photo.", examples=["A sunny day at the library."])
    status: str = Field(description="Moderation status of the photo.", examples=["pending"])
    created_at: datetime = Field(description="Timestamp when the photo was submitted (UTC).", examples=["2025-06-15T14:30:00Z"])


class CountryStatOut(Schema):
    """Statistics for a single country in the top-countries ranking.
    Includes display-friendly name and flag emoji."""

    country_code: str = Field(description="ISO 3166-1 alpha-2 country code.", examples=["DE"])
    country_name: str = Field(description="Human-readable country name.", examples=["Germany"])
    flag_emoji: str = Field(description="Unicode flag emoji for the country.", examples=["\U0001F1E9\U0001F1EA"])
    count: int = Field(description="Number of approved libraries in this country.", examples=[42])


class TimeSeriesPointOut(Schema):
    """A single data point in the cumulative growth time series.
    Represents the running total of approved libraries at a given period."""

    period: str = Field(description="Date or month label (YYYY-MM-DD or YYYY-MM-DD for month start).", examples=["2025-06-15"])
    cumulative_count: int = Field(description="Running total of approved libraries up to this period.", examples=[128])


class CountryListOut(Schema):
    """List of all countries that have at least one approved library.
    Wraps country statistics in a flat items list."""

    items: list[CountryStatOut] = Field(description="All countries with approved libraries, ordered by count descending.")


class StatisticsOut(Schema):
    """Aggregate platform statistics for approved libraries.
    Includes totals, geographic breakdown, and growth over time."""

    total_approved: int = Field(description="Total number of approved libraries.", examples=[350])
    total_with_image: int = Field(description="Number of approved libraries with at least one photo.", examples=[280])
    top_countries: list[CountryStatOut] = Field(description="Top 10 countries by number of approved libraries.")
    cumulative_series: list[TimeSeriesPointOut] = Field(description="Cumulative growth time series of approved libraries.")
    granularity: str = Field(description="Time series granularity: 'daily' or 'monthly'.", examples=["monthly"])
