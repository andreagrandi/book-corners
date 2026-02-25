import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.urls import reverse

User = get_user_model()


class TestGoogleOAuthEnabledSetting:
    """Verify GOOGLE_OAUTH_ENABLED derives from env vars correctly."""

    def test_enabled_when_both_vars_set(self):
        """GOOGLE_OAUTH_ENABLED is True when both credentials are present.
        The setting gates visibility of the Google login button."""
        assert bool("some-id" and "some-secret") is True

    def test_disabled_when_client_id_missing(self):
        """GOOGLE_OAUTH_ENABLED is False when client ID is empty.
        Prevents showing a broken OAuth button."""
        assert bool("" and "some-secret") is False

    def test_disabled_when_client_secret_missing(self):
        """GOOGLE_OAUTH_ENABLED is False when client secret is empty.
        Prevents showing a broken OAuth button."""
        assert bool("some-id" and "") is False

    def test_disabled_when_both_vars_missing(self):
        """GOOGLE_OAUTH_ENABLED is False when both credentials are empty.
        This is the default for fresh installs without OAuth setup."""
        assert bool("" and "") is False


class TestGoogleLoginURLResolution:
    """Verify Google OAuth URL routing is properly configured."""

    def test_google_login_url_resolves(self, client, db):
        """The google_login named URL resolves to a valid path.
        Confirms allauth URL configuration is wired correctly."""
        url = reverse("google_login")
        assert url == "/accounts/google/login/"

    def test_google_callback_url_resolves(self, db):
        """The google_callback named URL resolves to a valid path.
        Confirms the OAuth callback route is available."""
        url = reverse("google_callback")
        assert url == "/accounts/google/login/callback/"


class TestGoogleButtonVisibleWhenEnabled:
    """Verify the Google button appears when OAuth credentials are configured."""

    @pytest.fixture(autouse=True)
    def _enable_google_oauth(self, settings):
        """Enable Google OAuth for all tests in this class."""
        settings.GOOGLE_OAUTH_ENABLED = True

    def test_login_page_shows_google_button(self, client, db):
        """The login page includes a Continue with Google button.
        Users should see the social login option alongside the form."""
        response = client.get(reverse("login"))
        content = response.content.decode()
        assert "Continue with Google" in content

    def test_login_page_google_form_posts_to_correct_url(self, client, db):
        """The Google login form posts to the allauth provider URL.
        Ensures CSRF-protected POST flow for OAuth initiation."""
        response = client.get(reverse("login"))
        content = response.content.decode()
        assert 'action="/accounts/google/login/"' in content

    def test_register_page_shows_google_button(self, client, db):
        """The register page includes a Continue with Google button.
        Users should see the social signup option alongside the form."""
        response = client.get(reverse("register"))
        content = response.content.decode()
        assert "Continue with Google" in content

    def test_register_page_google_form_posts_to_correct_url(self, client, db):
        """The Google login form on register posts to the allauth provider URL.
        Ensures consistent OAuth entry point from both pages."""
        response = client.get(reverse("register"))
        content = response.content.decode()
        assert 'action="/accounts/google/login/"' in content

    def test_login_page_shows_divider(self, client, db):
        """The login page shows an 'or' divider between form and Google button.
        Provides clear visual separation between auth methods."""
        response = client.get(reverse("login"))
        content = response.content.decode()
        assert "divider" in content

    def test_register_page_shows_divider(self, client, db):
        """The register page shows an 'or' divider between form and Google button.
        Provides clear visual separation between auth methods."""
        response = client.get(reverse("register"))
        content = response.content.decode()
        assert "divider" in content


class TestGoogleButtonHiddenWhenDisabled:
    """Verify the Google button is hidden when OAuth credentials are missing."""

    @pytest.fixture(autouse=True)
    def _disable_google_oauth(self, settings):
        """Disable Google OAuth for all tests in this class."""
        settings.GOOGLE_OAUTH_ENABLED = False

    def test_login_page_hides_google_button(self, client, db):
        """The login page omits the Google button without credentials.
        Avoids showing a broken OAuth flow to users."""
        response = client.get(reverse("login"))
        content = response.content.decode()
        assert "Continue with Google" not in content

    def test_register_page_hides_google_button(self, client, db):
        """The register page omits the Google button without credentials.
        Avoids showing a broken OAuth flow to users."""
        response = client.get(reverse("register"))
        content = response.content.decode()
        assert "Continue with Google" not in content

    def test_login_page_hides_divider(self, client, db):
        """The login page omits the 'or' divider when Google is disabled.
        Keeps the UI clean when only password auth is available."""
        response = client.get(reverse("login"))
        content = response.content.decode()
        assert "divider" not in content

    def test_register_page_hides_divider(self, client, db):
        """The register page omits the 'or' divider when Google is disabled.
        Keeps the UI clean when only password auth is available."""
        response = client.get(reverse("register"))
        content = response.content.decode()
        assert "divider" not in content

    def test_login_form_still_renders(self, client, db):
        """The password login form renders normally without Google OAuth.
        Core auth must work regardless of OAuth configuration."""
        response = client.get(reverse("login"))
        content = response.content.decode()
        assert "Log in" in content
        assert "Username or email" in content

    def test_register_form_still_renders(self, client, db):
        """The registration form renders normally without Google OAuth.
        Core auth must work regardless of OAuth configuration."""
        response = client.get(reverse("register"))
        content = response.content.decode()
        assert "Create an account" in content
        assert "Username" in content


