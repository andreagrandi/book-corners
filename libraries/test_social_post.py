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

from libraries.models import InstagramToken, Library, SocialPost
from libraries.notifications import notify_social_post, notify_social_post_error
from libraries.social.image_ai import _parse_response, analyze_library_image
from libraries.social.text import (
    build_bluesky_text,
    build_hashtag_comment,
    build_post_text,
    _country_name,
)

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
            instagram_url="https://www.instagram.com/p/abc123/",
        )
        assert post.pk is not None
        assert post.library == approved_library
        assert post.post_text == "Test post text"
        assert post.mastodon_url == "https://mastodon.social/@test/123"
        assert post.bluesky_url == "https://bsky.app/profile/test/post/abc"
        assert post.instagram_url == "https://www.instagram.com/p/abc123/"
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
        assert post.instagram_url == ""


@pytest.mark.django_db
class TestInstagramTokenModel:
    """Tests for the InstagramToken model."""

    def test_create_token(self):
        """Verify basic token creation and field storage.
        Ensures the access token persists correctly."""
        token = InstagramToken.objects.create(access_token="test-token-abc123")
        assert token.pk is not None
        assert token.access_token == "test-token-abc123"
        assert token.refreshed_at is not None

    def test_token_str(self):
        """Verify the string representation shows refresh timestamp.
        Keeps admin display informative."""
        token = InstagramToken.objects.create(access_token="test-token")
        assert "InstagramToken" in str(token)
        assert "refreshed" in str(token)


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

    def test_bluesky_text_has_facets(self, approved_library):
        """Verify the Bluesky TextBuilder includes link and tag facets.
        Ensures URLs and hashtags render as clickable on Bluesky."""
        builder = build_bluesky_text(approved_library, "https://example.com/library/test")
        text = builder.build_text()
        assert "https://example.com/library/test" in text
        assert "#BookCorners" in text
        assert len(builder.build_facets()) > 0

    def test_bluesky_text_link_facet(self, approved_library):
        """Verify the Bluesky text contains a link facet for the detail URL.
        Makes the library link clickable in the Bluesky post."""
        builder = build_bluesky_text(approved_library, "https://example.com/library/test")
        facets = builder.build_facets()
        link_facets = [
            f for f in facets
            if any(hasattr(feat, "uri") for feat in f.features)
        ]
        assert len(link_facets) >= 1

    def test_bluesky_text_tag_facets(self, approved_library):
        """Verify the Bluesky text contains tag facets for hashtags.
        Makes hashtags clickable and searchable on Bluesky."""
        builder = build_bluesky_text(approved_library, "https://example.com/library/test")
        facets = builder.build_facets()
        tag_facets = [
            f for f in facets
            if any(hasattr(feat, "tag") for feat in f.features)
        ]
        assert len(tag_facets) >= 4  # at least the base hashtags


# --- Command tests ---


