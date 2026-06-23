from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.db.models.functions import Lower


class User(AbstractUser):
    """Custom user account model.
    Adds Book Corners-specific profile fields to Django auth."""

    language = models.CharField(max_length=10, choices=settings.LANGUAGES, default="en")

    class Meta:
        db_table = "users"
        constraints = [
            UniqueConstraint(
                Lower("email"),
                name="unique_email_ci",
                condition=Q(email__gt=""),
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Normalize email to lowercase before persisting.
        Ensures case-insensitive uniqueness across all code paths."""
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class DeviceToken(models.Model):
    """APNs device token registered by an authenticated user.
    Tracks environment and lifecycle data for server-side push delivery."""

    class Environment(models.TextChoices):
        """APNs delivery environment for a device token.
        Keeps development and production tokens routed to the right host."""

        SANDBOX = "sandbox", "Sandbox"
        PRODUCTION = "production", "Production"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    token = models.CharField(max_length=255, unique=True)
    environment = models.CharField(
        max_length=20,
        choices=Environment.choices,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "device_tokens"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "is_active"], name="idx_device_user_active"),
        ]

    def __str__(self) -> str:
        """Return a readable device token label.
        Shows enough context for admin search results and logs."""
        return f"{self.user} ({self.environment})"
