import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from ninja_jwt.tokens import RefreshToken

from libraries.models import Library
from libraries.tests import _build_uploaded_photo

User = get_user_model()


@pytest.fixture
def approved_library(user):
    """Create an approved library for list and detail tests.
    Provides a baseline approved entry with known coordinates."""
    return Library.objects.create(
        name="Approved Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=2.3522, y=48.8566, srid=4326),
        address="1 Rue de Rivoli",
        city="Paris",
        country="FR",
        postal_code="75001",
        status=Library.Status.APPROVED,
        created_by=user,
    )


@pytest.fixture
def pending_library(user):
    """Create a pending library owned by the default test user.
    Used to verify owner-only visibility for unapproved entries."""
    return Library.objects.create(
        name="Pending Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city="Florence",
        country="IT",
        status=Library.Status.PENDING,
        created_by=user,
    )


@pytest.fixture
def rejected_library(user):
    """Create a rejected library for negative visibility tests.
    Rejected entries should never appear in list or detail responses."""
    return Library.objects.create(
        name="Rejected Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=13.405, y=52.52, srid=4326),
        address="Unter den Linden 1",
        city="Berlin",
        country="DE",
        status=Library.Status.REJECTED,
        created_by=user,
    )


@pytest.fixture
def other_user(db):
    """Create a second user distinct from the default test user.
    Supports cross-user visibility tests for pending libraries."""
    return User.objects.create_user(
        username="otheruser",
        password="otherpass123",
    )


@pytest.fixture
def user_jwt(user):
    """Generate a valid JWT access token for the default test user.
    Provides bearer credentials for authenticated API requests."""
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


@pytest.fixture
def other_user_jwt(other_user):
    """Generate a valid JWT access token for the other user.
    Used to test that non-owners cannot see pending libraries."""
    refresh = RefreshToken.for_user(other_user)
    return str(refresh.access_token)


