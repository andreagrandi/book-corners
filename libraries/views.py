from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Page, Paginator
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from libraries.forms import LibrarySubmissionForm
from libraries.geolocation import extract_gps_coordinates, reverse_geocode_coordinates
from libraries.models import Library

LATEST_ENTRIES_PAGE_SIZE = 9
DEFAULT_SUBMIT_MAP_LATITUDE = 48.8566
DEFAULT_SUBMIT_MAP_LONGITUDE = 2.3522


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


def library_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Render a detail page for one approved library.
    Returns 404 for missing slugs or non-approved entries."""
    library = get_object_or_404(
        Library,
        slug=slug,
        status=Library.Status.APPROVED,
    )
    return render(
        request,
        "libraries/library_detail.html",
        {
            "library": library,
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
