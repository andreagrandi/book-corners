from __future__ import annotations

from datetime import datetime
from enum import Enum

from ninja import Schema
from pydantic import Field

from libraries.models import Library, Report


class PaginationMeta(Schema):
    """Pagination metadata for paginated list responses.
    Provides page navigation context alongside result items."""

    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool
    has_previous: bool


class LibraryOut(Schema):
    """Serialized representation of an approved library.
    Resolves geospatial and media fields into flat JSON values."""

    id: int
    slug: str
    name: str
    description: str
    photo_url: str
    thumbnail_url: str
    lat: float
    lng: float
    address: str
    city: str
    country: str
    postal_code: str
    created_at: datetime

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

    items: list[LibraryOut]
    pagination: PaginationMeta


class LatestLibrariesOut(Schema):
    """Flat list of latest approved libraries without pagination.
    Used by the /latest endpoint for lightweight newest-first results."""

    items: list[LibraryOut]


class LibrarySearchParams(Schema):
    """Query parameters for searching and filtering libraries.
    Validates bounds and defaults for pagination and geospatial queries."""

    q: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=2)
    postal_code: str | None = Field(default=None, max_length=20)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    radius_km: int | None = Field(default=None, ge=1, le=100)
    page: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=50)


class LibrarySubmitIn(Schema):
    """Input schema for submitting a new library.
    Captures location and descriptive fields from authenticated users."""

    name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=2000)
    address: str = Field(max_length=255)
    city: str = Field(max_length=100)
    country: str = Field(max_length=2)
    postal_code: str = Field(default="", max_length=20)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


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

    reason: ReportReasonEnum
    details: str = Field(default="", max_length=2000)


class ReportOut(Schema):
    """Serialized representation of a submitted report.
    Returns confirmation data after successful report creation."""

    id: int
    reason: str
    created_at: datetime
