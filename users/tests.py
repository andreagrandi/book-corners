import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from django.urls import reverse
from django.utils import translation

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

    @override_settings(
        AUTH_RATE_LIMIT_ENABLED=True,
        AUTH_RATE_LIMIT_WINDOW_SECONDS=300,
        AUTH_RATE_LIMIT_LOGIN_ATTEMPTS=1,
    )
    def test_login_rate_limit_blocks_excessive_attempts(self, client, user):
        """Verify repeated login attempts trigger auth throttling.
        Confirms brute-force protection returns HTTP 429 on excess attempts."""
        cache.clear()

        first_response = client.post(
            reverse("login"),
            data={
                "username": user.username,
                "password": "wrong-password",
            },
        )
        second_response = client.post(
            reverse("login"),
            data={
                "username": user.username,
                "password": "wrong-password",
            },
        )

        assert first_response.status_code == 200
        assert second_response.status_code == 429
        assert "Retry-After" in second_response.headers
        assert "Too many login attempts" in second_response.content.decode()


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


@pytest.mark.django_db
class TestEmailUniqueness:
    def test_save_normalizes_email_to_lowercase(self):
        """Verify User.save() lowercases and strips email.
        Ensures consistent storage regardless of input casing."""
        user = User.objects.create_user(
            username="mixedcase",
            email="  Alice@Example.COM  ",
            password="TestPass123!",
        )
        user.refresh_from_db()
        assert user.email == "alice@example.com"

    def test_blank_emails_do_not_conflict(self):
        """Verify multiple users can have blank emails.
        Supports superusers and admin-created accounts without email."""
        user1 = User.objects.create_user(username="noemail1", email="", password="TestPass123!")
        user2 = User.objects.create_user(username="noemail2", email="", password="TestPass123!")
        assert user1.pk != user2.pk

    def test_duplicate_email_rejected_by_db_constraint(self):
        """Verify the DB constraint prevents duplicate emails.
        Acts as a safety net when form-level checks are bypassed."""
        User.objects.create_user(
            username="first",
            email="taken@example.com",
            password="TestPass123!",
        )
        with pytest.raises(IntegrityError):
            User.objects.create_user(
                username="second",
                email="taken@example.com",
                password="TestPass123!",
            )

    def test_duplicate_email_case_insensitive_rejected_by_db(self):
        """Verify the DB constraint catches case variations.
        Prevents bypass via mixed-case email input."""
        User.objects.create_user(
            username="first",
            email="unique@example.com",
            password="TestPass123!",
        )
        with pytest.raises(IntegrityError):
            User.objects.create_user(
                username="second",
                email="UNIQUE@example.com",
                password="TestPass123!",
            )

    def test_registration_form_rejects_duplicate_email(self, client):
        """Verify the registration form shows a clear duplicate email error.
        Gives users actionable feedback before hitting the DB constraint."""
        User.objects.create_user(
            username="existing",
            email="taken@example.com",
            password="TestPass123!",
        )
        response = client.post(
            reverse("register"),
            data={
                "username": "newcomer",
                "email": "Taken@Example.COM",
                "password1": "SecretPass123!",
                "password2": "SecretPass123!",
            },
        )
        assert response.status_code == 200
        assert not User.objects.filter(username="newcomer").exists()
        content = response.content.decode()
        assert "A user with this email already exists" in content


@pytest.mark.django_db(transaction=True)
class TestEmailMigration:
    """Tests that exercise the 0002 migration transition via MigrationExecutor."""

    migrate_from = ("users", "0001_initial")
    migrate_to = ("users", "0003_add_language_field")

    def _run_migration(self, state):
        """Roll forward from migrate_from to migrate_to.
        Returns the post-migration app state for model access."""
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([state])
        return executor.loader.project_state([state]).apps

    def _rollback_to_initial(self):
        """Roll back to 0001_initial so pre-migration data can be inserted.
        Returns the pre-migration app state for model access."""
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([self.migrate_from])
        return executor.loader.project_state([self.migrate_from]).apps

    def test_migration_normalizes_mixed_case_emails(self):
        """Verify the data migration lowercases existing emails.
        Confirms transition behavior matches the normalization policy."""
        old_apps = self._rollback_to_initial()
        OldUser = old_apps.get_model("users", "User")
        OldUser.objects.create(username="alice", email="Alice@Example.COM", password="x")
        OldUser.objects.create(username="bob", email="  Bob@TEST.org  ", password="x")

        self._run_migration(self.migrate_to)

        alice = User.objects.get(username="alice")
        bob = User.objects.get(username="bob")
        assert alice.email == "alice@example.com"
        assert bob.email == "bob@test.org"

    def test_migration_skips_whitespace_only_emails(self):
        """Verify whitespace-only emails are treated as blank after trim.
        Prevents false duplicate errors during normalization."""
        old_apps = self._rollback_to_initial()
        OldUser = old_apps.get_model("users", "User")
        OldUser.objects.create(username="spaces", email="   ", password="x")
        OldUser.objects.create(username="blank", email="", password="x")

        self._run_migration(self.migrate_to)

        spaces = User.objects.get(username="spaces")
        blank = User.objects.get(username="blank")
        assert spaces.email == ""
        assert blank.email == ""

    def test_migration_rejects_case_insensitive_duplicates(self):
        """Verify the migration fails loudly on case-duplicate emails.
        Forces manual resolution before the constraint can be applied."""
        old_apps = self._rollback_to_initial()
        OldUser = old_apps.get_model("users", "User")
        OldUser.objects.create(username="first", email="dupe@example.com", password="x")
        OldUser.objects.create(username="second", email="DUPE@example.com", password="x")

        with pytest.raises(ValueError, match="duplicates found after lowercasing"):
            self._run_migration(self.migrate_to)


