import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.test import override_settings
from ninja_jwt.tokens import RefreshToken

from libraries.models import Library

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
            "created_at",
        }
        assert set(body.keys()) == expected_fields

    def test_coordinates_resolved_correctly(self, client, approved_library):
        """Verify lat and lng are correctly extracted from the PostGIS point.
        Confirms lat=Y and lng=X mapping from the geometry field."""
        response = client.get(f"/api/v1/libraries/{approved_library.slug}")

        body = response.json()
        assert body["lat"] == pytest.approx(48.8566, abs=1e-4)
        assert body["lng"] == pytest.approx(2.3522, abs=1e-4)
