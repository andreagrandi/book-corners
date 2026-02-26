from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point, Polygon
from django.contrib.gis.measure import D
from django.core.paginator import Page, Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from libraries.forms import LibrarySearchForm, LibrarySubmissionForm, ReportSubmissionForm
from libraries.geolocation import (
    extract_gps_coordinates,
    forward_geocode_place,
    reverse_geocode_coordinates,
)
from libraries.models import Library
from libraries.search import DEFAULT_SEARCH_RADIUS_KM, apply_text_search, run_library_search

LATEST_ENTRIES_PAGE_SIZE = 9
DEFAULT_SUBMIT_MAP_LATITUDE = 48.8566
DEFAULT_SUBMIT_MAP_LONGITUDE = 2.3522
DEFAULT_MAP_CENTER_LATITUDE = 50.1109
DEFAULT_MAP_CENTER_LONGITUDE = 8.6821
DEFAULT_MAP_ZOOM_LEVEL = 5
MAP_LIST_PAGE_SIZE = 12


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


def _run_library_search(
    *,
    cleaned_data: dict[str, object],
) -> tuple[QuerySet[Library], bool, tuple[float, float] | None]:
    """Execute combined text, field, and proximity search filters.
    Returns queryset, geocoding-failure flag, and optional resolved map center."""
    query_text = str(cleaned_data.get("q") or "")
    near_text = str(cleaned_data.get("near") or "")
    city = str(cleaned_data.get("city") or "")
    country = str(cleaned_data.get("country") or "")
    postal_code = str(cleaned_data.get("postal_code") or "")

    radius_km_value = cleaned_data.get("radius_km")
    radius_km = radius_km_value if isinstance(radius_km_value, int) else DEFAULT_SEARCH_RADIUS_KM

    if not near_text:
        queryset = run_library_search(
            q=query_text,
            city=city,
            country=country,
            postal_code=postal_code,
        )
        return queryset, False, None

    queryset = run_library_search(
        q=query_text,
        city=city,
        country=country,
        postal_code=postal_code,
    )

    coordinates = forward_geocode_place(
        place_query=near_text,
        user_agent=settings.NOMINATIM_USER_AGENT,
        timeout_seconds=settings.NOMINATIM_TIMEOUT_SECONDS,
        country_code=country or None,
    )
    if coordinates is None:
        if not query_text:
            queryset = apply_text_search(queryset=queryset, query_text=near_text)
        return queryset, True, None

    latitude, longitude = coordinates
    center_point = Point(x=longitude, y=latitude, srid=4326)
    queryset = (
        queryset
        .annotate(distance=Distance("location", center_point))
        .filter(location__distance_lte=(center_point, D(km=radius_km)))
        .order_by("distance", "-created_at")
    )
    return queryset, False, (latitude, longitude)


def _serialize_library_geojson_feature(*, library: Library) -> dict[str, object]:
    """Serialize one approved library into a GeoJSON feature.
    Provides marker coordinates and popup metadata for the map page."""
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [library.location.x, library.location.y],
        },
        "properties": {
            "id": library.id,
            "slug": library.slug,
            "name": library.name or "Neighborhood Library",
            "city": library.city,
            "country": library.country,
            "address": library.address,
            "photo_url": library.card_photo_url,
            "detail_url": reverse("library_detail", kwargs={"slug": library.slug}),
        },
    }


def _parse_query_float(*, request: HttpRequest, key: str) -> float | None:
    """Parse an optional float query parameter from the request.
    Returns None for missing or invalid numeric inputs."""
    raw_value = request.GET.get(key)
    if not isinstance(raw_value, str) or raw_value == "":
        return None

    try:
        return float(raw_value)
    except ValueError:
        return None


def _get_map_bounds_polygon(*, request: HttpRequest) -> Polygon | None:
    """Build a bounding-box polygon from query parameters.
    Returns None when bounds are missing or outside valid ranges."""
    min_lat = _parse_query_float(request=request, key="min_lat")
    min_lng = _parse_query_float(request=request, key="min_lng")
    max_lat = _parse_query_float(request=request, key="max_lat")
    max_lng = _parse_query_float(request=request, key="max_lng")

    if None in (min_lat, min_lng, max_lat, max_lng):
        return None

    if min_lat > max_lat or min_lng > max_lng:
        return None

    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        return None
    if not (-180 <= min_lng <= 180 and -180 <= max_lng <= 180):
        return None

    bounds_polygon = Polygon.from_bbox((min_lng, min_lat, max_lng, max_lat))
    bounds_polygon.srid = 4326
    return bounds_polygon


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


def about_page(request: HttpRequest) -> HttpResponse:
    """Render the project about page with mission and contribution details.
    Gives visitors clear context about goals, participation, and ownership."""
    return render(request, "about.html")


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


def _run_map_filters(
    *,
    form: LibrarySearchForm,
) -> tuple[QuerySet[Library], bool, str, tuple[float, float] | None]:
    """Apply shared filters for map markers and list results.
    Keeps map and list views synchronized from one filtering source."""
    near_query = ""
    location_resolution_failed = False
    resolved_center: tuple[float, float] | None = None

    if form.is_valid():
        cleaned_data = form.cleaned_data
        near_query = str(cleaned_data.get("near") or "")
        queryset, location_resolution_failed, resolved_center = _run_library_search(
            cleaned_data=cleaned_data
        )
        return queryset, location_resolution_failed, near_query, resolved_center

    queryset = Library.objects.filter(status=Library.Status.APPROVED).order_by("-created_at")
    return queryset, location_resolution_failed, near_query, resolved_center


