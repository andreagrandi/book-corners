from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When
from django.shortcuts import get_object_or_404
from ninja import File, Form, Query, Router
from ninja.files import UploadedFile
from ninja_jwt.authentication import JWTAuth

from config.api_schemas import ErrorOut
from libraries.api_auth import get_optional_jwt_user
from libraries.api_pagination import paginate_queryset
from libraries.api_schemas import (
    ContributionLibraryListOut,
    ContributionPaginationParams,
    ContributionPhotoListOut,
    ContributionReportListOut,
    CountryListOut,
    FavouriteListOut,
    FavouritePaginationParams,
    LatestLibrariesOut,
    LibraryListOut,
    LibraryModerationListOut,
    LibraryModerationOut,
    LibraryModerationParams,
    LibraryModerationUpdateIn,
    LibraryOut,
    LibraryPhotoIn,
    LibraryPhotoOut,
    LibrarySearchParams,
    LibrarySubmitIn,
    LibraryUpdateIn,
    ModerationSummaryOut,
    ModerationStatusFilterEnum,
    PhotoModerationListOut,
    PhotoModerationParams,
    PhotoModerationOut,
    PhotoModerationUpdateIn,
    ReportIn,
    ReportModerationListOut,
    ReportModerationParams,
    ReportModerationOut,
    ReportModerationUpdateIn,
    ReportOut,
    StatisticsOut,
)
from libraries.api_security import is_api_rate_limited
from libraries.stats import build_stats_data, get_countries
from libraries.forms import _validate_uploaded_photo
from libraries.models import Favourite, Library, LibraryPhoto, MAX_LIBRARY_PHOTOS_PER_USER, Report
from libraries.notifications import (
    notify_library_approved,
    notify_library_rejected,
    notify_library_update,
    notify_new_library,
    notify_new_photo,
    notify_new_report,
)
from libraries.tasks import enrich_library_with_ai
from libraries.search import run_library_search
from libraries.views import GEOJSON_CACHE_KEY, HOMEPAGE_COUNT_CACHE_KEY, invalidate_cluster_cache

library_router = Router(tags=["libraries"])
User = get_user_model()

LIBRARY_UPDATE_FIELDS = (
    "name",
    "description",
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
    "operator",
    "brand",
)


def _is_staff_user(request) -> bool:
    """Return whether the request user can use moderation endpoints.
    Centralizes staff checks for the API moderation surface."""
    return bool(getattr(request.user, "is_staff", False))


def _invalidate_library_caches() -> None:
    """Clear caches affected by library moderation changes.
    Keeps map, homepage, and clustering data fresh after status updates."""
    cache.delete(GEOJSON_CACHE_KEY)
    cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
    invalidate_cluster_cache()


def _library_moderation_queryset(*, filters: LibraryModerationParams):
    """Build the staff library moderation queryset from filters.
    Matches the manage UI's common status, country, source, and text filters."""
    queryset = Library.objects.select_related("created_by").all()
    if filters.status != ModerationStatusFilterEnum.ALL:
        queryset = queryset.filter(status=filters.status.value)
    if filters.country:
        queryset = queryset.filter(country__iexact=filters.country)
    if filters.source:
        queryset = queryset.filter(source__icontains=filters.source)
    if filters.q:
        query = filters.q.strip()
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(address__icontains=query)
            | Q(city__icontains=query)
        )
    return queryset.order_by("-created_at")


def _save_library_moderation_status(
    *,
    library: Library,
    payload: LibraryModerationUpdateIn,
) -> Library:
    """Persist a library moderation status update and side effects.
    Mirrors manage UI cache invalidation and submitter notifications."""
    old_status = library.status
    new_status = payload.status.value
    rejection_reason = payload.rejection_reason.strip()

    library.status = new_status
    update_fields = ["status", "updated_at"]
    if new_status == Library.Status.REJECTED:
        library.rejection_reason = rejection_reason
        update_fields.append("rejection_reason")

    library.save(update_fields=update_fields)
    _invalidate_library_caches()

    if old_status == Library.Status.PENDING and new_status == Library.Status.APPROVED:
        notify_library_approved(library)
    if (
        old_status != Library.Status.REJECTED
        and new_status == Library.Status.REJECTED
        and rejection_reason
    ):
        notify_library_rejected(library)

    return library


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
        search=filters.search or "",
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


