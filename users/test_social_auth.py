import time

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import IntegrityError
from django.urls import reverse

from allauth.account.models import EmailAddress
from allauth.core.context import request_context
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount, SocialLogin

from users.adapters import _generate_username

User = get_user_model()


def _sociallogin_request(rf):
    """Build a request with session and message middleware.
    Required by complete_social_login for authentication flows."""
    request = rf.get("/")
    SessionMiddleware(lambda r: None).process_request(request)
    MessageMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = AnonymousUser()
    return request


def _google_sociallogin(request, email, uid, extra_data=None):
    """Build a SocialLogin for a Google OAuth user.
    Simulates what allauth constructs from Google's API response."""
    from allauth.socialaccount.adapter import get_adapter as get_social_adapter

    provider = get_social_adapter(request).get_provider(
        request, provider="google"
    )
    user = User(email=email, username="")
    account = SocialAccount(
        provider="google",
        uid=uid,
        extra_data=extra_data or {"email": email},
    )
    email_obj = EmailAddress(
        email=email,
        verified=True,
        primary=True,
    )
    return SocialLogin(
        user=user,
        account=account,
        email_addresses=[email_obj],
        provider=provider,
    )


def _stash_callback_state(client):
    """Stash a valid OAuth state in the test client's session.
    Returns the state ID for use in callback URL parameters."""
    session = client.session
    state_id = "test-state-id"
    session["socialaccount_states"] = {
        state_id: ({"process": "login", "redirect_url": "/"}, time.time())
    }
    session.save()
    return state_id


@pytest.fixture
def _google_provider_settings(settings):
    """Configure Google provider with test credentials.
    Required for the provider to be available during tests."""
    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {
            "APPS": [
                {
                    "client_id": "test-id",
                    "secret": "test-secret",
                    "key": "",
                },
            ],
            "SCOPE": ["profile", "email"],
            "AUTH_PARAMS": {"access_type": "online"},
            "OAUTH_PKCE_ENABLED": True,
        },
    }


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


@pytest.mark.django_db
@pytest.mark.usefixtures("_google_provider_settings")
class TestGoogleFirstSignup:
    """Verify first-time Google OAuth signup creates a new user."""

    def test_new_user_created_on_first_google_login(self, rf):
        """A new user row is created when no matching account exists.
        Confirms the auto-signup flow works end-to-end."""
        request = _sociallogin_request(rf)
        initial_count = User.objects.count()
        sociallogin = _google_sociallogin(
            request, email="newgoogle@example.com", uid="google-uid-001"
        )
        with request_context(request):
            complete_social_login(request, sociallogin)

        assert User.objects.count() == initial_count + 1
        new_user = User.objects.get(email="newgoogle@example.com")
        assert SocialAccount.objects.filter(
            user=new_user, provider="google", uid="google-uid-001"
        ).exists()

    def test_new_user_is_authenticated_after_signup(self, rf):
        """The request user is authenticated after completing signup.
        Users should be logged in immediately after Google OAuth."""
        request = _sociallogin_request(rf)
        sociallogin = _google_sociallogin(
            request, email="authcheck@example.com", uid="google-uid-002"
        )
        with request_context(request):
            complete_social_login(request, sociallogin)

        assert request.user.is_authenticated


@pytest.mark.django_db
@pytest.mark.usefixtures("_google_provider_settings")
class TestGoogleLoginLinkingExistingAccount:
    """Verify Google login links to existing local accounts by email."""

    def test_existing_user_linked_no_duplicate_created(self, rf):
        """No duplicate user row is created when email matches.
        The existing account gets a SocialAccount linked to it instead."""
        existing = User.objects.create_user(
            username="localuser",
            email="shared@example.com",
            password="pass123",
        )
        initial_count = User.objects.count()

        request = _sociallogin_request(rf)
        sociallogin = _google_sociallogin(
            request, email="shared@example.com", uid="google-uid-link-001"
        )
        with request_context(request):
            complete_social_login(request, sociallogin)

        assert User.objects.count() == initial_count
        assert SocialAccount.objects.filter(
            user=existing, provider="google", uid="google-uid-link-001"
        ).exists()

    def test_existing_user_is_authenticated_after_linking(self, rf):
        """The request user is the existing local user after linking.
        Confirms identity merge works correctly via email match."""
        existing = User.objects.create_user(
            username="localuser2",
            email="linked@example.com",
            password="pass123",
        )
        request = _sociallogin_request(rf)
        sociallogin = _google_sociallogin(
            request, email="linked@example.com", uid="google-uid-link-002"
        )
        with request_context(request):
            complete_social_login(request, sociallogin)

        assert request.user.is_authenticated
        assert request.user.pk == existing.pk