def map_page(request: HttpRequest) -> HttpResponse:
    """Render the full map page and filter controls.
    Loads the map shell while marker data is fetched asynchronously."""
    requested_view = request.GET.get("view")
    map_view = requested_view if requested_view in {"map", "list", "split"} else ""
    form = LibrarySearchForm(request.GET or None)
    return render(
        request,
        "libraries/map.html",
        {
            "form": form,
            "map_view": map_view,
            "map_default_latitude": DEFAULT_MAP_CENTER_LATITUDE,
            "map_default_longitude": DEFAULT_MAP_CENTER_LONGITUDE,
            "map_default_zoom": DEFAULT_MAP_ZOOM_LEVEL,
        },
    )


def map_libraries_geojson(request: HttpRequest) -> JsonResponse:
    """Return approved libraries as a GeoJSON feature collection.
    Applies optional search filters so map markers update without page reloads."""
    form = LibrarySearchForm(request.GET or None)
    queryset, location_resolution_failed, near_query, resolved_center = _run_map_filters(form=form)
    map_bounds_polygon = _get_map_bounds_polygon(request=request)
    if map_bounds_polygon is not None:
        queryset = queryset.filter(location__within=map_bounds_polygon)

    features = [
        _serialize_library_geojson_feature(library=library)
        for library in queryset
    ]

    center_payload: dict[str, float] | None = None
    if resolved_center is not None:
        center_payload = {
            "lat": round(resolved_center[0], 6),
            "lng": round(resolved_center[1], 6),
        }

    payload: dict[str, object] = {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "near_query": near_query,
            "location_resolution_failed": location_resolution_failed,
            "center": center_payload,
            "bounds_applied": map_bounds_polygon is not None,
        },
    }
    if form.errors:
        payload_meta = payload["meta"]
        if isinstance(payload_meta, dict):
            payload_meta["form_errors"] = form.errors.get_json_data()

    return JsonResponse(payload)


def map_libraries_list(request: HttpRequest) -> HttpResponse:
    """Render filtered library cards for the map list panel.
    Returns an HTML fragment so list updates without full page reloads."""
    form = LibrarySearchForm(request.GET or None)
    libraries, location_resolution_failed, near_query, _ = _run_map_filters(form=form)
    page_value = request.GET.get("page")
    page_number = _parse_page_number(page_value if isinstance(page_value, str) else None)
    paginator = Paginator(libraries, MAP_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "libraries/_map_results_list.html",
        {
            "page_obj": page_obj,
            "location_resolution_failed": location_resolution_failed,
            "near_query": near_query,
            "form_errors": form.errors,
        },
    )


def _get_detail_visible_library(*, request: HttpRequest, slug: str) -> Library:
    """Fetch a library that is visible from the detail page context.
    Allows approved entries publicly and pending entries only for their owners."""
    visibility_filter = Q(status=Library.Status.APPROVED)
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        visibility_filter |= Q(created_by=user)

    return get_object_or_404(
        Library,
        visibility_filter,
        slug=slug,
    )


def library_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Render one public library detail or the creator's own submission.
    Keeps non-approved entries private while allowing author review."""
    library = _get_detail_visible_library(request=request, slug=slug)
    report_form: ReportSubmissionForm | None = None
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        report_form = ReportSubmissionForm(created_by=user, library=library)

    return render(
        request,
        "libraries/library_detail.html",
        {
            "library": library,
            "report_form": report_form,
        },
    )


@login_required(login_url="login")
def submit_library_report(request: HttpRequest, slug: str) -> HttpResponse:
    """Process inline report submissions from the library detail page.
    Returns HTMX-ready partial content for success or validation errors."""
    if request.method != "POST":
        return HttpResponse("Method not allowed.", status=405)

    library = _get_detail_visible_library(request=request, slug=slug)
    current_user = getattr(request, "user", None)
    report_form = ReportSubmissionForm(
        data=request.POST or None,
        files=request.FILES or None,
        created_by=current_user,
        library=library,
    )

    if report_form.is_valid():
        report_form.save()
        return render(
            request,
            "libraries/_report_form.html",
            {
                "library": library,
                "report_submitted": True,
                "report_form": ReportSubmissionForm(created_by=current_user, library=library),
            },
        )

    return render(
        request,
        "libraries/_report_form.html",
        {
            "library": library,
            "report_submitted": False,
            "report_form": report_form,
        },
        status=422,
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


def privacy_page(request: HttpRequest) -> HttpResponse:
    """Render the privacy policy page.
    Provides a dedicated route for legal and data-handling disclosures."""
    return render(request, "privacy.html")


def style_preview(request: HttpRequest) -> HttpResponse:
    """Render a dedicated Tailwind and daisyUI preview page.
    Provides a stable endpoint for CSS smoke and integration checks."""
    return render(request, "libraries/style_preview.html")
