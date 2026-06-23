from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from ninja_jwt.tokens import RefreshToken

from users.models import DeviceToken

User = get_user_model()


@pytest.fixture
def device_user(db):
    """Create a user for device token API tests.
    Keeps device endpoint tests independent from shared fixtures."""
    return User.objects.create_user(
        username="deviceuser",
        email="device@example.com",
        password="testpass123",
    )


def _auth_header(user):
    """Build a Bearer token header for a user.
    Generates a valid JWT access token for authenticated API calls."""
    access_token = str(RefreshToken.for_user(user).access_token)
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}


@pytest.mark.django_db
class TestDeviceTokenAPI:
    """Tests for APNs device token registration endpoints.
    Covers token upsert, unregister, authentication, and rate limiting."""

    def test_register_device_creates_token(self, client, device_user):
        """Verify registering a device token creates an active row.
        Confirms the response mirrors the stored lifecycle state."""
        response = client.post(
            "/api/v1/auth/devices",
            data={
                "token": "abc123",
                "environment": "sandbox",
            },
            content_type="application/json",
            **_auth_header(device_user),
        )

        body = response.json()
        device_token = DeviceToken.objects.get(token="abc123")
        assert response.status_code == 201
        assert body == {
            "token": "abc123",
            "environment": "sandbox",
            "is_active": True,
        }
        assert device_token.user == device_user
        assert device_token.environment == DeviceToken.Environment.SANDBOX.value
        assert device_token.is_active is True

    def test_register_device_upserts_and_reassigns_token(self, client, device_user):
        """Verify registering an existing token reassigns it.
        Handles account switching on the same physical device."""
        other_user = User.objects.create_user(
            username="otherdevice",
            email="other@example.com",
            password="testpass123",
        )
        device_token = DeviceToken.objects.create(
            user=other_user,
            token="shared-token",
            environment=DeviceToken.Environment.SANDBOX.value,
            is_active=False,
        )

        response = client.post(
            "/api/v1/auth/devices",
            data={
                "token": "shared-token",
                "environment": "production",
            },
            content_type="application/json",
            **_auth_header(device_user),
        )

        device_token.refresh_from_db()
        assert response.status_code == 201
        assert device_token.user == device_user
        assert device_token.environment == DeviceToken.Environment.PRODUCTION.value
        assert device_token.is_active is True

    def test_register_device_rejects_blank_token(self, client, device_user):
        """Verify blank device tokens return a structured 400.
        Prevents storing whitespace-only APNs token rows."""
        response = client.post(
            "/api/v1/auth/devices",
            data={
                "token": "   ",
                "environment": "sandbox",
            },
            content_type="application/json",
            **_auth_header(device_user),
        )

        assert response.status_code == 400
        assert response.json()["message"] == "Device token is required."
        assert not DeviceToken.objects.exists()

    def test_register_device_requires_auth(self, client):
        """Verify device registration requires authentication.
        Prevents anonymous clients from attaching tokens to accounts."""
        response = client.post(
            "/api/v1/auth/devices",
            data={
                "token": "abc123",
                "environment": "sandbox",
            },
            content_type="application/json",
        )

        assert response.status_code == 401

    def test_unregister_device_deletes_current_users_token(self, client, device_user):
        """Verify unregister removes only the current user's token.
        Supports logout cleanup without affecting other accounts."""
        device_token = DeviceToken.objects.create(
            user=device_user,
            token="logout-token",
            environment=DeviceToken.Environment.SANDBOX.value,
        )

        response = client.delete(
            f"/api/v1/auth/devices/{device_token.token}",
            **_auth_header(device_user),
        )

        assert response.status_code == 204
        assert not DeviceToken.objects.filter(pk=device_token.pk).exists()

    def test_unregister_device_requires_auth(self, client):
        """Verify device unregister requires authentication.
        Prevents anonymous clients from deleting stored tokens."""
        response = client.delete("/api/v1/auth/devices/abc123")

        assert response.status_code == 401

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_WRITE_REQUESTS=1,
    )
    def test_register_device_rate_limit_returns_429(self, client, device_user):
        """Verify device registration uses the write rate limit.
        Protects token lifecycle endpoints from excessive writes."""
        cache.clear()

        first_response = client.post(
            "/api/v1/auth/devices",
            data={
                "token": "first-token",
                "environment": "sandbox",
            },
            content_type="application/json",
            **_auth_header(device_user),
        )
        second_response = client.post(
            "/api/v1/auth/devices",
            data={
                "token": "second-token",
                "environment": "sandbox",
            },
            content_type="application/json",
            **_auth_header(device_user),
        )

        assert first_response.status_code == 201
        assert second_response.status_code == 429
        assert "Too many requests" in second_response.json()["message"]
