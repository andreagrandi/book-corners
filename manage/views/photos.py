from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from libraries.models import LibraryPhoto
from manage.decorators import staff_required

PHOTOS_PER_PAGE = 24


@staff_required
def photo_list(request: HttpRequest) -> HttpResponse:
    """List library photos in a grid with status filtering."""
    status = request.GET.get("status", "")
    qs = LibraryPhoto.objects.select_related("library", "created_by").all()

    if status:
        qs = qs.filter(status=status)

    paginator = Paginator(qs, PHOTOS_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page,
        "current_status": status,
        "total_count": paginator.count,
        "status_choices": LibraryPhoto.Status.choices,
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

    # Promote to library primary if library has no photo
    library = photo.library
    if not library.photo:
        library.photo = photo.photo
        library.photo_thumbnail = photo.photo_thumbnail
        library.save(update_fields=["photo", "photo_thumbnail"])

    if request.headers.get("HX-Request"):
        return render(request, "manage/photos/_card.html", {"photo": photo})
    return redirect("manage:photo_list")


@staff_required
@require_POST
def photo_reject(request: HttpRequest, pk: int) -> HttpResponse:
    """Reject a single photo."""
    photo = get_object_or_404(LibraryPhoto, pk=pk)
    photo.status = LibraryPhoto.Status.REJECTED
    photo.save(update_fields=["status"])

    if request.headers.get("HX-Request"):
        return render(request, "manage/photos/_card.html", {"photo": photo})
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
