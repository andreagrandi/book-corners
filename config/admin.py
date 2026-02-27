from django.contrib.admin import AdminSite

from libraries.models import Library, LibraryPhoto, Report


class BookCornersAdminSite(AdminSite):
    """Custom admin site with a moderation dashboard on the index page.
    Injects pending/open queue counts and recent items into the template context."""

    site_header = "Book Corners Admin"
    index_title = "Dashboard"

    def index(self, request, extra_context=None):
        """Override the admin index to add moderation queue data.
        Queries pending libraries, open reports, and pending photos."""
        pending_libraries = Library.objects.filter(status=Library.Status.PENDING)
        open_reports = Report.objects.filter(status=Report.Status.OPEN)
        pending_photos = LibraryPhoto.objects.filter(status=LibraryPhoto.Status.PENDING)

        moderation = {
            "pending_libraries_count": pending_libraries.count(),
            "pending_libraries_recent": list(pending_libraries[:5]),
            "open_reports_count": open_reports.count(),
            "open_reports_recent": list(
                open_reports.select_related("library")[:5]
            ),
            "pending_photos_count": pending_photos.count(),
            "pending_photos_recent": list(
                pending_photos.select_related("library")[:5]
            ),
        }
        moderation["total_count"] = (
            moderation["pending_libraries_count"]
            + moderation["open_reports_count"]
            + moderation["pending_photos_count"]
        )

        extra_context = extra_context or {}
        extra_context["moderation"] = moderation
        return super().index(request, extra_context=extra_context)
