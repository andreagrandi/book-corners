from __future__ import annotations

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef, Q, Value
from django.shortcuts import get_object_or_404
from ninja import File, Form, Query, Router
from ninja.files import UploadedFile
from ninja_jwt.authentication import JWTAuth

from config.api_schemas import ErrorOut
from libraries.api_auth import get_optional_jwt_user
from libraries.api_pagination import paginate_queryset
from libraries.api_schemas import (
    CountryListOut,
    FavouriteListOut,
    FavouritePaginationParams,
    LatestLibrariesOut,
    LibraryListOut,
    LibraryOut,
    LibraryPhotoIn,
    LibraryPhotoOut,
    LibrarySearchParams,
    LibrarySubmitIn,
    ReportIn,
    ReportOut,
    StatisticsOut,
)
from libraries.api_security import is_api_rate_limited
from libraries.stats import build_stats_data, get_countries
from libraries.forms import _validate_uploaded_photo
from libraries.models import Favourite, Library, LibraryPhoto, MAX_LIBRARY_PHOTOS_PER_USER, Report
from libraries.notifications import notify_new_library, notify_new_photo, notify_new_report
from libraries.tasks import enrich_library_with_ai
from libraries.search import run_library_search

library_router = Router(tags=["libraries"])


def _annotate_is_favourited(queryset, user):
    """Annotate a Library queryset with the current user's favourite status.
    Returns the queryset unchanged when user is None."""
    if user is None:
        return queryset
    return queryset.annotate(
        _is_favourited=Exists(
            Favourite.objects.filter(user=user, library_id=OuterRef("pk"))
        )
    )


@library_router.get("/", response={200: LibraryListOut, 429: ErrorOut}, auth=None, summary="List and search libraries")
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
        has_photo=filters.has_photo,
    )
    jwt_user = get_optional_jwt_user(request=request)
    queryset = _annotate_is_favourited(queryset, jwt_user)
    items, pagination = paginate_queryset(
        queryset=queryset, page=filters.page, page_size=filters.page_size,
    )
    return 200, {"items": items, "pagination": pagination}


@library_router.get("/latest", response={200: LatestLibrariesOut, 429: ErrorOut}, auth=None, summary="Get latest libraries")
def latest_libraries(
    request,
    limit: int = Query(default=10, ge=1, le=50),
    has_photo: bool | None = Query(default=None),
):
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

    queryset = Library.objects.filter(status=Library.Status.APPROVED).order_by("-created_at")
    if has_photo is True:
        queryset = queryset.exclude(photo="")
    elif has_photo is False:
        queryset = queryset.filter(photo="")
    jwt_user = get_optional_jwt_user(request=request)
    queryset = _annotate_is_favourited(queryset, jwt_user)
    queryset = queryset[:limit]
    return 200, {"items": list(queryset)}


@library_router.get("/countries/", response={200: CountryListOut, 429: ErrorOut}, auth=None, summary="List all countries with libraries")
def list_countries(request):
    """Return all countries that have at least one approved library.
    Ordered by library count descending, without a top-N limit."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-countries",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    countries = get_countries()
    return 200, {"items": countries}


@library_router.get("/favourites", response={200: FavouriteListOut, 429: ErrorOut}, auth=JWTAuth(), summary="List favourite libraries")
def list_favourites(request, filters: Query[FavouritePaginationParams]):
    """Return the authenticated user's favourite libraries, newest-favourited first.
    Only includes libraries that are still in approved status."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-favourites-list",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    queryset = (
        Library.objects.filter(
            status=Library.Status.APPROVED,
            favourites__user=request.user,
        )
        .order_by("-favourites__created_at")
        .annotate(_is_favourited=Value(True))
    )
    items, pagination = paginate_queryset(
        queryset=queryset, page=filters.page, page_size=filters.page_size,
    )
    return 200, {"items": items, "pagination": pagination}


@library_router.get("/{slug}", response={200: LibraryOut, 404: ErrorOut, 429: ErrorOut}, auth=None, summary="Get a library by slug")
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

    qs = Library.objects.filter(visibility_filter, slug=slug)
    qs = _annotate_is_favourited(qs, jwt_user)
    library = get_object_or_404(qs)
    return 200, library


