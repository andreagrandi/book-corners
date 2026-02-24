from django.contrib.gis import admin

from libraries.models import Library, Report


@admin.register(Library)
class LibraryAdmin(admin.GISModelAdmin):
    """Admin configuration for Library model."""

    list_display = ["name", "city", "country", "status", "created_at"]
    list_filter = ["status", "city", "country"]
    search_fields = ["name", "address", "city"]
    readonly_fields = ["slug", "created_at", "updated_at"]


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """Admin configuration for Report model."""

    list_display = ["library", "reason", "status", "created_by", "created_at"]
    list_filter = ["status", "reason"]
    search_fields = ["details"]
    readonly_fields = ["created_at"]
