from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from libraries.models import Library, LibraryPhoto
from manage.decorators import staff_required

PHOTOS_PER_PAGE = 60


@staff_required
def photo_list(request: HttpRequest) -> HttpResponse:
    """Show combined photo grid with primary library photos and community submissions."""
    status_filter = request.GET.get("status", "all")
    type_filter = request.GET.get("type", "all")

    photos = []

    if type_filter in ("all", "primary"):
        qs = Library.objects.exclude(photo="").select_related("created_by")
        if status_filter != "all":
            qs = qs.filter(status=status_filter)
        for lib in qs.order_by("-created_at"):
            thumb = lib.photo_thumbnail.url if lib.photo_thumbnail else lib.photo.url
            photos.append({
                "thumbnail_url": thumb,
                "library_name": lib.name or lib.address,
                "library_pk": lib.pk,
                "library_url": reverse("manage:library_detail", args=[lib.pk]),
                "photo_type": "primary",
                "status_display": lib.get_status_display(),
                "status_raw": lib.status,
                "submitted_by": str(lib.created_by) if lib.created_by else "",
                "date": lib.created_at,
            })

    if type_filter in ("all", "community"):
        qs = LibraryPhoto.objects.select_related("library", "created_by")
        if status_filter != "all":
            qs = qs.filter(status=status_filter)
        for photo in qs.order_by("-created_at"):
            thumb = photo.photo_thumbnail.url if photo.photo_thumbnail else photo.photo.url
            photos.append({
                "pk": photo.pk,
                "thumbnail_url": thumb,
                "library_name": photo.library.name or photo.library.address,
                "library_pk": photo.library.pk,
                "library_url": reverse("manage:library_detail", args=[photo.library.pk]),
                "photo_type": "community",
                "status_display": photo.get_status_display(),
                "status_raw": photo.status,
                "submitted_by": str(photo.created_by) if photo.created_by else "",
                "date": photo.created_at,
            })

    photos.sort(key=lambda p: p["date"], reverse=True)

    paginator = Paginator(photos, PHOTOS_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "total_count": paginator.count,
    }

    if request.headers.get("HX-Request"):
        return render(request, "manage/photos/_grid.html", context)
    return render(request, "manage/photos/list.html", context)


@staff_required
@require_POST
def photo_approve(request: HttpRequest, pk: int) -> HttpResponse:
    """Approve a single photo and promote it to the library primary if first."""
    photo = get_object_or_404(
        LibraryPhoto.objects.select_related("library"), pk=pk
    )
    photo.status = LibraryPhoto.Status.APPROVED
    photo.save(update_fields=["status"])

    library = photo.library
    if not library.photo:
        library.photo = photo.photo
        library.photo_thumbnail = photo.photo_thumbnail
        library.save(update_fields=["photo", "photo_thumbnail"])

    if request.headers.get("HX-Request"):
        return render(request, "manage/photos/_card.html", {"photo": _community_photo_dict(photo)})
    return redirect("manage:photo_list")


@staff_required
@require_POST
def photo_reject(request: HttpRequest, pk: int) -> HttpResponse:
    """Reject a single photo."""
    photo = get_object_or_404(
        LibraryPhoto.objects.select_related("library", "created_by"), pk=pk
    )
    photo.status = LibraryPhoto.Status.REJECTED
    photo.save(update_fields=["status"])

    if request.headers.get("HX-Request"):
        return render(request, "manage/photos/_card.html", {"photo": _community_photo_dict(photo)})
    return redirect("manage:photo_list")


@staff_required
@require_POST
def photo_bulk_action(request: HttpRequest) -> HttpResponse:
    """Handle bulk approve/reject actions on selected photos."""
    action = request.POST.get("action")
    ids = request.POST.getlist("selected")

    if not ids or action not in ("approve", "reject"):
        return redirect("manage:photo_list")

    qs = LibraryPhoto.objects.filter(pk__in=ids)

    if action == "approve":
        photos = list(qs.select_related("library"))
        qs.update(status=LibraryPhoto.Status.APPROVED)
        promoted = set()
        for photo in photos:
            library = photo.library
            if library.pk not in promoted and not library.photo:
                library.photo = photo.photo
                library.photo_thumbnail = photo.photo_thumbnail
                library.save(update_fields=["photo", "photo_thumbnail"])
                promoted.add(library.pk)
    elif action == "reject":
        qs.update(status=LibraryPhoto.Status.REJECTED)

    return redirect("manage:photo_list")


def _community_photo_dict(photo: LibraryPhoto) -> dict:
    """Build a photo dict from a LibraryPhoto for the grid card template."""
    thumb = photo.photo_thumbnail.url if photo.photo_thumbnail else photo.photo.url
    return {
        "pk": photo.pk,
        "thumbnail_url": thumb,
        "library_name": photo.library.name or photo.library.address,
        "library_pk": photo.library.pk,
        "library_url": reverse("manage:library_detail", args=[photo.library.pk]),
        "photo_type": "community",
        "status_display": photo.get_status_display(),
        "status_raw": photo.status,
        "submitted_by": str(photo.created_by) if photo.created_by else "",
        "date": photo.created_at,
    }
