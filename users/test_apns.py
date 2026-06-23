from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.test import override_settings

from users import apns
from users.models import DeviceToken
from users.tasks import send_push_to_staff, send_push_to_user

User = get_user_model()


@pytest.fixture
def ec_private_key_pem():
    """Create an EC private key PEM for APNs JWT tests.
    Avoids storing real Apple key material in the repository."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def push_user(db):
    """Create a user for push delivery task tests.
    Keeps APNs task tests independent from shared fixtures."""
    return User.objects.create_user(
        username="pushuser",
        email="push@example.com",
        password="testpass123",
    )


@pytest.fixture
def staff_push_user(db):
    """Create a staff user for staff push delivery tests.
    Provides a recipient for moderation push notifications."""
    return User.objects.create_user(
        username="staffpush",
        email="staffpush@example.com",
        password="testpass123",
        is_staff=True,
    )


class TestAPNSClient:
    """Tests for APNs JWT and HTTP client behavior.
    Keeps Apple integration behavior covered without real network calls."""

    @override_settings(
        APNS_KEY_ID="ABC123DEFG",
        APNS_TEAM_ID="DEF123GHIJ",
    )
    def test_build_jwt_includes_apple_header_and_claims(self, ec_private_key_pem):
        """Verify APNs provider JWT contains required Apple fields.
        Confirms ES256 signing uses the configured key ID and team ID."""
        apns.reset_provider_token_cache()
        with override_settings(APNS_AUTH_KEY=ec_private_key_pem):
            token = apns._build_jwt(now_seconds=1_700_000_000)

        header = jwt.get_unverified_header(token)
        claims = jwt.decode(token, options={"verify_signature": False})
        assert header["alg"] == "ES256"
        assert header["kid"] == "ABC123DEFG"
        assert claims == {
            "iss": "DEF123GHIJ",
            "iat": 1_700_000_000,
        }

    @override_settings(
        APNS_KEY_ID="ABC123DEFG",
        APNS_TEAM_ID="DEF123GHIJ",
    )
    def test_build_jwt_reuses_cached_token(self, ec_private_key_pem):
        """Verify provider JWTs are reused within the cache window.
        Avoids APNs TooManyProviderTokenUpdates responses."""
        apns.reset_provider_token_cache()
        with override_settings(APNS_AUTH_KEY=ec_private_key_pem):
            first_token = apns._build_jwt(now_seconds=1_700_000_000)
            cached_token = apns._build_jwt(now_seconds=1_700_000_060)
            refreshed_token = apns._build_jwt(now_seconds=1_700_004_000)

        assert cached_token == first_token
        assert refreshed_token != first_token

    @override_settings(APNS_BUNDLE_ID="org.bookcorners.app")
    @patch("users.apns._build_jwt", return_value="provider-jwt")
    def test_send_uses_sandbox_host_headers_and_payload(self, mock_build_jwt):
        """Verify sandbox delivery targets the APNs sandbox host.
        Confirms auth headers, topic, alert payload, and custom data."""
        response = httpx.Response(
            200,
            headers={"apns-id": "apns-id-1"},
            request=httpx.Request("POST", "https://example.com"),
        )
        client = MagicMock()
        client.post.return_value = response

        result = apns.send(
            token="sandbox-token",
            title="Review needed",
            body="A library needs review.",
            environment=DeviceToken.Environment.SANDBOX.value,
            data={"type": "library.submitted"},
            client=client,
        )

        client.post.assert_called_once()
        url = client.post.call_args.args[0]
        headers = client.post.call_args.kwargs["headers"]
        payload = client.post.call_args.kwargs["json"]
        assert url == "https://api.sandbox.push.apple.com/3/device/sandbox-token"
        assert headers["authorization"] == "bearer provider-jwt"
        assert headers["apns-topic"] == "org.bookcorners.app"
        assert headers["apns-push-type"] == "alert"
        assert payload["aps"]["alert"]["title"] == "Review needed"
        assert payload["data"] == {"type": "library.submitted"}
        assert result.status_code == 200
        assert result.apns_id == "apns-id-1"
        mock_build_jwt.assert_called_once()

    @override_settings(APNS_BUNDLE_ID="org.bookcorners.app")
    @patch("users.apns._build_jwt", return_value="provider-jwt")
    def test_send_uses_production_host(self, mock_build_jwt):
        """Verify production delivery targets the APNs production host.
        Prevents production tokens from being sent to the sandbox endpoint."""
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://example.com"),
        )
        client = MagicMock()
        client.post.return_value = response

        apns.send(
            token="production-token",
            title="Approved",
            body="A library was approved.",
            environment=DeviceToken.Environment.PRODUCTION.value,
            client=client,
        )

        url = client.post.call_args.args[0]
        assert url == "https://api.push.apple.com/3/device/production-token"
        mock_build_jwt.assert_called_once()

    def test_invalid_token_result_matches_bad_and_unregistered_tokens(self):
        """Verify APNs invalid-token responses are classified for cleanup.
        Covers both 400 BadDeviceToken and 410 Unregistered responses."""
        bad_token = apns.APNSResult(status_code=400, reason="BadDeviceToken")
        unregistered = apns.APNSResult(status_code=410, reason="Unregistered")
        auth_error = apns.APNSResult(status_code=403, reason="InvalidProviderToken")

        assert apns.is_invalid_token_result(result=bad_token) is True
        assert apns.is_invalid_token_result(result=unregistered) is True
        assert apns.is_invalid_token_result(result=auth_error) is False


@pytest.mark.django_db
class TestPushDeliveryTasks:
    """Tests for APNs background delivery tasks.
    Covers no-op behavior, success metadata, staff fan-out, and cleanup."""

    @override_settings(APNS_ENABLED=False)
    @patch("users.tasks.apns.send")
    def test_send_push_to_user_noops_when_apns_disabled(self, mock_send, push_user):
        """Verify disabled APNs settings skip delivery.
        Keeps local development and CI free from credential requirements."""
        DeviceToken.objects.create(
            user=push_user,
            token="disabled-token",
            environment=DeviceToken.Environment.SANDBOX.value,
        )

        send_push_to_user.enqueue(
            user_id=push_user.pk,
            title="Title",
            body="Body",
        )

        mock_send.assert_not_called()

    @override_settings(APNS_ENABLED=True)
    @patch("users.tasks.apns.send")
    def test_send_push_to_user_updates_last_used_on_success(self, mock_send, push_user):
        """Verify successful delivery updates last_used_at.
        Allows operators to identify recently working device tokens."""
        device_token = DeviceToken.objects.create(
            user=push_user,
            token="success-token",
            environment=DeviceToken.Environment.SANDBOX.value,
        )
        mock_send.return_value = apns.APNSResult(status_code=200)

        send_push_to_user.enqueue(
            user_id=push_user.pk,
            title="Title",
            body="Body",
        )

        device_token.refresh_from_db()
        assert device_token.last_used_at is not None
        mock_send.assert_called_once_with(
            token="success-token",
            title="Title",
            body="Body",
            environment=DeviceToken.Environment.SANDBOX.value,
            data=None,
        )

    @override_settings(APNS_ENABLED=True)
    @patch("users.tasks.apns.send")
    def test_send_push_to_user_deletes_unregistered_token(self, mock_send, push_user):
        """Verify APNs 410 responses delete stale tokens.
        Prevents repeated sends to devices APNs says are inactive."""
        device_token = DeviceToken.objects.create(
            user=push_user,
            token="stale-token",
            environment=DeviceToken.Environment.SANDBOX.value,
        )
        mock_send.return_value = apns.APNSResult(status_code=410, reason="Unregistered")

        send_push_to_user.enqueue(
            user_id=push_user.pk,
            title="Title",
            body="Body",
        )

        assert not DeviceToken.objects.filter(pk=device_token.pk).exists()

    @override_settings(APNS_ENABLED=True)
    @patch("users.tasks.apns.send")
    def test_send_push_to_user_deletes_bad_device_token(self, mock_send, push_user):
        """Verify APNs BadDeviceToken responses delete invalid tokens.
        Handles malformed or environment-mismatched APNs tokens."""
        device_token = DeviceToken.objects.create(
            user=push_user,
            token="bad-token",
            environment=DeviceToken.Environment.SANDBOX.value,
        )
        mock_send.return_value = apns.APNSResult(status_code=400, reason="BadDeviceToken")

        send_push_to_user.enqueue(
            user_id=push_user.pk,
            title="Title",
            body="Body",
        )

        assert not DeviceToken.objects.filter(pk=device_token.pk).exists()

    @override_settings(APNS_ENABLED=True)
    @patch("users.tasks.apns.send")
    def test_send_push_to_staff_sends_to_active_staff_tokens(
        self, mock_send, push_user, staff_push_user,
    ):
        """Verify staff pushes target active staff device tokens only.
        Prevents regular users from receiving moderation-work alerts."""
        DeviceToken.objects.create(
            user=push_user,
            token="regular-token",
            environment=DeviceToken.Environment.SANDBOX.value,
        )
        DeviceToken.objects.create(
            user=staff_push_user,
            token="staff-token",
            environment=DeviceToken.Environment.PRODUCTION.value,
        )
        DeviceToken.objects.create(
            user=staff_push_user,
            token="inactive-staff-token",
            environment=DeviceToken.Environment.PRODUCTION.value,
            is_active=False,
        )
        mock_send.return_value = apns.APNSResult(status_code=200)

        send_push_to_staff.enqueue(
            title="Moderation",
            body="A library needs review.",
            data={"type": "library.submitted"},
        )

        mock_send.assert_called_once_with(
            token="staff-token",
            title="Moderation",
            body="A library needs review.",
            environment=DeviceToken.Environment.PRODUCTION.value,
            data={"type": "library.submitted"},
        )
