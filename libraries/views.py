from __future__ import annotations

import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point, Polygon
from django.contrib.gis.measure import D
from django.core.cache import cache
from django.core.paginator import Page, Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from libraries.clustering import CLUSTER_ZOOM_THRESHOLD, build_clustered_features, get_grid_size_for_zoom
from libraries.forms import LibraryPhotoSubmissionForm, LibrarySearchForm, LibrarySubmissionForm, ReportSubmissionForm
from libraries.notifications import notify_new_library, notify_new_photo, notify_new_report
from libraries.tasks import enrich_library_with_ai
from libraries.geolocation import (
    extract_gps_coordinates,
    forward_geocode_place,
    reverse_geocode_coordinates,
)
from libraries.models import Favourite, Library
from libraries.search import DEFAULT_SEARCH_RADIUS_KM, apply_text_search, run_library_search
from libraries.stats import build_stats_data

LATEST_ENTRIES_PAGE_SIZE = 9
HOMEPAGE_COUNT_CACHE_KEY = "homepage_total_approved"
HOMEPAGE_COUNT_CACHE_TIMEOUT = 60
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


def _get_latest_entries_page(*, page_number: int) -> tuple[Page, int]:
    """Return a paginated page of approved libraries and the total approved count.
    Keeps homepage and HTMX pagination behavior consistent."""
    total_approved = cache.get(HOMEPAGE_COUNT_CACHE_KEY)
    if total_approved is None:
        total_approved = Library.objects.filter(status=Library.Status.APPROVED).count()
        cache.set(HOMEPAGE_COUNT_CACHE_KEY, total_approved, HOMEPAGE_COUNT_CACHE_TIMEOUT)
    queryset = (
        Library.objects.filter(status=Library.Status.APPROVED)
        .exclude(photo="")
        .order_by("-created_at")
    )
    paginator = Paginator(queryset, LATEST_ENTRIES_PAGE_SIZE)
    return paginator.get_page(page_number), total_approved


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


def _serialize_library_geojson_feature(
    *, library: Library, detail_url_template: str
) -> dict[str, object]:
    """Serialize one approved library into a GeoJSON feature.
    Provides marker coordinates and popup metadata for the map page."""
    if library.photo_thumbnail:
        photo_url = library.photo_thumbnail.url
    elif library.photo:
        photo_url = library.photo.url
    else:
        photo_url = ""

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
            "description": library.description,
            "city": library.city,
            "country": library.country,
            "address": library.address,
            "photo_url": photo_url,
            "detail_url": detail_url_template.replace("__SLUG__", library.slug),
        },
    }


