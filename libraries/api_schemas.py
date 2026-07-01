from __future__ import annotations

from datetime import datetime
from enum import Enum

from ninja import Schema
from pydantic import Field

from libraries.models import Library, LibraryPhoto, Report


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
    address: str = Field(description="Street address, or empty string when the library has no street address (e.g. inside a park).", examples=["Friedrichstr. 12"])
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
    is_favourited: bool = Field(default=False, description="Whether the current authenticated user has favourited this library. Always false for unauthenticated requests.", examples=[False])

    @staticmethod
    def resolve_is_favourited(obj: Library) -> bool:
        """Return whether the current user has favourited this library.
        Reads from a queryset annotation; defaults to False for anonymous requests."""
        return getattr(obj, "_is_favourited", False)

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


class ModerationUserOut(Schema):
    """Compact user representation for staff moderation responses.
    Identifies submitters and reporters without exposing account management data."""

    id: int = Field(description="Unique user identifier.", examples=[1])
    username: str = Field(description="Username.", examples=["janedoe"])


class LibraryModerationSummaryOut(Schema):
    """Compact library representation nested in moderation responses.
    Gives staff clients enough context without duplicating full library payloads."""

    id: int = Field(description="Unique library identifier.", examples=[42])
    slug: str = Field(description="URL-friendly unique slug.", examples=["berlin-friedrichstr-12-corner-books"])
    name: str = Field(description="Display name of the library.", examples=["Corner Books"])
    address: str = Field(description="Street address, or empty string when unavailable.", examples=["Friedrichstr. 12"])
    city: str = Field(description="City name.", examples=["Berlin"])
    country: str = Field(description="ISO 3166-1 alpha-2 country code.", examples=["DE"])
    status: str = Field(description="Current moderation status of the library.", examples=["approved"])


class ModerationStatusFilterEnum(str, Enum):
    """Shared moderation status filter values.
    Supports listing all items or one concrete moderation status."""

    ALL = "all"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class LibraryModerationStatusEnum(str, Enum):
    """Enumeration of staff-selectable library moderation statuses.
    Keeps API payload values aligned with model choices."""

    PENDING = Library.Status.PENDING
    APPROVED = Library.Status.APPROVED
    REJECTED = Library.Status.REJECTED


class LibraryModerationOut(LibraryOut):
    """Serialized library representation for staff moderation.
    Adds moderation-only fields to the public library payload."""

    status: str = Field(description="Current moderation status of the library.", examples=["pending"])
    rejection_reason: str = Field(description="Reason shown to the submitter when rejected.", examples=["Photo does not show a book corner."])
    created_by: ModerationUserOut | None = Field(default=None, description="User who submitted the library, or null for imported data.")


class LibraryModerationListOut(Schema):
    """Paginated list of libraries for staff moderation.
    Wraps matching library submissions and pagination metadata for admin clients."""

    items: list[LibraryModerationOut] = Field(description="Library submissions for the current page.")
    pagination: PaginationMeta = Field(description="Pagination metadata for navigating the moderation queue.")


class LibraryModerationParams(Schema):
    """Query parameters for filtering and paginating library moderation.
    Keeps the staff queue bounded while supporting common admin filters."""

    status: ModerationStatusFilterEnum = Field(default=ModerationStatusFilterEnum.ALL, description="Filter by moderation status, or all.")
    q: str | None = Field(default=None, max_length=200, description="Search name, address, or city.", json_schema_extra={"example": "Berlin"})
    country: str | None = Field(default=None, max_length=2, description="Filter by ISO 3166-1 alpha-2 country code.", json_schema_extra={"example": "DE"})
    source: str | None = Field(default=None, max_length=100, description="Filter by data source.", json_schema_extra={"example": "OpenStreetMap"})
    page: int = Field(default=1, ge=1, le=1000, description="Page number to retrieve (1-indexed).", json_schema_extra={"example": 1})
    page_size: int = Field(default=20, ge=1, le=50, description="Number of submissions per page.", json_schema_extra={"example": 20})


