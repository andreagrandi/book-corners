"""Tests for social media posting: model, text builder, command, and notifications."""

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from PIL import Image

from libraries.models import Library, SocialPost
from libraries.notifications import notify_social_post, notify_social_post_error
from libraries.social.text import build_post_text, _country_name

User = get_user_model()


def _build_uploaded_photo(
    *,
    file_name: str = "library.jpg",
    width: int = 640,
    height: int = 480,
) -> SimpleUploadedFile:
    """Build an in-memory JPEG upload for test fixtures.
    Creates a minimal image without touching disk."""
    image_bytes = BytesIO()
    image = Image.new("RGB", (width, height), color=(140, 165, 210))
    image.save(image_bytes, format="JPEG", quality=50)
    image_bytes.seek(0)
    return SimpleUploadedFile(
        name=file_name,
        content=image_bytes.getvalue(),
        content_type="image/jpeg",
    )


@pytest.fixture
def social_user(db):
    """Create a test user for social posting tests.
    Provides a minimal user record for library ownership."""
    return User.objects.create_user(username="socialuser", password="testpass123")


@pytest.fixture
def approved_library(social_user):
    """Create an approved library with a photo for social posting tests.
    Represents the typical candidate for social media sharing."""
    return Library.objects.create(
        name="Cozy Corner Library",
        description="A lovely little free library on the corner",
        photo=_build_uploaded_photo(),
        location=Point(2.3522, 48.8566),
        address="123 Rue de Rivoli",
        city="Paris",
        country="FR",
        status=Library.Status.APPROVED,
        created_by=social_user,
    )


@pytest.fixture
def approved_library_no_photo(social_user):
    """Create an approved library without a photo.
    Used to test that libraries without photos are excluded."""
    return Library.objects.create(
        name="No Photo Library",
        description="A library without any image",
        location=Point(13.405, 52.52),
        address="456 Unter den Linden",
        city="Berlin",
        country="DE",
        status=Library.Status.APPROVED,
        created_by=social_user,
    )


# --- Model tests ---


@pytest.mark.django_db
class TestSocialPostModel:
    """Tests for the SocialPost model."""

    def test_create_social_post(self, approved_library):
        """Verify basic SocialPost creation and field storage.
        Ensures the record persists with all expected attributes."""
        post = SocialPost.objects.create(
            library=approved_library,
            post_text="Test post text",
            mastodon_url="https://mastodon.social/@test/123",
            bluesky_url="https://bsky.app/profile/test/post/abc",
        )
        assert post.pk is not None
        assert post.library == approved_library
        assert post.post_text == "Test post text"
        assert post.mastodon_url == "https://mastodon.social/@test/123"
        assert post.bluesky_url == "https://bsky.app/profile/test/post/abc"
        assert post.posted_at is not None

    def test_social_post_str(self, approved_library):
        """Verify the string representation includes library and timestamp.
        Keeps admin and log output readable."""
        post = SocialPost.objects.create(
            library=approved_library,
            post_text="Test",
        )
        assert "SocialPost for" in str(post)
        assert str(approved_library) in str(post)

    def test_social_post_relationship(self, approved_library):
        """Verify the reverse relationship from Library to SocialPost.
        Ensures the related_name works for querying posted status."""
        SocialPost.objects.create(
            library=approved_library,
            post_text="Test",
        )
        assert approved_library.social_posts.count() == 1

    def test_social_post_partial_urls(self, approved_library):
        """Verify that SocialPost accepts empty URL fields.
        Supports partial posting when one platform fails."""
        post = SocialPost.objects.create(
            library=approved_library,
            post_text="Only mastodon",
            mastodon_url="https://mastodon.social/@test/123",
        )
        assert post.bluesky_url == ""


# --- Text builder tests ---