def _parse_query_int(*, request: HttpRequest, key: str) -> int | None:
    """Parse an optional integer query parameter from the request.
    Returns None for missing or invalid inputs."""
    raw_value = request.GET.get(key)
    if not isinstance(raw_value, str) or raw_value == "":
        return None

    try:
        return int(raw_value)
    except ValueError:
        return None


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
    page_obj, total_libraries = _get_latest_entries_page(page_number=1)
    return render(
        request,
        "home.html",
        {
            "latest_entries_page": page_obj,
            "total_libraries": total_libraries,
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
    page_obj, _total = _get_latest_entries_page(page_number=page_number)

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


GEOJSON_CACHE_KEY = "map_geojson_all"
GEOJSON_CACHE_TIMEOUT = 300  # 5 minutes
CLUSTER_CACHE_PREFIX = "map_cluster_v"
CLUSTER_CACHE_VERSION_KEY = "map_cluster_version"
CLUSTER_CACHE_TIMEOUT = 300  # 5 minutes


def invalidate_cluster_cache() -> None:
    """Bump the cluster cache version counter to invalidate all cached cluster responses.
    Called alongside GEOJSON_CACHE_KEY invalidation when library data changes."""
    current = cache.get(CLUSTER_CACHE_VERSION_KEY, 0)
    cache.set(CLUSTER_CACHE_VERSION_KEY, current + 1, timeout=None)


def _build_all_approved_geojson_json() -> str:
    """Serialize all approved libraries to a GeoJSON JSON string.
    The result is cached so subsequent requests skip DB and serialization."""
    cached = cache.get(GEOJSON_CACHE_KEY)
    if cached is not None:
        return cached

    queryset = (
        Library.objects.filter(status=Library.Status.APPROVED)
        .order_by("-created_at")
        .only("id", "slug", "name", "description", "city", "country", "address",
              "location", "photo", "photo_thumbnail")
    )
    detail_url_template = reverse("library_detail", kwargs={"slug": "__SLUG__"})
    features = [
        _serialize_library_geojson_feature(
            library=library, detail_url_template=detail_url_template,
        )
        for library in queryset
    ]
    payload = {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "total_count": len(features),
            "near_query": "",
            "location_resolution_failed": False,
            "center": None,
            "bounds_applied": False,
        },
    }
    json_str = json.dumps(payload)
    cache.set(GEOJSON_CACHE_KEY, json_str, GEOJSON_CACHE_TIMEOUT)
    return json_str


def _build_cluster_cache_key(*, zoom: int, bounds: Polygon | None) -> str:
    """Build a versioned cache key for a clustered GeoJSON response.
    Includes zoom, rounded bounds, and a version counter for invalidation."""
    version = cache.get(CLUSTER_CACHE_VERSION_KEY, 0)
    if bounds is None:
        bounds_part = "none"
    else:
        extent = bounds.extent
        bounds_part = f"{extent[0]:.2f}_{extent[1]:.2f}_{extent[2]:.2f}_{extent[3]:.2f}"
    return f"{CLUSTER_CACHE_PREFIX}{version}_z{zoom}_{bounds_part}"


def map_libraries_geojson(request: HttpRequest) -> JsonResponse | HttpResponse:
    """Return approved libraries as a GeoJSON feature collection.
    Serves a cached JSON string for unfiltered requests to avoid re-serialization."""
    form = LibrarySearchForm(request.GET or None)
    has_search_filters = form.is_valid() and any(
        form.cleaned_data.get(k) for k in ("q", "near", "city", "country", "postal_code")
    )
    has_bounds = _get_map_bounds_polygon(request=request) is not None

    zoom = _parse_query_int(request=request, key="zoom")
    if zoom is not None and zoom < CLUSTER_ZOOM_THRESHOLD and not has_search_filters:
        bounds_polygon = _get_map_bounds_polygon(request=request)
        cluster_cache_key = _build_cluster_cache_key(zoom=zoom, bounds=bounds_polygon)
        cached_cluster = cache.get(cluster_cache_key)
        if cached_cluster is not None:
            return HttpResponse(cached_cluster, content_type="application/json")

        grid_size = get_grid_size_for_zoom(zoom)
        features = build_clustered_features(zoom=zoom, bounds=bounds_polygon)
        total_libraries = sum(
            f["properties"]["point_count"] for f in features
        )
        payload = {
            "type": "FeatureCollection",
            "features": features,
            "meta": {
                "count": total_libraries,
                "total_count": total_libraries,
                "near_query": "",
                "location_resolution_failed": False,
                "center": None,
                "bounds_applied": bounds_polygon is not None,
                "clustered": True,
                "grid_size": grid_size,
            },
        }
        json_str = json.dumps(payload)
        cache.set(cluster_cache_key, json_str, CLUSTER_CACHE_TIMEOUT)
        return HttpResponse(json_str, content_type="application/json")

    if not has_search_filters and not has_bounds:
        json_str = _build_all_approved_geojson_json()
        return HttpResponse(json_str, content_type="application/json")

    if has_search_filters:
        queryset, location_resolution_failed, near_query, resolved_center = _run_map_filters(form=form)
    else:
        queryset = Library.objects.filter(status=Library.Status.APPROVED).order_by("-created_at")
        location_resolution_failed = False
        near_query = ""
        resolved_center = None

    queryset = queryset.only(
        "id", "slug", "name", "city", "country", "address",
        "location", "photo", "photo_thumbnail",
    )
    total_count = queryset.count()

    map_bounds_polygon = _get_map_bounds_polygon(request=request)
    if map_bounds_polygon is not None:
        queryset = queryset.filter(location__within=map_bounds_polygon)

    detail_url_template = reverse("library_detail", kwargs={"slug": "__SLUG__"})
    features = [
        _serialize_library_geojson_feature(
            library=library, detail_url_template=detail_url_template,
        )
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
            "total_count": total_count,
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

    libraries = libraries.only(
        "id", "slug", "name", "city", "country", "address",
        "description", "location", "photo", "photo_thumbnail",
    )

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
        Library.objects.select_related("created_by"),
        visibility_filter,
        slug=slug,
    )


@login_required(login_url="login")
@require_POST
def toggle_favourite(request: HttpRequest, slug: str) -> HttpResponse:
    """Toggle favourite status and return the updated heart button partial."""
    library = get_object_or_404(Library, slug=slug, status=Library.Status.APPROVED)
    deleted_count, _ = Favourite.objects.filter(user=request.user, library=library).delete()
    is_favourited = deleted_count == 0
    if is_favourited:
        Favourite.objects.get_or_create(user=request.user, library=library)
    if not request.headers.get("HX-Request"):
        return redirect("library_detail", slug=slug)
    return render(
        request,
        "libraries/_favourite_button.html",
        {"library": library, "is_favourited": is_favourited},
    )


def library_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Render one public library detail or the creator's own submission.
    Keeps non-approved entries private while allowing author review."""
    library = _get_detail_visible_library(request=request, slug=slug)
    report_form: ReportSubmissionForm | None = None
    photo_form: LibraryPhotoSubmissionForm | None = None
    is_favourited = False
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        report_form = ReportSubmissionForm(created_by=user, library=library)
        if library.status == Library.Status.APPROVED:
            photo_form = LibraryPhotoSubmissionForm(created_by=user, library=library)
            is_favourited = Favourite.objects.filter(user=user, library=library).exists()

    return render(
        request,
        "libraries/library_detail.html",
        {
            "library": library,
            "report_form": report_form,
            "photo_form": photo_form,
            "is_favourited": is_favourited,
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
        report = report_form.save()
        notify_new_report(report)
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
def submit_library_photo(request: HttpRequest, slug: str) -> HttpResponse:
    """Process inline photo submissions from the library detail page.
    Returns HTMX-ready partial content for success or validation errors."""
    if request.method != "POST":
        return HttpResponse("Method not allowed.", status=405)

    library = get_object_or_404(Library, slug=slug, status=Library.Status.APPROVED)
    current_user = getattr(request, "user", None)
    photo_form = LibraryPhotoSubmissionForm(
        data=request.POST or None,
        files=request.FILES or None,
        created_by=current_user,
        library=library,
    )

    if photo_form.is_valid():
        photo = photo_form.save()
        notify_new_photo(photo)
        return render(
            request,
            "libraries/_photo_submission_form.html",
            {
                "library": library,
                "photo_submitted": True,
                "photo_form": LibraryPhotoSubmissionForm(created_by=current_user, library=library),
            },
        )

    return render(
        request,
        "libraries/_photo_submission_form.html",
        {
            "library": library,
            "photo_submitted": False,
            "photo_form": photo_form,
        },
        status=422,
    )


@login_required(login_url="login")
def dashboard(request: HttpRequest) -> HttpResponse:
    """Render the authenticated dashboard with user info and paginated submissions.
    Shows pending libraries first, then by newest creation date."""
    from django.core.paginator import Paginator
    from django.db.models import Case, IntegerField, Value, When

    from users.auth import is_social_only_user

    submissions = (
        Library.objects.filter(created_by=request.user)
        .only(
            "name", "slug", "address", "city", "country",
            "status", "photo", "photo_thumbnail", "created_at",
        )
        .annotate(
            status_order=Case(
                When(status=Library.Status.PENDING, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )
        .order_by("status_order", "-created_at")
    )

    paginator = Paginator(submissions, 10)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    favourites = (
        Library.objects.filter(
            status=Library.Status.APPROVED,
            favourites__user=request.user,
        )
        .only(
            "name", "slug", "address", "city", "country",
            "status", "photo", "photo_thumbnail", "created_at",
        )
        .order_by("-favourites__created_at")
    )

    return render(
        request,
        "libraries/dashboard.html",
        {
            "submissions": page_obj,
            "page_obj": page_obj,
            "is_social_only": is_social_only_user(request.user),
            "favourites": favourites,
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
        library = form.save()
        try:
            enrich_library_with_ai.enqueue(library_id=library.pk)
        except Exception:
            notify_new_library(library)
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


def stats_page(request: HttpRequest) -> HttpResponse:
    """Render the public statistics page with charts and summary data.
    Displays library growth, geographic distribution, and photo coverage."""
    stats = build_stats_data()
    return render(request, "libraries/stats.html", {"stats": stats})


def privacy_page(request: HttpRequest) -> HttpResponse:
    """Render the privacy policy page in the active language.
    Provides a dedicated route for legal and data-handling disclosures."""
    language_code = getattr(request, "LANGUAGE_CODE", "en")
    if language_code == "it":
        return render(request, "privacy_it.html")
    return render(request, "privacy.html")


def style_preview(request: HttpRequest) -> HttpResponse:
    """Render a dedicated Tailwind and daisyUI preview page.
    Provides a stable endpoint for CSS smoke and integration checks."""
    return render(request, "libraries/style_preview.html")
