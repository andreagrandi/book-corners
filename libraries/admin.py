import json

from django.contrib import messages
from django.contrib.gis import admin
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from libraries.geojson_import import parse_geojson
from libraries.management.commands.find_duplicates import (
    DEFAULT_RADIUS_METERS,
    find_duplicate_groups,
)
from libraries.models import Library, LibraryPhoto, Report, SocialPost
from libraries.notifications import notify_library_approved
from libraries.views import GEOJSON_CACHE_KEY, HOMEPAGE_COUNT_CACHE_KEY, invalidate_cluster_cache


class LibraryPhotoInline(admin.TabularInline):
    """Inline display of community photos on the Library change page."""

    model = LibraryPhoto
    extra = 0
    readonly_fields = ["photo_preview", "created_by", "created_at"]
    fields = ["photo_preview", "caption", "status", "created_by", "created_at"]

    def get_queryset(self, request):
        """Prefetch creator to avoid N+1 queries on inline display."""
        return super().get_queryset(request).select_related("created_by")

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
        "country",
        "wheelchair_accessible",
        "is_indoor",
        "is_lit",
        "source",
        "brand",
    ]
    search_fields = ["name", "address", "city"]
    readonly_fields = ["slug", "photo_preview", "created_at", "updated_at"]
    fields = [
        "name",
        "description",
        "photo",
        "photo_preview",
        "photo_thumbnail",
        "location",
        "address",
        "city",
        "country",
        "postal_code",
        "wheelchair_accessible",
        "capacity",
        "is_indoor",
        "is_lit",
        "website",
        "contact",
        "source",
        "operator",
        "brand",
        "external_id",
        "status",
        "created_by",
        "slug",
        "created_at",
        "updated_at",
    ]
    actions = ["approve_libraries", "reject_libraries"]
    inlines = [LibraryPhotoInline]

    @admin.display(description="Photo preview")
    def photo_preview(self, obj: Library) -> str:
        """Render an inline preview of the library photo.
        Lets admins visually review the photo without clicking the file link."""
        if obj.photo:
            return format_html('<img src="{}" style="max-height:200px;">', obj.photo.url)
        return "-"

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
            path(
                "photo-grid/",
                self.admin_site.admin_view(self.photo_grid_view),
                name="libraries_library_photo_grid",
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

        import tempfile
        from django.conf import settings

        imports_dir = settings.MEDIA_ROOT / "geojson_imports"
        imports_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=imports_dir, suffix=".json", delete=False, mode="w"
        ) as tmp:
            json.dump(geojson_data, tmp)
            geojson_path = tmp.name

        from libraries.tasks import run_geojson_import

        run_geojson_import.enqueue(
            geojson_path=geojson_path,
            source=source,
            status=status,
            user_id=request.user.pk,
        )

        messages.success(
            request,
            f"Import of {len(candidates)} features has been queued for background processing.",
        )
        return redirect("admin:libraries_library_changelist")

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
            "use_proximity": True,
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
                cache.delete(GEOJSON_CACHE_KEY)
                cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
                invalidate_cluster_cache()
                context["deleted_count"] = deleted_count
            return render(request, "admin/libraries/find_duplicates.html", context)

        radius = int(request.GET.get("radius", DEFAULT_RADIUS_METERS))
        filter_city = request.GET.get("city", "").strip()
        filter_country = request.GET.get("country", "").strip()
        use_proximity = request.GET.get("proximity", "on") == "on"
        scanned = "radius" in request.GET

        context["radius"] = radius
        context["filter_city"] = filter_city
        context["filter_country"] = filter_country
        context["use_proximity"] = use_proximity
        context["scanned"] = scanned

        if scanned:
            groups = find_duplicate_groups(
                radius_meters=radius,
                city=filter_city,
                country=filter_country,
                use_proximity=use_proximity,
            )
            context["groups"] = groups
            context["total_duplicates"] = sum(len(g) - 1 for g in groups)

        return render(request, "admin/libraries/find_duplicates.html", context)

    def photo_grid_view(self, request: HttpRequest) -> HttpResponse:
        """Show all photos across libraries in a resizable grid.
        Combines primary library photos and community submissions for quick review."""
        status_filter = request.GET.get("status", "all")
        type_filter = request.GET.get("type", "all")

        photos: list[dict] = []

        # Primary library photos
        if type_filter in ("all", "primary"):
            qs = Library.objects.exclude(photo="").select_related("created_by")
            if status_filter != "all":
                qs = qs.filter(status=status_filter)
            for lib in qs.order_by("-created_at"):
                thumb = lib.photo_thumbnail.url if lib.photo_thumbnail else lib.photo.url
                photos.append({
                    "thumbnail_url": thumb,
                    "library_name": lib.name,
                    "library_url": reverse("admin:libraries_library_change", args=[lib.pk]),
                    "photo_type": "primary",
                    "status": lib.get_status_display(),
                    "status_raw": lib.status,
                    "submitted_by": str(lib.created_by) if lib.created_by else "",
                    "date": lib.created_at,
                })

        # Community-submitted photos
        if type_filter in ("all", "community"):
            qs = LibraryPhoto.objects.select_related("library", "created_by")
            if status_filter != "all":
                qs = qs.filter(status=status_filter)
            for photo in qs.order_by("-created_at"):
                thumb = photo.photo_thumbnail.url if photo.photo_thumbnail else photo.photo.url
                photos.append({
                    "thumbnail_url": thumb,
                    "library_name": photo.library.name,
                    "library_url": reverse(
                        "admin:libraries_library_change", args=[photo.library.pk]
                    ),
                    "photo_type": "community",
                    "status": photo.get_status_display(),
                    "status_raw": photo.status,
                    "submitted_by": str(photo.created_by) if photo.created_by else "",
                    "date": photo.created_at,
                })

        # Sort combined results by date descending
        photos.sort(key=lambda p: p["date"], reverse=True)

        paginator = Paginator(photos, 60)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context = {
            **self.admin_site.each_context(request),
            "title": "Photo Grid",
            "opts": self.model._meta,
            "photos": page_obj,
            "page_obj": page_obj,
            "status_filter": status_filter,
            "type_filter": type_filter,
        }
        return render(request, "admin/libraries/photo_grid.html", context)

    @admin.action(description="Approve selected libraries")
    def approve_libraries(
        self, request: HttpRequest, queryset: QuerySet[Library]
    ) -> None:
        """Approve selected libraries and notify submitters via email.
        Only sends notifications for libraries transitioning from pending."""
        to_notify = list(
            queryset.filter(status=Library.Status.PENDING).select_related("created_by")
        )
        count = queryset.update(status=Library.Status.APPROVED)
        cache.delete(GEOJSON_CACHE_KEY)
        cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
        invalidate_cluster_cache()
        for library in to_notify:
            notify_library_approved(library)
        self.message_user(
            request, f"{count} {'library' if count == 1 else 'libraries'} approved."
        )

    def save_model(self, request, obj, form, change):
        """Save library and notify submitter when status changes to approved.
        Detects the pending-to-approved transition by comparing with the DB."""
        was_pending = False
        if change and obj.pk:
            old_status = Library.objects.filter(pk=obj.pk).values_list("status", flat=True).first()
            was_pending = old_status == Library.Status.PENDING
        super().save_model(request, obj, form, change)
        cache.delete(GEOJSON_CACHE_KEY)
        cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
        invalidate_cluster_cache()
        if was_pending and obj.status == Library.Status.APPROVED:
            notify_library_approved(obj)

    @admin.action(description="Reject selected libraries")
    def reject_libraries(
        self, request: HttpRequest, queryset: QuerySet[Library]
    ) -> None:
        """Handle reject libraries.
        Supports the module workflow with a focused operation."""
        count = queryset.update(status=Library.Status.REJECTED)
        cache.delete(GEOJSON_CACHE_KEY)
        cache.delete(HOMEPAGE_COUNT_CACHE_KEY)
        invalidate_cluster_cache()
        self.message_user(
            request, f"{count} {'library' if count == 1 else 'libraries'} rejected."
        )


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """Admin configuration for Report model."""

    list_display = ["library", "reason", "status", "created_by", "created_at"]
    list_filter = ["status", "reason"]
    list_select_related = ["library", "created_by"]
    search_fields = ["details"]
    readonly_fields = ["created_at"]
    autocomplete_fields = ["library"]
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


