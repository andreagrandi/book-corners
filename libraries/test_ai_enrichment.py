"""Tests for AI-powered library enrichment on submission."""

from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from libraries.models import Library
from libraries.social.image_ai import (
    _parse_enrichment_response,
    enrich_library_from_image,
)
from libraries.tasks import enrich_library_with_ai

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
def enrichment_user(db):
    """Create a test user for enrichment tests.
    Provides a minimal user record for library ownership."""
    return User.objects.create_user(
        username="enrichuser",
        password="testpass123",
        email="enrichuser@example.com",
    )


@pytest.fixture
def pending_library(enrichment_user):
    """Create a pending library with a photo but no name or description.
    Represents the typical submission needing AI enrichment."""
    return Library.objects.create(
        name="",
        description="",
        address="Via Roma 1",
        city="Florence",
        country="IT",
        location=Point(x=11.255, y=43.77, srid=4326),
        photo=_build_uploaded_photo(),
        status=Library.Status.PENDING,
        created_by=enrichment_user,
    )


@pytest.fixture
def pending_library_with_name(enrichment_user):
    """Create a pending library with user-provided name and description.
    Tests that AI does not overwrite user-provided values."""
    return Library.objects.create(
        name="My Library",
        description="A lovely library I found.",
        address="Via Roma 2",
        city="Florence",
        country="IT",
        location=Point(x=11.256, y=43.77, srid=4326),
        photo=_build_uploaded_photo(),
        status=Library.Status.PENDING,
        created_by=enrichment_user,
    )


# --- AI enrichment function tests ---


@pytest.mark.django_db
class TestEnrichLibraryFromImage:
    """Tests for the enrich_library_from_image function."""

    @override_settings(OPENROUTER_API_KEY="")
    def test_skips_when_no_api_key(self, pending_library, tmp_path):
        """Verify enrichment is skipped when no API key is configured.
        Returns None without making any API calls."""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"fake image data")
        assert enrich_library_from_image(image_file, pending_library) is None

    @override_settings(
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_MODEL="test/model",
    )
    @patch("openai.OpenAI")
    def test_successful_enrichment(self, mock_openai_class, pending_library, tmp_path):
        """Verify successful enrichment returns name and description.
        Tests the full flow with a mocked OpenAI client."""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"fake image data")

        mock_client = mock_openai_class.return_value
        mock_response = mock_client.chat.completions.create.return_value
        mock_response.choices = [
            type("Choice", (), {
                "message": type("Message", (), {
                    "content": '{"name": "Corner Book Nook", "description": "A charming blue wooden box."}'
                })()
            })()
        ]

        result = enrich_library_from_image(image_file, pending_library)
        assert result == {
            "name": "Corner Book Nook",
            "description": "A charming blue wooden box.",
        }

    @override_settings(
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_MODEL="test/model",
    )
    @patch("openai.OpenAI")
    def test_api_error_returns_none(self, mock_openai_class, pending_library, tmp_path):
        """Verify API errors are caught and return None.
        Ensures enrichment failures are graceful."""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"fake image data")

        mock_client = mock_openai_class.return_value
        mock_client.chat.completions.create.side_effect = Exception("API error")

        assert enrich_library_from_image(image_file, pending_library) is None


class TestParseEnrichmentResponse:
    """Tests for the _parse_enrichment_response parser."""

    def test_valid_json(self):
        """Verify valid JSON is parsed correctly.
        Returns dict with name and description."""
        result = _parse_enrichment_response(
            '{"name": "My Library", "description": "A nice spot."}'
        )
        assert result == {"name": "My Library", "description": "A nice spot."}

    def test_code_fenced_json(self):
        """Verify markdown code fences are stripped.
        Handles common LLM response wrapping."""
        result = _parse_enrichment_response(
            '```json\n{"name": "Fenced", "description": "Desc"}\n```'
        )
        assert result == {"name": "Fenced", "description": "Desc"}

    def test_invalid_json_returns_none(self):
        """Verify malformed JSON returns None.
        Prevents crashes from unexpected AI output."""
        assert _parse_enrichment_response("not json") is None

    def test_truncates_long_name(self):
        """Verify name is truncated to 255 characters.
        Respects model field max_length."""
        long_name = "A" * 300
        result = _parse_enrichment_response(
            f'{{"name": "{long_name}", "description": "Short"}}'
        )
        assert result is not None
        assert len(result["name"]) == 255

    def test_truncates_long_description(self):
        """Verify description is truncated to 2000 characters.
        Respects form validation limit."""
        long_desc = "B" * 2500
        result = _parse_enrichment_response(
            f'{{"name": "Short", "description": "{long_desc}"}}'
        )
        assert result is not None
        assert len(result["description"]) == 2000

    def test_wrong_types_returns_none(self):
        """Verify non-string values return None.
        Guards against unexpected AI response shapes."""
        assert _parse_enrichment_response('{"name": 123, "description": "ok"}') is None


