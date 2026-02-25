import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from ninja_jwt.tokens import RefreshToken

User = get_user_model()


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