@library_router.get(
    "/mine",
    response={200: ContributionLibraryListOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="List my submitted libraries",
)
def list_my_libraries(request, filters: Query[ContributionPaginationParams]):
    """Return the authenticated user's submitted libraries with status.
    Pending submissions appear first, followed by newest contributions."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-mine-list",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    queryset = Library.objects.filter(created_by=request.user).annotate(
        status_order=Case(
            When(status=Library.Status.PENDING, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    )
    queryset = _annotate_is_favourited(
        queryset.order_by("status_order", "-created_at"), request.user,
    )
    items, pagination = paginate_queryset(
        queryset=queryset, page=filters.page, page_size=filters.page_size,
    )
    return 200, {"items": items, "pagination": pagination}


@library_router.get(
    "/mine/reports",
    response={200: ContributionReportListOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="List my submitted reports",
)
def list_my_reports(request, filters: Query[ContributionPaginationParams]):
    """Return the authenticated user's submitted reports with status.
    Results include minimal library context and are ordered newest first."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-mine-reports-list",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    queryset = (
        Report.objects.filter(created_by=request.user)
        .select_related("library")
        .order_by("-created_at")
    )
    items, pagination = paginate_queryset(
        queryset=queryset, page=filters.page, page_size=filters.page_size,
    )
    return 200, {"items": items, "pagination": pagination}


@library_router.get(
    "/mine/photos",
    response={200: ContributionPhotoListOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="List my submitted community photos",
)
def list_my_photos(request, filters: Query[ContributionPaginationParams]):
    """Return the authenticated user's community photo submissions.
    Results include photo URLs, status, and library context newest first."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-mine-photos-list",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    queryset = (
        LibraryPhoto.objects.filter(created_by=request.user)
        .select_related("library")
        .order_by("-created_at")
    )
    items, pagination = paginate_queryset(
        queryset=queryset, page=filters.page, page_size=filters.page_size,
    )
    return 200, {"items": items, "pagination": pagination}


@library_router.get(
    "/moderation/summary",
    response={200: ModerationSummaryOut, 403: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Get staff moderation dashboard counts",
)
def moderation_summary(request):
    """Return aggregate moderation counts for staff users.
    Non-staff authenticated users receive a structured 403 response."""
    if not _is_staff_user(request):
        return 403, ErrorOut(message="Staff access required.")

    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-moderation-summary",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    pending_libraries_count = Library.objects.filter(status=Library.Status.PENDING).count()
    open_reports_count = Report.objects.filter(status=Report.Status.OPEN).count()
    pending_photos_count = LibraryPhoto.objects.filter(
        status=LibraryPhoto.Status.PENDING
    ).count()
    return 200, {
        "pending_libraries_count": pending_libraries_count,
        "open_reports_count": open_reports_count,
        "pending_photos_count": pending_photos_count,
        "total_pending": (
            pending_libraries_count + open_reports_count + pending_photos_count
        ),
        "total_libraries": Library.objects.filter(status=Library.Status.APPROVED).count(),
        "total_users": User.objects.count(),
    }


@library_router.get(
    "/moderation",
    response={200: LibraryModerationListOut, 403: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="List libraries for staff moderation",
)
def list_moderation_libraries(request, filters: Query[LibraryModerationParams]):
    """Return all library submissions for staff users.
    Supports status, text, country, source, and pagination filters."""
    if not _is_staff_user(request):
        return 403, ErrorOut(message="Staff access required.")

    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-moderation-list",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    queryset = _library_moderation_queryset(filters=filters)
    items, pagination = paginate_queryset(
        queryset=queryset, page=filters.page, page_size=filters.page_size,
    )
    return 200, {"items": items, "pagination": pagination}


@library_router.get(
    "/moderation/pending",
    response={200: LibraryModerationListOut, 403: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="List pending library submissions for staff moderation",
)
def list_pending_libraries(request, filters: Query[LibraryModerationParams]):
    """Return pending library submissions for staff users.
    Supports the same filters as the all-library staff list."""
    if not _is_staff_user(request):
        return 403, ErrorOut(message="Staff access required.")

    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-moderation-list",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    queryset = Library.objects.select_related("created_by").filter(
        status=Library.Status.PENDING
    )
    if filters.country:
        queryset = queryset.filter(country__iexact=filters.country)
    if filters.source:
        queryset = queryset.filter(source__icontains=filters.source)
    if filters.q:
        query = filters.q.strip()
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(address__icontains=query)
            | Q(city__icontains=query)
        )
    queryset = queryset.order_by("-created_at")
    items, pagination = paginate_queryset(
        queryset=queryset, page=filters.page, page_size=filters.page_size,
    )
    return 200, {"items": items, "pagination": pagination}


@library_router.get(
    "/moderation/reports",
    response={200: ReportModerationListOut, 403: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="List user reports for staff moderation",
)
def list_moderation_reports(request, filters: Query[ReportModerationParams]):
    """Return user-submitted reports for staff users.
    Supports status, reason, and pagination filters."""
    if not _is_staff_user(request):
        return 403, ErrorOut(message="Staff access required.")

    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-report-moderation-list",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    queryset = Report.objects.select_related("library", "created_by").all()
    if filters.status.value != "all":
        queryset = queryset.filter(status=filters.status.value)
    if filters.reason.value != "all":
        queryset = queryset.filter(reason=filters.reason.value)
    queryset = queryset.order_by("-created_at")
    items, pagination = paginate_queryset(
        queryset=queryset, page=filters.page, page_size=filters.page_size,
    )
    return 200, {"items": items, "pagination": pagination}


@library_router.patch(
    "/moderation/reports/{report_id}",
    response={200: ReportModerationOut, 403: ErrorOut, 404: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Update a report moderation status",
)
def moderate_report(request, report_id: int, payload: ReportModerationUpdateIn):
    """Update a user report moderation status as a staff user.
    Allows staff clients to reopen, resolve, or dismiss reports."""
    if not _is_staff_user(request):
        return 403, ErrorOut(message="Staff access required.")

    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-report-moderation-update",
        max_requests=settings.API_RATE_LIMIT_WRITE_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    report = get_object_or_404(
        Report.objects.select_related("library", "created_by"), pk=report_id,
    )
    report.status = payload.status.value
    report.save(update_fields=["status"])
    return 200, report


@library_router.get(
    "/moderation/photos",
    response={200: PhotoModerationListOut, 403: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="List community photos for staff moderation",
)
def list_moderation_photos(request, filters: Query[PhotoModerationParams]):
    """Return community photos for staff users.
    Supports status and pagination filters."""
    if not _is_staff_user(request):
        return 403, ErrorOut(message="Staff access required.")

    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-photo-moderation-list",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    queryset = LibraryPhoto.objects.select_related("library", "created_by").all()
    if filters.status.value != "all":
        queryset = queryset.filter(status=filters.status.value)
    queryset = queryset.order_by("-created_at")
    items, pagination = paginate_queryset(
        queryset=queryset, page=filters.page, page_size=filters.page_size,
    )
    return 200, {"items": items, "pagination": pagination}


@library_router.patch(
    "/moderation/photos/{photo_id}",
    response={200: PhotoModerationOut, 403: ErrorOut, 404: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Update a community photo moderation status",
)
def moderate_photo(request, photo_id: int, payload: PhotoModerationUpdateIn):
    """Update a community photo moderation status as a staff user.
    Approving a photo promotes it to the parent library's primary image."""
    if not _is_staff_user(request):
        return 403, ErrorOut(message="Staff access required.")

    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-photo-moderation-update",
        max_requests=settings.API_RATE_LIMIT_WRITE_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    photo = get_object_or_404(
        LibraryPhoto.objects.select_related("library", "created_by"), pk=photo_id,
    )
    photo.status = payload.status.value
    photo.save(update_fields=["status"])
    _invalidate_library_caches()
    return 200, photo


@library_router.get(
    "/moderation/{slug}",
    response={200: LibraryModerationOut, 403: ErrorOut, 404: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Get a library for staff moderation",
)
def get_moderation_library(request, slug: str):
    """Return any library by slug for staff users.
    Includes pending and rejected libraries that public endpoints hide."""
    if not _is_staff_user(request):
        return 403, ErrorOut(message="Staff access required.")

    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-moderation-detail",
        max_requests=settings.API_RATE_LIMIT_READ_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    library = get_object_or_404(
        Library.objects.select_related("created_by"), slug=slug,
    )
    return 200, library


@library_router.patch(
    "/moderation/{slug}",
    response={200: LibraryModerationOut, 403: ErrorOut, 404: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Update a library moderation status",
)
def moderate_library(request, slug: str, payload: LibraryModerationUpdateIn):
    """Update a library moderation status as a staff user.
    Applies the same cache and notification side effects as the manage UI."""
    if not _is_staff_user(request):
        return 403, ErrorOut(message="Staff access required.")

    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-moderation-update",
        max_requests=settings.API_RATE_LIMIT_WRITE_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    library = get_object_or_404(
        Library.objects.select_related("created_by"), slug=slug,
    )
    library = _save_library_moderation_status(library=library, payload=payload)
    return 200, library


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


@library_router.patch(
    "/{slug}",
    response={200: LibraryOut, 400: ErrorOut, 404: ErrorOut, 413: ErrorOut, 429: ErrorOut},
    auth=JWTAuth(),
    summary="Update one of your submitted libraries",
)
def update_library(
    request,
    slug: str,
    payload: Form[LibraryUpdateIn],
    photo: UploadedFile = File(None),
):
    """Update a submitted library owned by the authenticated user.
    Owner edits return pending and require moderator approval."""
    limited, retry_after = is_api_rate_limited(
        request=request,
        scope="api-library-update",
        max_requests=settings.API_RATE_LIMIT_WRITE_REQUESTS,
    )
    if limited:
        return 429, ErrorOut(
            message="Too many requests. Please try again later.",
            details={"retry_after": retry_after},
        )

    submitted_fields = set(request.POST)
    coordinate_fields = {"latitude", "longitude"}
    submitted_coordinates = submitted_fields & coordinate_fields
    submitted_update_fields = submitted_fields & set(LIBRARY_UPDATE_FIELDS)

    if len(submitted_coordinates) == 1:
        return 400, ErrorOut(message="Latitude and longitude must be provided together.")

    if not submitted_update_fields and not submitted_coordinates and photo is None:
        return 400, ErrorOut(message="Provide at least one field to update.")

    if photo is not None:
        try:
            _validate_uploaded_photo(
                uploaded_photo=photo,
                max_size_bytes=settings.MAX_LIBRARY_PHOTO_UPLOAD_BYTES,
            )
        except ValidationError as exc:
            message = exc.message if hasattr(exc, "message") else str(exc)
            status_code = 413 if ("MB or smaller" in str(message) or "at most" in str(message)) else 400
            return status_code, ErrorOut(message=str(message))

    library = get_object_or_404(
        Library,
        slug=slug,
        created_by=request.user,
        status__in=[Library.Status.PENDING, Library.Status.APPROVED],
    )

    for field_name in LIBRARY_UPDATE_FIELDS:
        if field_name in submitted_fields:
            setattr(library, field_name, getattr(payload, field_name))

    if submitted_coordinates:
        if payload.latitude is None or payload.longitude is None:
            return 400, ErrorOut(message="Latitude and longitude must be provided together.")
        library.location = Point(x=payload.longitude, y=payload.latitude, srid=4326)

    if photo is not None:
        library.photo = photo

    library.status = Library.Status.PENDING
    library.save()
    notify_library_update(library)
    return 200, library


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