class LibraryModerationUpdateIn(Schema):
    """Payload for updating a library moderation status.
    Staff clients send the target status and optional rejection reason."""

    status: LibraryModerationStatusEnum = Field(description="New moderation status: pending, approved, or rejected.", examples=["approved"])
    rejection_reason: str = Field(default="", max_length=2000, description="Optional reason stored when rejecting the library.", examples=["Duplicate submission."])


class ReportStatusFilterEnum(str, Enum):
    """Report status filter values for staff moderation.
    Supports listing all reports or one concrete report status."""

    ALL = "all"
    OPEN = Report.Status.OPEN
    RESOLVED = Report.Status.RESOLVED
    DISMISSED = Report.Status.DISMISSED


class ReportReasonFilterEnum(str, Enum):
    """Report reason filter values for staff moderation.
    Supports listing all reasons or one concrete report reason."""

    ALL = "all"
    DAMAGED = Report.Reason.DAMAGED
    MISSING = Report.Reason.MISSING
    INCORRECT_INFO = Report.Reason.INCORRECT_INFO
    INAPPROPRIATE = Report.Reason.INAPPROPRIATE
    OTHER = Report.Reason.OTHER


class ReportModerationStatusEnum(str, Enum):
    """Staff-selectable report moderation statuses.
    Matches the report model's status choices."""

    OPEN = Report.Status.OPEN
    RESOLVED = Report.Status.RESOLVED
    DISMISSED = Report.Status.DISMISSED


class ReportModerationParams(Schema):
    """Query parameters for staff report moderation lists.
    Supports status, reason, and pagination filters."""

    status: ReportStatusFilterEnum = Field(default=ReportStatusFilterEnum.ALL, description="Filter by report status, or all.")
    reason: ReportReasonFilterEnum = Field(default=ReportReasonFilterEnum.ALL, description="Filter by report reason, or all.")
    page: int = Field(default=1, ge=1, le=1000, description="Page number to retrieve (1-indexed).", json_schema_extra={"example": 1})
    page_size: int = Field(default=20, ge=1, le=50, description="Number of reports per page.", json_schema_extra={"example": 20})


class ReportModerationUpdateIn(Schema):
    """Payload for updating a report moderation status.
    Staff clients can reopen, resolve, or dismiss reports."""

    status: ReportModerationStatusEnum = Field(description="New report status: open, resolved, or dismissed.", examples=["resolved"])


class ReportModerationOut(Schema):
    """Serialized user report for staff moderation.
    Includes reporter, library, details, and optional evidence photo."""

    id: int = Field(description="Unique report identifier.", examples=[7])
    library: LibraryModerationSummaryOut = Field(description="Library the report is about.")
    created_by: ModerationUserOut | None = Field(default=None, description="User who submitted the report, or null when unavailable.")
    reason: str = Field(description="Reason category of the report.", examples=["damaged"])
    details: str = Field(description="Free-text details submitted by the reporter.", examples=["The door hinge is broken."])
    photo_url: str = Field(description="Evidence photo URL, or empty string if unavailable.", examples=["/media/reports/photos/report.jpg"])
    status: str = Field(description="Current report moderation status.", examples=["open"])
    created_at: datetime = Field(description="Timestamp when the report was created (UTC).", examples=["2025-06-15T14:30:00Z"])

    @staticmethod
    def resolve_photo_url(obj: Report) -> str:
        """Return the report photo URL or empty string.
        Handles reports without uploaded evidence photos gracefully."""
        if obj.photo:
            try:
                return obj.photo.url
            except ValueError:
                return ""
        return ""


class ReportModerationListOut(Schema):
    """Paginated list of user reports for staff moderation.
    Wraps reports and pagination metadata in one response envelope."""

    items: list[ReportModerationOut] = Field(description="Reports for the current page.")
    pagination: PaginationMeta = Field(description="Pagination metadata for navigating reports.")


class PhotoModerationStatusFilterEnum(str, Enum):
    """Community photo status filter values for staff moderation.
    Supports listing all photos or one concrete photo status."""

    ALL = "all"
    PENDING = LibraryPhoto.Status.PENDING
    APPROVED = LibraryPhoto.Status.APPROVED
    REJECTED = LibraryPhoto.Status.REJECTED