@pytest.mark.django_db
class TestPostRandomLibraryCommand:
    """Tests for the post_random_library management command."""

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="test.bsky.social",
        BLUESKY_APP_PASSWORD="test-password",
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
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
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="ig-token",
        SITE_URL="https://bookcorners.org",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_instagram")
    @patch("libraries.management.commands.post_random_library.Command._post_to_bluesky")
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_posts_to_all_three_platforms(
        self, mock_photo, mock_mastodon, mock_bluesky, mock_instagram, approved_library, capsys
    ):
        """Verify the command posts to all three platforms when configured.
        Creates a SocialPost record with URLs from all platforms."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_mastodon.return_value = "https://mastodon.test/@user/123"
        mock_bluesky.return_value = "https://bsky.app/profile/test/post/abc"
        mock_instagram.return_value = "https://www.instagram.com/p/abc123/"

        call_command("post_random_library")

        assert SocialPost.objects.count() == 1
        post = SocialPost.objects.first()
        assert post.mastodon_url == "https://mastodon.test/@user/123"
        assert post.bluesky_url == "https://bsky.app/profile/test/post/abc"
        assert post.instagram_url == "https://www.instagram.com/p/abc123/"

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="test.bsky.social",
        BLUESKY_APP_PASSWORD="test-password",
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
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
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
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
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
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
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
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
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
        SITE_URL="https://bookcorners.org",
    )
    def test_skips_libraries_without_photo(self, approved_library_no_photo, capsys):
        """Verify libraries without photos are excluded from selection.
        Social posts require a visual to be engaging."""
        call_command("post_random_library", dry_run=True)

        captured = capsys.readouterr()
        assert "All libraries have been posted" in captured.out


@pytest.mark.django_db
class TestOnlyPlatformFlag:
    """Tests for the --only flag on the post_random_library command."""

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="test.bsky.social",
        BLUESKY_APP_PASSWORD="test-password",
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="ig-token",
        SITE_URL="https://bookcorners.org",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_instagram")
    @patch("libraries.management.commands.post_random_library.Command._post_to_bluesky")
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_only_instagram(
        self, mock_photo, mock_mastodon, mock_bluesky, mock_instagram, approved_library
    ):
        """Verify --only instagram skips Mastodon and Bluesky.
        Useful for testing and populating a new platform."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_instagram.return_value = "https://www.instagram.com/p/abc123/"

        call_command("post_random_library", only="instagram")

        mock_mastodon.assert_not_called()
        mock_bluesky.assert_not_called()
        mock_instagram.assert_called_once()
        post = SocialPost.objects.first()
        assert post.instagram_url == "https://www.instagram.com/p/abc123/"
        assert post.mastodon_url == ""
        assert post.bluesky_url == ""

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="test.bsky.social",
        BLUESKY_APP_PASSWORD="test-password",
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="ig-token",
        SITE_URL="https://bookcorners.org",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_instagram")
    @patch("libraries.management.commands.post_random_library.Command._post_to_bluesky")
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_only_mastodon(
        self, mock_photo, mock_mastodon, mock_bluesky, mock_instagram, approved_library
    ):
        """Verify --only mastodon skips Bluesky and Instagram.
        Isolates posting to a single platform."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_mastodon.return_value = "https://mastodon.test/@user/123"

        call_command("post_random_library", only="mastodon")

        mock_mastodon.assert_called_once()
        mock_bluesky.assert_not_called()
        mock_instagram.assert_not_called()

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="test.bsky.social",
        BLUESKY_APP_PASSWORD="test-password",
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="ig-token",
        SITE_URL="https://bookcorners.org",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_instagram")
    @patch("libraries.management.commands.post_random_library.Command._post_to_bluesky")
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_only_bluesky(
        self, mock_photo, mock_mastodon, mock_bluesky, mock_instagram, approved_library
    ):
        """Verify --only bluesky skips Mastodon and Instagram.
        Isolates posting to a single platform."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_bluesky.return_value = "https://bsky.app/profile/test/post/abc"

        call_command("post_random_library", only="bluesky")

        mock_mastodon.assert_not_called()
        mock_bluesky.assert_called_once()
        mock_instagram.assert_not_called()


