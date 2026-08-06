from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import IntegrityError
from django.http import HttpResponseRedirect
from django.urls import reverse

from allauth.account.models import EmailAddress
from allauth.core.context import request_context
from allauth.socialaccount.adapter import get_adapter as get_socialaccount_adapter
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.internal import statekit
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.socialaccount.providers.base import AuthProcess

from users.adapters import SocialAccountAdapter
from users.contributor_agreements import web_social_registration_state_data
from users.models import ContributorAgreementAcceptance

User = get_user_model()


def _agreement_payload(*, version: str = "1.0", accepted: bool = True) -> dict[str, object]:
    """Build the web contributor-agreement fields used by registration forms.
    Keeps exact version and explicit boolean choices consistent across tests."""
    return {
        "contributor_agreement_version": version,
        "contributor_agreement_accepted": accepted,
    }


def _password_registration_payload(**overrides: object) -> dict[str, object]:
    """Build valid password-registration data with current agreement acceptance.
    Allows focused tests to override only the field under examination."""
    payload: dict[str, object] = {
        "username": "webmember",
        "email": "webmember@example.com",
        "password1": "SecretPass123!",
        "password2": "SecretPass123!",
        **_agreement_payload(),
    }
    payload.update(overrides)
    return payload


def _sociallogin_request(rf, *, user=None):
    """Build an allauth request with working session and message middleware.
    Supports direct completion tests without making external OAuth requests."""
    request = rf.get("/")
    SessionMiddleware(lambda incoming_request: None).process_request(request)
    MessageMiddleware(lambda incoming_request: None).process_request(request)
    request.session.save()
    request.user = user or AnonymousUser()
    return request


def _social_login(*, request, provider_id: str, email: str, uid: str) -> SocialLogin:
    """Build an unsaved provider-specific login like an OAuth callback result.
    Uses configured allauth providers while avoiding external token exchange."""
    provider = get_socialaccount_adapter(request).get_provider(
        request,
        provider=provider_id,
    )
    return SocialLogin(
        user=User(email=email, username=""),
        account=SocialAccount(
            provider=provider_id,
            uid=uid,
            extra_data={"email": email},
        ),
        email_addresses=[
            EmailAddress(email=email, verified=True, primary=True),
        ],
        provider=provider,
    )


@pytest.fixture
def social_provider_settings(settings):
    """Configure test-only Google and Apple applications for allauth lookups.
    Supplies no usable production secrets and performs no external requests."""
    settings.SOCIALACCOUNT_PROVIDERS = {
        "apple": {
            "APPS": [
                {
                    "client_id": "test-apple-client",
                    "secret": "test-apple-key-id",
                    "key": "test-apple-team-id",
                    "settings": {"certificate_key": "test-certificate"},
                },
            ],
        },
        "google": {
            "APPS": [
                {
                    "client_id": "test-google-client",
                    "secret": "test-google-secret",
                    "key": "",
                },
            ],
        },
    }


