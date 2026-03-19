"""Email notifications for user registration events."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _get_admin_email() -> str | None:
    """Return the configured admin notification email address.
    Returns None when notifications are disabled."""
    email = getattr(settings, "ADMIN_NOTIFICATION_EMAIL", "")
    return email if email else None


def _get_admin_url(*, app_label: str, model_name: str, object_id: int) -> str:
    """Build a full admin change-page URL for the given object.
    Uses SITE_URL so links work in production emails."""
    site_url = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{site_url}/admin/{app_label}/{model_name}/{object_id}/change/"


def notify_new_registration(user, *, via: str = "email") -> None:
    """Send an admin notification about a new user registration.
    Fails silently so errors never block the registration flow."""
    recipient = _get_admin_email()
    if not recipient:
        return

    admin_url = _get_admin_url(
        app_label="users", model_name="user", object_id=user.pk,
    )
    subject = f"New user registration: {user.username}"
    body = (
        f"A new user has registered on Book Corners.\n\n"
        f"Username: {user.username}\n"
        f"Email: {user.email or '(not provided)'}\n"
        f"Registration method: {via}\n\n"
        f"View in admin:\n{admin_url}\n"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[recipient],
        )
    except Exception:
        logger.exception("Failed to send registration notification for user %s", user.pk)
