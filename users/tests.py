import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


@pytest.mark.django_db
class TestAuthPages:
    def test_register_creates_user_and_logs_in(self, client):
        """Verify register creates user and logs in.
        Confirms the expected behavior stays stable."""
        response = client.post(
            reverse("register"),
            data={
                "username": "newmember",
                "email": "newmember@example.com",
                "password1": "SecretPass123!",
                "password2": "SecretPass123!",
            },
            follow=True,
        )

        assert response.status_code == 200
        assert User.objects.filter(username="newmember").exists()
        assert response.wsgi_request.user.is_authenticated

    def test_login_accepts_username_and_email(self, client, user):
        """Verify login accepts username and email.
        Confirms the expected behavior stays stable."""
        user.email = "testuser@example.com"
        user.save(update_fields=["email"])

        response_by_username = client.post(
            reverse("login"),
            data={
                "username": user.username,
                "password": "testpass123",
            },
            follow=True,
        )

        assert response_by_username.status_code == 200
        assert response_by_username.wsgi_request.user.is_authenticated

        client.post(reverse("logout"), follow=True)

        response_by_email = client.post(
            reverse("login"),
            data={
                "username": user.email,
                "password": "testpass123",
            },
            follow=True,
        )

        assert response_by_email.status_code == 200
        assert response_by_email.wsgi_request.user.is_authenticated

    def test_logout_requires_post_and_logs_user_out(self, client, user):
        """Verify logout requires post and logs user out.
        Confirms the expected behavior stays stable."""
        client.force_login(user)

        get_response = client.get(reverse("logout"))
        assert get_response.status_code == 405

        post_response = client.post(reverse("logout"), follow=True)
        assert post_response.status_code == 200
        assert not post_response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
class TestNavbarAuthState:
    def test_navbar_shows_login_and_register_for_anonymous_user(self, client):
        """Verify navbar shows login and register for anonymous user.
        Confirms the expected behavior stays stable."""
        response = client.get(reverse("home"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "href=\"/about/\"" in content
        assert "href=\"/login/\"" in content
        assert "href=\"/register/\"" in content
        assert "href=\"/dashboard/\"" not in content
        assert "action=\"/logout/\"" not in content

    def test_navbar_shows_username_and_logout_for_authenticated_user(self, client, user):
        """Verify navbar shows username and logout for authenticated user.
        Confirms the expected behavior stays stable."""
        client.force_login(user)
        response = client.get(reverse("home"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "href=\"/about/\"" in content
        assert f'href="{reverse("dashboard")}"' in content
        assert f'>{user.username}<' in content
        assert ">Dashboard<" not in content
        assert "action=\"/logout/\"" in content
        assert "href=\"/login/\"" not in content
        assert "href=\"/register/\"" not in content