@admin.register(SocialPost)
class SocialPostAdmin(admin.ModelAdmin):
    """Admin configuration for SocialPost model with read-only fields and links."""

    list_display = ["library_link", "posted_at", "mastodon_url_short", "bluesky_url_short"]
    list_select_related = ["library"]
    readonly_fields = [
        "library_admin_link",
        "post_text",
        "posted_at",
        "mastodon_url_link",
        "bluesky_url_link",
    ]
    fields = [
        "library_admin_link",
        "post_text",
        "posted_at",
        "mastodon_url_link",
        "bluesky_url_link",
    ]

    def has_add_permission(self, request):
        """Prevent manual creation of social post records.
        Posts are created automatically by the management command."""
        return False

    def has_change_permission(self, request, obj=None):
        """Prevent editing of social post records.
        All fields are read-only by design."""
        return False

    @admin.display(description="Library")
    def library_link(self, obj):
        """Render the library name as a link to its admin change page.
        Makes navigation between related records easy."""
        from django.urls import reverse

        url = reverse("admin:libraries_library_change", args=[obj.library.pk])
        return format_html('<a href="{}">{}</a>', url, obj.library)

    @admin.display(description="Library")
    def library_admin_link(self, obj):
        """Render the library name as a clickable admin link in detail view.
        Provides quick navigation to the parent library record."""
        from django.urls import reverse

        url = reverse("admin:libraries_library_change", args=[obj.library.pk])
        return format_html('<a href="{}">{}</a>', url, obj.library)

    @admin.display(description="Mastodon")
    def mastodon_url_short(self, obj):
        """Show a truncated Mastodon URL in the list view.
        Keeps the table compact while still showing the link."""
        if not obj.mastodon_url:
            return "-"
        short = obj.mastodon_url[:50] + "..." if len(obj.mastodon_url) > 50 else obj.mastodon_url
        return format_html('<a href="{}" target="_blank">{}</a>', obj.mastodon_url, short)

    @admin.display(description="Bluesky")
    def bluesky_url_short(self, obj):
        """Show a truncated Bluesky URL in the list view.
        Keeps the table compact while still showing the link."""
        if not obj.bluesky_url:
            return "-"
        short = obj.bluesky_url[:50] + "..." if len(obj.bluesky_url) > 50 else obj.bluesky_url
        return format_html('<a href="{}" target="_blank">{}</a>', obj.bluesky_url, short)

    @admin.display(description="Mastodon URL")
    def mastodon_url_link(self, obj):
        """Render the full Mastodon URL as a clickable link in detail view.
        Opens in a new tab for easy verification."""
        if not obj.mastodon_url:
            return "-"
        return format_html('<a href="{}" target="_blank">{}</a>', obj.mastodon_url, obj.mastodon_url)

    @admin.display(description="Bluesky URL")
    def bluesky_url_link(self, obj):
        """Render the full Bluesky URL as a clickable link in detail view.
        Opens in a new tab for easy verification."""
        if not obj.bluesky_url:
            return "-"
        return format_html('<a href="{}" target="_blank">{}</a>', obj.bluesky_url, obj.bluesky_url)


@admin.register(LibraryPhoto)
class LibraryPhotoAdmin(admin.ModelAdmin):
    """Admin configuration for LibraryPhoto model."""

    list_display = ["library", "status", "caption", "created_by", "created_at"]
    list_filter = ["status"]
    list_select_related = ["library", "created_by"]
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

        # Batch update all photo statuses at once
        queryset.update(status=LibraryPhoto.Status.APPROVED)

        # Only loop for library promotion logic
        for photo in photos:
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
