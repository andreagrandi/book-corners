from django.contrib.gis import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from libraries.models import Library, Report


@admin.register(Library)
class LibraryAdmin(admin.GISModelAdmin):
    """Admin configuration for Library model."""

    list_display = ["name", "city", "country", "status", "created_at"]
    list_filter = ["status", "city", "country"]
    search_fields = ["name", "address", "city"]
    readonly_fields = ["slug", "created_at", "updated_at"]
    actions = ["approve_libraries", "reject_libraries"]

    @admin.action(description="Approve selected libraries")
    def approve_libraries(
        self, request: HttpRequest, queryset: QuerySet[Library]
    ) -> None:
        count = queryset.update(status=Library.Status.APPROVED)
        self.message_user(
            request, f"{count} {'library' if count == 1 else 'libraries'} approved."
        )

    @admin.action(description="Reject selected libraries")
    def reject_libraries(
        self, request: HttpRequest, queryset: QuerySet[Library]
    ) -> None:
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
        count = queryset.update(status=Report.Status.RESOLVED)
        self.message_user(
            request, f"{count} {'report' if count == 1 else 'reports'} resolved."
        )

    @admin.action(description="Dismiss selected reports")
    def dismiss_reports(
        self, request: HttpRequest, queryset: QuerySet[Report]
    ) -> None:
        count = queryset.update(status=Report.Status.DISMISSED)
        self.message_user(
            request, f"{count} {'report' if count == 1 else 'reports'} dismissed."
        )