@pytest.mark.django_db
class TestCredentialGating:
    """Tests for credential gating behavior."""

    @override_settings(
        MASTODON_INSTANCE_URL="",
        MASTODON_ACCESS_TOKEN="",
        BLUESKY_HANDLE="",
        BLUESKY_APP_PASSWORD="",
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
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
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
        SITE_URL="https://bookcorners.org",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_only_mastodon_configured(
        self, mock_photo, mock_mastodon, approved_library, capsys
    ):
        """Verify only Mastodon is called when Bluesky and Instagram are unconfigured.
        Supports gradual platform rollout."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_mastodon.return_value = "https://mastodon.test/@user/123"

        call_command("post_random_library")

        assert SocialPost.objects.count() == 1
        post = SocialPost.objects.first()
        assert post.mastodon_url == "https://mastodon.test/@user/123"
        assert post.bluesky_url == ""
        assert post.instagram_url == ""

    @override_settings(
        MASTODON_INSTANCE_URL="",
        MASTODON_ACCESS_TOKEN="",
        BLUESKY_HANDLE="",
        BLUESKY_APP_PASSWORD="",
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="ig-token",
        SITE_URL="https://bookcorners.org",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_instagram")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_only_instagram_configured(
        self, mock_photo, mock_instagram, approved_library, capsys
    ):
        """Verify only Instagram is called when Mastodon and Bluesky are unconfigured.
        Supports gradual platform rollout."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_instagram.return_value = "https://www.instagram.com/p/abc123/"

        call_command("post_random_library")

        assert SocialPost.objects.count() == 1
        post = SocialPost.objects.first()
        assert post.mastodon_url == ""
        assert post.bluesky_url == ""
        assert post.instagram_url == "https://www.instagram.com/p/abc123/"

    @override_settings(
        MASTODON_INSTANCE_URL="",
        MASTODON_ACCESS_TOKEN="",
        BLUESKY_HANDLE="",
        BLUESKY_APP_PASSWORD="",
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="",
        SITE_URL="https://bookcorners.org",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_instagram")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_instagram_configured_via_db_token(
        self, mock_photo, mock_instagram, approved_library
    ):
        """Verify Instagram is detected as configured when token exists in DB.
        Supports the DB-first token strategy after initial refresh."""
        InstagramToken.objects.create(access_token="db-stored-token")
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_instagram.return_value = "https://www.instagram.com/p/abc123/"

        call_command("post_random_library")

        assert SocialPost.objects.count() == 1
        post = SocialPost.objects.first()
        assert post.instagram_url == "https://www.instagram.com/p/abc123/"


# --- Instagram client tests ---


@pytest.mark.django_db
class TestInstagramClient:
    """Tests for the Instagram posting client module."""

    @override_settings(
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="test-ig-token",
        SITE_URL="https://bookcorners.org",
    )
    @patch("libraries.social.instagram.time.sleep")
    @patch("libraries.social.instagram.requests.get")
    @patch("libraries.social.instagram.requests.post")
    def test_post_library_success(self, mock_post, mock_get, mock_sleep, approved_library):
        """Verify the full Instagram posting flow works end to end.
        Container creation, status polling, publishing, and permalink retrieval."""
        from libraries.social.instagram import post_library

        mock_post.side_effect = [
            _mock_response(json_data={"id": "container-123"}),
            _mock_response(json_data={"id": "media-456"}),
        ]
        mock_get.side_effect = [
            _mock_response(json_data={"status_code": "FINISHED"}),
            _mock_response(json_data={"permalink": "https://www.instagram.com/p/abc123/"}),
        ]

        result = post_library(
            approved_library,
            text="Test caption",
            image_path=Path("/tmp/test.jpg"),
        )

        assert result.permalink == "https://www.instagram.com/p/abc123/"
        assert result.media_id == "media-456"
        assert mock_post.call_count == 2
        assert mock_get.call_count == 2

        # Verify container creation call
        container_call = mock_post.call_args_list[0]
        assert "123456/media" in container_call.args[0]
        assert container_call.kwargs["data"]["caption"] == "Test caption"
        assert "bookcorners.org" in container_call.kwargs["data"]["image_url"]

    @override_settings(
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="test-ig-token",
        SITE_URL="https://bookcorners.org",
    )
    @patch("libraries.social.instagram.requests.post")
    def test_post_library_container_fails(self, mock_post, approved_library):
        """Verify HTTP errors from the container step propagate correctly.
        Allows the command to catch and handle the failure."""
        from libraries.social.instagram import post_library

        mock_post.return_value = _mock_response(status_code=400, raise_on_status=True)

        with pytest.raises(Exception):
            post_library(
                approved_library,
                text="Test caption",
                image_path=Path("/tmp/test.jpg"),
            )

    @override_settings(
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="test-ig-token",
        SITE_URL="https://bookcorners.org",
    )
    @patch("libraries.social.instagram.time.sleep")
    @patch("libraries.social.instagram.requests.get")
    @patch("libraries.social.instagram.requests.post")
    def test_post_library_polls_until_finished(self, mock_post, mock_get, mock_sleep, approved_library):
        """Verify the client polls container status before publishing.
        Handles the asynchronous container processing delay."""
        from libraries.social.instagram import post_library

        mock_post.side_effect = [
            _mock_response(json_data={"id": "container-123"}),
            _mock_response(json_data={"id": "media-456"}),
        ]
        mock_get.side_effect = [
            _mock_response(json_data={"status_code": "IN_PROGRESS"}),
            _mock_response(json_data={"status_code": "IN_PROGRESS"}),
            _mock_response(json_data={"status_code": "FINISHED"}),
            _mock_response(json_data={"permalink": "https://www.instagram.com/p/abc123/"}),
        ]

        result = post_library(
            approved_library,
            text="Test caption",
            image_path=Path("/tmp/test.jpg"),
        )

        assert result.permalink == "https://www.instagram.com/p/abc123/"
        assert result.media_id == "media-456"
        assert mock_sleep.call_count == 2
        assert mock_get.call_count == 4

    @override_settings(
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="test-ig-token",
        SITE_URL="https://bookcorners.org",
    )
    @patch("libraries.social.instagram.time.sleep")
    @patch("libraries.social.instagram.requests.get")
    @patch("libraries.social.instagram.requests.post")
    def test_post_library_container_error_raises(self, mock_post, mock_get, mock_sleep, approved_library):
        """Verify a container ERROR status raises an exception.
        Prevents publishing a broken container."""
        from libraries.social.instagram import post_library

        mock_post.return_value = _mock_response(json_data={"id": "container-123"})
        mock_get.return_value = _mock_response(json_data={"status_code": "ERROR"})

        with pytest.raises(RuntimeError, match="failed with status: ERROR"):
            post_library(
                approved_library,
                text="Test caption",
                image_path=Path("/tmp/test.jpg"),
            )

    @override_settings(
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="",
        SITE_URL="https://bookcorners.org",
    )
    @patch("libraries.social.instagram.time.sleep")
    @patch("libraries.social.instagram.requests.get")
    @patch("libraries.social.instagram.requests.post")
    def test_post_library_uses_db_token(self, mock_post, mock_get, mock_sleep, approved_library):
        """Verify the client prefers the DB-stored token over the env var.
        Ensures refreshed tokens are picked up automatically."""
        from libraries.social.instagram import post_library

        InstagramToken.objects.create(access_token="db-token-xyz")

        mock_post.side_effect = [
            _mock_response(json_data={"id": "container-123"}),
            _mock_response(json_data={"id": "media-456"}),
        ]
        mock_get.side_effect = [
            _mock_response(json_data={"status_code": "FINISHED"}),
            _mock_response(json_data={"permalink": "https://www.instagram.com/p/xyz/"}),
        ]

        post_library(
            approved_library,
            text="Test caption",
            image_path=Path("/tmp/test.jpg"),
        )

        # Verify DB token was used in container creation
        container_call = mock_post.call_args_list[0]
        assert container_call.kwargs["data"]["access_token"] == "db-token-xyz"


# --- Token refresh command tests ---


@pytest.mark.django_db
class TestRefreshInstagramToken:
    """Tests for the refresh_instagram_token management command."""

    @override_settings(INSTAGRAM_ACCESS_TOKEN="env-token-abc")
    @patch("libraries.management.commands.refresh_instagram_token.requests.get")
    def test_refresh_from_env_var(self, mock_get, capsys):
        """Verify token refresh works when bootstrapping from env var.
        Creates a DB row on first successful refresh."""
        mock_get.return_value = _mock_response(
            json_data={"access_token": "new-token-xyz"}
        )

        call_command("refresh_instagram_token")

        captured = capsys.readouterr()
        assert "refreshed successfully" in captured.out
        assert InstagramToken.objects.count() == 1
        assert InstagramToken.objects.first().access_token == "new-token-xyz"

    @override_settings(INSTAGRAM_ACCESS_TOKEN="")
    @patch("libraries.management.commands.refresh_instagram_token.requests.get")
    def test_refresh_from_db_token(self, mock_get):
        """Verify token refresh uses the DB-stored token when available.
        Ensures continuity across multiple refresh cycles."""
        InstagramToken.objects.create(access_token="old-db-token")
        mock_get.return_value = _mock_response(
            json_data={"access_token": "refreshed-token"}
        )

        call_command("refresh_instagram_token")

        assert InstagramToken.objects.count() == 1
        assert InstagramToken.objects.first().access_token == "refreshed-token"

    @override_settings(INSTAGRAM_ACCESS_TOKEN="")
    def test_skips_when_no_token(self, capsys):
        """Verify clean exit when no Instagram token exists anywhere.
        Prevents errors in environments without Instagram configured."""
        call_command("refresh_instagram_token")

        captured = capsys.readouterr()
        assert "No Instagram token configured" in captured.out

    @override_settings(
        INSTAGRAM_ACCESS_TOKEN="env-token",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.management.commands.refresh_instagram_token.requests.get")
    def test_notifies_on_failure(self, mock_get, capsys):
        """Verify admin is notified when token refresh fails.
        Alerts before the token expires so manual action can be taken."""
        mock_get.return_value = _mock_response(status_code=400, raise_on_status=True)

        call_command("refresh_instagram_token")

        captured = capsys.readouterr()
        assert "Token refresh failed" in captured.err
        assert len(mail.outbox) == 1
        assert "refresh failed" in mail.outbox[0].subject


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
    def test_success_notification_includes_instagram(self, approved_library):
        """Verify the success notification includes the Instagram URL.
        Keeps admins informed about all platforms posted to."""
        social_post = SocialPost.objects.create(
            library=approved_library,
            post_text="Test post",
            instagram_url="https://www.instagram.com/p/abc123/",
        )

        notify_social_post(social_post)

        assert len(mail.outbox) == 1
        assert "instagram.com" in mail.outbox[0].body

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


# --- AI image analysis tests ---


class TestParseResponse:
    """Tests for parsing AI model responses into structured data."""

    def test_valid_json(self):
        """Verify valid JSON with expected keys is parsed correctly.
        The happy path for AI responses."""
        result = _parse_response('{"alt_text": "A wooden library box", "hashtags": ["wooden", "cozy"]}')
        assert result == {"alt_text": "A wooden library box", "hashtags": ["wooden", "cozy"]}

    def test_json_with_code_fences(self):
        """Verify JSON wrapped in markdown code fences is handled.
        Some models wrap output in triple backticks."""
        result = _parse_response('```json\n{"alt_text": "A library", "hashtags": ["books"]}\n```')
        assert result == {"alt_text": "A library", "hashtags": ["books"]}

    def test_strips_hash_prefix_from_hashtags(self):
        """Verify hashtags with # prefix get cleaned.
        Normalizes inconsistent model output."""
        result = _parse_response('{"alt_text": "test", "hashtags": ["#wooden", "cozy"]}')
        assert result["hashtags"] == ["wooden", "cozy"]

    def test_lowercases_hashtags(self):
        """Verify hashtags are lowercased.
        Ensures consistent hashtag formatting."""
        result = _parse_response('{"alt_text": "test", "hashtags": ["Wooden", "COZY"]}')
        assert result["hashtags"] == ["wooden", "cozy"]

    def test_invalid_json_returns_none(self):
        """Verify malformed JSON returns None gracefully.
        Prevents crashes from unexpected model output."""
        assert _parse_response("not json at all") is None

    def test_missing_keys_returns_empty(self):
        """Verify missing keys result in empty defaults.
        Handles partial model responses."""
        result = _parse_response('{"other": "value"}')
        assert result == {"alt_text": "", "hashtags": []}

    def test_wrong_types_returns_none(self):
        """Verify wrong value types return None.
        Catches type mismatches from models."""
        assert _parse_response('{"alt_text": 123, "hashtags": "not a list"}') is None


@pytest.mark.django_db
class TestAnalyzeLibraryImage:
    """Tests for the AI image analysis function."""

    @override_settings(OPENROUTER_API_KEY="")
    def test_skips_when_no_api_key(self, approved_library, tmp_path):
        """Verify analysis is skipped when no API key is configured.
        Preserves current behaviour for users without OpenRouter."""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"fake image data")
        assert analyze_library_image(image_file, approved_library) is None

    @override_settings(
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_MODEL="test/model",
    )
    @patch("openai.OpenAI")
    def test_successful_analysis(self, mock_openai_class, approved_library, tmp_path):
        """Verify successful AI analysis returns alt_text and hashtags.
        Tests the full flow with a mocked OpenAI client."""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"fake image data")

        mock_client = mock_openai_class.return_value
        mock_response = mock_client.chat.completions.create.return_value
        mock_response.choices = [
            type("Choice", (), {
                "message": type("Message", (), {
                    "content": '{"alt_text": "A cozy book nook", "hashtags": ["cozy", "reading"]}'
                })()
            })()
        ]

        result = analyze_library_image(image_file, approved_library)
        assert result == {"alt_text": "A cozy book nook", "hashtags": ["cozy", "reading"]}
        mock_openai_class.assert_called_once_with(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
        )

    @override_settings(
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_MODEL="test/model",
    )
    @patch("openai.OpenAI")
    def test_api_error_returns_none(self, mock_openai_class, approved_library, tmp_path):
        """Verify API errors are caught and return None.
        Ensures posting continues even when AI fails."""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"fake image data")

        mock_client = mock_openai_class.return_value
        mock_client.chat.completions.create.side_effect = Exception("API error")

        assert analyze_library_image(image_file, approved_library) is None


