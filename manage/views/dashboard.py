from django.shortcuts import render

from libraries.models import Library, LibraryPhoto, Report
from manage.decorators import staff_required
from users.models import User


@staff_required
def dashboard(request):
    """Render the custom admin dashboard with moderation queue summaries."""
    pending_libraries = Library.objects.filter(status=Library.Status.PENDING)
    open_reports = Report.objects.filter(status=Report.Status.OPEN)
    pending_photos = LibraryPhoto.objects.filter(status=LibraryPhoto.Status.PENDING)

    context = {
        "pending_libraries_count": pending_libraries.count(),
        "pending_libraries_recent": pending_libraries.select_related("created_by")[:5],
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

    return render(request, "manage/dashboard.html", context)
