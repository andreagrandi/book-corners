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


class ContributorAgreementAcceptance(models.Model):
    """Immutable record of one contributor agreement acceptance.
    Retains version, timestamp, and channel evidence independently of the user profile."""

    class Channel(models.TextChoices):
        """Server-controlled channels that can create acceptance records.
        Keeps audit provenance explicit without trusting request input."""

        API_CREDENTIAL_REGISTRATION = (
            "api_credential_registration",
            "API credential registration",
        )
        API_SOCIAL_APPLE = "api_social_apple", "API Apple registration"
        API_SOCIAL_GOOGLE = "api_social_google", "API Google registration"
        API_EXISTING_USER = "api_existing_user", "API existing-user acceptance"
        WEB_CREDENTIAL_REGISTRATION = (
            "web_credential_registration",
            "Web credential registration",
        )
        WEB_SOCIAL_APPLE = "web_social_apple", "Web Apple registration"
        WEB_SOCIAL_GOOGLE = "web_social_google", "Web Google registration"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contributor_agreement_acceptances",
    )
    agreement_version = models.CharField(max_length=32)
    accepted_at = models.DateTimeField(auto_now_add=True)
    channel = models.CharField(max_length=40, choices=Channel.choices)

    class Meta:
        db_table = "contributor_agreement_acceptances"
        ordering = ["-accepted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "agreement_version"],
                name="unique_user_agreement_version",
            ),
        ]
        indexes = [
            models.Index(
                fields=["agreement_version", "-accepted_at"],
                name="idx_agreement_version_time",
            ),
        ]

    def __str__(self) -> str:
        """Return a readable acceptance audit label.
        Includes the agreement version and channel without exposing unnecessary data."""
        return f"{self.agreement_version} ({self.channel})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Prevent updates to an existing acceptance record.
        Preserves the original audit fields after the initial write."""
        if not self._state.adding:
            raise ValueError("Contributor agreement acceptances are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Prevent direct deletion of an acceptance record.
        Preserves audit evidence through normal application code paths."""
        raise ValueError("Contributor agreement acceptances cannot be deleted.")


class PublicDatasetEmailDelivery(models.Model):
    """Record one successful public-dataset email delivery.
    Prevents duplicate sends when the one-time command is retried."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="public_dataset_email_delivery",
    )
    recipient_email = models.EmailField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "public_dataset_email_deliveries"
        ordering = ["sent_at"]

    def __str__(self) -> str:
        """Return a concise delivery audit label.
        Identifies the account and successful delivery time."""
        return f"{self.user} at {self.sent_at}"


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