# --- Extra hashtags in text builder tests ---


@pytest.mark.django_db
class TestExtraHashtags:
    """Tests for the extra_hashtags parameter in text builders."""

    def test_extra_hashtags_appended(self, approved_library):
        """Verify AI-generated hashtags are appended to post text.
        Extends discoverability beyond base and geo tags."""
        text = build_post_text(
            approved_library, "https://example.com/lib",
            max_length=500, extra_hashtags=["cozy", "reading"],
        )
        assert "#cozy" in text
        assert "#reading" in text
        assert "#BookCorners" in text

    def test_extra_hashtags_no_duplicates(self, approved_library):
        """Verify duplicate hashtags are not added twice.
        Prevents repetitive hashtag lines."""
        text = build_post_text(
            approved_library, "https://example.com/lib",
            max_length=500, extra_hashtags=["freebooks"],
        )
        # #FreeBooks is already in BASE_HASHTAGS, #freebooks shouldn't duplicate
        assert text.count("#FreeBooks") == 1
        # The lowercase version should be added since it differs case-wise
        assert "#freebooks" in text

    def test_max_hashtags_caps_total(self, approved_library):
        """Verify max_hashtags limits the total number of hashtags.
        Instagram allows at most 5 hashtags."""
        text = build_post_text(
            approved_library, "https://example.com/lib",
            max_length=2200, max_hashtags=5,
            extra_hashtags=["cozy", "reading", "nature", "sunset"],
        )
        hashtag_count = text.count("#")
        assert hashtag_count <= 5

    def test_extra_hashtags_none_is_default(self, approved_library):
        """Verify None extra_hashtags produces same output as before.
        Maintains backwards compatibility."""
        text_default = build_post_text(
            approved_library, "https://example.com/lib", max_length=300,
        )
        text_none = build_post_text(
            approved_library, "https://example.com/lib", max_length=300,
            extra_hashtags=None,
        )
        assert text_default == text_none

    def test_bluesky_text_with_extra_hashtags(self, approved_library):
        """Verify Bluesky TextBuilder includes extra hashtag facets.
        Ensures AI tags are clickable on Bluesky."""
        builder = build_bluesky_text(
            approved_library, "https://example.com/lib",
            max_length=500, extra_hashtags=["cozy"],
        )
        text = builder.build_text()
        assert "#cozy" in text


