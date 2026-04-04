"""Email notifications for submissions, moderation, and social posting."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

logger = logging.getLogger(__name__)


def _get_admin_email() -> str | None:
    """Return the configured admin notification email address.
    Returns None when notifications are disabled."""
    email = getattr(settings, "ADMIN_NOTIFICATION_EMAIL", "")
    return email if email else None


def _get_manage_library_url(library_pk: int) -> str:
    """Build a full manage URL for a library detail page.
    Uses SITE_URL so links work in production emails."""
    site_url = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{site_url}/manage/libraries/{library_pk}/"


def notify_new_library(library) -> None:
    """Send an admin notification about a new library submission.
    Fails silently so Resend outages never block user submissions."""
    recipient = _get_admin_email()
    if not recipient:
        return

    manage_url = _get_manage_library_url(library.pk)
    subject = f"New library submission: {library.name or library.address}"
    lines = [
        "A new library has been submitted and needs review.\n",
        f"Name: {library.name or '(unnamed)'}",
        f"City: {library.city}",
    ]
    if library.description:
        lines.append(f"Description: {library.description}")
    lines.append(f"Submitted by: {library.created_by}")
    lines.append(f"\nReview it:\n{manage_url}")
    body = "\n".join(lines)

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

    manage_url = _get_manage_library_url(report.library.pk)
    details_excerpt = (report.details or "")[:200]
    subject = f"New report: {report.library}"
    body = (
        f"A new report has been submitted and needs review.\n\n"
        f"Library: {report.library}\n"
        f"Reason: {report.get_reason_display()}\n"
        f"Details: {details_excerpt}\n"
        f"Submitted by: {report.created_by}\n\n"
        f"Review it:\n{manage_url}\n"
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

    manage_url = _get_manage_library_url(photo.library.pk)
    subject = f"New photo submission: {photo.library}"
    body = (
        f"A new community photo has been submitted and needs review.\n\n"
        f"Library: {photo.library}\n"
        f"Caption: {photo.caption or '(none)'}\n"
        f"Submitted by: {photo.created_by}\n\n"
        f"Review it:\n{manage_url}\n"
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


def notify_social_post(social_post) -> None:
    """Send an admin notification about a successful social media post.
    Fails silently so email outages never block the posting workflow."""
    recipient = _get_admin_email()
    if not recipient:
        return

    library = social_post.library
    manage_url = _get_manage_library_url(library.pk)
    subject = f"Social post published: {library.name or library.address}"
    lines = [
        "A library has been posted to social media.\n",
        f"Library: {library.name or library.address}",
        f"City: {library.city}",
        f"\nPost text:\n{social_post.post_text}",
    ]
    if social_post.mastodon_url:
        lines.append(f"\nMastodon: {social_post.mastodon_url}")
    if social_post.bluesky_url:
        lines.append(f"\nBluesky: {social_post.bluesky_url}")
    if social_post.instagram_url:
        lines.append(f"\nInstagram: {social_post.instagram_url}")
    lines.append(f"\nView library:\n{manage_url}")
    body = "\n".join(lines)

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[recipient],
        )
    except Exception:
        logger.exception("Failed to send social-post notification for post %s", social_post.pk)


def notify_social_post_error(library, error_details: str) -> None:
    """Send an admin notification about a failed social media post.
    Fails silently so email outages never block the posting workflow."""
    recipient = _get_admin_email()
    if not recipient:
        return

    manage_url = _get_manage_library_url(library.pk)
    subject = f"Social post failed: {library.name or library.address}"
    body = (
        f"Social media posting failed for a library.\n\n"
        f"Library: {library.name or library.address}\n"
        f"City: {library.city}\n\n"
        f"Errors:\n{error_details}\n\n"
        f"View library:\n{manage_url}\n"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[recipient],
        )
    except Exception:
        logger.exception("Failed to send social-post-error notification for library %s", library.pk)


def notify_library_approved(library) -> None:
    """Email the submitter when their library is approved.
    Fails silently so email outages never block the approval workflow."""
    if not library.created_by or not library.created_by.email:
        return

    site_url = getattr(settings, "SITE_URL", "").rstrip("/")
    detail_path = reverse("library_detail", kwargs={"slug": library.slug})
    public_url = f"{site_url}{detail_path}"

    subject = "Your library is now live on Book Corners!"
    library_label = library.name or library.address
    body = (
        f"Great news! Your library \"{library_label}\" "
        f"in {library.city} has been approved and is now live.\n\n"
        f"View it here:\n{public_url}\n\n"
        f"Thank you for contributing to Book Corners!\n"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email="no-reply@bookcorners.org",
            recipient_list=[library.created_by.email],
        )
    except Exception:
        logger.exception("Failed to send approval notification for library %s", library.pk)


def notify_library_rejected(library) -> None:
    """Email the submitter when their library is rejected with the reason.
    Fails silently so email outages never block the rejection workflow."""
    if not library.created_by or not library.created_by.email:
        return

    library_label = library.name or library.address
    subject = "Update on your Book Corners submission"
    body = (
        f"Thank you for your submission of \"{library_label}\" "
        f"in {library.city}, but unfortunately we could not add "
        f"the library you submitted, for the following reason:\n\n"
        f"{library.rejection_reason}\n\n"
        f"If you have any questions, feel free to reach out.\n"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email="no-reply@bookcorners.org",
            recipient_list=[library.created_by.email],
        )
    except Exception:
        logger.exception("Failed to send rejection notification for library %s", library.pk)
