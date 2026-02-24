import pytest
from django.contrib.auth import get_user_model
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