# --- Command AI integration tests ---


@pytest.mark.django_db
class TestCommandAIIntegration:
    """Tests for AI integration in the post_random_library command."""

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="",
        BLUESKY_APP_PASSWORD="",
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
        OPENROUTER_API_KEY="test-key",
        SITE_URL="https://bookcorners.org",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.social.image_ai.analyze_library_image")
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_ai_alt_text_passed_to_mastodon(
        self, mock_photo, mock_mastodon, mock_ai, approved_library,
    ):
        """Verify AI-generated alt text is forwarded to Mastodon.
        Improves accessibility of posted images."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_ai.return_value = {"alt_text": "A cozy book nook", "hashtags": ["cozy"]}
        mock_mastodon.return_value = "https://mastodon.test/@user/123"

        call_command("post_random_library")

        mock_mastodon.assert_called_once()
        call_kwargs = mock_mastodon.call_args
        assert call_kwargs.kwargs["alt_text"] == "A cozy book nook"

    @override_settings(
        MASTODON_INSTANCE_URL="https://mastodon.test",
        MASTODON_ACCESS_TOKEN="test-token",
        BLUESKY_HANDLE="",
        BLUESKY_APP_PASSWORD="",
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
        OPENROUTER_API_KEY="",
        SITE_URL="https://bookcorners.org",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.management.commands.post_random_library.Command._post_to_mastodon")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_no_ai_key_falls_back(
        self, mock_photo, mock_mastodon, approved_library,
    ):
        """Verify posting works without OpenRouter API key.
        Preserves existing behaviour when AI is not configured."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_mastodon.return_value = "https://mastodon.test/@user/123"

        call_command("post_random_library")

        mock_mastodon.assert_called_once()
        call_kwargs = mock_mastodon.call_args
        assert call_kwargs.kwargs["alt_text"] is None

    @override_settings(
        MASTODON_INSTANCE_URL="",
        MASTODON_ACCESS_TOKEN="",
        BLUESKY_HANDLE="",
        BLUESKY_APP_PASSWORD="",
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
        OPENROUTER_API_KEY="test-key",
        SITE_URL="https://bookcorners.org",
    )
    @patch("libraries.social.image_ai.analyze_library_image")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_dry_run_shows_ai_results(
        self, mock_photo, mock_ai, approved_library, capsys,
    ):
        """Verify dry-run output includes AI analysis results.
        Allows inspecting AI-generated content before posting."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_ai.return_value = {"alt_text": "A wooden library box", "hashtags": ["wooden", "cozy"]}

        call_command("post_random_library", dry_run=True)

        captured = capsys.readouterr()
        assert "AI alt text: A wooden library box" in captured.out
        assert "wooden" in captured.out
        assert "cozy" in captured.out

    @override_settings(
        MASTODON_INSTANCE_URL="",
        MASTODON_ACCESS_TOKEN="",
        BLUESKY_HANDLE="",
        BLUESKY_APP_PASSWORD="",
        INSTAGRAM_USER_ID="",
        INSTAGRAM_ACCESS_TOKEN="",
        OPENROUTER_API_KEY="",
        SITE_URL="https://bookcorners.org",
    )
    def test_dry_run_without_ai_key(self, approved_library, capsys):
        """Verify dry-run output shows AI was skipped when unconfigured.
        Confirms fallback messaging in dry-run mode."""
        call_command("post_random_library", dry_run=True)

        captured = capsys.readouterr()
        assert "AI analysis: skipped" in captured.out

    @override_settings(
        MASTODON_INSTANCE_URL="",
        MASTODON_ACCESS_TOKEN="",
        BLUESKY_HANDLE="",
        BLUESKY_APP_PASSWORD="",
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="ig-token",
        OPENROUTER_API_KEY="test-key",
        SITE_URL="https://bookcorners.org",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ADMIN_NOTIFICATION_EMAIL="admin@test.com",
    )
    @patch("libraries.social.image_ai.analyze_library_image")
    @patch("libraries.management.commands.post_random_library.Command._post_to_instagram")
    @patch("libraries.management.commands.post_random_library.Command._get_photo_path")
    def test_instagram_uses_separate_text_with_hashtag_cap(
        self, mock_photo, mock_instagram, mock_ai, approved_library,
    ):
        """Verify Instagram gets its own text with a 5-hashtag cap.
        Respects Instagram's hashtag limit since December 2025."""
        mock_photo.return_value = Path("/tmp/test.jpg")
        mock_ai.return_value = {
            "alt_text": "A library",
            "hashtags": ["cozy", "reading", "nature", "sunset"],
        }
        mock_instagram.return_value = "https://www.instagram.com/p/abc123/"

        call_command("post_random_library")

        mock_instagram.assert_called_once()
        instagram_text = mock_instagram.call_args.args[1]
        hashtag_count = instagram_text.count("#")
        assert hashtag_count <= 5
        assert len(instagram_text) <= 2200


