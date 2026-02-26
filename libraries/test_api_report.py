import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from ninja_jwt.tokens import RefreshToken

from libraries.models import Library, Report
from libraries.tests import _build_uploaded_photo

User = get_user_model()


@pytest.fixture
def user_jwt(user):
    """Generate a valid JWT access token for the default test user.
    Provides bearer credentials for authenticated API requests."""
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


@pytest.fixture
def approved_library(user):
    """Create an approved library for report tests.
    Only approved libraries can be reported against."""
    return Library.objects.create(
        name="Approved Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city="Florence",
        country="IT",
        status=Library.Status.APPROVED,
        created_by=user,
    )


def _report_payload(**overrides):
    """Build a default valid report payload dictionary.
    Merges caller overrides on top of sensible defaults."""
    defaults = {
        "reason": "damaged",
        "details": "The box is broken",
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.django_db
class TestReportEndpoint:
    """Tests for POST /api/v1/libraries/{slug}/report endpoint."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def _url(self, slug):
        """Build the report endpoint URL for a given library slug.
        Centralises path construction for all test methods."""
        return f"/api/v1/libraries/{slug}/report"

    def test_requires_authentication(self, client, approved_library):
        """Verify the endpoint rejects unauthenticated requests with 401.
        JWT is mandatory for submitting reports."""
        response = client.post(
            self._url(slug=approved_library.slug),
            data=_report_payload(),
        )

        assert response.status_code == 401

    def test_valid_report_creates_open_report(self, client, user_jwt, approved_library):
        """Verify a valid report is created with open status.
        New reports start as open and await moderation."""
        response = client.post(
            self._url(slug=approved_library.slug),
            data=_report_payload(),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 201
        report = Report.objects.get(id=response.json()["id"])
        assert report.status == Report.Status.OPEN

    def test_response_contains_all_report_out_fields(self, client, user_jwt, approved_library):
        """Verify the response includes every field defined in ReportOut.
        Guards against accidental field omissions in the output schema."""
        response = client.post(
            self._url(slug=approved_library.slug),
            data=_report_payload(),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        body = response.json()
        expected_fields = {"id", "reason", "created_at"}
        assert set(body.keys()) == expected_fields

    def test_report_linked_to_correct_library_and_user(
        self, client, user, user_jwt, approved_library,
    ):
        """Verify the report is linked to the correct library and user.
        Prevents reports from being misattributed."""
        response = client.post(
            self._url(slug=approved_library.slug),
            data=_report_payload(),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        report = Report.objects.get(id=response.json()["id"])
        assert report.library == approved_library
        assert report.created_by == user

    def test_404_for_nonexistent_library(self, client, user_jwt):
        """Verify a nonexistent slug returns 404.
        Reports can only target libraries that exist."""
        response = client.post(
            self._url(slug="does-not-exist"),
            data=_report_payload(),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 404

    def test_404_for_pending_library(self, client, user_jwt, user):
        """Verify a pending library cannot be reported against.
        Only approved libraries are eligible for user reports."""
        pending_library = Library.objects.create(
            name="Pending Library",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Dante 1",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=user,
        )

        response = client.post(
            self._url(slug=pending_library.slug),
            data=_report_payload(),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 404

    def test_invalid_reason_returns_422(self, client, user_jwt, approved_library):
        """Verify an invalid reason value is rejected with 422.
        Schema validation catches unknown reason values before the handler."""
        response = client.post(
            self._url(slug=approved_library.slug),
            data=_report_payload(reason="invalid_reason"),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 422

    def test_photo_optional(self, client, user_jwt, approved_library):
        """Verify a report without a photo succeeds.
        Photo is optional for library reports."""
        response = client.post(
            self._url(slug=approved_library.slug),
            data=_report_payload(),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 201

    def test_report_with_photo(self, client, user_jwt, approved_library, tmp_path, settings):
        """Verify a report with a valid photo succeeds and stores the file.
        Photos provide visual evidence to support reports."""
        settings.MEDIA_ROOT = tmp_path
        photo = _build_uploaded_photo()

        response = client.post(
            self._url(slug=approved_library.slug),
            data={**_report_payload(), "photo": photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 201
        report = Report.objects.get(id=response.json()["id"])
        assert report.photo

    def test_invalid_photo_format_returns_400(self, client, user_jwt, approved_library):
        """Verify a non-image file is rejected with 400.
        Only valid image formats are accepted for report photos."""
        fake_file = SimpleUploadedFile(
            name="malware.txt",
            content=b"this is not an image",
            content_type="text/plain",
        )

        response = client.post(
            self._url(slug=approved_library.slug),
            data={**_report_payload(), "photo": fake_file},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 400
        body = response.json()
        assert "valid image" in body["message"].lower()

    @override_settings(MAX_REPORT_PHOTO_UPLOAD_BYTES=1024)
    def test_oversized_photo_returns_413(self, client, user_jwt, approved_library):
        """Verify an oversized photo is rejected with 413.
        Files exceeding the configured limit are blocked before storage."""
        large_photo = _build_uploaded_photo(width=1200, height=1200, quality=100)

        response = client.post(
            self._url(slug=approved_library.slug),
            data={**_report_payload(), "photo": large_photo},
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 413
        body = response.json()
        assert "at most" in body["message"].lower() or "smaller" in body["message"].lower()

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_WRITE_REQUESTS=1,
    )
    def test_rate_limit_returns_429(self, client, user_jwt, approved_library):
        """Verify the report endpoint returns 429 when rate limited.
        Write endpoints have a stricter rate limit than read endpoints."""
        client.post(
            self._url(slug=approved_library.slug),
            data=_report_payload(),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        response = client.post(
            self._url(slug=approved_library.slug),
            data=_report_payload(),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 429
        body = response.json()
        assert "Too many requests" in body["message"]
