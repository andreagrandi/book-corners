import json

from django.contrib import messages
from django.contrib.gis import admin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import path
from django.utils.html import format_html

from libraries.geojson_import import GeoJSONImporter, parse_geojson
from libraries.management.commands.find_duplicates import (
    DEFAULT_RADIUS_METERS,
    find_duplicate_groups,
)
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

    change_list_template = "admin/libraries/library_changelist.html"
    list_display = ["name", "city", "country", "status", "created_at"]
    list_filter = [
        "status",
        "city",
        "country",
        "wheelchair_accessible",
        "capacity",
        "is_indoor",
        "is_lit",
        "source",
        "operator",
        "brand",
    ]
    search_fields = ["name", "address", "city"]
    readonly_fields = ["slug", "created_at", "updated_at"]
    actions = ["approve_libraries", "reject_libraries"]
    inlines = [LibraryPhotoInline]

    def get_urls(self):
        """Extend admin URLs with custom management endpoints.
        Adds GeoJSON import and duplicate finder views."""
        custom_urls = [
            path(
                "import-geojson/",
                self.admin_site.admin_view(self.import_geojson_view),
                name="libraries_library_import_geojson",
            ),
            path(
                "find-duplicates/",
                self.admin_site.admin_view(self.find_duplicates_view),
                name="libraries_library_find_duplicates",
            ),
        ]
        return custom_urls + super().get_urls()

    def import_geojson_view(self, request: HttpRequest) -> HttpResponse:
        """Handle GeoJSON file upload and import into Library records.
        Renders a form on GET and processes the upload on POST."""
        if request.method != "POST":
            context = {
                **self.admin_site.each_context(request),
                "title": "Import GeoJSON",
                "opts": self.model._meta,
            }
            return render(request, "admin/libraries/geojson_import_form.html", context)

        uploaded_file = request.FILES.get("geojson_file")
        if not uploaded_file:
            messages.error(request, "Please select a GeoJSON file to upload.")
            context = {
                **self.admin_site.each_context(request),
                "title": "Import GeoJSON",
                "opts": self.model._meta,
            }
            return render(request, "admin/libraries/geojson_import_form.html", context)

        try:
            raw_data = uploaded_file.read().decode("utf-8")
            geojson_data = json.loads(raw_data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            messages.error(request, f"Invalid GeoJSON file: {exc}")
            context = {
                **self.admin_site.each_context(request),
                "title": "Import GeoJSON",
                "opts": self.model._meta,
            }
            return render(request, "admin/libraries/geojson_import_form.html", context)

        source = request.POST.get("source", "").strip()
        status = request.POST.get("status", Library.Status.PENDING)

        if status not in (Library.Status.APPROVED, Library.Status.PENDING):
            status = Library.Status.PENDING

        candidates = parse_geojson(geojson_data)
        importer = GeoJSONImporter(
            source=source,
            status=status,
            created_by=request.user,
        )
        result = importer.run(candidates)

        context = {
            **self.admin_site.each_context(request),
            "title": "Import Results",
            "opts": self.model._meta,
            "result": result,
            "total_features": len(candidates),
        }
        return render(request, "admin/libraries/geojson_import_result.html", context)

    def find_duplicates_view(self, request: HttpRequest) -> HttpResponse:
        """Scan for duplicate libraries and allow bulk deletion.
        GET shows grouped duplicates; POST deletes selected entries."""
        context = {
            **self.admin_site.each_context(request),
            "title": "Find Duplicates",
            "opts": self.model._meta,
            "radius": DEFAULT_RADIUS_METERS,
            "filter_city": "",
            "filter_country": "",
            "scanned": False,
            "groups": [],
            "total_duplicates": 0,
            "deleted_count": None,
        }

        if request.method == "POST":
            delete_ids = request.POST.getlist("delete_ids")
            if delete_ids:
                pk_list = [int(pk) for pk in delete_ids]
                deleted_count = Library.objects.filter(pk__in=pk_list).delete()[0]
                context["deleted_count"] = deleted_count
            return render(request, "admin/libraries/find_duplicates.html", context)

        radius = int(request.GET.get("radius", DEFAULT_RADIUS_METERS))
        filter_city = request.GET.get("city", "").strip()
        filter_country = request.GET.get("country", "").strip()
        scanned = "radius" in request.GET

        context["radius"] = radius
        context["filter_city"] = filter_city
        context["filter_country"] = filter_country
        context["scanned"] = scanned

        if scanned:
            groups = find_duplicate_groups(
                radius_meters=radius,
                city=filter_city,
                country=filter_country,
            )
            context["groups"] = groups
            context["total_duplicates"] = sum(len(g) - 1 for g in groups)

        return render(request, "admin/libraries/find_duplicates.html", context)

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