# --- Hashtag comment tests ---


@pytest.mark.django_db
class TestBuildHashtagComment:
    """Tests for the build_hashtag_comment function."""

    def test_brand_and_geo_tags_present(self, approved_library):
        """Verify brand and geo hashtags are always included.
        These are the core tags for every Instagram comment."""
        result = build_hashtag_comment(approved_library)
        assert "#BookCorners" in result
        assert "#FreeBooks" in result
        assert "#Paris" in result
        assert "#France" in result

    def test_respects_max_hashtags(self, approved_library):
        """Verify the comment never exceeds 30 hashtags.
        Instagram limits comments to 30 hashtags."""
        result = build_hashtag_comment(approved_library)
        tag_count = result.count("#")
        assert tag_count <= 30

    def test_includes_ai_tags(self, approved_library):
        """Verify AI-generated hashtags are included in the comment.
        Enriches discovery with visually relevant tags."""
        result = build_hashtag_comment(
            approved_library, extra_hashtags=["cozy", "wooden"],
        )
        assert "#cozy" in result
        assert "#wooden" in result

    def test_deduplicates_tags(self, approved_library):
        """Verify duplicate hashtags are removed.
        Prevents redundant tags when AI generates brand-like hashtags."""
        result = build_hashtag_comment(
            approved_library, extra_hashtags=["BookCorners", "cozy"],
        )
        assert result.count("#BookCorners") == 1

    def test_community_tags_fill_remaining_slots(self, approved_library):
        """Verify community pool tags are added after brand/geo/AI tags.
        Maximizes discovery potential up to the 30-tag limit."""
        result = build_hashtag_comment(approved_library)
        assert "#LittleFreeLibrary" in result
        assert "#Bookstagram" in result

    def test_custom_max_hashtags(self, approved_library):
        """Verify the max_hashtags parameter caps the output.
        Allows callers to set a lower limit than the default 30."""
        result = build_hashtag_comment(approved_library, max_hashtags=6)
        tag_count = result.count("#")
        assert tag_count == 6


