"""Tests for user registration email notifications."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings

from users.notifications import notify_new_registration

User = get_user_model()


@pytest.fixture()
def registered_user(db):
    """Create a user for notification tests.
    Avoids coupling to the root conftest user fixture."""
    return User.objects.create_user(
        username="newuser",
        email="newuser@example.com",
        password="testpass123",
    )


@pytest.mark.django_db()
class TestNotifyNewRegistration:
    """Tests for the new-registration notification helper."""

    @override_settings(
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
        SITE_URL="https://example.com",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_sends_email_with_correct_subject_and_admin_link(self, registered_user):
        """Verify that one email is sent with username, email, method, and admin URL."""
        notify_new_registration(registered_user, via="email")

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert "newuser" in message.subject
        assert "newuser@example.com" in message.body
        assert "Registration method: email" in message.body
        assert f"/admin/users/user/{registered_user.pk}/change/" in message.body
        assert "https://example.com" in message.body
        assert message.to == ["admin@example.com"]

    @override_settings(
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
        SITE_URL="https://example.com",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_includes_oauth_registration_method(self, registered_user):
        """Verify that the registration method reflects OAuth when specified."""
        notify_new_registration(registered_user, via="Google OAuth")

        assert len(mail.outbox) == 1
        assert "Registration method: Google OAuth" in mail.outbox[0].body

    @override_settings(ADMIN_NOTIFICATION_EMAIL="")
    def test_no_email_when_admin_email_not_configured(self, registered_user):
        """Verify that no email is sent when ADMIN_NOTIFICATION_EMAIL is empty."""
        notify_new_registration(registered_user)

        assert len(mail.outbox) == 0

    @override_settings(
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_email_failure_does_not_raise(self, registered_user):
        """Verify that a send_mail exception is swallowed silently."""
        with patch(
            "users.notifications.send_mail",
            side_effect=Exception("SMTP down"),
        ):
            notify_new_registration(registered_user)  # should not raise
