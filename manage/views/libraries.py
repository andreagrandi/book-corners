from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from libraries.models import Library
from libraries.notifications import notify_library_approved, notify_library_rejected
from libraries.views import (
    GEOJSON_CACHE_KEY,
    HOMEPAGE_COUNT_CACHE_KEY,
    invalidate_cluster_cache,
)
from manage.decorators import staff_required
from manage.forms import LibraryFilterForm

LIBRARIES_PER_PAGE = 25


def _invalidate_library_caches():
    """Clear all caches affected by library data changes."""
    cache.delete(GEOJSON_CACHE_KEY)
    cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
    invalidate_cluster_cache()


def _get_filtered_libraries(request: HttpRequest):
    """Return a filtered queryset and bound form from request GET params."""
    form = LibraryFilterForm(request.GET)
    qs = Library.objects.select_related("created_by").all()

    if form.is_valid():
        status = form.cleaned_data.get("status")
        country = form.cleaned_data.get("country")
        source = form.cleaned_data.get("source")
        q = form.cleaned_data.get("q")

        if status:
            qs = qs.filter(status=status)
        if country:
            qs = qs.filter(country__iexact=country)
        if source:
            qs = qs.filter(source__icontains=source)
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(address__icontains=q) | Q(city__icontains=q)
            )

    return qs, form


@staff_required
def library_list(request: HttpRequest) -> HttpResponse:
    """List libraries with filtering, search, and pagination."""
    qs, form = _get_filtered_libraries(request)

    paginator = Paginator(qs, LIBRARIES_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page,
        "form": form,
        "total_count": paginator.count,
    }

    if request.headers.get("HX-Request"):
        return render(request, "manage/libraries/_table.html", context)
    return render(request, "manage/libraries/list.html", context)


@staff_required
@require_POST
def library_approve(request: HttpRequest, pk: int) -> HttpResponse:
    """Approve a single library and notify the submitter."""
    library = get_object_or_404(Library, pk=pk)
    was_pending = library.status == Library.Status.PENDING
    library.status = Library.Status.APPROVED
    library.save(update_fields=["status", "updated_at"])
    _invalidate_library_caches()
    if was_pending:
        notify_library_approved(library)

    if request.headers.get("HX-Request"):
        return render(request, "manage/libraries/_row.html", {"library": library})
    return redirect("manage:library_list")


@staff_required
@require_POST
def library_reject(request: HttpRequest, pk: int) -> HttpResponse:
    """Reject a single library."""
    library = get_object_or_404(Library, pk=pk)
    library.status = Library.Status.REJECTED
    rejection_reason = request.POST.get("rejection_reason", "")
    if rejection_reason:
        library.rejection_reason = rejection_reason
    library.save(update_fields=["status", "rejection_reason", "updated_at"])
    _invalidate_library_caches()
    if rejection_reason:
        notify_library_rejected(library)

    if request.headers.get("HX-Request"):
        return render(request, "manage/libraries/_row.html", {"library": library})
    return redirect("manage:library_list")


@staff_required
@require_POST
def library_bulk_action(request: HttpRequest) -> HttpResponse:
    """Handle bulk approve/reject actions on selected libraries."""
    action = request.POST.get("action")
    ids = request.POST.getlist("selected")

    if not ids or action not in ("approve", "reject"):
        return redirect("manage:library_list")

    qs = Library.objects.filter(pk__in=ids)

    if action == "approve":
        to_notify = list(
            qs.filter(status=Library.Status.PENDING).select_related("created_by")
        )
        qs.update(status=Library.Status.APPROVED)
        for library in to_notify:
            notify_library_approved(library)

    elif action == "reject":
        qs.update(status=Library.Status.REJECTED)

    _invalidate_library_caches()
    return redirect("manage:library_list")


@staff_required
def library_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Display library detail for review."""
    library = get_object_or_404(
        Library.objects.select_related("created_by"), pk=pk
    )
    photos = library.user_photos.select_related("created_by").all()[:12]
    reports = library.reports.select_related("created_by").all()[:10]
    context = {
        "library": library,
        "photos": photos,
        "reports": reports,
    }
    return render(request, "manage/libraries/detail.html", context)