@pytest.mark.django_db
class TestPasswordWebRegistrationAgreement:
    """Cover required acceptance and audit evidence for password registration."""

    def test_registration_renders_current_agreement_unchecked(self, client):
        """Show the current version with an unchecked checkbox by default.
        Gives users a readable agreement link before any explicit choice."""
        response = client.get(reverse("register"))

        agreement_form = response.context["agreement_form"]
        assert response.status_code == 200
        assert agreement_form["contributor_agreement_version"].value() == "1.0"
        assert not agreement_form["contributor_agreement_accepted"].value()
        content = response.content.decode()
        assert 'href="/contributor-agreement/"' in content
        assert 'target="_blank"' in content

    def test_registration_rejects_missing_acceptance(self, client):
        """Reject password account creation when the checkbox is not selected.
        Leaves no user or acceptance evidence after the validation error."""
        payload = _password_registration_payload()
        payload.pop("contributor_agreement_accepted")

        response = client.post(reverse("register"), data=payload)

        assert response.status_code == 200
        assert not User.objects.filter(username="webmember").exists()
        assert ContributorAgreementAcceptance.objects.count() == 0
        assert "must accept the contributor agreement" in response.content.decode()

    def test_registration_rejects_stale_checked_version_and_resets_choice(self, client):
        """Reject stale checked acceptance and render the current version unchecked.
        Prevents a previous-version choice from becoming acceptance of new terms."""
        response = client.post(
            reverse("register"),
            data=_password_registration_payload(
                contributor_agreement_version="0.9",
            ),
        )

        agreement_form = response.context["agreement_form"]
        assert response.status_code == 200
        assert not User.objects.filter(username="webmember").exists()
        assert agreement_form["contributor_agreement_version"].value() == "1.0"
        assert not agreement_form["contributor_agreement_accepted"].value()
        assert "agreement has changed" in response.content.decode()

    def test_unrelated_validation_error_preserves_current_checked_choice(self, client):
        """Preserve a current checked choice when another registration field fails.
        Avoids forcing the user to repeat an explicit choice in the same form flow."""
        response = client.post(
            reverse("register"),
            data=_password_registration_payload(password2="DifferentPass123!"),
        )

        agreement_form = response.context["agreement_form"]
        assert response.status_code == 200
        assert agreement_form["contributor_agreement_accepted"].value() is True
        assert not User.objects.filter(username="webmember").exists()

    def test_registration_records_web_credential_acceptance(self, client):
        """Create password account and exact web-channel acceptance together.
        Logs in the new user only after both writes complete successfully."""
        response = client.post(
            reverse("register"),
            data=_password_registration_payload(),
            follow=True,
        )

        user = User.objects.get(username="webmember")
        acceptance = ContributorAgreementAcceptance.objects.get(user=user)
        assert response.status_code == 200
        assert response.wsgi_request.user == user
        assert acceptance.agreement_version == "1.0"
        assert (
            acceptance.channel
            == ContributorAgreementAcceptance.Channel.WEB_CREDENTIAL_REGISTRATION
        )

    def test_acceptance_failure_rolls_back_password_account(self, client):
        """Roll back the new password user if acceptance persistence fails.
        Prevents a partially created web account from bypassing the agreement."""
        with patch(
            "users.views.record_current_acceptance",
            side_effect=IntegrityError("acceptance write failed"),
        ):
            with pytest.raises(IntegrityError, match="acceptance write failed"):
                client.post(
                    reverse("register"),
                    data=_password_registration_payload(),
                )

        assert not User.objects.filter(username="webmember").exists()

    def test_existing_password_login_does_not_record_acceptance(self, client):
        """Keep ordinary password login available without prompting or recording.
        Confirms the requirement applies only when creating a new account."""
        user = User.objects.create_user(
            username="existingmember",
            email="existingmember@example.com",
            password="SecretPass123!",
        )

        response = client.post(
            reverse("login"),
            data={"username": user.username, "password": "SecretPass123!"},
            follow=True,
        )

        assert response.status_code == 200
        assert response.wsgi_request.user == user
        assert not ContributorAgreementAcceptance.objects.filter(user=user).exists()

    def test_registration_copy_has_italian_catalog_translations(self):
        """Track Italian explanatory, checkbox, and validation translations.
        Confirms both supported languages ship the complete registration choice."""
        catalog = (
            settings.BASE_DIR
            / "locale"
            / "it"
            / "LC_MESSAGES"
            / "django.po"
        ).read_text(encoding="utf-8")

        assert "L'accordo consente a Book Corners" in catalog
        assert "Ho letto e accetto" in catalog
        assert "Accordo per i contributori v%(agreement_version)s" in catalog
        assert "Devi accettare l'accordo per i contributori" in catalog