# --- Photo description tests ---


@pytest.mark.django_db
class TestBuildPostTextPhotoDescription:
    """Tests for the photo_description parameter in build_post_text."""

    def test_photo_description_appears_in_caption(self, approved_library):
        """Verify alt text is included in the post text when provided.
        Enriches the Instagram caption with AI-generated image context."""
        result = build_post_text(
            approved_library,
            "https://bookcorners.org/library/test/",
            max_length=2200,
            photo_description="A wooden book exchange box on a sunny street corner",
        )
        assert "A wooden book exchange box on a sunny street corner" in result

    def test_photo_description_absent_when_none(self, approved_library):
        """Verify no extra text appears when photo_description is None.
        Preserves the existing caption format for non-Instagram platforms."""
        result = build_post_text(
            approved_library,
            "https://bookcorners.org/library/test/",
            max_length=2200,
            photo_description=None,
        )
        assert result.startswith("A lovely little free library")

    def test_photo_description_truncated_when_too_long(self, approved_library):
        """Verify long photo descriptions are truncated gracefully.
        Prevents the caption from exceeding the max_length limit."""
        long_desc = "A" * 500
        result = build_post_text(
            approved_library,
            "https://bookcorners.org/library/test/",
            max_length=300,
            photo_description=long_desc,
        )
        assert len(result) <= 300
        assert result.endswith("\u2026") or "#" in result


# --- Comment on media tests ---


