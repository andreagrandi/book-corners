from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from ninja_jwt.tokens import RefreshToken

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialLogin

from users.contributor_agreements import (
    CURRENT_CONTRIBUTOR_AGREEMENT_VERSION,
    record_current_acceptance,
)
from users.models import ContributorAgreementAcceptance

User = get_user_model()


def _auth_header(*, user):
    """Build a JWT authorization header for a user.
    Keeps authenticated agreement endpoint tests concise and consistent."""
    access_token = str(RefreshToken.for_user(user).access_token)
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}


def _agreement_payload(*, version="1.0", accepted=True):
    """Build an agreement request payload for API tests.
    Allows each test to focus on the acceptance variation under test."""
    return {
        "contributor_agreement_version": version,
        "contributor_agreement_accepted": accepted,
    }


def _social_login(*, provider, uid, email):
    """Build an unsaved social login for native API tests.
    Mirrors the identity object returned after provider token verification."""
    user = User(email=email)
    account = SocialAccount(
        provider=provider,
        uid=uid,
        extra_data={"email": email},
    )
    account.user = user
    email_obj = EmailAddress(email=email, verified=True, primary=True)
    provider_obj = MagicMock()
    provider_obj.app = None
    return SocialLogin(
        user=user,
        account=account,
        email_addresses=[email_obj],
        provider=provider_obj,
    )


@pytest.mark.django_db
class TestContributorAgreementModel:
    """Verify acceptance records preserve immutable legal audit evidence."""

    def test_record_contains_user_version_timestamp_and_channel(self):
        """Verify every acceptance stores server-owned audit fields.
        Confirms the current-version helper creates the expected record."""
        user = User.objects.create_user(username="audited", email="audited@example.com")

        acceptance = record_current_acceptance(
            user=user,
            channel=ContributorAgreementAcceptance.Channel.API_EXISTING_USER,
        )

        assert acceptance.user_id == user.id
        assert acceptance.agreement_version == CURRENT_CONTRIBUTOR_AGREEMENT_VERSION
        assert acceptance.accepted_at is not None
        assert acceptance.channel == "api_existing_user"

    def test_each_version_is_stored_and_same_version_is_unique(self):
        """Verify users can have separate version history without duplicates.
        Prevents a later acceptance from rewriting an earlier version record."""
        user = User.objects.create_user(username="versions")
        ContributorAgreementAcceptance.objects.create(
            user=user,
            agreement_version="0.9",
            channel=ContributorAgreementAcceptance.Channel.API_EXISTING_USER,
        )
        ContributorAgreementAcceptance.objects.create(
            user=user,
            agreement_version="1.0",
            channel=ContributorAgreementAcceptance.Channel.API_CREDENTIAL_REGISTRATION,
        )

        assert ContributorAgreementAcceptance.objects.filter(user=user).count() == 2
        with pytest.raises(IntegrityError):
            ContributorAgreementAcceptance.objects.create(
                user=user,
                agreement_version="1.0",
                channel=ContributorAgreementAcceptance.Channel.API_EXISTING_USER,
            )

    def test_existing_record_cannot_be_updated_or_deleted(self):
        """Verify application model methods reject audit mutation.
        Keeps original legal evidence stable after creation."""
        user = User.objects.create_user(username="immutable")
        acceptance = ContributorAgreementAcceptance.objects.create(
            user=user,
            agreement_version="1.0",
            channel=ContributorAgreementAcceptance.Channel.API_EXISTING_USER,
        )

        acceptance.channel = ContributorAgreementAcceptance.Channel.API_SOCIAL_APPLE
        with pytest.raises(ValueError, match="immutable"):
            acceptance.save()
        with pytest.raises(ValueError, match="cannot be deleted"):
            acceptance.delete()

    def test_account_deletion_unlinks_user_but_retains_acceptance_evidence(self):
        """Verify account deletion preserves non-identifying agreement evidence.
        Applies the selected SET_NULL retention policy without blocking deletion."""
        user = User.objects.create_user(username="leaving")
        acceptance = ContributorAgreementAcceptance.objects.create(
            user=user,
            agreement_version="1.0",
            channel=ContributorAgreementAcceptance.Channel.API_EXISTING_USER,
        )

        user.delete()

        acceptance.refresh_from_db()
        assert acceptance.user_id is None
        assert acceptance.agreement_version == "1.0"
        assert acceptance.accepted_at is not None
        assert acceptance.channel == "api_existing_user"


