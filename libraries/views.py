from __future__ import annotations

from django.core.paginator import Page, Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from libraries.models import Library

LATEST_ENTRIES_PAGE_SIZE = 9


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


def style_preview(request: HttpRequest) -> HttpResponse:
    return render(request, "libraries/style_preview.html")
