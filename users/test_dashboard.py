import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point

from allauth.socialaccount.models import SocialAccount

from libraries.models import Library

User = get_user_model()


@pytest.fixture
def dashboard_user(db):
    """Create a test user with email for dashboard tests.
    Provides a realistic user profile for template rendering."""
    return User.objects.create_user(
        username="dashuser",
        email="dash@example.com",
        password="testpass123",
        first_name="Dash",
        last_name="User",
    )


@pytest.fixture
def social_only_user(db):
    """Create a social-only user with no usable password.
    Simulates a user who signed up exclusively via Apple or Google."""
    user = User.objects.create_user(
        username="socialuser",
        email="social@example.com",
    )
    user.set_unusable_password()
    user.save()
    SocialAccount.objects.create(
        user=user, provider="apple", uid="apple-test-uid", extra_data={},
    )
    return user


def _create_library(*, user, name, status, city="TestCity"):
    """Create a library with minimal required fields.
    Provides a helper for dashboard test data setup."""
    return Library.objects.create(
        name=name,
        address=f"123 {name} St",
        city=city,
        country="IT",
        location=Point(x=11.25, y=43.77, srid=4326),
        status=status,
        created_by=user,
    )


@pytest.mark.django_db
class TestDashboardView:
    """Tests for the user dashboard page.
    Covers pagination, ordering, user info display, and access control."""

    def test_dashboard_requires_login(self, client):
        """Verify unauthenticated users are redirected to login.
        Ensures the dashboard is only accessible to logged-in users."""
        response = client.get("/dashboard/")
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_dashboard_shows_user_info(self, client, dashboard_user):
        """Verify the dashboard displays the user's profile information.
        Checks username, email, and name are rendered in the template."""
        client.force_login(dashboard_user)
        response = client.get("/dashboard/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "dashuser" in content
        assert "dash@example.com" in content
        assert "Dash" in content

    def test_dashboard_paginates_at_10(self, client, dashboard_user):
        """Verify the dashboard limits results to 10 per page.
        Prevents performance issues with large submission counts."""
        for i in range(15):
            _create_library(
                user=dashboard_user,
                name=f"Library {i}",
                status=Library.Status.APPROVED,
            )
        client.force_login(dashboard_user)
        response = client.get("/dashboard/")
        page_obj = response.context["page_obj"]
        assert len(page_obj.object_list) == 10
        assert page_obj.paginator.count == 15
        assert page_obj.has_next()

    def test_dashboard_page_2(self, client, dashboard_user):
        """Verify the second page shows remaining submissions.
        Ensures pagination navigation works correctly."""
        for i in range(15):
            _create_library(
                user=dashboard_user,
                name=f"Library {i}",
                status=Library.Status.APPROVED,
            )
        client.force_login(dashboard_user)
        response = client.get("/dashboard/?page=2")
        page_obj = response.context["page_obj"]
        assert len(page_obj.object_list) == 5
        assert page_obj.has_previous()
        assert not page_obj.has_next()

    def test_dashboard_pending_shown_first(self, client, dashboard_user):
        """Verify pending libraries appear before approved ones.
        Ensures users see items needing attention at the top."""
        approved = _create_library(
            user=dashboard_user,
            name="Approved Lib",
            status=Library.Status.APPROVED,
        )
        pending = _create_library(
            user=dashboard_user,
            name="Pending Lib",
            status=Library.Status.PENDING,
        )
        client.force_login(dashboard_user)
        response = client.get("/dashboard/")
        submissions = list(response.context["submissions"])
        assert submissions[0].pk == pending.pk
        assert submissions[1].pk == approved.pk

    def test_dashboard_shows_only_own_libraries(self, client, dashboard_user):
        """Verify the dashboard only shows libraries created by the logged-in user.
        Prevents cross-user data leakage."""
        other_user = User.objects.create_user(
            username="other", password="testpass123",
        )
        _create_library(
            user=dashboard_user, name="My Lib", status=Library.Status.APPROVED,
        )
        _create_library(
            user=other_user, name="Other Lib", status=Library.Status.APPROVED,
        )
        client.force_login(dashboard_user)
        response = client.get("/dashboard/")
        submissions = list(response.context["submissions"])
        assert len(submissions) == 1
        assert submissions[0].name == "My Lib"

    def test_dashboard_shows_account_management_links(self, client, dashboard_user):
        """Verify the dashboard renders links to account management pages.
        Ensures users can discover email, password, and deletion features."""
        client.force_login(dashboard_user)
        response = client.get("/dashboard/")
        content = response.content.decode()
        assert "/account/email/" in content
        assert "/account/password/" in content
        assert "/account/delete/" in content


@pytest.mark.django_db
class TestChangeEmailView:
    """Tests for the email change web view.
    Covers success, validation, and access control."""

    def test_change_email_requires_login(self, client):
        """Verify unauthenticated users are redirected to login.
        Ensures email changes require authentication."""
        response = client.get("/account/email/")
        assert response.status_code == 302

    def test_change_email_renders_form(self, client, dashboard_user):
        """Verify the change email page renders with the form.
        Confirms the GET request displays the input fields."""
        client.force_login(dashboard_user)
        response = client.get("/account/email/")
        assert response.status_code == 200
        assert "form" in response.context

    def test_change_email_success(self, client, dashboard_user):
        """Verify a valid email change updates the user and redirects.
        Confirms the database is updated with the new address."""
        client.force_login(dashboard_user)
        response = client.post("/account/email/", data={"email": "new@example.com"})
        assert response.status_code == 302
        dashboard_user.refresh_from_db()
        assert dashboard_user.email == "new@example.com"

    def test_change_email_rejects_duplicate(self, client, dashboard_user):
        """Verify duplicate email addresses are rejected.
        Prevents two users from having the same email."""
        User.objects.create_user(
            username="existing", email="taken@example.com", password="testpass123",
        )
        client.force_login(dashboard_user)
        response = client.post("/account/email/", data={"email": "taken@example.com"})
        assert response.status_code == 200
        assert response.context["form"].errors

    def test_change_email_rejects_same_email(self, client, dashboard_user):
        """Verify submitting the current email is rejected.
        Prevents unnecessary database writes for no-op changes."""
        client.force_login(dashboard_user)
        response = client.post("/account/email/", data={"email": "dash@example.com"})
        assert response.status_code == 200
        assert response.context["form"].errors


@pytest.mark.django_db
class TestChangePasswordView:
    """Tests for the password change web view.
    Covers success, wrong current password, and mismatch."""

    def test_change_password_requires_login(self, client):
        """Verify unauthenticated users are redirected to login.
        Ensures password changes require authentication."""
        response = client.get("/account/password/")
        assert response.status_code == 302

    def test_change_password_renders_form(self, client, dashboard_user):
        """Verify the change password page renders with the form.
        Confirms the GET request displays the input fields."""
        client.force_login(dashboard_user)
        response = client.get("/account/password/")
        assert response.status_code == 200
        assert "form" in response.context

    def test_change_password_success(self, client, dashboard_user):
        """Verify a valid password change updates the user and redirects.
        Confirms the user can log in with the new password."""
        client.force_login(dashboard_user)
        response = client.post("/account/password/", data={
            "current_password": "testpass123",
            "new_password1": "newSecure!Pass99",
            "new_password2": "newSecure!Pass99",
        })
        assert response.status_code == 302
        dashboard_user.refresh_from_db()
        assert dashboard_user.check_password("newSecure!Pass99")

    def test_change_password_wrong_current(self, client, dashboard_user):
        """Verify an incorrect current password is rejected.
        Prevents unauthorized password changes via stolen sessions."""
        client.force_login(dashboard_user)
        response = client.post("/account/password/", data={
            "current_password": "wrongpassword",
            "new_password1": "newSecure!Pass99",
            "new_password2": "newSecure!Pass99",
        })
        assert response.status_code == 200
        assert response.context["form"].errors

    def test_change_password_mismatch(self, client, dashboard_user):
        """Verify mismatched new passwords are rejected.
        Ensures the user confirms the new password correctly."""
        client.force_login(dashboard_user)
        response = client.post("/account/password/", data={
            "current_password": "testpass123",
            "new_password1": "newSecure!Pass99",
            "new_password2": "differentPass99!",
        })
        assert response.status_code == 200
        assert response.context["form"].errors


@pytest.mark.django_db
class TestDeleteAccountView:
    """Tests for the account deletion web view.
    Covers success, wrong password, and access control."""

    def test_delete_account_requires_login(self, client):
        """Verify unauthenticated users are redirected to login.
        Ensures account deletion requires authentication."""
        response = client.get("/account/delete/")
        assert response.status_code == 302

    def test_delete_account_renders_form(self, client, dashboard_user):
        """Verify the delete account page renders with the form.
        Confirms the GET request displays the confirmation input."""
        client.force_login(dashboard_user)
        response = client.get("/account/delete/")
        assert response.status_code == 200
        assert "form" in response.context

    def test_delete_account_success(self, client, dashboard_user):
        """Verify a correct password deletes the user and redirects.
        Confirms the user no longer exists in the database."""
        user_pk = dashboard_user.pk
        client.force_login(dashboard_user)
        response = client.post("/account/delete/", data={"password": "testpass123"})
        assert response.status_code == 302
        assert not User.objects.filter(pk=user_pk).exists()

    def test_delete_account_wrong_password(self, client, dashboard_user):
        """Verify an incorrect password prevents deletion.
        Ensures the destructive action requires proper confirmation."""
        client.force_login(dashboard_user)
        response = client.post("/account/delete/", data={"password": "wrongpassword"})
        assert response.status_code == 200
        assert response.context["form"].errors
        assert User.objects.filter(pk=dashboard_user.pk).exists()

    def test_delete_account_preserves_libraries(self, client, dashboard_user):
        """Verify libraries survive account deletion with created_by set to None.
        Ensures community content is preserved when users leave."""
        library = _create_library(
            user=dashboard_user,
            name="Preserved Lib",
            status=Library.Status.APPROVED,
        )
        client.force_login(dashboard_user)
        client.post("/account/delete/", data={"password": "testpass123"})
        library.refresh_from_db()
        assert library.pk is not None
        assert library.created_by is None

    def test_social_user_delete_with_confirm_text(self, client, social_only_user):
        """Verify social-only users can delete by typing DELETE.
        Ensures social users have a path to account deletion without a password."""
        user_pk = social_only_user.pk
        client.force_login(social_only_user)
        response = client.post("/account/delete/", data={"confirm_text": "DELETE"})
        assert response.status_code == 302
        assert not User.objects.filter(pk=user_pk).exists()

    def test_social_user_delete_wrong_text(self, client, social_only_user):
        """Verify typing anything other than DELETE is rejected.
        Prevents accidental account deletion for social users."""
        client.force_login(social_only_user)
        response = client.post("/account/delete/", data={"confirm_text": "delete"})
        assert response.status_code == 200
        assert response.context["form"].errors
        assert User.objects.filter(pk=social_only_user.pk).exists()


@pytest.mark.django_db
class TestSocialOnlyRestrictions:
    """Tests for blocking email and password changes for social-only users.
    Social users authenticate via their provider and have no local password."""

    def test_social_user_cannot_change_email(self, client, social_only_user):
        """Verify social-only users are redirected when accessing email change.
        Their email is managed by the social provider."""
        client.force_login(social_only_user)
        response = client.get("/account/email/")
        assert response.status_code == 302
        assert "/dashboard/" in response.url

    def test_social_user_cannot_change_password(self, client, social_only_user):
        """Verify social-only users are redirected when accessing password change.
        They authenticate via their provider and have no local password."""
        client.force_login(social_only_user)
        response = client.get("/account/password/")
        assert response.status_code == 302
        assert "/dashboard/" in response.url

    def test_dashboard_hides_email_password_buttons_for_social(self, client, social_only_user):
        """Verify the dashboard hides email and password buttons for social users.
        Prevents confusion by not showing unusable account management options."""
        client.force_login(social_only_user)
        response = client.get("/dashboard/")
        content = response.content.decode()
        assert "/account/email/" not in content
        assert "/account/password/" not in content
        assert "/account/delete/" in content