# --- Background task tests ---


@pytest.mark.django_db
class TestEnrichLibraryWithAiTask:
    """Tests for the enrich_library_with_ai background task."""

    @override_settings(
        OPENROUTER_API_KEY="test-key",
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
    )
    @patch("libraries.social.image_ai.enrich_library_from_image")
    @patch("libraries.tasks.get_library_photo_path")
    def test_fills_empty_name_and_description(
        self, mock_photo_path, mock_enrich, pending_library, tmp_path,
    ):
        """Verify AI fills blank name and description.
        The core enrichment case for user submissions without metadata."""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"fake")
        mock_photo_path.return_value = image_file
        mock_enrich.return_value = {
            "name": "AI Library Name",
            "description": "AI-generated description.",
        }

        enrich_library_with_ai.enqueue(library_id=pending_library.pk)

        pending_library.refresh_from_db()
        assert pending_library.name == "AI Library Name"
        assert pending_library.description == "AI-generated description."
        assert len(mail.outbox) == 1

    @override_settings(
        OPENROUTER_API_KEY="test-key",
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
    )
    @patch("libraries.social.image_ai.enrich_library_from_image")
    @patch("libraries.tasks.get_library_photo_path")
    def test_preserves_user_provided_values(
        self, mock_photo_path, mock_enrich, pending_library_with_name, tmp_path,
    ):
        """Verify AI does not overwrite user-provided name and description.
        Respects the user's explicit input."""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"fake")
        mock_photo_path.return_value = image_file
        mock_enrich.return_value = {
            "name": "AI Library Name",
            "description": "AI-generated description.",
        }

        enrich_library_with_ai.enqueue(library_id=pending_library_with_name.pk)

        pending_library_with_name.refresh_from_db()
        assert pending_library_with_name.name == "My Library"
        assert pending_library_with_name.description == "A lovely library I found."
        assert len(mail.outbox) == 1

    @override_settings(
        OPENROUTER_API_KEY="test-key",
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
    )
    @patch("libraries.social.image_ai.enrich_library_from_image")
    @patch("libraries.tasks.get_library_photo_path")
    def test_sends_notification_on_ai_failure(
        self, mock_photo_path, mock_enrich, pending_library, tmp_path,
    ):
        """Verify admin notification is sent even when AI fails.
        The notification must never be lost due to AI errors."""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"fake")
        mock_photo_path.return_value = image_file
        mock_enrich.return_value = None

        enrich_library_with_ai.enqueue(library_id=pending_library.pk)

        pending_library.refresh_from_db()
        assert pending_library.name == ""
        assert len(mail.outbox) == 1

    @override_settings(
        OPENROUTER_API_KEY="",
        ADMIN_NOTIFICATION_EMAIL="admin@example.com",
    )
    def test_skips_ai_when_no_api_key(self, pending_library):
        """Verify notification is sent without AI when no key is configured.
        Graceful degradation when OpenRouter is not set up."""
        enrich_library_with_ai.enqueue(library_id=pending_library.pk)

        assert len(mail.outbox) == 1
        pending_library.refresh_from_db()
        assert pending_library.name == ""

    @override_settings(ADMIN_NOTIFICATION_EMAIL="admin@example.com")
    def test_handles_missing_library(self):
        """Verify task handles a deleted library gracefully.
        Does not crash when the library no longer exists."""
        enrich_library_with_ai.enqueue(library_id=99999)
        assert len(mail.outbox) == 0


# --- Submission flow tests ---


