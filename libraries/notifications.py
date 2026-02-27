"""Admin email notifications for new submissions requiring moderation."""

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


def notify_new_library(library) -> None:
    """Send an admin notification about a new library submission.
    Fails silently so Resend outages never block user submissions."""
    recipient = _get_admin_email()
    if not recipient:
        return

    admin_url = _get_admin_url(
        app_label="libraries", model_name="library", object_id=library.pk,
    )
    subject = f"New library submission: {library.name or library.address}"
    body = (
        f"A new library has been submitted and needs review.\n\n"
        f"Name: {library.name or '(unnamed)'}\n"
        f"City: {library.city}\n"
        f"Submitted by: {library.created_by}\n\n"
        f"Review it in admin:\n{admin_url}\n"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[recipient],
        )
    except Exception:
        logger.exception("Failed to send new-library notification for library %s", library.pk)


def notify_new_report(report) -> None:
    """Send an admin notification about a new library report.
    Fails silently so Resend outages never block user submissions."""
    recipient = _get_admin_email()
    if not recipient:
        return

    admin_url = _get_admin_url(
        app_label="libraries", model_name="report", object_id=report.pk,
    )
    details_excerpt = (report.details or "")[:200]
    subject = f"New report: {report.library}"
    body = (
        f"A new report has been submitted and needs review.\n\n"
        f"Library: {report.library}\n"
        f"Reason: {report.get_reason_display()}\n"
        f"Details: {details_excerpt}\n"
        f"Submitted by: {report.created_by}\n\n"
        f"Review it in admin:\n{admin_url}\n"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[recipient],
        )
    except Exception:
        logger.exception("Failed to send new-report notification for report %s", report.pk)


def notify_new_photo(photo) -> None:
    """Send an admin notification about a new community photo submission.
    Fails silently so Resend outages never block user submissions."""
    recipient = _get_admin_email()
    if not recipient:
        return

    admin_url = _get_admin_url(
        app_label="libraries", model_name="libraryphoto", object_id=photo.pk,
    )
    subject = f"New photo submission: {photo.library}"
    body = (
        f"A new community photo has been submitted and needs review.\n\n"
        f"Library: {photo.library}\n"
        f"Caption: {photo.caption or '(none)'}\n"
        f"Submitted by: {photo.created_by}\n\n"
        f"Review it in admin:\n{admin_url}\n"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[recipient],
        )
    except Exception:
        logger.exception("Failed to send new-photo notification for photo %s", photo.pk)