@pytest.mark.django_db
class TestBuildPostText:
    """Tests for the social media post text builder."""

    def test_basic_text_generation(self, approved_library):
        """Verify the post text includes description, location, and URL.
        Checks that all required components appear in the output."""
        text = build_post_text(approved_library, "https://example.com/library/test")
        assert "A lovely little free library" in text
        assert "Paris" in text
        assert "France" in text
        assert "https://example.com/library/test" in text

    def test_max_length_respected(self, approved_library):
        """Verify the post text never exceeds max_length.
        Bluesky's 300-char limit is the tightest constraint."""
        text = build_post_text(approved_library, "https://example.com/library/test", max_length=300)
        assert len(text) <= 300

    def test_short_max_length_truncates_description(self, approved_library):
        """Verify that long descriptions are truncated to fit.
        Ensures the post stays within platform limits."""
        text = build_post_text(approved_library, "https://example.com/library/test", max_length=150)
        assert len(text) <= 150

    def test_hashtags_included(self, approved_library):
        """Verify that hashtags appear in the post text.
        Hashtags improve discoverability on social platforms."""
        text = build_post_text(approved_library, "https://example.com/lib", max_length=300)
        assert "#BookCorners" in text

    def test_country_name_lookup(self):
        """Verify country code to name conversion for known codes.
        Ensures the lookup table covers expected European countries."""
        assert _country_name("FR") == "France"
        assert _country_name("DE") == "Germany"
        assert _country_name("IT") == "Italy"

    def test_country_name_fallback(self):
        """Verify unknown country codes fall back to the raw code.
        Prevents crashes for countries not in the lookup table."""
        assert _country_name("XX") == "XX"

    def test_library_without_description_uses_name(self, social_user):
        """Verify fallback to name when description is empty.
        Ensures every post has meaningful text content."""
        lib = Library.objects.create(
            name="Named Library",
            location=Point(0, 0),
            address="1 Main St",
            city="London",
            country="GB",
            status=Library.Status.APPROVED,
            created_by=social_user,
        )
        text = build_post_text(lib, "https://example.com/lib")
        assert "Named Library" in text


# --- Command tests ---


@pytest.mark.django_db
class TestPostRandomLibraryCommand:
    """Tests for the post_random_library management command."""

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="test.bsky.social",
        BLUESKY_APP_PASSWORD="test-password",
        SITE_URL="https://bookcorners.org",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_bluesky")
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_posts_to_both_platforms(
        self, mock_photo, mock_mastodon, mock_bluesky, approved_library, capsys
    ):
        """Verify the command posts to both Mastodon and Bluesky.
        Creates a SocialPost record with URLs from both platforms."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_mastodon.return_value = "https://mastodon.test/@user/123"
        mock_bluesky.return_value = "https://bsky.app/profile/test/post/abc"

        call_command("post_random_library")

        assert SocialPost.objects.count() == 1
        post = SocialPost.objects.first()
        assert post.library == approved_library
        assert post.mastodon_url == "https://mastodon.test/@user/123"
        assert post.bluesky_url == "https://bsky.app/profile/test/post/abc"
        assert post.post_text != ""

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="test.bsky.social",
        BLUESKY_APP_PASSWORD="test-password",
        SITE_URL="https://bookcorners.org",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_bluesky")
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_does_not_repeat_posted_library(
        self, mock_photo, mock_mastodon, mock_bluesky, approved_library, capsys
    ):
        """Verify that already-posted libraries are not selected again.
        Prevents duplicate posts across invocations."""
        SocialPost.objects.create(
            library=approved_library,
            post_text="Already posted",
        )

        call_command("post_random_library")

        captured = capsys.readouterr()
        assert "All libraries have been posted" in captured.out
        mock_mastodon.assert_not_called()

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="test.bsky.social",
        BLUESKY_APP_PASSWORD="test-password",
        SITE_URL="https://bookcorners.org",
    )
    def test_dry_run_does_not_post(self, approved_library, capsys):
        """Verify the dry-run flag prints text without posting.
        Allows previewing posts before going live."""
        call_command("post_random_library", dry_run=True)

        captured = capsys.readouterr()
        assert "Library:" in captured.out
        assert "Text (" in captured.out
        assert SocialPost.objects.count() == 0

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="test.bsky.social",
        BLUESKY_APP_PASSWORD="test-password",
        SITE_URL="https://bookcorners.org",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_bluesky")
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_one_platform_fails_still_posts_other(
        self, mock_photo, mock_mastodon, mock_bluesky, approved_library, capsys
    ):
        """Verify that failure on one platform doesn't block the other.
        Creates a partial SocialPost record with the successful URL."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_mastodon.side_effect = Exception("Mastodon is down")
        mock_bluesky.return_value = "https://bsky.app/profile/test/post/abc"

        call_command("post_random_library")

        assert SocialPost.objects.count() == 1
        post = SocialPost.objects.first()
        assert post.mastodon_url == ""
        assert post.bluesky_url == "https://bsky.app/profile/test/post/abc"

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="test.bsky.social",
        BLUESKY_APP_PASSWORD="test-password",
        SITE_URL="https://bookcorners.org",
    )
    def test_no_eligible_libraries(self, social_user, capsys):
        """Verify clean exit when no unposted libraries exist.
        Prevents errors when the posting pool is exhausted."""
        call_command("post_random_library")

        captured = capsys.readouterr()
        assert "All libraries have been posted" in captured.out

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        SITE_URL="https://bookcorners.org",
    )
    def test_skips_libraries_without_photo(self, approved_library_no_photo, capsys):
        """Verify libraries without photos are excluded from selection.
        Social posts require a visual to be engaging."""
        call_command("post_random_library", dry_run=True)

        captured = capsys.readouterr()
        assert "All libraries have been posted" in captured.out