@pytest.mark.django_db(transaction=True)
class TestContributorAgreementMigration:
    """Verify the acceptance migration never infers consent for old users."""

    migrate_from = ("users", "0004_add_device_token")
    migrate_to = ("users", "0005_add_contributor_agreement_acceptance")

    def _migrate_to(self, target):
        """Run the users app to a migration target.
        Returns historical models for inserting pre-migration data safely."""
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def test_existing_users_receive_no_inferred_acceptance(self):
        """Verify migrating existing accounts creates no acceptance rows.
        Requires explicit consent after deployment for every pre-existing user."""
        old_apps = self._migrate_to(self.migrate_from)
        OldUser = old_apps.get_model("users", "User")
        old_user = OldUser.objects.create(username="legacy", email="legacy@example.com")

        self._migrate_to(self.migrate_to)

        assert not ContributorAgreementAcceptance.objects.filter(
            user_id=old_user.id,
        ).exists()


@pytest.mark.django_db
class TestContributorAgreementAPI:
    """Verify credential registration and authenticated acceptance contracts."""

    def test_credential_registration_records_current_acceptance(self, client):
        """Verify valid credential registration writes the audit record.
        Confirms the registration channel is selected by the server."""
        response = client.post(
            "/api/v1/auth/register",
            data={
                "username": "credentialuser",
                "email": "credential@example.com",
                "password": "StrongPass123!",
                **_agreement_payload(),
            },
            content_type="application/json",
        )

        user = User.objects.get(username="credentialuser")
        acceptance = ContributorAgreementAcceptance.objects.get(user=user)
        assert response.status_code == 201
        assert acceptance.agreement_version == "1.0"
        assert acceptance.channel == "api_credential_registration"
        assert acceptance.accepted_at is not None

    @pytest.mark.parametrize(
        ("suffix", "payload"),
        [
            ("missing", {}),
            ("false", _agreement_payload(accepted=False)),
            ("stale", _agreement_payload(version="0.9")),
        ],
    )
    def test_legacy_credential_registration_remains_available_before_activation(
        self,
        client,
        suffix,
        payload,
    ):
        """Verify old clients can register while enforcement remains disabled.
        Compatibility requests create unaccepted accounts without inferred consent."""
        username = f"legacycredential{suffix}"
        response = client.post(
            "/api/v1/auth/register",
            data={
                "username": username,
                "email": f"{username}@example.com",
                "password": "StrongPass123!",
                **payload,
            },
            content_type="application/json",
        )

        user = User.objects.get(username=username)
        assert response.status_code == 201
        assert not ContributorAgreementAcceptance.objects.filter(user=user).exists()

    @pytest.mark.parametrize(
        ("payload", "status_code", "message"),
        [
            ({}, 400, "Contributor agreement acceptance is required."),
            (_agreement_payload(accepted=False), 400, "Contributor agreement acceptance must be true."),
            (_agreement_payload(version="0.9"), 400, "Contributor agreement version is not current."),
            (_agreement_payload(accepted="true"), 422, None),
        ],
    )
    @override_settings(CONTRIBUTOR_AGREEMENT_REGISTRATION_REQUIRED=True)
    def test_credential_registration_rejects_missing_false_stale_or_non_boolean_acceptance(
        self,
        client,
        payload,
        status_code,
        message,
    ):
        """Verify activated enforcement rejects invalid credential acceptance.
        Keeps breaking validation behind the explicit rollout switch."""
        response = client.post(
            "/api/v1/auth/register",
            data={
                "username": "rejectedcredential",
                "email": "rejectedcredential@example.com",
                "password": "StrongPass123!",
                **payload,
            },
            content_type="application/json",
        )

        assert response.status_code == status_code
        if message:
            assert response.json()["message"] == message
        assert not User.objects.filter(username="rejectedcredential").exists()

    @override_settings(SITE_URL="https://bookcorners.org")
    def test_authenticated_me_reports_missing_status_and_public_url(self, client):
        """Verify an unaccepted user sees current metadata and false status.
        Ensures clients can discover the immutable agreement URL after login."""
        user = User.objects.create_user(username="statususer")

        response = client.get(
            "/api/v1/auth/me",
            **_auth_header(user=user),
        )

        body = response.json()["contributor_agreement"]
        assert response.status_code == 200
        assert body == {
            "current_version": "1.0",
            "agreement_url": "https://bookcorners.org/contributor-agreement/1.0/en/",
            "is_current": False,
        }

    @override_settings(API_RATE_LIMIT_ENABLED=False)
    def test_acceptance_endpoint_records_server_owned_fields_and_is_idempotent(self, client):
        """Verify acceptance records the authenticated user and stable audit values.
        A repeated request must preserve the original timestamp and channel."""
        user = User.objects.create_user(username="acceptor")
        payload = _agreement_payload()

        first_response = client.post(
            "/api/v1/auth/me/contributor-agreement",
            data=payload,
            content_type="application/json",
            **_auth_header(user=user),
        )
        acceptance = ContributorAgreementAcceptance.objects.get(user=user)
        accepted_at = acceptance.accepted_at

        second_response = client.post(
            "/api/v1/auth/me/contributor-agreement",
            data={**payload, "user_id": 999, "channel": "forged", "accepted_at": "2020-01-01T00:00:00Z"},
            content_type="application/json",
            **_auth_header(user=user),
        )
        acceptance.refresh_from_db()

        assert first_response.status_code == 200
        assert first_response.json()["is_current"] is True
        assert second_response.status_code == 200
        assert ContributorAgreementAcceptance.objects.filter(user=user).count() == 1
        assert acceptance.user_id == user.id
        assert acceptance.accepted_at == accepted_at
        assert acceptance.channel == "api_existing_user"

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            (_agreement_payload(accepted=False), "Contributor agreement acceptance must be true."),
            (_agreement_payload(version="0.9"), "Contributor agreement version is not current."),
        ],
    )
    def test_acceptance_endpoint_rejects_false_or_stale_versions(self, client, payload, message):
        """Verify existing users cannot record false or stale acceptance.
        Prevents the current-status flag from being forged through the endpoint."""
        user = User.objects.create_user(username="rejector")

        response = client.post(
            "/api/v1/auth/me/contributor-agreement",
            data=payload,
            content_type="application/json",
            **_auth_header(user=user),
        )

        assert response.status_code == 400
        assert response.json()["message"] == message
        assert not ContributorAgreementAcceptance.objects.filter(user=user).exists()

    def test_acceptance_endpoint_requires_authentication(self, client):
        """Verify anonymous clients cannot create acceptance records.
        Requires the JWT identity to supply the audited user field."""
        response = client.post(
            "/api/v1/auth/me/contributor-agreement",
            data=_agreement_payload(),
            content_type="application/json",
        )

        assert response.status_code == 401

    def test_email_profile_update_cannot_create_acceptance(self, client):
        """Verify generic profile updates cannot forge agreement evidence.
        Acceptance is available only through the dedicated endpoint."""
        user = User.objects.create_user(
            username="profileuser",
            email="profile@example.com",
            password="testpass123",
        )

        response = client.patch(
            "/api/v1/auth/me/email",
            data={
                "email": "changed@example.com",
                **_agreement_payload(),
                "channel": "forged",
                "accepted_at": "2020-01-01T00:00:00Z",
            },
            content_type="application/json",
            **_auth_header(user=user),
        )

        assert response.status_code == 200
        assert not ContributorAgreementAcceptance.objects.filter(user=user).exists()

    @patch("users.api.record_current_acceptance")
    def test_credential_registration_rolls_back_when_acceptance_fails(
        self,
        mock_record,
        client,
    ):
        """Verify an acceptance write failure rolls back user creation.
        Prevents unaccepted credential accounts from surviving partial transactions."""
        mock_record.side_effect = IntegrityError("acceptance failure")

        response = client.post(
            "/api/v1/auth/register",
            data={
                "username": "rollbackuser",
                "email": "rollback@example.com",
                "password": "StrongPass123!",
                **_agreement_payload(),
            },
            content_type="application/json",
        )

        assert response.status_code == 500
        assert not User.objects.filter(username="rollbackuser").exists()


