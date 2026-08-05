from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from users.models import ContributorAgreementAcceptance, DeviceToken, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    pass


@admin.register(ContributorAgreementAcceptance)
class ContributorAgreementAcceptanceAdmin(admin.ModelAdmin):
    """Display contributor agreement acceptance evidence without edit access.
    Keeps the legal audit trail inspectable but not manually rewriteable."""

    list_display = ("user", "agreement_version", "channel", "accepted_at")
    list_filter = ("agreement_version", "channel", "accepted_at")
    search_fields = ("user__username", "user__email", "agreement_version")
    readonly_fields = ("user", "agreement_version", "channel", "accepted_at")

    def has_add_permission(self, request):
        """Prevent manual creation of acceptance evidence.
        Acceptance records must originate from an authenticated application flow."""
        return False

    def has_change_permission(self, request, obj=None):
        """Prevent edits to immutable acceptance evidence.
        Preserves the original user, version, timestamp, and channel."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion through the admin interface.
        Keeps the audit trail available for inspection after creation."""
        return False


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "environment",
        "is_active",
        "created_at",
        "updated_at",
        "last_used_at",
    )
    list_filter = ("environment", "is_active", "created_at", "last_used_at")
    search_fields = ("token", "user__username", "user__email")
    readonly_fields = ("created_at", "updated_at", "last_used_at")