@pytest.mark.django_db
class TestSubmissionEnqueuesEnrichment:
    """Tests that library submission enqueues the AI enrichment task."""

    @patch("libraries.views.enrich_library_with_ai")
    def test_web_submit_enqueues_task(self, mock_task, client, enrichment_user):
        """Verify web form submission enqueues the enrichment task.
        The task replaces direct notification for AI enrichment."""
        client.force_login(enrichment_user)
        photo = _build_uploaded_photo()

        response = client.post(
            reverse("submit_library"),
            data={
                "name": "",
                "description": "",
                "address": "Via Dante 5",
                "city": "Milan",
                "country": "IT",
                "postal_code": "",
                "latitude": "45.46",
                "longitude": "9.19",
                "photo": photo,
            },
        )

        assert response.status_code == 302
        mock_task.enqueue.assert_called_once()
        call_kwargs = mock_task.enqueue.call_args.kwargs
        assert "library_id" in call_kwargs

    @patch("libraries.views.enrich_library_with_ai")
    @patch("libraries.views.notify_new_library")
    def test_web_submit_falls_back_on_enqueue_failure(
        self, mock_notify, mock_task, client, enrichment_user,
    ):
        """Verify direct notification when task enqueue fails.
        Ensures admin always gets notified even if task backend is down."""
        client.force_login(enrichment_user)
        mock_task.enqueue.side_effect = Exception("Backend down")

        response = client.post(
            reverse("submit_library"),
            data={
                "name": "",
                "description": "",
                "address": "Via Dante 6",
                "city": "Milan",
                "country": "IT",
                "postal_code": "",
                "latitude": "45.46",
                "longitude": "9.19",
                "photo": _build_uploaded_photo(),
            },
        )

        assert response.status_code == 302
        mock_notify.assert_called_once()


# --- Admin AI Enrich button tests ---


@pytest.mark.django_db
class TestAdminAIEnrich:
    """Tests for the admin AI Enrich button and confirmation flow."""

    @override_settings(OPENROUTER_API_KEY="test-key")
    @patch("libraries.social.image_ai.enrich_library_from_image")
    @patch("libraries.storage.get_library_photo_path")
    def test_generate_shows_confirmation(
        self, mock_photo_path, mock_enrich, admin_client, pending_library, tmp_path,
    ):
        """Verify AI Enrich POST renders the confirmation page.
        Shows current vs AI-suggested values for admin review."""
        image_file = tmp_path / "test.jpg"
        image_file.write_bytes(b"fake")
        mock_photo_path.return_value = image_file
        mock_enrich.return_value = {
            "name": "AI Name",
            "description": "AI Description",
        }

        url = reverse("admin:libraries_library_ai_enrich", args=[pending_library.pk])
        response = admin_client.post(url)

        assert response.status_code == 200
        assert b"AI Name" in response.content
        assert b"AI Description" in response.content

    def test_apply_saves_fields(self, admin_client, pending_library):
        """Verify Apply POST writes AI values to the library.
        Completes the two-step enrichment confirmation flow."""
        url = reverse("admin:libraries_library_ai_enrich_apply", args=[pending_library.pk])
        response = admin_client.post(url, data={
            "ai_name": "Applied Name",
            "ai_description": "Applied Description",
        })

        assert response.status_code == 302
        pending_library.refresh_from_db()
        assert pending_library.name == "Applied Name"
        assert pending_library.description == "Applied Description"

    def test_cancel_does_not_save(self, admin_client, pending_library):
        """Verify GET to apply URL redirects without saving.
        Cancel link is a simple redirect, not a POST."""
        url = reverse("admin:libraries_library_ai_enrich_apply", args=[pending_library.pk])
        response = admin_client.get(url)

        assert response.status_code == 302
        pending_library.refresh_from_db()
        assert pending_library.name == ""

    def test_no_photo_shows_error(self, admin_client, enrichment_user):
        """Verify error message when library has no photo.
        The AI Enrich button should handle missing photos gracefully."""
        library = Library.objects.create(
            name="",
            address="Via Test 1",
            city="Rome",
            country="IT",
            location=Point(x=12.49, y=41.89, srid=4326),
            status=Library.Status.PENDING,
            created_by=enrichment_user,
        )

        url = reverse("admin:libraries_library_ai_enrich", args=[library.pk])
        response = admin_client.post(url, follow=True)

        assert response.status_code == 200
        messages_list = list(response.context["messages"])
        assert any("no photo" in str(m).lower() for m in messages_list)