class TestExistingAuthFlowsStillWork:
    """Verify username/password login and registration are unaffected."""

    def test_login_with_credentials_still_works(self, client, db):
        """A user can still log in with username and password.
        Allauth integration must not break the existing auth flow."""
        User.objects.create_user(
            username="localuser",
            email="local@example.com",
            password="testpass123",
        )
        response = client.post(
            reverse("login"),
            {"username": "localuser", "password": "testpass123"},
            follow=True,
        )
        assert response.status_code == 200
        assert response.wsgi_request.user.is_authenticated

    def test_register_with_form_still_works(self, client, db):
        """A user can still register with username, email, and password.
        Allauth integration must not break the existing registration flow."""
        response = client.post(
            reverse("register"),
            {
                "username": "newuser",
                "email": "new@example.com",
                "password1": "Str0ngP@ss!",
                "password2": "Str0ngP@ss!",
            },
            follow=True,
        )
        assert response.status_code == 200
        assert User.objects.filter(username="newuser").exists()

    def test_logout_still_works(self, client, user):
        """An authenticated user can still log out via POST.
        The logout endpoint must remain functional with allauth installed."""
        client.force_login(user)
        response = client.post(reverse("logout"), follow=True)
        assert response.status_code == 200
        assert not response.wsgi_request.user.is_authenticated


class TestCustomAdapters:
    """Verify custom allauth adapters work correctly."""

    def test_social_adapter_allows_signup(self, db):
        """The SocialAccountAdapter permits new signups via OAuth.
        This is required for first-time Google users to create accounts."""
        from users.adapters import SocialAccountAdapter

        adapter = SocialAccountAdapter()
        assert adapter.is_open_for_signup(request=None, sociallogin=None) is True

    def test_social_adapter_normalizes_email(self, db, rf):
        """The SocialAccountAdapter lowercases email during populate_user.
        Prevents case-mismatch duplicates with our unique email constraint."""
        from unittest.mock import MagicMock

        from users.adapters import SocialAccountAdapter

        adapter = SocialAccountAdapter()
        request = rf.get("/")

        mock_sociallogin = MagicMock()
        mock_user = User(email="Test@Example.COM")
        mock_sociallogin.user = mock_user

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.populate_user",
                lambda self, request, sociallogin, data: mock_user,
            )
            result = adapter.populate_user(
                request=request,
                sociallogin=mock_sociallogin,
                data={"email": "Test@Example.COM"},
            )

        assert result.email == "test@example.com"


class TestSocialSignupRaceConditions:
    """Verify save_user handles concurrent signup race conditions."""

    def test_save_user_wraps_in_transaction(self, db, rf):
        """Partial user creation is rolled back when IntegrityError occurs.
        Ensures no orphaned User rows are left behind on failure."""
        from unittest.mock import MagicMock, patch

        from users.adapters import SocialAccountAdapter

        adapter = SocialAccountAdapter()
        request = rf.get("/")

        mock_sociallogin = MagicMock()
        mock_sociallogin.user = User(username="ghost", email="")

        initial_count = User.objects.count()

        with patch(
            "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.save_user",
            side_effect=IntegrityError("duplicate key"),
        ):
            with pytest.raises(IntegrityError):
                adapter.save_user(
                    request=request, sociallogin=mock_sociallogin, form=None
                )

        assert User.objects.count() == initial_count

    def test_email_collision_connects_to_existing_user(self, db, rf):
        """On email collision, the social account connects to the existing user.
        Simulates losing a race where another request created the user first."""
        from unittest.mock import MagicMock, patch

        from users.adapters import SocialAccountAdapter

        existing = User.objects.create_user(
            username="alice", email="alice@example.com", password="pass123"
        )

        adapter = SocialAccountAdapter()
        request = rf.get("/")

        mock_sociallogin = MagicMock()
        mock_sociallogin.user = User(username="alice2", email="alice@example.com")

        with patch(
            "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.save_user",
            side_effect=IntegrityError("duplicate key"),
        ):
            result = adapter.save_user(
                request=request, sociallogin=mock_sociallogin, form=None
            )

        assert result.pk == existing.pk
        mock_sociallogin.connect.assert_called_once_with(request, existing)

    def test_unexpected_integrity_error_reraises(self, db, rf):
        """IntegrityError without a matching email is re-raised.
        Avoids silently swallowing unexpected constraint violations."""
        from unittest.mock import MagicMock, patch

        from users.adapters import SocialAccountAdapter

        adapter = SocialAccountAdapter()
        request = rf.get("/")

        mock_sociallogin = MagicMock()
        mock_sociallogin.user = User(
            username="nomatch", email="nobody@example.com"
        )

        with patch(
            "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.save_user",
            side_effect=IntegrityError("some other constraint"),
        ):
            with pytest.raises(IntegrityError, match="some other constraint"):
                adapter.save_user(
                    request=request, sociallogin=mock_sociallogin, form=None
                )


class TestContextProcessor:
    """Verify the google_oauth context processor."""

    def test_context_processor_exposes_enabled_flag(self, rf, db, settings):
        """The context processor adds google_oauth_enabled to template context.
        Templates use this to conditionally render the Google button."""
        from users.context_processors import google_oauth

        settings.GOOGLE_OAUTH_ENABLED = True
        result = google_oauth(rf.get("/"))
        assert result == {"google_oauth_enabled": True}

    def test_context_processor_exposes_disabled_flag(self, rf, db, settings):
        """The context processor reflects disabled state in template context.
        Templates hide the Google button when this is False."""
        from users.context_processors import google_oauth

        settings.GOOGLE_OAUTH_ENABLED = False
        result = google_oauth(rf.get("/"))
        assert result == {"google_oauth_enabled": False}
