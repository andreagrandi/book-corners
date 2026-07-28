from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse

from libraries.models import Library, LibraryPhoto, Report
from manage.decorators import staff_required
from users.models import User


@staff_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Render the custom admin dashboard with moderation queue summaries.
    Routes the summary card to the only active queue when one is available."""
    pending_libraries = Library.objects.filter(
        Q(status=Library.Status.PENDING)
        | Q(pending_changes__isnull=False)
    ).order_by("-updated_at", "-created_at")
    pending_libraries_recent = [
        library.moderation_preview()
        for library in pending_libraries.select_related("created_by")[:5]
    ]
    open_reports = Report.objects.filter(status=Report.Status.OPEN)
    pending_photos = LibraryPhoto.objects.filter(status=LibraryPhoto.Status.PENDING)

    context = {
        "pending_libraries_count": pending_libraries.count(),
        "pending_libraries_recent": pending_libraries_recent,
        "open_reports_count": open_reports.count(),
        "open_reports_recent": open_reports.select_related("library", "created_by")[:5],
        "pending_photos_count": pending_photos.count(),
        "pending_photos_recent": pending_photos.select_related("library", "created_by")[:5],
        "total_libraries": Library.objects.filter(status=Library.Status.APPROVED).count(),
        "total_users": User.objects.count(),
    }
    context["total_pending"] = (
        context["pending_libraries_count"]
        + context["open_reports_count"]
        + context["pending_photos_count"]
    )
    pending_destinations: list[str] = []
    if context["pending_libraries_count"]:
        library_list_url = reverse("manage:library_list")
        pending_destinations.append(
            f"{library_list_url}?status={Library.Status.PENDING}"
        )
    if context["open_reports_count"]:
        report_list_url = reverse("manage:report_list")
        pending_destinations.append(
            f"{report_list_url}?status={Report.Status.OPEN}"
        )
    if context["pending_photos_count"]:
        photo_list_url = reverse("manage:photo_list")
        pending_destinations.append(
            f"{photo_list_url}?status={LibraryPhoto.Status.PENDING}"
            "&type=community"
        )
    dashboard_url = reverse("manage:dashboard")
    context["pending_moderation_url"] = (
        pending_destinations[0]
        if len(pending_destinations) == 1
        else f"{dashboard_url}#moderation-queues"
    )

    return render(request, "manage/dashboard.html", context)