@pytest.mark.django_db
class TestSocialWebRegistrationStart:
    """Cover validated signup intent before redirecting to social providers."""

    @pytest.mark.parametrize("provider_id", ["apple", "google"])
    def test_valid_acceptance_starts_provider_with_per_flow_state(
        self,
        client,
        settings,
        provider_id,
    ):
        """Start each supported provider with explicit server-stashed acceptance.
        Keeps signup intent tied to the individual OAuth state value."""
        setattr(settings, f"{provider_id.upper()}_OAUTH_ENABLED", True)
        provider = MagicMock()
        provider.redirect.return_value = HttpResponseRedirect("https://provider.example/")
        adapter = MagicMock()
        adapter.get_provider.return_value = provider

        with patch("users.views.get_socialaccount_adapter", return_value=adapter):
            response = client.post(
                reverse("social_register", args=[provider_id]),
                data=_agreement_payload(),
            )

        assert response.status_code == 302
        adapter.get_provider.assert_called_once_with(
            response.wsgi_request,
            provider=provider_id,
        )
        provider.redirect.assert_called_once_with(
            response.wsgi_request,
            process=AuthProcess.LOGIN,
            data=web_social_registration_state_data(provider=provider_id),
        )

    def test_real_allauth_redirect_stashes_and_restores_signup_state(
        self,
        client,
        rf,
        settings,
        social_provider_settings,
    ):
        """Round-trip accepted signup data through installed allauth state storage.
        Verifies the custom route uses the same per-flow state restored on callback."""
        settings.GOOGLE_OAUTH_ENABLED = True

        response = client.post(
            reverse("social_register", args=["google"]),
            data=_agreement_payload(),
        )

        assert response.status_code == 302
        assert response["Location"].startswith(
            "https://accounts.google.com/o/oauth2/v2/auth",
        )
        states = client.session[statekit.STATES_SESSION_KEY]
        assert len(states) == 1
        state_id = next(iter(states))
        callback_request = rf.get("/accounts/google/login/callback/")
        callback_request.session = client.session
        restored_state = statekit.unstash_state(callback_request, state_id)
        assert restored_state is not None
        assert restored_state["process"] == AuthProcess.LOGIN
        assert restored_state["data"] == web_social_registration_state_data(
            provider="google",
        )

    @pytest.mark.parametrize("provider_id", ["apple", "google"])
    def test_missing_acceptance_never_starts_provider(
        self,
        client,
        settings,
        provider_id,
    ):
        """Keep both social providers closed when acceptance is missing.
        Returns the bound registration page without creating OAuth state."""
        setattr(settings, f"{provider_id.upper()}_OAUTH_ENABLED", True)

        with patch("users.views.get_socialaccount_adapter") as get_adapter:
            response = client.post(
                reverse("social_register", args=[provider_id]),
                data={"contributor_agreement_version": "1.0"},
            )

        assert response.status_code == 200
        get_adapter.assert_not_called()
        content = response.content.decode()
        assert "must accept the contributor agreement" in content
        assert "This field is required" not in content

    @pytest.mark.parametrize(
        "payload",
        [
            _agreement_payload(accepted=False),
            _agreement_payload(version="0.9"),
        ],
        ids=["false", "stale"],
    )
    def test_false_or_stale_acceptance_never_starts_provider(
        self,
        client,
        settings,
        payload,
    ):
        """Reject false and stale choices before creating Google OAuth state.
        Resets stale checked submissions so current terms remain explicitly unchecked."""
        settings.GOOGLE_OAUTH_ENABLED = True

        with patch("users.views.get_socialaccount_adapter") as get_adapter:
            response = client.post(
                reverse("social_register", args=["google"]),
                data=payload,
            )

        assert response.status_code == 200
        get_adapter.assert_not_called()
        assert not response.context["agreement_form"][
            "contributor_agreement_accepted"
        ].value()

    def test_authenticated_user_cannot_start_social_registration(self, client, user, settings):
        """Redirect authenticated users before starting a social login process.
        Prevents the registration route from logging out an existing session."""
        settings.GOOGLE_OAUTH_ENABLED = True
        client.force_login(user)

        with patch("users.views.get_socialaccount_adapter") as get_adapter:
            response = client.post(
                reverse("social_register", args=["google"]),
                data=_agreement_payload(),
            )

        assert response.status_code == 302
        assert response["Location"] == reverse("home")
        get_adapter.assert_not_called()

    def test_disabled_or_unsupported_provider_is_not_available(self, client, settings):
        """Return not found for disabled and unsupported social signup providers.
        Keeps the custom start route limited to configured Google and Apple flows."""
        settings.GOOGLE_OAUTH_ENABLED = False

        disabled_response = client.post(
            reverse("social_register", args=["google"]),
            data=_agreement_payload(),
        )
        unsupported_response = client.post(
            reverse("social_register", args=["other"]),
            data=_agreement_payload(),
        )

        assert disabled_response.status_code == 404
        assert unsupported_response.status_code == 404


