"""Tests for admin email notifications on new submissions."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core import mail
from django.test import override_settings

from libraries.models import Library, LibraryPhoto, Report
from libraries.notifications import notify_new_library, notify_new_photo, notify_new_report

User = get_user_model()


@pytest.fixture()
def notification_user(db):
    """Create a basic user for notification tests.
    Avoids coupling to the root conftest user fixture."""
    return User.objects.create_user(username="notifier", password="testpass123")


@pytest.fixture()
def approved_library(notification_user):
    """Create an approved library for report and photo notification tests.
    Uses minimal fields to keep fixtures focused."""
    return Library.objects.create(
        name="Test Library",
        address="123 Main St",
        city="Berlin",
        country="DE",
        location=Point(x=13.405, y=52.52, srid=4326),
        status=Library.Status.APPROVED,
        created_by=notification_user,
    )


@pytest.mark.django_db()
class TestNotifyNewLibrary:
    """Tests for the new-library notification helper."""

    @override_settings(
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
        SITE_URL="https://example.com",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_sends_email_with_correct_subject_and_admin_link(self, notification_user):
        """Verify that one email is sent with the library name and admin URL."""
        library = Library.objects.create(
            name="Corner Library",
            address="42 Oak Ave",
            city="Paris",
            country="FR",
            location=Point(x=2.35, y=48.86, srid=4326),
            status=Library.Status.PENDING,
            created_by=notification_user,
        )

        notify_new_library(library)

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert "Corner Library" in message.subject
        assert f"/manage/libraries/{library.pk}/" in message.body
        assert "https://example.com" in message.body
        assert message.to == ["admin@example.com"]

    @override_settings(ADMIN_NOTIFICATION_EMAIL="")
    def test_no_email_when_admin_email_not_configured(self, notification_user):
        """Verify that no email is sent when ADMIN_NOTIFICATION_EMAIL is empty."""
        library = Library.objects.create(
            name="Silent Library",
            address="1 Quiet St",
            city="London",
            country="GB",
            location=Point(x=-0.12, y=51.51, srid=4326),
            status=Library.Status.PENDING,
            created_by=notification_user,
        )

        notify_new_library(library)

        assert len(mail.outbox) == 0

    @override_settings(
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_email_failure_does_not_raise(self, notification_user):
        """Verify that a send_mail exception is swallowed silently."""
        library = Library.objects.create(
            name="Failing Library",
            address="99 Error Rd",
            city="Rome",
            country="IT",
            location=Point(x=12.50, y=41.90, srid=4326),
            status=Library.Status.PENDING,
            created_by=notification_user,
        )

        with patch(
            "libraries.notifications.send_mail",
            side_effect=Exception("SMTP down"),
        ):
            notify_new_library(library)  # should not raise


@pytest.mark.django_db()
class TestNotifyNewReport:
    """Tests for the new-report notification helper."""

    @override_settings(
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
        SITE_URL="https://example.com",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_sends_email_with_correct_subject_and_admin_link(self, approved_library, notification_user):
        """Verify that one email is sent with the library name and admin URL."""
        report = Report.objects.create(
            library=approved_library,
            created_by=notification_user,
            reason=Report.Reason.DAMAGED,
            details="The door is broken.",
            status=Report.Status.OPEN,
        )

        notify_new_report(report)

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert str(approved_library) in message.subject
        assert f"/manage/libraries/{report.library.pk}/" in message.body
        assert "Damaged" in message.body

    @override_settings(ADMIN_NOTIFICATION_EMAIL="")
    def test_no_email_when_admin_email_not_configured(self, approved_library, notification_user):
        """Verify that no email is sent when ADMIN_NOTIFICATION_EMAIL is empty."""
        report = Report.objects.create(
            library=approved_library,
            created_by=notification_user,
            reason=Report.Reason.MISSING,
            details="Gone.",
            status=Report.Status.OPEN,
        )

        notify_new_report(report)

        assert len(mail.outbox) == 0


@pytest.mark.django_db()
class TestNotifyNewPhoto:
    """Tests for the new-photo notification helper."""

    @override_settings(
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
        SITE_URL="https://example.com",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_sends_email_with_correct_subject_and_admin_link(self, approved_library, notification_user):
        """Verify that one email is sent with the library name and admin URL."""
        library_photo = LibraryPhoto(
            library=approved_library,
            created_by=notification_user,
            caption="Nice shelves",
            status=LibraryPhoto.Status.PENDING,
        )
        # Save without a real photo file to avoid image processing in tests
        library_photo.photo.name = "libraries/user_photos/2026/01/test.jpg"
        super(LibraryPhoto, library_photo).save()

        notify_new_photo(library_photo)

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert str(approved_library) in message.subject
        assert f"/manage/libraries/{approved_library.pk}/" in message.body
        assert "Nice shelves" in message.body

    @override_settings(ADMIN_NOTIFICATION_EMAIL="")
    def test_no_email_when_admin_email_not_configured(self, approved_library, notification_user):
        """Verify that no email is sent when ADMIN_NOTIFICATION_EMAIL is empty."""
        library_photo = LibraryPhoto(
            library=approved_library,
            created_by=notification_user,
            caption="Silent photo",
            status=LibraryPhoto.Status.PENDING,
        )
        library_photo.photo.name = "libraries/user_photos/2026/01/test.jpg"
        super(LibraryPhoto, library_photo).save()

        notify_new_photo(library_photo)

        assert len(mail.outbox) == 0
