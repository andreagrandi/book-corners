from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.paginator import Page, Paginator
from django.db import connection
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from libraries.forms import LibrarySearchForm, LibrarySubmissionForm
from libraries.geolocation import (
    extract_gps_coordinates,
    forward_geocode_place,
    reverse_geocode_coordinates,
)
from libraries.models import Library

LATEST_ENTRIES_PAGE_SIZE = 9
DEFAULT_SUBMIT_MAP_LATITUDE = 48.8566
DEFAULT_SUBMIT_MAP_LONGITUDE = 2.3522
DEFAULT_SEARCH_RADIUS_KM = 10


def _parse_page_number(value: str | None) -> int:
    """Parse a querystring page value into a safe page number.
    Falls back to page 1 for missing, invalid, or negative inputs."""
    if value is None:
        return 1

    try:
        page_number = int(value)
    except ValueError:
        return 1

    if page_number < 1:
        return 1

    return page_number


def _get_latest_entries_page(*, page_number: int) -> Page:
    """Return a paginated page of approved libraries.
    Keeps homepage and HTMX pagination behavior consistent."""
    queryset = (
        Library.objects.filter(status=Library.Status.APPROVED)
        .order_by("-created_at")
    )
    paginator = Paginator(queryset, LATEST_ENTRIES_PAGE_SIZE)
    return paginator.get_page(page_number)


def _is_htmx_request(*, request: HttpRequest) -> bool:
    """Check whether the current request originates from HTMX.
    Allows one view to return either full pages or partial fragments."""
    return request.headers.get("HX-Request", "").lower() == "true"


def _has_active_search_criteria(*, cleaned_data: dict[str, object]) -> bool:
    """Detect whether the user provided any meaningful search filters.
    Prevents showing empty-state results before a real search is submitted."""
    for key in ("q", "near", "city", "country", "postal_code"):
        value = cleaned_data.get(key)
        if isinstance(value, str) and value:
            return True
    return False


def _apply_text_search(*, queryset: QuerySet[Library], query_text: str) -> QuerySet[Library]:
    """Filter approved libraries by text on name and description.
    Uses PostgreSQL full-text search with a safe fallback for non-Postgres DBs."""
    if connection.vendor != "postgresql":
        return queryset.filter(
            Q(name__icontains=query_text) | Q(description__icontains=query_text)
        )

    search_vector = SearchVector("name", weight="A") + SearchVector("description", weight="B")
    search_query = SearchQuery(query_text, search_type="plain")
    return (
        queryset
        .annotate(search=search_vector, rank=SearchRank(search_vector, search_query))
        .filter(search=search_query)
        .order_by("-rank", "-created_at")
    )


def _run_library_search(*, cleaned_data: dict[str, object]) -> tuple[QuerySet[Library], bool]:
    """Execute combined text, field, and proximity search filters.
    Returns a queryset plus a flag indicating unresolved place queries."""
    query_text = str(cleaned_data.get("q") or "")
    near_text = str(cleaned_data.get("near") or "")
    city = str(cleaned_data.get("city") or "")
    country = str(cleaned_data.get("country") or "")
    postal_code = str(cleaned_data.get("postal_code") or "")

    radius_km_value = cleaned_data.get("radius_km")
    radius_km = radius_km_value if isinstance(radius_km_value, int) else DEFAULT_SEARCH_RADIUS_KM

    queryset = Library.objects.filter(status=Library.Status.APPROVED).order_by("-created_at")

    if city:
        queryset = queryset.filter(city__icontains=city)
    if country:
        queryset = queryset.filter(country__iexact=country)
    if postal_code:
        queryset = queryset.filter(postal_code__icontains=postal_code)

    if query_text:
        queryset = _apply_text_search(queryset=queryset, query_text=query_text)

    location_resolution_failed = False
    if near_text:
        coordinates = forward_geocode_place(
            place_query=near_text,
            user_agent=settings.NOMINATIM_USER_AGENT,
            timeout_seconds=settings.NOMINATIM_TIMEOUT_SECONDS,
            country_code=country or None,
        )
        if coordinates is None:
            location_resolution_failed = True
            if not query_text:
                queryset = _apply_text_search(queryset=queryset, query_text=near_text)
        else:
            latitude, longitude = coordinates
            center_point = Point(x=longitude, y=latitude, srid=4326)
            queryset = (
                queryset
                .annotate(distance=Distance("location", center_point))
                .filter(location__distance_lte=(center_point, D(km=radius_km)))
                .order_by("distance", "-created_at")
            )
            return queryset, location_resolution_failed

    return queryset, location_resolution_failed


def home(request: HttpRequest) -> HttpResponse:
    """Render the homepage with the first latest-entries page.
    Loads approved libraries for the initial full-page response."""
    page_obj = _get_latest_entries_page(page_number=1)
    return render(
        request,
        "home.html",
        {
            "latest_entries_page": page_obj,
        },
    )