def _patch_multipart(client, url, data, token):
    """Send a multipart PATCH request through Django's test client.
    Encodes fields and files the same way browser clients submit forms."""
    return client.patch(
        url,
        data=encode_multipart(BOUNDARY, data),
        content_type=MULTIPART_CONTENT,
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


@pytest.mark.django_db
class TestLibraryListEndpoint:
    """Tests for GET /api/v1/libraries/ list endpoint."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_returns_approved_libraries(self, client, approved_library):
        """Verify the list includes approved libraries.
        Confirms the baseline happy path returns items."""
        response = client.get("/api/v1/libraries/")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["slug"] == approved_library.slug

    def test_excludes_pending_and_rejected(self, client, pending_library, rejected_library):
        """Verify pending and rejected libraries are excluded from the list.
        Public consumers should only see approved entries."""
        response = client.get("/api/v1/libraries/")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 0

    def test_empty_response(self, client, db):
        """Verify the list returns an empty result when no libraries exist.
        Confirms the endpoint handles zero-row queries gracefully."""
        response = client.get("/api/v1/libraries/")

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["pagination"]["total"] == 0

    def test_default_pagination(self, client, approved_library):
        """Verify default pagination metadata is page 1 with size 20.
        Confirms the endpoint applies correct defaults without query params."""
        response = client.get("/api/v1/libraries/")

        body = response.json()
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["page_size"] == 20

    def test_custom_page_and_page_size(self, client, user):
        """Verify custom page and page_size parameters are respected.
        Confirms pagination controls affect which items are returned."""
        for i in range(5):
            Library.objects.create(
                name=f"Library {i}",
                photo="libraries/photos/2026/02/test.jpg",
                location=Point(x=2.0 + i * 0.01, y=48.0, srid=4326),
                address=f"{i} Rue Test",
                city="Paris",
                country="FR",
                status=Library.Status.APPROVED,
                created_by=user,
            )

        response = client.get("/api/v1/libraries/?page=2&page_size=2")

        body = response.json()
        assert len(body["items"]) == 2
        assert body["pagination"]["page"] == 2
        assert body["pagination"]["page_size"] == 2
        assert body["pagination"]["total"] == 5

    def test_page_size_above_50_returns_422(self, client, approved_library):
        """Verify page_size exceeding 50 is rejected with 422.
        Ninja validates the schema upper bound before reaching the handler."""
        response = client.get("/api/v1/libraries/?page_size=100")

        assert response.status_code == 422

    def test_ordered_newest_first(self, client, user):
        """Verify libraries are ordered by created_at descending.
        Confirms the list shows the most recent entries first."""
        lib_a = Library.objects.create(
            name="Older",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=2.0, y=48.0, srid=4326),
            address="1 Rue A",
            city="Paris",
            country="FR",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        lib_b = Library.objects.create(
            name="Newer",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=3.0, y=49.0, srid=4326),
            address="2 Rue B",
            city="Lyon",
            country="FR",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get("/api/v1/libraries/")

        body = response.json()
        slugs = [item["slug"] for item in body["items"]]
        assert slugs[0] == lib_b.slug
        assert slugs[1] == lib_a.slug

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_READ_REQUESTS=2,
    )
    def test_rate_limit_returns_429(self, client, approved_library):
        """Verify the list endpoint returns 429 when rate limited.
        Confirms excessive requests are throttled with a retry hint."""
        for _ in range(3):
            client.get("/api/v1/libraries/")

        response = client.get("/api/v1/libraries/")

        assert response.status_code == 429
        body = response.json()
        assert "Too many requests" in body["message"]


@pytest.mark.django_db
class TestLibraryDetailEndpoint:
    """Tests for GET /api/v1/libraries/{slug} detail endpoint."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_returns_approved_library_by_slug(self, client, approved_library):
        """Verify an approved library is returned by its slug.
        Confirms the happy path for public detail lookups."""
        response = client.get(f"/api/v1/libraries/{approved_library.slug}")

        assert response.status_code == 200
        body = response.json()
        assert body["slug"] == approved_library.slug
        assert body["name"] == "Approved Library"

    def test_404_for_nonexistent_slug(self, client, db):
        """Verify a nonexistent slug returns 404.
        Confirms the global not-found handler catches missing libraries."""
        response = client.get("/api/v1/libraries/does-not-exist")

        assert response.status_code == 404
        body = response.json()
        assert body["message"] == "Not found."

    def test_404_for_pending_without_auth(self, client, pending_library):
        """Verify a pending library is not visible without authentication.
        Unauthenticated users should not discover unapproved entries."""
        response = client.get(f"/api/v1/libraries/{pending_library.slug}")

        assert response.status_code == 404

    def test_404_for_rejected(self, client, rejected_library, user_jwt):
        """Verify a rejected library returns 404 even with owner JWT.
        Rejected entries should never be accessible via the API."""
        response = client.get(
            f"/api/v1/libraries/{rejected_library.slug}",
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 404

    def test_pending_visible_to_owner_with_jwt(self, client, pending_library, user_jwt):
        """Verify a pending library is visible to its owner via JWT.
        Allows creators to preview their submissions before approval."""
        response = client.get(
            f"/api/v1/libraries/{pending_library.slug}",
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["slug"] == pending_library.slug

    def test_404_for_pending_with_other_user_jwt(self, client, pending_library, other_user_jwt):
        """Verify a pending library is not visible to a different user.
        Non-owners should not be able to view unapproved entries."""
        response = client.get(
            f"/api/v1/libraries/{pending_library.slug}",
            HTTP_AUTHORIZATION=f"Bearer {other_user_jwt}",
        )

        assert response.status_code == 404

    def test_invalid_jwt_treated_as_no_auth_approved_visible(self, client, approved_library):
        """Verify an invalid JWT does not prevent viewing approved libraries.
        Bad tokens should degrade to anonymous access, not cause errors."""
        response = client.get(
            f"/api/v1/libraries/{approved_library.slug}",
            HTTP_AUTHORIZATION="Bearer invalid.token.here",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["slug"] == approved_library.slug

    def test_invalid_jwt_treated_as_no_auth_pending_hidden(self, client, pending_library):
        """Verify an invalid JWT does not grant access to pending libraries.
        Bad tokens should not bypass the owner visibility check."""
        response = client.get(
            f"/api/v1/libraries/{pending_library.slug}",
            HTTP_AUTHORIZATION="Bearer invalid.token.here",
        )

        assert response.status_code == 404

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_READ_REQUESTS=2,
    )
    def test_rate_limit_returns_429(self, client, approved_library):
        """Verify the detail endpoint returns 429 when rate limited.
        Confirms excessive requests are throttled with a retry hint."""
        for _ in range(3):
            client.get(f"/api/v1/libraries/{approved_library.slug}")

        response = client.get(f"/api/v1/libraries/{approved_library.slug}")

        assert response.status_code == 429
        body = response.json()
        assert "Too many requests" in body["message"]


@pytest.mark.django_db
class TestLibraryDetailResponseShape:
    """Tests for the response shape of GET /api/v1/libraries/{slug}."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_all_expected_fields_present(self, client, approved_library):
        """Verify the detail response contains all expected fields.
        Guards against accidental field removal from the schema."""
        response = client.get(f"/api/v1/libraries/{approved_library.slug}")

        body = response.json()
        expected_fields = {
            "id", "slug", "name", "description", "photo_url", "thumbnail_url",
            "lat", "lng", "address", "city", "country", "postal_code",
            "wheelchair_accessible", "capacity", "is_indoor", "is_lit",
            "website", "contact", "source", "operator", "brand",
            "created_at", "is_favourited",
        }
        assert set(body.keys()) == expected_fields

    def test_coordinates_resolved_correctly(self, client, approved_library):
        """Verify lat and lng are correctly extracted from the PostGIS point.
        Confirms lat=Y and lng=X mapping from the geometry field."""
        response = client.get(f"/api/v1/libraries/{approved_library.slug}")

        body = response.json()
        assert body["lat"] == pytest.approx(48.8566, abs=1e-4)
        assert body["lng"] == pytest.approx(2.3522, abs=1e-4)


@pytest.mark.django_db
class TestLibraryUpdateEndpoint:
    """Tests for PATCH /api/v1/libraries/{slug}.
    Covers owner edits, moderation reset, validation, and file uploads."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_requires_authentication(self, client, pending_library):
        """Verify the update endpoint rejects anonymous requests.
        JWT authentication is mandatory for owner edits."""
        response = client.patch(
            f"/api/v1/libraries/{pending_library.slug}",
            data=encode_multipart(BOUNDARY, {"description": "Updated"}),
            content_type=MULTIPART_CONTENT,
        )

        assert response.status_code == 401

    def test_owner_can_partially_update_pending_library(self, client, pending_library, user_jwt):
        """Verify owners can update selected fields only.
        Omitted fields keep their previous values."""
        response = _patch_multipart(
            client,
            f"/api/v1/libraries/{pending_library.slug}",
            {"description": "Updated via API.", "capacity": "42"},
            user_jwt,
        )

        body = response.json()
        pending_library.refresh_from_db()
        assert response.status_code == 200
        assert body["description"] == "Updated via API."
        assert body["capacity"] == 42
        assert body["city"] == "Florence"
        assert pending_library.description == "Updated via API."
        assert pending_library.capacity == 42
        assert pending_library.status == Library.Status.PENDING

    def test_owner_editing_approved_library_resets_to_pending(self, client, approved_library, user_jwt):
        """Verify approved library edits return to pending status.
        Moderation is required again before edits become public."""
        response = _patch_multipart(
            client,
            f"/api/v1/libraries/{approved_library.slug}",
            {"name": "API Edited Library"},
            user_jwt,
        )

        approved_library.refresh_from_db()
        assert response.status_code == 200
        assert response.json()["name"] == "API Edited Library"
        assert approved_library.status == Library.Status.PENDING

    def test_non_owner_cannot_update_library(self, client, pending_library, other_user_jwt):
        """Verify non-owners receive a not-found response.
        Prevents users from discovering or editing other submissions."""
        response = _patch_multipart(
            client,
            f"/api/v1/libraries/{pending_library.slug}",
            {"description": "Malicious edit."},
            other_user_jwt,
        )

        pending_library.refresh_from_db()
        assert response.status_code == 404
        assert pending_library.description != "Malicious edit."

    def test_rejected_library_cannot_be_updated(self, client, rejected_library, user_jwt):
        """Verify rejected libraries are not editable through the API.
        Rejected submissions remain historical records."""
        response = _patch_multipart(
            client,
            f"/api/v1/libraries/{rejected_library.slug}",
            {"description": "Try to revive."},
            user_jwt,
        )

        assert response.status_code == 404

    def test_coordinates_must_be_provided_together(self, client, pending_library, user_jwt):
        """Verify latitude and longitude are validated as a pair.
        Prevents partial coordinates from corrupting the stored Point."""
        response = _patch_multipart(
            client,
            f"/api/v1/libraries/{pending_library.slug}",
            {"latitude": "43.7700"},
            user_jwt,
        )

        assert response.status_code == 400
        assert response.json()["message"] == "Latitude and longitude must be provided together."

    def test_owner_can_update_coordinates(self, client, pending_library, user_jwt):
        """Verify coordinate updates rewrite the stored Point correctly.
        Confirms latitude maps to y and longitude maps to x."""
        response = _patch_multipart(
            client,
            f"/api/v1/libraries/{pending_library.slug}",
            {"latitude": "43.7700", "longitude": "11.2600"},
            user_jwt,
        )

        pending_library.refresh_from_db()
        assert response.status_code == 200
        assert response.json()["lat"] == pytest.approx(43.7700, abs=1e-4)
        assert response.json()["lng"] == pytest.approx(11.2600, abs=1e-4)
        assert pending_library.location.y == pytest.approx(43.7700, abs=1e-6)
        assert pending_library.location.x == pytest.approx(11.2600, abs=1e-6)

    def test_no_fields_returns_400(self, client, pending_library, user_jwt):
        """Verify empty update requests are rejected.
        Clients must provide at least one changed field or replacement photo."""
        response = _patch_multipart(
            client,
            f"/api/v1/libraries/{pending_library.slug}",
            {},
            user_jwt,
        )

        assert response.status_code == 400
        assert response.json()["message"] == "Provide at least one field to update."

    def test_photo_replacement_updates_primary_photo(self, client, pending_library, user_jwt, tmp_path, settings):
        """Verify multipart PATCH can replace a library photo.
        The Django Ninja file middleware makes PATCH files available."""
        settings.MEDIA_ROOT = tmp_path / "media"

        response = _patch_multipart(
            client,
            f"/api/v1/libraries/{pending_library.slug}",
            {"photo": _build_uploaded_photo(file_name="api-replacement.jpg")},
            user_jwt,
        )

        pending_library.refresh_from_db()
        assert response.status_code == 200
        assert pending_library.photo.name
        assert "api-replacement" in pending_library.photo.name
        assert pending_library.photo_thumbnail.name

    def test_invalid_photo_format_returns_400(self, client, pending_library, user_jwt):
        """Verify invalid replacement photos are rejected.
        Only supported image formats can enter library photo storage."""
        fake_file = SimpleUploadedFile(
            name="not-image.txt",
            content=b"not an image",
            content_type="text/plain",
        )

        response = _patch_multipart(
            client,
            f"/api/v1/libraries/{pending_library.slug}",
            {"photo": fake_file},
            user_jwt,
        )

        assert response.status_code == 400
        assert "valid image" in response.json()["message"].lower()

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_WRITE_REQUESTS=1,
    )
    def test_rate_limit_returns_429(self, client, pending_library, user_jwt):
        """Verify update requests respect write rate limits.
        Excessive owner-edit attempts receive a retry response."""
        _patch_multipart(
            client,
            f"/api/v1/libraries/{pending_library.slug}",
            {"description": "First update."},
            user_jwt,
        )

        response = _patch_multipart(
            client,
            f"/api/v1/libraries/{pending_library.slug}",
            {"description": "Second update."},
            user_jwt,
        )

        assert response.status_code == 429
        assert "Too many requests" in response.json()["message"]