@library_router.post(
    "/",
    response={201: LibraryOut, 400: ErrorOut, 413: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Submit a new library",
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
        wheelchair_accessible=payload.wheelchair_accessible,
        capacity=payload.capacity,
        is_indoor=payload.is_indoor,
        is_lit=payload.is_lit,
        website=payload.website,
        contact=payload.contact,
        operator=payload.operator,
        brand=payload.brand,
        photo=photo,
        location=Point(x=payload.longitude, y=payload.latitude, srid=4326),
        status=Library.Status.PENDING,
        created_by=request.user,
    )
    library.save()
    try:
        enrich_library_with_ai.enqueue(library_id=library.pk)
    except Exception:
        notify_new_library(library)
    return 201, library


@library_router.post(
    "/{slug}/report",
    response={201: ReportOut, 400: ErrorOut, 404: ErrorOut, 413: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Report an issue with a library",
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
    notify_new_report(report)
    return 201, report


@library_router.post(
    "/{slug}/photo",
    response={201: LibraryPhotoOut, 400: ErrorOut, 404: ErrorOut, 413: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Submit a community photo for a library",
)
def submit_library_photo(
    request,
    slug: str,
    payload: Form[LibraryPhotoIn],
    photo: UploadedFile = File(...),
):
    """Submit a community photo for an approved library.
    Validates the photo and enforces per-user limits before persisting."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-photo",
        max_requests=settings.API_RATE_LIMIT_WRITE_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    library = get_object_or_404(Library, slug=slug, status=Library.Status.APPROVED)

    try:
        _validate_uploaded_photo(
            uploaded_photo=photo,
            max_size_bytes=settings.MAX_LIBRARY_PHOTO_SUBMISSION_BYTES,
        )
    except ValidationError as exc:
        message = exc.message if hasattr(exc, "message") else str(exc)
        status_code = 413 if ("MB or smaller" in str(message) or "at most" in str(message)) else 400
        return status_code, ErrorOut(message=str(message))

    existing_count = LibraryPhoto.objects.filter(
        library=library,
        created_by=request.user,
    ).exclude(
        status=LibraryPhoto.Status.REJECTED,
    ).count()
    if existing_count >= MAX_LIBRARY_PHOTOS_PER_USER:
        return 400, ErrorOut(
            message=f"You can submit at most {MAX_LIBRARY_PHOTOS_PER_USER} photos per library."
        )

    library_photo = LibraryPhoto(
        library=library,
        created_by=request.user,
        photo=photo,
        caption=payload.caption,
        status=LibraryPhoto.Status.PENDING,
    )
    library_photo.save()
    notify_new_photo(library_photo)
    return 201, library_photo


@library_router.post(
    "/{slug}/favourite",
    response={201: ErrorOut, 200: ErrorOut, 404: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Mark a library as favourite",
)
def mark_favourite(request, slug: str):
    """Add an approved library to the authenticated user's favourites.
    Returns 201 if newly added, 200 if already favourited."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-favourite",
        max_requests=settings.API_RATE_LIMIT_WRITE_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    library = get_object_or_404(Library, slug=slug, status=Library.Status.APPROVED)
    _, created = Favourite.objects.get_or_create(user=request.user, library=library)
    if created:
        return 201, ErrorOut(message="Library added to favourites.")
    return 200, ErrorOut(message="Library is already in your favourites.")


@library_router.delete(
    "/{slug}/favourite",
    response={204: None, 404: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Remove a library from favourites",
)
def unmark_favourite(request, slug: str):
    """Remove an approved library from the authenticated user's favourites.
    Returns 204 whether the favourite existed or not (idempotent)."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-favourite",
        max_requests=settings.API_RATE_LIMIT_WRITE_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    library = get_object_or_404(Library, slug=slug, status=Library.Status.APPROVED)
    Favourite.objects.filter(user=request.user, library=library).delete()
    return 204, None


statistics_router = Router(tags=["statistics"])


@statistics_router.get("/", response={200: StatisticsOut, 429: ErrorOut}, auth=None, summary="Get platform statistics")
def get_statistics(request):
    """Return aggregate statistics about approved libraries.
    Includes totals, top countries, and cumulative growth series."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-statistics",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    data = build_stats_data()
    return 200, data