@pytest.mark.django_db
@pytest.mark.usefixtures("social_provider_settings")
class TestSocialWebRegistrationCompletion:
    """Cover account-creation gating and acceptance recording after OAuth."""

    @pytest.mark.parametrize(
        ("provider_id", "expected_channel"),
        [
            (
                "apple",
                ContributorAgreementAcceptance.Channel.WEB_SOCIAL_APPLE,
            ),
            (
                "google",
                ContributorAgreementAcceptance.Channel.WEB_SOCIAL_GOOGLE,
            ),
        ],
    )
    def test_valid_signup_state_records_provider_channel(
        self,
        rf,
        provider_id,
        expected_channel,
    ):
        """Create first-time social accounts only with valid per-flow acceptance.
        Records the exact Google or Apple web registration audit channel."""
        request = _sociallogin_request(rf)
        sociallogin = _social_login(
            request=request,
            provider_id=provider_id,
            email=f"new-{provider_id}@example.com",
            uid=f"new-{provider_id}-uid",
        )
        sociallogin.state = {
            "process": AuthProcess.LOGIN,
            "data": web_social_registration_state_data(provider=provider_id),
        }

        with request_context(request):
            response = complete_social_login(request, sociallogin)

        user = User.objects.get(email=f"new-{provider_id}@example.com")
        acceptance = ContributorAgreementAcceptance.objects.get(user=user)
        assert response.status_code == 302
        assert request.user == user
        assert acceptance.agreement_version == "1.0"
        assert acceptance.channel == expected_channel

    @pytest.mark.parametrize("provider_id", ["apple", "google"])
    def test_login_origin_new_identity_cannot_create_account(self, rf, provider_id):
        """Reject new identities when OAuth began from ordinary social login.
        Sends the user to registration without inferring agreement acceptance."""
        request = _sociallogin_request(rf)
        sociallogin = _social_login(
            request=request,
            provider_id=provider_id,
            email=f"login-origin-{provider_id}@example.com",
            uid=f"login-origin-{provider_id}-uid",
        )
        sociallogin.state = {"process": AuthProcess.LOGIN}

        with request_context(request):
            response = complete_social_login(request, sociallogin)

        assert response.status_code == 302
        assert response["Location"] == reverse("register")
        assert not User.objects.filter(
            email=f"login-origin-{provider_id}@example.com",
        ).exists()
        assert ContributorAgreementAcceptance.objects.count() == 0

    @pytest.mark.parametrize("provider_id", ["apple", "google"])
    def test_existing_social_login_requires_no_marker_or_acceptance(self, rf, provider_id):
        """Log in existing linked identities without signup state or a new record.
        Preserves ordinary Google and Apple login behavior for current users."""
        user = User.objects.create_user(
            username=f"existing-{provider_id}",
            email=f"existing-{provider_id}@example.com",
        )
        SocialAccount.objects.create(
            user=user,
            provider=provider_id,
            uid=f"existing-{provider_id}-uid",
            extra_data={},
        )
        request = _sociallogin_request(rf)
        sociallogin = _social_login(
            request=request,
            provider_id=provider_id,
            email=user.email,
            uid=f"existing-{provider_id}-uid",
        )
        sociallogin.state = {"process": AuthProcess.LOGIN}

        with request_context(request):
            response = complete_social_login(request, sociallogin)

        assert response.status_code == 302
        assert request.user == user
        assert not ContributorAgreementAcceptance.objects.filter(user=user).exists()

    @pytest.mark.parametrize("provider_id", ["apple", "google"])
    def test_existing_social_login_with_signup_marker_records_nothing(
        self,
        rf,
        provider_id,
    ):
        """Ignore accepted signup state when OAuth resolves to an existing identity.
        Avoids adding agreement evidence unless a new social account is created."""
        user = User.objects.create_user(
            username=f"marked-existing-{provider_id}",
            email=f"marked-existing-{provider_id}@example.com",
        )
        SocialAccount.objects.create(
            user=user,
            provider=provider_id,
            uid=f"marked-existing-{provider_id}-uid",
            extra_data={},
        )
        request = _sociallogin_request(rf)
        sociallogin = _social_login(
            request=request,
            provider_id=provider_id,
            email=user.email,
            uid=f"marked-existing-{provider_id}-uid",
        )
        sociallogin.state = {
            "process": AuthProcess.LOGIN,
            "data": web_social_registration_state_data(provider=provider_id),
        }

        with request_context(request):
            response = complete_social_login(request, sociallogin)

        assert response.status_code == 302
        assert request.user == user
        assert not ContributorAgreementAcceptance.objects.filter(user=user).exists()

    def test_provider_mismatch_rejects_new_social_account(self, rf):
        """Reject callback state issued for a different social provider.
        Prevents cross-provider state confusion from authorizing account creation."""
        request = _sociallogin_request(rf)
        sociallogin = _social_login(
            request=request,
            provider_id="google",
            email="mismatch@example.com",
            uid="mismatch-uid",
        )
        sociallogin.state = {
            "process": AuthProcess.LOGIN,
            "data": web_social_registration_state_data(provider="apple"),
        }

        with request_context(request):
            response = complete_social_login(request, sociallogin)

        assert response.status_code == 302
        assert response["Location"] == reverse("register")
        assert not User.objects.filter(email="mismatch@example.com").exists()

    @pytest.mark.parametrize("process", [AuthProcess.CONNECT, AuthProcess.REDIRECT])
    def test_non_login_allauth_processes_are_not_blocked(self, rf, process):
        """Leave authenticated connection and redirect processes outside the gate.
        Applies agreement enforcement only to potential new-account login flows."""
        request = _sociallogin_request(rf)
        sociallogin = _social_login(
            request=request,
            provider_id="google",
            email="process@example.com",
            uid="process-uid",
        )
        sociallogin.state = {"process": process}

        SocialAccountAdapter().pre_social_login(request, sociallogin)

    def test_acceptance_failure_rolls_back_social_account(self, rf):
        """Roll back the social user and identity if acceptance persistence fails.
        Prevents partial first-time OAuth registration without matching evidence."""
        request = _sociallogin_request(rf)
        sociallogin = _social_login(
            request=request,
            provider_id="google",
            email="social-rollback@example.com",
            uid="social-rollback-uid",
        )
        sociallogin.state = {
            "process": AuthProcess.LOGIN,
            "data": web_social_registration_state_data(provider="google"),
        }

        with patch(
            "users.adapters.record_current_acceptance",
            side_effect=IntegrityError("acceptance write failed"),
        ):
            with pytest.raises(IntegrityError, match="acceptance write failed"):
                SocialAccountAdapter().save_user(request, sociallogin)

        assert not User.objects.filter(email="social-rollback@example.com").exists()
        assert not SocialAccount.objects.filter(uid="social-rollback-uid").exists()
        assert ContributorAgreementAcceptance.objects.count() == 0

    def test_email_race_links_winner_without_web_acceptance(self, rf):
        """Roll back social acceptance when another request wins the email race.
        Links the existing account without treating the failed signup as acceptance."""
        existing_user = User.objects.create_user(
            username="race-winner",
            email="race@example.com",
        )
        request = _sociallogin_request(rf)
        sociallogin = _social_login(
            request=request,
            provider_id="google",
            email="race@example.com",
            uid="race-loser-uid",
        )
        sociallogin.state = {
            "process": AuthProcess.LOGIN,
            "data": web_social_registration_state_data(provider="google"),
        }

        with patch(
            "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.save_user",
            side_effect=IntegrityError("duplicate email"),
        ):
            user = SocialAccountAdapter().save_user(request, sociallogin)

        assert user == existing_user
        assert not ContributorAgreementAcceptance.objects.filter(
            user=existing_user,
        ).exists()

