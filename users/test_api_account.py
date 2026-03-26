import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from ninja_jwt.tokens import RefreshToken

from allauth.socialaccount.models import SocialAccount

from libraries.models import Library

User = get_user_model()


@pytest.fixture
def account_user(db):
    """Create a test user with email for account management API tests.
    Provides a realistic user for testing email, password, and deletion endpoints."""
    return User.objects.create_user(
        username="accountuser",
        email="account@example.com",
        password="testpass123",
    )


def _auth_header(user):
    """Build a Bearer token header for a user.
    Generates a valid JWT access token for authenticated API calls."""
    access_token = str(RefreshToken.for_user(user).access_token)
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}


@pytest.mark.django_db
class TestChangeEmailAPI:
    """Tests for PATCH /api/v1/auth/me/email.
    Covers success, validation errors, and duplicate rejection."""

    def test_change_email_success(self, client, account_user):
        """Verify a valid email change returns updated profile.
        Confirms the database is updated with the new address."""
        response = client.patch(
            "/api/v1/auth/me/email",
            data={"email": "new@example.com"},
            content_type="application/json",
            **_auth_header(account_user),
        )
        body = response.json()
        assert response.status_code == 200
        assert body["email"] == "new@example.com"
        account_user.refresh_from_db()
        assert account_user.email == "new@example.com"

    def test_change_email_normalizes_case(self, client, account_user):
        """Verify email is normalized to lowercase.
        Ensures case-insensitive storage consistency."""
        response = client.patch(
            "/api/v1/auth/me/email",
            data={"email": "New@Example.COM"},
            content_type="application/json",
            **_auth_header(account_user),
        )
        body = response.json()
        assert response.status_code == 200
        assert body["email"] == "new@example.com"

    def test_change_email_rejects_invalid(self, client, account_user):
        """Verify invalid email format is rejected.
        Ensures server-side email validation."""
        response = client.patch(
            "/api/v1/auth/me/email",
            data={"email": "not-an-email"},
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 400
        assert response.json()["message"] == "Provide a valid email address."

    def test_change_email_rejects_duplicate(self, client, account_user):
        """Verify a duplicate email is rejected.
        Prevents two users from having the same email."""
        User.objects.create_user(
            username="other", email="taken@example.com", password="testpass123",
        )
        response = client.patch(
            "/api/v1/auth/me/email",
            data={"email": "taken@example.com"},
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 400
        assert response.json()["message"] == "Email already exists."

    def test_change_email_rejects_same_email(self, client, account_user):
        """Verify submitting the current email is rejected.
        Prevents no-op updates to the database."""
        response = client.patch(
            "/api/v1/auth/me/email",
            data={"email": "account@example.com"},
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 400
        assert response.json()["message"] == "This is already your current email address."

    def test_change_email_requires_auth(self, client):
        """Verify unauthenticated requests are rejected.
        Ensures the endpoint requires a valid JWT token."""
        response = client.patch(
            "/api/v1/auth/me/email",
            data={"email": "new@example.com"},
            content_type="application/json",
        )
        assert response.status_code == 401


@pytest.mark.django_db
class TestChangePasswordAPI:
    """Tests for PUT /api/v1/auth/me/password.
    Covers success, wrong current password, mismatch, and weak password."""

    def test_change_password_success(self, client, account_user):
        """Verify a valid password change succeeds.
        Confirms the user can authenticate with the new password."""
        response = client.put(
            "/api/v1/auth/me/password",
            data={
                "current_password": "testpass123",
                "new_password": "newSecure!Pass99",
                "new_password_confirm": "newSecure!Pass99",
            },
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Password changed successfully."
        account_user.refresh_from_db()
        assert account_user.check_password("newSecure!Pass99")

    def test_change_password_wrong_current(self, client, account_user):
        """Verify an incorrect current password is rejected.
        Prevents unauthorized password changes."""
        response = client.put(
            "/api/v1/auth/me/password",
            data={
                "current_password": "wrongpassword",
                "new_password": "newSecure!Pass99",
                "new_password_confirm": "newSecure!Pass99",
            },
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 400
        assert response.json()["message"] == "Current password is incorrect."

    def test_change_password_mismatch(self, client, account_user):
        """Verify mismatched new passwords are rejected.
        Ensures the user confirms the new password correctly."""
        response = client.put(
            "/api/v1/auth/me/password",
            data={
                "current_password": "testpass123",
                "new_password": "newSecure!Pass99",
                "new_password_confirm": "differentPass99!",
            },
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 400
        assert response.json()["message"] == "New passwords do not match."

    def test_change_password_rejects_weak(self, client, account_user):
        """Verify weak passwords are rejected by Django validators.
        Ensures password policies apply to API password changes."""
        response = client.put(
            "/api/v1/auth/me/password",
            data={
                "current_password": "testpass123",
                "new_password": "12345678",
                "new_password_confirm": "12345678",
            },
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 400
        assert response.json()["message"]

    def test_change_password_requires_auth(self, client):
        """Verify unauthenticated requests are rejected.
        Ensures the endpoint requires a valid JWT token."""
        response = client.put(
            "/api/v1/auth/me/password",
            data={
                "current_password": "testpass123",
                "new_password": "newSecure!Pass99",
                "new_password_confirm": "newSecure!Pass99",
            },
            content_type="application/json",
        )
        assert response.status_code == 401


@pytest.mark.django_db
class TestDeleteAccountAPI:
    """Tests for DELETE /api/v1/auth/me.
    Covers success, wrong password, and access control."""

    def test_delete_account_success(self, client, account_user):
        """Verify a correct password deletes the account.
        Confirms the user no longer exists in the database."""
        user_pk = account_user.pk
        response = client.delete(
            "/api/v1/auth/me",
            data={"password": "testpass123"},
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Account deleted successfully."
        assert not User.objects.filter(pk=user_pk).exists()

    def test_delete_account_wrong_password(self, client, account_user):
        """Verify an incorrect password prevents deletion.
        Ensures the destructive action requires proper confirmation."""
        response = client.delete(
            "/api/v1/auth/me",
            data={"password": "wrongpassword"},
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 400
        assert response.json()["message"] == "Incorrect password."
        assert User.objects.filter(pk=account_user.pk).exists()

    def test_delete_account_requires_auth(self, client):
        """Verify unauthenticated requests are rejected.
        Ensures the endpoint requires a valid JWT token."""
        response = client.delete(
            "/api/v1/auth/me",
            data={"password": "testpass123"},
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_delete_account_preserves_libraries(self, client, account_user):
        """Verify libraries survive account deletion with created_by set to None.
        Ensures community content is preserved when users leave."""
        library = Library.objects.create(
            name="API Preserved Lib",
            address="123 Test St",
            city="TestCity",
            country="IT",
            location=Point(x=11.25, y=43.77, srid=4326),
            status=Library.Status.APPROVED,
            created_by=account_user,
        )
        response = client.delete(
            "/api/v1/auth/me",
            data={"password": "testpass123"},
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 200
        library.refresh_from_db()
        assert library.pk is not None
        assert library.created_by is None

    def test_delete_account_requires_password_for_regular_user(self, client, account_user):
        """Verify regular users cannot delete with just confirm_text.
        Password is mandatory for non-social accounts."""
        response = client.delete(
            "/api/v1/auth/me",
            data={"confirm_text": "DELETE"},
            content_type="application/json",
            **_auth_header(account_user),
        )
        assert response.status_code == 400
        assert response.json()["message"] == "Password is required."


@pytest.fixture
def social_only_user(db):
    """Create a social-only user with no usable password.
    Simulates a user who signed up exclusively via Apple or Google."""
    user = User.objects.create_user(
        username="socialapi",
        email="socialapi@example.com",
    )
    user.set_unusable_password()
    user.save()
    SocialAccount.objects.create(
        user=user, provider="google", uid="google-test-uid", extra_data={},
    )
    return user


@pytest.mark.django_db
class TestSocialOnlyAPIRestrictions:
    """Tests for blocking email and password changes for social-only API users.
    Social users authenticate via their provider and have no local password."""

    def test_social_user_cannot_change_email(self, client, social_only_user):
        """Verify social-only users get 403 when changing email.
        Their email is managed by the social provider."""
        response = client.patch(
            "/api/v1/auth/me/email",
            data={"email": "new@example.com"},
            content_type="application/json",
            **_auth_header(social_only_user),
        )
        assert response.status_code == 403
        assert response.json()["message"] == "Social login accounts cannot change their email address."

    def test_social_user_cannot_change_password(self, client, social_only_user):
        """Verify social-only users get 403 when changing password.
        They have no local password to verify or replace."""
        response = client.put(
            "/api/v1/auth/me/password",
            data={
                "current_password": "anything",
                "new_password": "newSecure!Pass99",
                "new_password_confirm": "newSecure!Pass99",
            },
            content_type="application/json",
            **_auth_header(social_only_user),
        )
        assert response.status_code == 403
        assert response.json()["message"] == "Social login accounts cannot change their password."

    def test_social_user_can_delete_with_confirm_text(self, client, social_only_user):
        """Verify social-only users can delete by sending confirm_text=DELETE.
        No password is needed since they don't have one."""
        user_pk = social_only_user.pk
        response = client.delete(
            "/api/v1/auth/me",
            data={"confirm_text": "DELETE"},
            content_type="application/json",
            **_auth_header(social_only_user),
        )
        assert response.status_code == 200
        assert not User.objects.filter(pk=user_pk).exists()

    def test_social_user_delete_requires_confirm_text(self, client, social_only_user):
        """Verify social-only users must send confirm_text=DELETE.
        Prevents accidental deletions without explicit confirmation."""
        response = client.delete(
            "/api/v1/auth/me",
            data={},
            content_type="application/json",
            **_auth_header(social_only_user),
        )
        assert response.status_code == 400
        assert response.json()["message"] == "Send confirm_text set to 'DELETE' to delete your account."

    def test_social_user_delete_wrong_text(self, client, social_only_user):
        """Verify wrong confirm_text is rejected.
        Only the exact string DELETE is accepted."""
        response = client.delete(
            "/api/v1/auth/me",
            data={"confirm_text": "delete"},
            content_type="application/json",
            **_auth_header(social_only_user),
        )
        assert response.status_code == 400
