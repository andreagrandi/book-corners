from django.contrib.gis import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html

from libraries.models import Library, LibraryPhoto, Report


class LibraryPhotoInline(admin.TabularInline):
    """Inline display of community photos on the Library change page."""

    model = LibraryPhoto
    extra = 0
    readonly_fields = ["photo_preview", "created_by", "created_at"]
    fields = ["photo_preview", "caption", "status", "created_by", "created_at"]

    @admin.display(description="Preview")
    def photo_preview(self, obj: LibraryPhoto) -> str:
        """Render a small thumbnail of the community photo.
        Gives admins a visual preview without leaving the library page."""
        if obj.photo:
            return format_html('<img src="{}" style="max-height:80px;">', obj.photo.url)
        return "-"


@admin.register(Library)
class LibraryAdmin(admin.GISModelAdmin):
    """Admin configuration for Library model."""

    list_display = ["name", "city", "country", "status", "created_at"]
    list_filter = ["status", "city", "country"]
    search_fields = ["name", "address", "city"]
    readonly_fields = ["slug", "created_at", "updated_at"]
    actions = ["approve_libraries", "reject_libraries"]
    inlines = [LibraryPhotoInline]

    @admin.action(description="Approve selected libraries")
    def approve_libraries(
        self, request: HttpRequest, queryset: QuerySet[Library]
    ) -> None:
        """Handle approve libraries.
        Supports the module workflow with a focused operation."""
        count = queryset.update(status=Library.Status.APPROVED)
        self.message_user(
            request, f"{count} {'library' if count == 1 else 'libraries'} approved."
        )

    @admin.action(description="Reject selected libraries")
    def reject_libraries(
        self, request: HttpRequest, queryset: QuerySet[Library]
    ) -> None:
        """Handle reject libraries.
        Supports the module workflow with a focused operation."""
        count = queryset.update(status=Library.Status.REJECTED)
        self.message_user(
            request, f"{count} {'library' if count == 1 else 'libraries'} rejected."
        )


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """Admin configuration for Report model."""

    list_display = ["library", "reason", "status", "created_by", "created_at"]
    list_filter = ["status", "reason"]
    search_fields = ["details"]
    readonly_fields = ["created_at"]
    actions = ["resolve_reports", "dismiss_reports"]

    @admin.action(description="Resolve selected reports")
    def resolve_reports(
        self, request: HttpRequest, queryset: QuerySet[Report]
    ) -> None:
        """Handle resolve reports.
        Supports the module workflow with a focused operation."""
        count = queryset.update(status=Report.Status.RESOLVED)
        self.message_user(
            request, f"{count} {'report' if count == 1 else 'reports'} resolved."
        )

    @admin.action(description="Dismiss selected reports")
    def dismiss_reports(
        self, request: HttpRequest, queryset: QuerySet[Report]
    ) -> None:
        """Handle dismiss reports.
        Supports the module workflow with a focused operation."""
        count = queryset.update(status=Report.Status.DISMISSED)
        self.message_user(
            request, f"{count} {'report' if count == 1 else 'reports'} dismissed."
        )


@admin.register(LibraryPhoto)
class LibraryPhotoAdmin(admin.ModelAdmin):
    """Admin configuration for LibraryPhoto model."""

    list_display = ["library", "status", "caption", "created_by", "created_at"]
    list_filter = ["status"]
    search_fields = ["caption"]
    readonly_fields = ["created_at"]
    actions = ["approve_photos", "reject_photos", "set_as_primary_photo"]

    @admin.action(description="Approve selected photos")
    def approve_photos(
        self, request: HttpRequest, queryset: QuerySet[LibraryPhoto]
    ) -> None:
        """Approve selected community photos and auto-promote when needed.
        Copies the first approved photo to the library if it has no primary."""
        photos = list(queryset.select_related("library"))
        promoted_libraries: set[int] = set()
        for photo in photos:
            photo.status = LibraryPhoto.Status.APPROVED
            photo.save(update_fields=["status"])

            library = photo.library
            if not library.photo and library.pk not in promoted_libraries:
                library.photo = photo.photo
                library.photo_thumbnail = photo.photo_thumbnail
                library.save(update_fields=["photo", "photo_thumbnail"])
                promoted_libraries.add(library.pk)

        count = len(photos)
        self.message_user(
            request, f"{count} {'photo' if count == 1 else 'photos'} approved."
        )

    @admin.action(description="Reject selected photos")
    def reject_photos(
        self, request: HttpRequest, queryset: QuerySet[LibraryPhoto]
    ) -> None:
        """Reject selected community photos to hide them from galleries.
        Updates status so rejected photos are not publicly visible."""
        count = queryset.update(status=LibraryPhoto.Status.REJECTED)
        self.message_user(
            request, f"{count} {'photo' if count == 1 else 'photos'} rejected."
        )

    @admin.action(description="Set selected photo as library primary photo")
    def set_as_primary_photo(
        self, request: HttpRequest, queryset: QuerySet[LibraryPhoto]
    ) -> None:
        """Promote a single community photo to the library's primary photo.
        Copies the photo and thumbnail to the parent library record."""
        if queryset.count() != 1:
            self.message_user(
                request,
                "Please select exactly one photo to set as primary.",
                level="error",
            )
            return

        library_photo = queryset.select_related("library").first()
        library = library_photo.library
        library.photo = library_photo.photo
        library.photo_thumbnail = library_photo.photo_thumbnail
        library.save(update_fields=["photo", "photo_thumbnail"])

        if library_photo.status != LibraryPhoto.Status.APPROVED:
            library_photo.status = LibraryPhoto.Status.APPROVED
            library_photo.save(update_fields=["status"])

        self.message_user(
            request, f"Primary photo updated for {library}."
        )