@pytest.mark.django_db
class TestNewSocialAgreementAPI:
    """Verify native social account creation requires current acceptance."""

    @patch("allauth.socialaccount.providers.google.provider.GoogleProvider.verify_token")
    def test_new_social_account_records_provider_channel(self, mock_verify, client):
        """Verify valid social registration creates an auditable acceptance.
        Confirms the provider channel is selected server-side."""
        mock_verify.return_value = _social_login(
            provider="google",
            uid="google-accepted-uid",
            email="accepted@example.com",
        )

        response = client.post(
            "/api/v1/auth/social",
            data={
                "provider": "google",
                "id_token": "g" * 40,
                **_agreement_payload(),
            },
            content_type="application/json",
        )

        user = User.objects.get(email="accepted@example.com")
        acceptance = ContributorAgreementAcceptance.objects.get(user=user)
        assert response.status_code == 200
        assert response.json()["account_created"] is True
        assert acceptance.channel == "api_social_google"
        assert SocialAccount.objects.filter(
            provider="google",
            uid="google-accepted-uid",
            user=user,
        ).exists()

    @pytest.mark.parametrize(
        ("suffix", "payload"),
        [
            ("missing", {}),
            ("false", _agreement_payload(accepted=False)),
            ("stale", _agreement_payload(version="0.9")),
        ],
    )
    @patch("allauth.socialaccount.providers.apple.provider.AppleProvider.verify_token")
    def test_legacy_social_registration_remains_available_before_activation(
        self,
        mock_verify,
        client,
        suffix,
        payload,
    ):
        """Verify old social clients can create accounts before activation.
        Compatibility requests create unaccepted accounts without inferred consent."""
        email = f"legacysocial{suffix}@example.com"
        mock_verify.return_value = _social_login(
            provider="apple",
            uid=f"apple-legacy-{suffix}-uid",
            email=email,
        )

        response = client.post(
            "/api/v1/auth/social",
            data={
                "provider": "apple",
                "id_token": "a" * 40,
                **payload,
            },
            content_type="application/json",
        )

        user = User.objects.get(email=email)
        assert response.status_code == 200
        assert response.json()["account_created"] is True
        assert not ContributorAgreementAcceptance.objects.filter(user=user).exists()

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({}, "Contributor agreement acceptance is required."),
            (_agreement_payload(accepted=False), "Contributor agreement acceptance must be true."),
            (_agreement_payload(version="0.9"), "Contributor agreement version is not current."),
        ],
    )
    @override_settings(CONTRIBUTOR_AGREEMENT_REGISTRATION_REQUIRED=True)
    @patch("allauth.socialaccount.providers.apple.provider.AppleProvider.verify_token")
    def test_new_social_account_rejects_missing_false_or_stale_acceptance(
        self,
        mock_verify,
        client,
        payload,
        message,
    ):
        """Verify invalid new social registrations create no account.
        Applies the same explicit acceptance rules as credential registration."""
        mock_verify.return_value = _social_login(
            provider="apple",
            uid="apple-rejected-uid",
            email="rejected@example.com",
        )

        response = client.post(
            "/api/v1/auth/social",
            data={
                "provider": "apple",
                "id_token": "a" * 40,
                **payload,
            },
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["message"] == message
        assert not User.objects.filter(email="rejected@example.com").exists()
        assert not SocialAccount.objects.filter(uid="apple-rejected-uid").exists()

    @patch("users.api.record_current_acceptance")
    @patch("allauth.socialaccount.providers.apple.provider.AppleProvider.verify_token")
    def test_new_social_account_rolls_back_when_acceptance_fails(
        self,
        mock_verify,
        mock_record,
        client,
    ):
        """Verify social account and identity writes share acceptance transaction.
        Prevents a partially created unaccepted social account."""
        mock_verify.return_value = _social_login(
            provider="apple",
            uid="apple-rollback-uid",
            email="socialrollback@example.com",
        )
        mock_record.side_effect = IntegrityError("acceptance failure")

        response = client.post(
            "/api/v1/auth/social",
            data={
                "provider": "apple",
                "id_token": "a" * 40,
                **_agreement_payload(),
            },
            content_type="application/json",
        )

        assert response.status_code == 500
        assert not User.objects.filter(email="socialrollback@example.com").exists()
        assert not SocialAccount.objects.filter(uid="apple-rollback-uid").exists()
