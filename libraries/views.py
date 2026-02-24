from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Page, Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from libraries.forms import LibrarySubmissionForm
from libraries.models import Library

LATEST_ENTRIES_PAGE_SIZE = 9
DEFAULT_SUBMIT_MAP_LATITUDE = 48.8566
DEFAULT_SUBMIT_MAP_LONGITUDE = 2.3522


def _parse_page_number(value: str | None) -> int:
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
    queryset = (
        Library.objects.filter(status=Library.Status.APPROVED)
        .order_by("-created_at")
    )
    paginator = Paginator(queryset, LATEST_ENTRIES_PAGE_SIZE)
    return paginator.get_page(page_number)


def home(request: HttpRequest) -> HttpResponse:
    page_obj = _get_latest_entries_page(page_number=1)
    return render(
        request,
        "home.html",
        {
            "latest_entries_page": page_obj,
        },
    )


def latest_entries(request: HttpRequest) -> HttpResponse:
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
    return render(request, "libraries/submit_library_confirmation.html")


def style_preview(request: HttpRequest) -> HttpResponse:
    return render(request, "libraries/style_preview.html")