@pytest.mark.django_db
class TestCredentialGating:
    """Tests for credential gating behavior."""

    @override_settings(
        MASTODON_INSTANCE_URL="",
        MASTODON_ACCESS_TOKEN="",
        BLUESKY_HANDLE="",
        BLUESKY_APP_PASSWORD="",
    )
    def test_no_credentials_skips_silently(self, approved_library, capsys):
        """Verify clean exit when no social credentials are configured.
        Allows deploying code before accounts are ready."""
        call_command("post_random_library")

        captured = capsys.readouterr()
        assert "No social media credentials configured" in captured.out
        assert SocialPost.objects.count() == 0

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="",
        BLUESKY_APP_PASSWORD="",
        SITE_URL="https://bookcorners.org",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_only_mastodon_configured(
        self, mock_photo, mock_mastodon, approved_library, capsys
    ):
        """Verify only Mastodon is called when Bluesky is unconfigured.
        Supports gradual platform rollout."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_mastodon.return_value = "https://mastodon.test/@user/123"

        call_command("post_random_library")

        assert SocialPost.objects.count() == 1
        post = SocialPost.objects.first()
        assert post.mastodon_url == "https://mastodon.test/@user/123"
        assert post.bluesky_url == ""


# --- Notification tests ---


@pytest.mark.django_db
class TestSocialPostNotifications:
    """Tests for social posting email notifications."""

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
        SITE_URL="https://bookcorners.org",
    )
    def test_success_notification_sent(self, approved_library):
        """Verify success notification email is sent with post URLs.
        Keeps admins informed about automated social activity."""
        social_post = SocialPost.objects.create(
            library=approved_library,
            post_text="Test post",
            mastodon_url="https://mastodon.test/@user/123",
            bluesky_url="https://bsky.app/profile/test/post/abc",
        )

        notify_social_post(social_post)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert "Social post published" in email.subject
        assert "mastodon.test" in email.body
        assert "bsky.app" in email.body

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
        SITE_URL="https://bookcorners.org",
    )
    def test_error_notification_sent(self, approved_library):
        """Verify error notification email is sent with failure details.
        Alerts admins when social posting encounters problems."""
        notify_social_post_error(approved_library, "Mastodon: Connection refused")

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert "Social post failed" in email.subject
        assert "Connection refused" in email.body

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="",
    )
    def test_no_email_when_not_configured(self, approved_library):
        """Verify no email is sent when admin email is not configured.
        Prevents errors in environments without email setup."""
        social_post = SocialPost.objects.create(
            library=approved_library,
            post_text="Test post",
        )

        notify_social_post(social_post)
        notify_social_post_error(approved_library, "error")

        assert len(mail.outbox) == 0
