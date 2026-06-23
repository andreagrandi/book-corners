from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from users.models import DeviceToken, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    pass


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