class PhotoModerationStatusEnum(str, Enum):
    """Staff-selectable community photo moderation statuses.
    Matches the photo model's status choices."""

    PENDING = LibraryPhoto.Status.PENDING
    APPROVED = LibraryPhoto.Status.APPROVED
    REJECTED = LibraryPhoto.Status.REJECTED


class PhotoModerationParams(Schema):
    """Query parameters for staff photo moderation lists.
    Supports status filtering and pagination."""

    status: PhotoModerationStatusFilterEnum = Field(default=PhotoModerationStatusFilterEnum.ALL, description="Filter by photo status, or all.")
    page: int = Field(default=1, ge=1, le=1000, description="Page number to retrieve (1-indexed).", json_schema_extra={"example": 1})
    page_size: int = Field(default=20, ge=1, le=50, description="Number of photos per page.", json_schema_extra={"example": 20})


class PhotoModerationUpdateIn(Schema):
    """Payload for updating a community photo moderation status.
    Staff clients can mark photos pending, approved, or rejected."""

    status: PhotoModerationStatusEnum = Field(description="New photo status: pending, approved, or rejected.", examples=["approved"])


class PhotoModerationOut(Schema):
    """Serialized community photo for staff moderation.
    Includes the parent library and submitter context."""

    id: int = Field(description="Unique photo identifier.", examples=[12])
    library: LibraryModerationSummaryOut = Field(description="Library the photo belongs to.")
    created_by: ModerationUserOut | None = Field(default=None, description="User who submitted the photo, or null when unavailable.")
    caption: str = Field(description="Caption for the photo.", examples=["A sunny day at the library."])
    photo_url: str = Field(description="Full-size photo URL.", examples=["/media/libraries/user_photos/photo.jpg"])
    thumbnail_url: str = Field(description="Thumbnail photo URL, or empty string if unavailable.", examples=["/media/libraries/user_photos/thumbnails/photo.jpg"])
    status: str = Field(description="Current photo moderation status.", examples=["pending"])
    created_at: datetime = Field(description="Timestamp when the photo was submitted (UTC).", examples=["2025-06-15T14:30:00Z"])

    @staticmethod
    def resolve_photo_url(obj: LibraryPhoto) -> str:
        """Return the community photo URL or empty string.
        Handles missing uploaded files gracefully."""
        if obj.photo:
            try:
                return obj.photo.url
            except ValueError:
                return ""
        return ""

    @staticmethod
    def resolve_thumbnail_url(obj: LibraryPhoto) -> str:
        """Return the community photo thumbnail URL or empty string.
        Falls back gracefully when no thumbnail exists."""
        if obj.photo_thumbnail:
            try:
                return obj.photo_thumbnail.url
            except ValueError:
                return ""
        return ""


class PhotoModerationListOut(Schema):
    """Paginated list of community photos for staff moderation.
    Wraps photos and pagination metadata in one response envelope."""

    items: list[PhotoModerationOut] = Field(description="Community photos for the current page.")
    pagination: PaginationMeta = Field(description="Pagination metadata for navigating photos.")


class ModerationSummaryOut(Schema):
    """Aggregate moderation dashboard counts for staff clients.
    Mirrors the custom manage dashboard's queue totals."""

    pending_libraries_count: int = Field(description="Number of libraries awaiting review.", examples=[4])
    open_reports_count: int = Field(description="Number of open user reports.", examples=[2])
    pending_photos_count: int = Field(description="Number of community photos awaiting review.", examples=[5])
    total_pending: int = Field(description="Combined count of pending libraries, open reports, and pending photos.", examples=[11])
    total_libraries: int = Field(description="Number of approved libraries.", examples=[350])
    total_users: int = Field(description="Total registered user count.", examples=[128])


class LatestLibrariesOut(Schema):
    """Flat list of latest approved libraries without pagination.
    Used by the /latest endpoint for lightweight newest-first results."""

    items: list[LibraryOut] = Field(description="List of the most recently approved libraries.")