@pytest.mark.django_db
class TestCommentOnMedia:
    """Tests for the comment_on_media Instagram client function."""

    @override_settings(
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="test-ig-token",
    )
    @patch("libraries.social.instagram.requests.post")
    def test_comment_success(self, mock_post):
        """Verify a comment is posted successfully.
        Returns the comment ID from the API response."""
        from libraries.social.instagram import comment_on_media

        mock_post.return_value = _mock_response(json_data={"id": "comment-789"})

        result = comment_on_media(media_id="media-456", text="#BookCorners #FreeBooks")
        assert result == "comment-789"

        call_kwargs = mock_post.call_args
        assert "media-456/comments" in call_kwargs.args[0]
        assert call_kwargs.kwargs["data"]["message"] == "#BookCorners #FreeBooks"

    @override_settings(
        INSTAGRAM_USER_ID="123456",
        INSTAGRAM_ACCESS_TOKEN="test-ig-token",
    )
    @patch("libraries.social.instagram.requests.post")
    def test_comment_api_error(self, mock_post):
        """Verify API errors propagate as RuntimeError.
        Allows the caller to catch and handle comment failures separately."""
        from libraries.social.instagram import comment_on_media

        mock_post.return_value = _mock_response(
            status_code=400,
            json_data={"error": {"message": "Invalid media ID"}},
        )

        with pytest.raises(RuntimeError, match="Invalid media ID"):
            comment_on_media(media_id="bad-id", text="test")


# --- Set Instagram token command tests ---


@pytest.mark.django_db
class TestSetInstagramToken:
    """Tests for the set_instagram_token management command."""

    @patch("libraries.management.commands.set_instagram_token.requests.get")
    def test_stores_token(self, mock_get, capsys):
        """Verify the command stores a validated token in the database.
        Replaces any existing tokens."""
        mock_get.return_value = _mock_response(json_data={"id": "12345"})

        call_command("set_instagram_token", "new-token-abc")

        assert InstagramToken.objects.count() == 1
        assert InstagramToken.objects.first().access_token == "new-token-abc"
        captured = capsys.readouterr()
        assert "stored successfully" in captured.out

    @patch("libraries.management.commands.set_instagram_token.requests.get")
    def test_replaces_existing_token(self, mock_get):
        """Verify existing tokens are deleted when setting a new one.
        Ensures only one active token exists at a time."""
        InstagramToken.objects.create(access_token="old-token")
        mock_get.return_value = _mock_response(json_data={"id": "12345"})

        call_command("set_instagram_token", "new-token-abc")

        assert InstagramToken.objects.count() == 1
        assert InstagramToken.objects.first().access_token == "new-token-abc"

    @patch("libraries.management.commands.set_instagram_token.requests.get")
    def test_validates_token(self, mock_get):
        """Verify the command calls the Graph API to validate the token.
        Prevents storing invalid tokens."""
        mock_get.return_value = _mock_response(json_data={"id": "12345"})

        call_command("set_instagram_token", "test-token")

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args.kwargs["params"]["access_token"] == "test-token"

    def test_skip_validation(self):
        """Verify --skip-validation stores the token without API call.
        Useful for offline or testing scenarios."""
        call_command("set_instagram_token", "offline-token", skip_validation=True)

        assert InstagramToken.objects.count() == 1
        assert InstagramToken.objects.first().access_token == "offline-token"

    @patch("libraries.management.commands.set_instagram_token.requests.get")
    def test_validation_failure_raises(self, mock_get):
        """Verify invalid tokens are rejected and not stored.
        Protects against storing expired or malformed tokens."""
        mock_get.return_value = _mock_response(
            status_code=400,
            json_data={"error": {"message": "Invalid token"}},
        )

        with pytest.raises(Exception, match="Token validation failed"):
            call_command("set_instagram_token", "bad-token")

        assert InstagramToken.objects.count() == 0


# --- Test helpers ---


class _MockResponse:
    """Minimal mock for requests.Response objects."""

    def __init__(self, *, json_data=None, status_code=200, should_raise=False):
        """Initialize with response data and status code.
        Optionally configured to raise on raise_for_status."""
        self._json_data = json_data or {}
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = str(json_data) if json_data else ""
        self.url = "https://graph.instagram.com/mock"
        self._should_raise = should_raise

    def json(self):
        """Return the mock JSON body."""
        return self._json_data

    def raise_for_status(self):
        """Raise an HTTPError if configured to do so.
        Simulates failed API responses."""
        if self._should_raise:
            import requests

            raise requests.HTTPError(
                response=self, request=None,
            )


def _mock_response(*, json_data=None, status_code=200, raise_on_status=False):
    """Create a mock requests.Response for testing API calls.
    Supports both success and error scenarios."""
    return _MockResponse(
        json_data=json_data,
        status_code=status_code,
        should_raise=raise_on_status,
    )