@pytest.mark.django_db
class TestI18nLanguageSwitching:
    """Tests for language switching, user preference persistence, and admin enforcement."""

    def test_anonymous_user_defaults_to_english(self, client):
        """Verify anonymous requests render in English by default.
        Confirms the base language without any cookie or preference."""
        response = client.get(reverse("home"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "Book Corners" in content

    def test_cookie_based_language_switching(self, client):
        """Verify setting the language cookie switches the rendered language.
        Confirms anonymous users can browse in Italian via cookie."""
        client.cookies.load({settings.LANGUAGE_COOKIE_NAME: "it"})
        response = client.get(reverse("home"))
        content = response.content.decode()
        assert response.status_code == 200
        assert 'lang="it"' in content

    def test_logged_in_user_gets_preferred_language(self, client, user):
        """Verify authenticated users see content in their saved language.
        Confirms the UserLanguageMiddleware activates the user preference."""
        user.language = "it"
        user.save(update_fields=["language"])
        client.force_login(user)

        response = client.get(reverse("home"))
        content = response.content.decode()
        assert response.status_code == 200
        assert 'lang="it"' in content

    def test_set_language_view_updates_cookie_and_user_model(self, client, user):
        """Verify the set_language view persists the choice for logged-in users.
        Confirms both cookie and user.language field are updated."""
        client.force_login(user)

        response = client.post(
            reverse("set_language"),
            data={"language": "it", "next": "/"},
            follow=False,
        )
        assert response.status_code in (302, 200)

        user.refresh_from_db()
        assert user.language == "it"

    def test_set_language_view_anonymous_user(self, client):
        """Verify anonymous users can switch language without errors.
        Confirms the view handles unauthenticated requests gracefully."""
        response = client.post(
            reverse("set_language"),
            data={"language": "it", "next": "/"},
            follow=False,
        )
        assert response.status_code in (302, 200)

    def test_admin_always_stays_english(self, client, admin_user):
        """Verify admin pages render in English regardless of user preference.
        Confirms the middleware forces English for /admin/ paths."""
        admin_user.language = "it"
        admin_user.save(update_fields=["language"])
        client.force_login(admin_user)

        response = client.get("/admin/", follow=True)
        content = response.content.decode()
        assert response.status_code == 200
        assert 'lang="en"' in content

    def test_about_page_renders_in_italian(self, client):
        """Verify the about page renders translated content in Italian.
        Smoke test for template-level i18n on a static page."""
        client.cookies.load({settings.LANGUAGE_COOKIE_NAME: "it"})
        response = client.get(reverse("about_page"))
        content = response.content.decode()
        assert response.status_code == 200
        assert 'lang="it"' in content

    def test_privacy_page_renders_italian_template(self, client):
        """Verify the privacy page uses the Italian template when language is Italian.
        Confirms the view selects the correct language-specific template."""
        client.cookies.load({settings.LANGUAGE_COOKIE_NAME: "it"})
        response = client.get(reverse("privacy_page"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "Informativa sulla Privacy" in content

    def test_privacy_page_renders_english_template(self, client):
        """Verify the privacy page uses the English template by default.
        Confirms the default language selection works correctly."""
        response = client.get(reverse("privacy_page"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "Privacy Policy" in content

    def test_set_language_rejects_invalid_code(self, client, user):
        """Verify invalid language codes are ignored.
        Confirms the view does not persist unsupported language choices."""
        client.force_login(user)

        client.post(
            reverse("set_language"),
            data={"language": "xx", "next": "/"},
            follow=False,
        )

        user.refresh_from_db()
        assert user.language == "en"

    def test_user_language_field_default(self):
        """Verify new users default to English language preference.
        Confirms the model field default is applied correctly."""
        user = User.objects.create_user(
            username="langtest",
            password="TestPass123!",
        )
        assert user.language == "en"