def latest_entries(request: HttpRequest) -> HttpResponse:
    """Render the HTMX partial for paginated latest entries.
    Supports incremental loading without a full page refresh."""
    page_value = request.GET.get("page")
    page_number = _parse_page_number(page_value if isinstance(page_value, str) else None)
    page_obj = _get_latest_entries_page(page_number=page_number)

    return render(
        request,
        "libraries/_latest_entries.html",
        {
            "page_obj": page_obj,
            "is_first_page": page_obj.number == 1,
        },
    )


def search_libraries(request: HttpRequest) -> HttpResponse:
    """Render the search page and HTMX results fragment.
    Supports keyword, structured filters, and radius-based place searches."""
    form = LibrarySearchForm(request.GET or None)
    has_submitted_search = False
    location_resolution_failed = False
    libraries = Library.objects.none()
    near_query = ""

    if form.is_valid():
        cleaned_data = form.cleaned_data
        near_query = str(cleaned_data.get("near") or "")
        has_submitted_search = _has_active_search_criteria(cleaned_data=cleaned_data)
        if has_submitted_search:
            libraries, location_resolution_failed = _run_library_search(
                cleaned_data=cleaned_data
            )
    elif request.GET:
        has_submitted_search = True
        near_value = request.GET.get("near", "")
        near_query = near_value if isinstance(near_value, str) else ""

    context = {
        "form": form,
        "libraries": libraries,
        "has_submitted_search": has_submitted_search,
        "location_resolution_failed": location_resolution_failed,
        "near_query": near_query,
    }

    template_name = (
        "libraries/_search_results.html"
        if _is_htmx_request(request=request)
        else "libraries/search.html"
    )
    return render(request, template_name, context)


def library_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Render one public library detail or the creator's own submission.
    Keeps non-approved entries private while allowing author review."""
    visibility_filter = Q(status=Library.Status.APPROVED)
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        visibility_filter |= Q(created_by=user)

    library = get_object_or_404(
        Library,
        visibility_filter,
        slug=slug,
    )
    return render(
        request,
        "libraries/library_detail.html",
        {
            "library": library,
        },
    )


@login_required(login_url="login")
def dashboard(request: HttpRequest) -> HttpResponse:
    """Render the authenticated dashboard with user submissions.
    Shows the current user's libraries and moderation statuses."""
    submissions = Library.objects.filter(created_by=request.user).order_by("-created_at")
    return render(
        request,
        "libraries/dashboard.html",
        {
            "submissions": submissions,
        },
    )


@login_required(login_url="login")
def submit_library(request: HttpRequest) -> HttpResponse:
    """Render and process the authenticated submit-library form.
    Saves valid submissions as pending entries with map-selected coordinates."""
    current_user = getattr(request, "user", None)
    form = LibrarySubmissionForm(
        data=request.POST or None,
        files=request.FILES or None,
        created_by=current_user,
    )

    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("submit_library_confirmation")

    return render(
        request,
        "libraries/submit_library.html",
        {
            "form": form,
            "submit_map_default_latitude": DEFAULT_SUBMIT_MAP_LATITUDE,
            "submit_map_default_longitude": DEFAULT_SUBMIT_MAP_LONGITUDE,
        },
    )


def submit_library_confirmation(request: HttpRequest) -> HttpResponse:
    """Render the post-submission confirmation page.
    Confirms the library was queued for moderation review."""
    return render(request, "libraries/submit_library_confirmation.html")


@login_required(login_url="login")
def submit_library_photo_metadata(request: HttpRequest) -> JsonResponse:
    """Return EXIF GPS and reverse-geocoded photo metadata as JSON.
    Powers submit-form prefill when users upload geotagged images."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    photo = request.FILES.get("photo")
    if photo is None:
        return JsonResponse({"error": "Photo is required."}, status=400)

    coordinates = extract_gps_coordinates(photo)
    if coordinates is None:
        return JsonResponse({"gps_found": False})

    latitude, longitude = coordinates
    geocoded_data = reverse_geocode_coordinates(
        latitude=latitude,
        longitude=longitude,
        user_agent=settings.NOMINATIM_USER_AGENT,
        timeout_seconds=settings.NOMINATIM_TIMEOUT_SECONDS,
    )

    response_data = {
        "gps_found": True,
        "latitude": round(latitude, 6),
        "longitude": round(longitude, 6),
        "geocoded": geocoded_data is not None,
        "address": "",
        "city": "",
        "country": "",
        "postal_code": "",
    }
    if geocoded_data is not None:
        response_data.update(geocoded_data)

    return JsonResponse(response_data)


def style_preview(request: HttpRequest) -> HttpResponse:
    """Render a dedicated Tailwind and daisyUI preview page.
    Provides a stable endpoint for CSS smoke and integration checks."""
    return render(request, "libraries/style_preview.html")