class FavouriteListOut(Schema):
    """Paginated list of a user's favourite libraries with navigation metadata.
    Wraps items and pagination in a single response envelope."""

    items: list[LibraryOut] = Field(description="Favourite libraries for the current page, newest-favourited first.")
    pagination: PaginationMeta = Field(description="Pagination metadata for navigating the result set.")


class FavouritePaginationParams(Schema):
    """Query parameters for paginating the favourites list.
    Provides page and page_size controls without search filters."""

    page: int = Field(default=1, ge=1, le=1000, description="Page number to retrieve (1-indexed).", json_schema_extra={"example": 1})
    page_size: int = Field(default=20, ge=1, le=50, description="Number of items per page.", json_schema_extra={"example": 20})


class ContributionPaginationParams(Schema):
    """Query parameters for paginating contribution lists.
    Provides page and page_size controls for authenticated dashboard endpoints."""

    page: int = Field(default=1, ge=1, le=1000, description="Page number to retrieve (1-indexed).", json_schema_extra={"example": 1})
    page_size: int = Field(default=20, ge=1, le=50, description="Number of contributions per page.", json_schema_extra={"example": 20})


class ContributionLibraryOut(LibraryOut):
    """Serialized current-user library submission with moderation state.
    Extends the standard library payload with status details for the owner."""

    status: str = Field(description="Current moderation status of the library.", examples=["pending"])
    rejection_reason: str = Field(description="Reason shown when the library is rejected, or an empty string.", examples=["Duplicate submission."])


class ContributionLibraryListOut(Schema):
    """Paginated list of the current user's library submissions.
    Wraps submissions and pagination in a single response envelope."""

    items: list[ContributionLibraryOut] = Field(description="Library submissions for the current page.")
    pagination: PaginationMeta = Field(description="Pagination metadata for navigating submissions.")


class ContributionLibrarySummaryOut(Schema):
    """Compact library representation for contribution responses.
    Gives mobile clients enough context for reports and photo submissions."""

    id: int = Field(description="Unique library identifier.", examples=[42])
    slug: str = Field(description="URL-friendly unique slug.", examples=["florence-via-rosina-15-corner-books"])
    name: str = Field(description="Display name of the library.", examples=["Corner Books"])
    city: str = Field(description="City name.", examples=["Florence"])
    country: str = Field(description="ISO 3166-1 alpha-2 country code.", examples=["IT"])
    status: str = Field(description="Current moderation status of the library.", examples=["approved"])


class ContributionReportOut(Schema):
    """Serialized current-user report with moderation state.
    Includes the related library summary for contribution center lists."""

    id: int = Field(description="Unique report identifier.", examples=[7])
    library: ContributionLibrarySummaryOut = Field(description="Library the report is about.")
    reason: str = Field(description="Reason category of the report.", examples=["damaged"])
    status: str = Field(description="Current report moderation status.", examples=["open"])
    created_at: datetime = Field(description="Timestamp when the report was created (UTC).", examples=["2025-06-15T14:30:00Z"])


class ContributionReportListOut(Schema):
    """Paginated list of the current user's reports.
    Wraps reports and pagination in a single response envelope."""

    items: list[ContributionReportOut] = Field(description="Reports for the current page.")
    pagination: PaginationMeta = Field(description="Pagination metadata for navigating reports.")


