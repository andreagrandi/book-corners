import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from ninja_jwt.tokens import RefreshToken

from libraries.models import Library
from libraries.tests import _build_uploaded_photo

User = get_user_model()


@pytest.fixture
def user_jwt(user):
    """Generate a valid JWT access token for the default test user.
    Provides bearer credentials for authenticated API requests."""
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


def _submit_payload(**overrides):
    """Build a default valid submission payload dictionary.
    Merges caller overrides on top of sensible defaults."""
    defaults = {
        "name": "My Little Library",
        "description": "A cozy corner for books.",
        "address": "10 Rue de la Paix",
        "city": "Paris",
        "country": "FR",
        "postal_code": "75002",
        "latitude": "48.8698",
        "longitude": "2.3311",
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.django_db
class TestSubmitLibraryEndpoint:
    """Tests for POST /api/v1/libraries/ submit endpoint."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_requires_authentication(self, client):
        """Verify the endpoint rejects unauthenticated requests with 401.
        JWT is mandatory for library submissions."""
        photo = _build_uploaded_photo()
        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(), "photo": photo},
        )

        assert response.status_code == 401

    def test_valid_submission_creates_pending_library(self, client, user_jwt, tmp_path, settings):
        """Verify a valid submission creates a library with pending status.
        New submissions require moderation before becoming public."""
        settings.MEDIA_ROOT = tmp_path
        photo = _build_uploaded_photo()

        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(), "photo": photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 201
        body = response.json()
        library = Library.objects.get(id=body["id"])
        assert library.status == Library.Status.PENDING

    def test_response_contains_all_library_out_fields(self, client, user_jwt, tmp_path, settings):
        """Verify the response includes every field defined in LibraryOut.
        Guards against accidental field omissions in the output schema."""
        settings.MEDIA_ROOT = tmp_path
        photo = _build_uploaded_photo()

        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(), "photo": photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        body = response.json()
        expected_fields = {
            "id", "slug", "name", "description", "photo_url", "thumbnail_url",
            "lat", "lng", "address", "city", "country", "postal_code", "created_at",
        }
        assert set(body.keys()) == expected_fields

    def test_coordinates_stored_correctly(self, client, user_jwt, tmp_path, settings):
        """Verify latitude and longitude are stored and returned accurately.
        Confirms the Point(x=lng, y=lat) mapping is correct."""
        settings.MEDIA_ROOT = tmp_path
        photo = _build_uploaded_photo()

        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(latitude="48.8698", longitude="2.3311"), "photo": photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        body = response.json()
        assert body["lat"] == pytest.approx(48.8698, abs=1e-4)
        assert body["lng"] == pytest.approx(2.3311, abs=1e-4)

    def test_slug_auto_generated(self, client, user_jwt, tmp_path, settings):
        """Verify a non-empty slug is auto-generated on creation.
        Slugs are derived from city, address, and name by the model."""
        settings.MEDIA_ROOT = tmp_path
        photo = _build_uploaded_photo()

        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(), "photo": photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        body = response.json()
        assert body["slug"]
        assert len(body["slug"]) > 0

    def test_created_by_set_from_jwt_user(self, client, user, user_jwt, tmp_path, settings):
        """Verify the library is owned by the authenticated JWT user.
        Prevents submissions from being attributed to the wrong account."""
        settings.MEDIA_ROOT = tmp_path
        photo = _build_uploaded_photo()

        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(), "photo": photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        body = response.json()
        library = Library.objects.get(id=body["id"])
        assert library.created_by == user

    def test_photo_required(self, client, user_jwt):
        """Verify the endpoint rejects submissions without a photo.
        Photo is a mandatory field for library submissions."""
        response = client.post(
            "/api/v1/libraries/",
            data=_submit_payload(),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 422

    def test_invalid_photo_format_returns_400(self, client, user_jwt):
        """Verify a non-image file is rejected with 400.
        Only JPEG, PNG, and WEBP formats are accepted."""
        fake_file = SimpleUploadedFile(
            name="malware.txt",
            content=b"this is not an image",
            content_type="text/plain",
        )

        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(), "photo": fake_file},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 400
        body = response.json()
        assert "valid image" in body["message"].lower()

    @override_settings(MAX_LIBRARY_PHOTO_UPLOAD_BYTES=1024)
    def test_oversized_photo_returns_413(self, client, user_jwt):
        """Verify an oversized photo is rejected with 413.
        Files exceeding the configured limit are blocked before storage."""
        large_photo = _build_uploaded_photo(width=1200, height=1200, quality=100)

        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(), "photo": large_photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 413
        body = response.json()
        assert "at most" in body["message"].lower() or "smaller" in body["message"].lower()

    def test_latitude_out_of_range_returns_422(self, client, user_jwt, tmp_path, settings):
        """Verify latitude outside [-90, 90] is rejected with 422.
        Schema validation catches invalid coordinates before the handler."""
        settings.MEDIA_ROOT = tmp_path
        photo = _build_uploaded_photo()

        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(latitude="91"), "photo": photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 422

    def test_longitude_out_of_range_returns_422(self, client, user_jwt, tmp_path, settings):
        """Verify longitude outside [-180, 180] is rejected with 422.
        Schema validation catches invalid coordinates before the handler."""
        settings.MEDIA_ROOT = tmp_path
        photo = _build_uploaded_photo()

        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(longitude="181"), "photo": photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 422

    def test_missing_required_field_returns_422(self, client, user_jwt, tmp_path, settings):
        """Verify omitting a required field returns 422.
        Address is mandatory and schema validation enforces it."""
        settings.MEDIA_ROOT = tmp_path
        photo = _build_uploaded_photo()
        payload = _submit_payload()
        del payload["address"]

        response = client.post(
            "/api/v1/libraries/",
            data={**payload, "photo": photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 422

    def test_optional_fields_have_defaults(self, client, user_jwt, tmp_path, settings):
        """Verify submission succeeds with only required fields.
        Optional fields like name and description default to empty strings."""
        settings.MEDIA_ROOT = tmp_path
        photo = _build_uploaded_photo()
        payload = {
            "address": "5 Rue Mouffetard",
            "city": "Paris",
            "country": "FR",
            "latitude": "48.8462",
            "longitude": "2.3497",
        }

        response = client.post(
            "/api/v1/libraries/",
            data={**payload, "photo": photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == ""
        assert body["description"] == ""
        assert body["postal_code"] == ""

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_WRITE_REQUESTS=1,
    )
    def test_rate_limit_returns_429(self, client, user_jwt, tmp_path, settings):
        """Verify the submit endpoint returns 429 when rate limited.
        Write endpoints have a stricter rate limit than read endpoints."""
        settings.MEDIA_ROOT = tmp_path

        client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(), "photo": _build_uploaded_photo()},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        response = client.post(
            "/api/v1/libraries/",
            data={**_submit_payload(), "photo": _build_uploaded_photo()},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 429
        body = response.json()
        assert "Too many requests" in body["message"]