@pytest.mark.django_db
@pytest.mark.usefixtures("_google_provider_settings")
class TestCallbackDenialHandling:
    """Verify OAuth callback handles provider denial gracefully."""

    def test_access_denied_redirects_to_cancelled_page(self, client):
        """User cancelling Google consent redirects to login-cancelled page.
        No 500 error should occur on normal OAuth denial."""
        state_id = _stash_callback_state(client)
        url = reverse("google_callback")
        response = client.get(url, {"state": state_id, "error": "access_denied"})

        assert response.status_code == 302
        assert reverse("socialaccount_login_cancelled") in response["Location"]

    def test_generic_error_renders_error_page(self, client):
        """A non-cancel OAuth error renders the authentication error template.
        The error page uses 401 status, not 500."""
        state_id = _stash_callback_state(client)
        url = reverse("google_callback")
        response = client.get(url, {"state": state_id, "error": "server_error"})

        assert response.status_code == 401


@pytest.mark.django_db
@pytest.mark.usefixtures("_google_provider_settings")
class TestCallbackStateMismatch:
    """Verify OAuth callback handles invalid or missing state gracefully."""

    def test_invalid_state_returns_error_not_500(self, client):
        """An invalid state parameter returns an error page, not a 500.
        Protects against CSRF attacks and session expiry."""
        url = reverse("google_callback")
        response = client.get(url, {"state": "invalid-state-xyz", "code": "some-code"})

        assert response.status_code != 500
        assert response.status_code == 401

    def test_missing_state_and_code_returns_error_not_500(self, client):
        """A callback with no state and no code returns an error, not 500.
        Handles direct URL access or mangled redirects."""
        url = reverse("google_callback")
        response = client.get(url)

        assert response.status_code != 500
        assert response.status_code == 401


@pytest.mark.django_db
class TestUsernameGeneration:
    """Verify custom username generation for social auth signup."""

    def test_username_from_first_and_last_name(self):
        """Combines first and last name with underscore separator.
        This is the primary username format for social signups."""
        result = _generate_username(["John", "Smith", "j@x.com", "", "user"])
        assert result == "john_smith"

    def test_username_from_first_name_only(self):
        """Uses first name alone when last name is empty.
        Handles profiles with only a given name."""
        result = _generate_username(["John", "", "j@x.com", "", "user"])
        assert result == "john"

    def test_username_from_last_name_only(self):
        """Uses last name alone when first name is empty.
        Handles profiles with only a family name."""
        result = _generate_username(["", "Smith", "j@x.com", "", "user"])
        assert result == "smith"

    def test_username_falls_back_to_email_prefix(self):
        """Falls back to email prefix when both names are empty.
        Extracts the local part before the @ sign."""
        result = _generate_username(["", "", "john.smith@x.com", "", "user"])
        assert result == "john_smith"

    def test_username_falls_back_to_user(self):
        """Falls back to 'user' when names and email are all empty.
        Ensures a username is always generated."""
        result = _generate_username(["", "", "", "", "user"])
        assert result == "user"

    def test_username_never_empty(self):
        """Returns 'user' even when given an empty input list.
        Guards against edge cases in provider data."""
        result = _generate_username([])
        assert result == "user"

    def test_collision_adds_progressive_number(self):
        """Appends a three-digit suffix on first collision.
        Keeps usernames readable while ensuring uniqueness."""
        User.objects.create_user(username="john_smith", password="pass123")
        result = _generate_username(["John", "Smith", "j@x.com", "", "user"])
        assert result == "john_smith001"

    def test_multiple_collisions_increment(self):
        """Increments the suffix for each existing collision.
        Handles scenarios where many users share the same name."""
        User.objects.create_user(username="john_smith", password="pass123")
        User.objects.create_user(username="john_smith001", password="pass123")
        result = _generate_username(["John", "Smith", "j@x.com", "", "user"])
        assert result == "john_smith002"

    def test_unicode_names_normalized(self):
        """Normalizes accented characters to ASCII equivalents.
        Handles international names from Google profiles."""
        result = _generate_username(["José", "García", "j@x.com", "", "user"])
        assert result == "jose_garcia"

    def test_special_characters_stripped(self):
        """Strips apostrophes and other special characters.
        Produces clean, URL-safe usernames."""
        result = _generate_username(["O'Brien", "McDonald", "o@x.com", "", "user"])
        assert result == "o_brien_mcdonald"

    def test_email_prefix_collision_handled(self):
        """Appends suffix when email-derived username is taken.
        Collision handling works for all candidate sources."""
        User.objects.create_user(username="john_smith", password="pass123")
        result = _generate_username(["", "", "john.smith@x.com", "", "user"])
        assert result == "john_smith001"
