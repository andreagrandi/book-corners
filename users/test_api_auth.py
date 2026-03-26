from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import override_settings
from ninja_jwt.tokens import RefreshToken

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialLogin

User = get_user_model()


def _make_social_login(*, provider, uid, email, first_name="", last_name=""):
    """Build an unsaved SocialLogin for mocking verify_token.
    Mirrors what allauth providers return after token verification."""
    user = User(email=email, first_name=first_name, last_name=last_name)
    account = SocialAccount(provider=provider, uid=uid, extra_data={"email": email})
    account.user = user
    email_obj = EmailAddress(email=email, verified=True, primary=True)
    mock_provider = MagicMock()
    mock_provider.app = None
    return SocialLogin(
        user=user, account=account, email_addresses=[email_obj], provider=mock_provider,
    )


@pytest.fixture
def user_with_email(db):
    """Create a test user with an email address.
    Required for testing email-based API login."""
    return User.objects.create_user(
        username="emailuser",
        email="emailuser@example.com",
        password="testpass123",
    )


@pytest.mark.django_db
class TestAuthAPI:
    def test_register_returns_tokens_and_creates_user(self, client):
        """Verify register returns tokens and creates user.
        Confirms the expected behavior stays stable."""
        response = client.post(
            "/api/v1/auth/register",
            data={
                "username": "newuser",
                "password": "newpass123",
                "email": "newuser@example.com",
            },
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 201
        assert "access" in body
        assert "refresh" in body
        assert User.objects.filter(username="newuser").exists()

    def test_login_returns_jwt_pair(self, client, user):
        """Verify login returns jwt pair.
        Confirms the expected behavior stays stable."""
        response = client.post(
            "/api/v1/auth/login",
            data={
                "username": user.username,
                "password": "testpass123",
            },
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 200
        assert "access" in body
        assert "refresh" in body

    def test_login_by_email_returns_jwt_pair(self, client, user_with_email):
        """Verify login accepts an email address instead of username.
        Matches the web login flow that resolves email to username."""
        response = client.post(
            "/api/v1/auth/login",
            data={
                "username": "emailuser@example.com",
                "password": "testpass123",
            },
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 200
        assert "access" in body
        assert "refresh" in body

    def test_login_by_email_case_insensitive(self, client, user_with_email):
        """Verify email-based login is case-insensitive.
        Users should be able to log in regardless of email casing."""
        response = client.post(
            "/api/v1/auth/login",
            data={
                "username": "EmailUser@Example.COM",
                "password": "testpass123",
            },
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 200
        assert "access" in body
        assert "refresh" in body

    def test_login_by_email_with_whitespace(self, client, user_with_email):
        """Verify login trims whitespace from the identifier.
        Prevents authentication failures from copy-paste artifacts."""
        response = client.post(
            "/api/v1/auth/login",
            data={
                "username": "  emailuser@example.com  ",
                "password": "testpass123",
            },
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 200
        assert "access" in body

    def test_login_by_nonexistent_email_returns_401(self, client, user_with_email):
        """Verify login with an unknown email returns 401.
        Should not leak whether the email exists."""
        response = client.post(
            "/api/v1/auth/login",
            data={
                "username": "nobody@example.com",
                "password": "testpass123",
            },
            content_type="application/json",
        )

        assert response.status_code == 401
        assert response.json()["message"] == "Invalid credentials."

    def test_login_invalid_credentials_returns_401(self, client, user):
        """Verify login with wrong password returns 401.
        Prevents unauthorized access with incorrect credentials."""
        response = client.post(
            "/api/v1/auth/login",
            data={
                "username": user.username,
                "password": "wrongpassword",
            },
            content_type="application/json",
        )

        assert response.status_code == 401
        assert response.json()["message"] == "Invalid credentials."

    def test_register_rejects_weak_password(self, client):
        """Verify register endpoint applies Django password validators.
        Prevents weak credentials from being accepted via API registration."""
        response = client.post(
            "/api/v1/auth/register",
            data={
                "username": "weakuser",
                "password": "12345678",
                "email": "weakuser@example.com",
            },
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 400
        assert body["message"]
        assert not User.objects.filter(username="weakuser").exists()

    def test_register_rejects_invalid_email(self, client):
        """Verify register endpoint rejects invalid email inputs.
        Ensures API-side input validation blocks malformed addresses."""
        response = client.post(
            "/api/v1/auth/register",
            data={
                "username": "invalidemailuser",
                "password": "StrongPass123!",
                "email": "not-an-email",
            },
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 400
        assert body["message"] == "Provide a valid email address."
        assert not User.objects.filter(username="invalidemailuser").exists()

    @override_settings(
        AUTH_RATE_LIMIT_ENABLED=True,
        AUTH_RATE_LIMIT_WINDOW_SECONDS=300,
        AUTH_RATE_LIMIT_LOGIN_ATTEMPTS=1,
    )
    def test_login_rate_limit_returns_429_after_excessive_attempts(self, client, user):
        """Verify API login endpoint throttles excessive attempts.
        Protects token issuance from brute-force credential probing."""
        cache.clear()

        first_response = client.post(
            "/api/v1/auth/login",
            data={
                "username": user.username,
                "password": "wrong-password",
            },
            content_type="application/json",
        )
        second_response = client.post(
            "/api/v1/auth/login",
            data={
                "username": user.username,
                "password": "wrong-password",
            },
            content_type="application/json",
        )

        assert first_response.status_code == 401
        assert second_response.status_code == 429
        assert "Too many login attempts" in second_response.json()["message"]

    def test_refresh_returns_new_access_token(self, client, user):
        """Verify refresh returns new access token.
        Confirms the expected behavior stays stable."""
        refresh_token = str(RefreshToken.for_user(user))
        response = client.post(
            "/api/v1/auth/refresh",
            data={"refresh": refresh_token},
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 200
        assert "access" in body

    def test_me_returns_current_user(self, client, user):
        """Verify me returns current user.
        Confirms the expected behavior stays stable."""
        access_token = str(RefreshToken.for_user(user).access_token)
        response = client.get(
            "/api/v1/auth/me",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )

        body = response.json()
        assert response.status_code == 200
        assert body["id"] == user.id
        assert body["username"] == user.username
        assert body["email"] == user.email
        assert body["is_social_only"] is False

    def test_me_returns_social_only_for_social_user(self, client):
        """Verify me returns is_social_only=true for social-only users.
        Allows clients to hide email/password change options."""
        social_user = User.objects.create_user(username="socialme", email="socialme@example.com")
        social_user.set_unusable_password()
        social_user.save()
        SocialAccount.objects.create(
            user=social_user, provider="google", uid="google-me-uid", extra_data={},
        )
        access_token = str(RefreshToken.for_user(social_user).access_token)
        response = client.get(
            "/api/v1/auth/me",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )

        body = response.json()
        assert response.status_code == 200
        assert body["is_social_only"] is True


@pytest.mark.django_db
class TestSocialLoginAPI:
    """Tests for the POST /api/v1/auth/social endpoint.
    All tests mock provider.verify_token() to avoid hitting Apple/Google."""

    @patch("allauth.socialaccount.providers.apple.provider.AppleProvider.verify_token")
    def test_social_login_apple_returns_tokens(self, mock_verify, client):
        """Verify Apple social login creates a user and returns JWT tokens.
        Simulates the first sign-in from an iOS app using Apple identity token."""
        mock_verify.return_value = _make_social_login(
            provider="apple", uid="apple-uid-001", email="apple@example.com",
        )

        response = client.post(
            "/api/v1/auth/social",
            data={"provider": "apple", "id_token": "a]" * 20},
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 200
        assert "access" in body
        assert "refresh" in body
        assert User.objects.filter(email="apple@example.com").exists()

    @patch("allauth.socialaccount.providers.google.provider.GoogleProvider.verify_token")
    def test_social_login_google_returns_tokens(self, mock_verify, client):
        """Verify Google social login creates a user and returns JWT tokens.
        Simulates the first sign-in from an iOS app using Google identity token."""
        mock_verify.return_value = _make_social_login(
            provider="google", uid="google-uid-001", email="google@example.com",
        )

        response = client.post(
            "/api/v1/auth/social",
            data={"provider": "google", "id_token": "g" * 40},
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 200
        assert "access" in body
        assert "refresh" in body
        assert User.objects.filter(email="google@example.com").exists()

    @patch("allauth.socialaccount.providers.apple.provider.AppleProvider.verify_token")
    def test_social_login_invalid_token_returns_400(self, mock_verify, client):
        """Verify invalid identity token returns a 400 error.
        Covers tokens that fail Apple/Google signature verification."""
        mock_verify.side_effect = DjangoValidationError("Invalid token")

        response = client.post(
            "/api/v1/auth/social",
            data={"provider": "apple", "id_token": "x" * 40},
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["message"] == "Invalid identity token."

    def test_social_login_unsupported_provider_returns_400(self, client, db):
        """Verify unsupported provider name returns a 400 error.
        Only 'apple' and 'google' are accepted."""
        response = client.post(
            "/api/v1/auth/social",
            data={"provider": "facebook", "id_token": "f" * 40},
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["message"] == "Unsupported provider. Use 'apple' or 'google'."

    @patch("allauth.socialaccount.providers.apple.provider.AppleProvider.verify_token")
    def test_social_login_existing_account_returns_tokens(self, mock_verify, client):
        """Verify second social login returns tokens for the same user.
        Confirms returning users are matched by their social account UID."""
        existing_user = User.objects.create_user(
            username="existing_apple", email="apple@example.com", password="unused",
        )
        SocialAccount.objects.create(
            provider="apple", uid="apple-uid-001", user=existing_user, extra_data={},
        )
        mock_verify.return_value = _make_social_login(
            provider="apple", uid="apple-uid-001", email="apple@example.com",
        )

        response = client.post(
            "/api/v1/auth/social",
            data={"provider": "apple", "id_token": "a" * 40},
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 200
        assert "access" in body
        assert User.objects.filter(email="apple@example.com").count() == 1

    @patch("allauth.socialaccount.providers.apple.provider.AppleProvider.verify_token")
    def test_social_login_links_to_existing_email_user(self, mock_verify, client):
        """Verify social login links to an existing user with the same email.
        Prevents duplicate accounts when a user signed up with email first."""
        existing_user = User.objects.create_user(
            username="emailfirst", email="shared@example.com", password="testpass123",
        )
        mock_verify.return_value = _make_social_login(
            provider="apple", uid="apple-uid-link", email="shared@example.com",
        )

        response = client.post(
            "/api/v1/auth/social",
            data={"provider": "apple", "id_token": "a" * 40},
            content_type="application/json",
        )

        body = response.json()
        assert response.status_code == 200
        assert "access" in body
        assert User.objects.filter(email="shared@example.com").count() == 1
        assert SocialAccount.objects.filter(
            provider="apple", uid="apple-uid-link", user=existing_user,
        ).exists()

    @override_settings(
        AUTH_RATE_LIMIT_ENABLED=True,
        AUTH_RATE_LIMIT_WINDOW_SECONDS=300,
        AUTH_RATE_LIMIT_SOCIAL_ATTEMPTS=1,
    )
    @patch("allauth.socialaccount.providers.apple.provider.AppleProvider.verify_token")
    def test_social_login_rate_limit_returns_429(self, mock_verify, client):
        """Verify social login endpoint throttles excessive attempts.
        Protects against brute-force token probing."""
        cache.clear()
        mock_verify.return_value = _make_social_login(
            provider="apple", uid="apple-uid-rate", email="rate@example.com",
        )

        first_response = client.post(
            "/api/v1/auth/social",
            data={"provider": "apple", "id_token": "a" * 40},
            content_type="application/json",
        )
        second_response = client.post(
            "/api/v1/auth/social",
            data={"provider": "apple", "id_token": "a" * 40},
            content_type="application/json",
        )

        assert first_response.status_code == 200
        assert second_response.status_code == 429
        assert "Too many social login attempts" in second_response.json()["message"]

    @patch("allauth.socialaccount.providers.apple.provider.AppleProvider.verify_token")
    def test_social_login_apple_captures_name(self, mock_verify, client):
        """Verify Apple first sign-in saves the user's first and last name.
        Apple only provides the name on the very first authentication."""
        mock_verify.return_value = _make_social_login(
            provider="apple", uid="apple-uid-name", email="named@example.com",
        )

        response = client.post(
            "/api/v1/auth/social",
            data={
                "provider": "apple",
                "id_token": "a" * 40,
                "first_name": "Jane",
                "last_name": "Doe",
            },
            content_type="application/json",
        )

        assert response.status_code == 200
        user = User.objects.get(email="named@example.com")
        assert user.first_name == "Jane"
        assert user.last_name == "Doe"