class ContributionPhotoOut(Schema):
    """Serialized current-user community photo with moderation state.
    Includes media URLs and parent library context for dashboard clients."""

    id: int = Field(description="Unique photo identifier.", examples=[12])
    library: ContributionLibrarySummaryOut = Field(description="Library the photo belongs to.")
    caption: str = Field(description="Caption for the photo.", examples=["A sunny day at the library."])
    photo_url: str = Field(description="Full-size photo URL.", examples=["/media/libraries/user_photos/photo.jpg"])
    thumbnail_url: str = Field(description="Thumbnail photo URL, or empty string if unavailable.", examples=["/media/libraries/user_photos/thumbnails/photo.jpg"])
    status: str = Field(description="Current photo moderation status.", examples=["pending"])
    created_at: datetime = Field(description="Timestamp when the photo was submitted (UTC).", examples=["2025-06-15T14:30:00Z"])

    @staticmethod
    def resolve_photo_url(obj: LibraryPhoto) -> str:
        """Return the community photo URL or empty string.
        Handles missing uploaded files gracefully."""
        if obj.photo:
            try:
                return obj.photo.url
            except ValueError:
                return ""
        return ""

    @staticmethod
    def resolve_thumbnail_url(obj: LibraryPhoto) -> str:
        """Return the community photo thumbnail URL or empty string.
        Falls back gracefully when no thumbnail exists."""
        if obj.photo_thumbnail:
            try:
                return obj.photo_thumbnail.url
            except ValueError:
                return ""
        return ""


class ContributionPhotoListOut(Schema):
    """Paginated list of the current user's community photos.
    Wraps photo submissions and pagination in a single response envelope."""

    items: list[ContributionPhotoOut] = Field(description="Community photos for the current page.")
    pagination: PaginationMeta = Field(description="Pagination metadata for navigating photos.")


class LibrarySearchParams(Schema):
    """Query parameters for searching and filtering libraries.
    Validates bounds and defaults for pagination and geospatial queries."""

    q: str | None = Field(default=None, max_length=200, description="Free-text search query matched against name and description (full-text ranked).", json_schema_extra={"example": "corner books"})
    search: str | None = Field(default=None, max_length=200, description="Global search across name, description, city, address, and postal code. Supports substring matches and outranks `q` when both are provided.", json_schema_extra={"example": "Berlin"})
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
    address: str = Field(default="", max_length=255, description="Street address of the library. May be empty only when coordinates (latitude and longitude) are provided — e.g. libraries inside a park with no street address.", examples=["Friedrichstr. 12"])
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


class LibraryUpdateIn(Schema):
    """Input schema for editing an existing submitted library.
    All fields are optional so omitted values keep their current value."""

    name: str | None = Field(default=None, max_length=255, description="Display name of the library.", examples=["Corner Books"])
    description: str | None = Field(default=None, max_length=2000, description="Free-text description of the library.", examples=["A cozy little free library near the park entrance."])
    address: str | None = Field(default=None, max_length=255, description="Street address of the library. May be empty when coordinates identify the location.", examples=["Friedrichstr. 12"])
    city: str | None = Field(default=None, min_length=1, max_length=100, description="City where the library is located.", examples=["Berlin"])
    country: str | None = Field(default=None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2 country code.", examples=["DE"])
    postal_code: str | None = Field(default=None, max_length=20, description="Postal or ZIP code.", examples=["10117"])
    wheelchair_accessible: str | None = Field(default=None, max_length=10, description="Wheelchair accessibility: yes, no, or limited.", examples=["yes"])
    capacity: int | None = Field(default=None, ge=0, description="Approximate book capacity.", examples=[50])
    is_indoor: bool | None = Field(default=None, description="Whether the library is inside a building.", examples=[False])
    is_lit: bool | None = Field(default=None, description="Whether the library is illuminated at night.", examples=[True])
    website: str | None = Field(default=None, max_length=500, description="External website link.", examples=["https://littlefreelibrary.org/charter/12345"])
    contact: str | None = Field(default=None, max_length=255, description="Contact information (email, phone, etc.).", examples=["info@example.org"])
    operator: str | None = Field(default=None, max_length=255, description="Organisation that maintains the library.", examples=["City Library Association"])
    brand: str | None = Field(default=None, max_length=255, description="Network or brand name.", examples=["Little Free Library"])
    latitude: float | None = Field(default=None, ge=-90, le=90, description="Latitude of the library (WGS 84). Must be provided with longitude.", examples=[52.5200])
    longitude: float | None = Field(default=None, ge=-180, le=180, description="Longitude of the library (WGS 84). Must be provided with latitude.", examples=[13.4050])


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
