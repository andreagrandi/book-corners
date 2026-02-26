from __future__ import annotations

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import File, Form, Query, Router
from ninja.files import UploadedFile
from ninja_jwt.authentication import JWTAuth

from config.api_schemas import ErrorOut
from libraries.api_auth import get_optional_jwt_user
from libraries.api_pagination import paginate_queryset
from libraries.api_schemas import (
    LatestLibrariesOut,
    LibraryListOut,
    LibraryOut,
    LibrarySearchParams,
    LibrarySubmitIn,
    ReportIn,
    ReportOut,
)
from libraries.api_security import is_api_rate_limited
from libraries.forms import _validate_uploaded_photo
from libraries.models import Library, Report
from libraries.search import run_library_search

library_router = Router(tags=["libraries"])


@library_router.get("/", response={200: LibraryListOut, 429: ErrorOut}, auth=None)
def list_libraries(request, filters: Query[LibrarySearchParams]):
    """Return a paginated list of approved libraries with optional search filters.
    Supports text, field, and proximity filtering with configurable pagination."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-list",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    queryset = run_library_search(
        q=filters.q or "",
        city=filters.city or "",
        country=filters.country or "",
        postal_code=filters.postal_code or "",
        lat=filters.lat,
        lng=filters.lng,
        radius_km=filters.radius_km,
    )
    items, pagination = paginate_queryset(
        queryset=queryset, page=filters.page, page_size=filters.page_size,
    )
    return 200, {"items": items, "pagination": pagination}


@library_router.get("/latest", response={200: LatestLibrariesOut, 429: ErrorOut}, auth=None)
def latest_libraries(request, limit: int = Query(default=10, ge=1, le=50)):
    """Return the most recent approved libraries as a flat list.
    Provides a lightweight endpoint for newest-first results without pagination."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-latest",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    queryset = Library.objects.filter(status=Library.Status.APPROVED).order_by("-created_at")[:limit]
    return 200, {"items": list(queryset)}


@library_router.get("/{slug}", response={200: LibraryOut, 404: ErrorOut, 429: ErrorOut}, auth=None)
def get_library(request, slug: str):
    """Return a single library by its slug.
    Pending libraries are visible only to their authenticated owner."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-detail",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    visibility_filter = Q(status=Library.Status.APPROVED)
    jwt_user = get_optional_jwt_user(request=request)
    if jwt_user is not None:
        visibility_filter |= Q(status=Library.Status.PENDING, created_by=jwt_user)

    library = get_object_or_404(Library, visibility_filter, slug=slug)
    return 200, library


@library_router.post(
    "/",
    response={201: LibraryOut, 400: ErrorOut, 413: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
)
def submit_library(request, payload: Form[LibrarySubmitIn], photo: UploadedFile = File(...)):
    """Create a new library submission from an authenticated user.
    Validates the photo and fields, then persists as pending for moderation."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-submit",
        max_requests=settings.API_RATE_LIMIT_WRITE_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    try:
        _validate_uploaded_photo(
            uploaded_photo=photo,
            max_size_bytes=settings.MAX_LIBRARY_PHOTO_UPLOAD_BYTES,
        )
    except ValidationError as exc:
        message = exc.message if hasattr(exc, "message") else str(exc)
        status_code = 413 if ("MB or smaller" in str(message) or "at most" in str(message)) else 400
        return status_code, ErrorOut(message=str(message))

    library = Library(
        name=payload.name,
        description=payload.description,
        address=payload.address,
        city=payload.city,
        country=payload.country,
        postal_code=payload.postal_code,
        photo=photo,
        location=Point(x=payload.longitude, y=payload.latitude, srid=4326),
        status=Library.Status.PENDING,
        created_by=request.user,
    )
    library.save()
    return 201, library


@library_router.post(
    "/{slug}/report",
    response={201: ReportOut, 400: ErrorOut, 404: ErrorOut, 413: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
)
def submit_library_report(
    request,
    slug: str,
    payload: Form[ReportIn],
    photo: UploadedFile = File(None),
):
    """Create a report about an approved library from an authenticated user.
    Validates optional photo and persists the report as open for moderation."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-report",
        max_requests=settings.API_RATE_LIMIT_WRITE_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    library = get_object_or_404(Library, slug=slug, status=Library.Status.APPROVED)

    if photo:
        try:
            _validate_uploaded_photo(
                uploaded_photo=photo,
                max_size_bytes=settings.MAX_REPORT_PHOTO_UPLOAD_BYTES,
            )
        except ValidationError as exc:
            message = exc.message if hasattr(exc, "message") else str(exc)
            status_code = 413 if ("MB or smaller" in str(message) or "at most" in str(message)) else 400
            return status_code, ErrorOut(message=str(message))

    report = Report.objects.create(
        library=library,
        created_by=request.user,
        reason=payload.reason.value,
        details=payload.details,
        photo=photo or "",
        status=Report.Status.OPEN,
    )
    return 201, report
