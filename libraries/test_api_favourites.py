import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.test import override_settings
from ninja_jwt.tokens import RefreshToken

from libraries.models import Favourite, Library

User = get_user_model()


@pytest.fixture
def user_jwt(user):
    """Generate a valid JWT access token for the default test user.
    Provides bearer credentials for authenticated API requests."""
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


@pytest.fixture
def approved_library(user):
    """Create an approved library for favourite tests.
    Only approved libraries can be favourited."""
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


@pytest.fixture
def second_approved_library(user):
    """Create a second approved library for ordering and isolation tests.
    Provides a distinct library to verify multi-favourite scenarios."""
    return Library.objects.create(
        name="Second Library",
        photo="libraries/photos/2026/02/test2.jpg",
        location=Point(x=12.4964, y=41.9028, srid=4326),
        address="Via Roma 1",
        city="Rome",
        country="IT",
        status=Library.Status.APPROVED,
        created_by=user,
    )


@pytest.mark.django_db
class TestMarkFavourite:
    """Tests for POST /api/v1/libraries/{slug}/favourite endpoint."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def _url(self, slug):
        """Build the favourite endpoint URL for a given library slug.
        Centralises path construction for all test methods."""
        return f"/api/v1/libraries/{slug}/favourite"

    def test_requires_authentication(self, client, approved_library):
        """Verify the endpoint rejects unauthenticated requests with 401.
        JWT is mandatory for marking favourites."""
        response = client.post(self._url(slug=approved_library.slug))
        assert response.status_code == 401

    def test_mark_approved_library_returns_201(self, client, user_jwt, approved_library):
        """Verify marking a new favourite returns 201.
        First-time favourite creates a new database row."""
        response = client.post(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 201

    def test_mark_already_favourited_returns_200(self, client, user, user_jwt, approved_library):
        """Verify marking an existing favourite returns 200 without error.
        Duplicate favourites are idempotent, not rejected."""
        Favourite.objects.create(user=user, library=approved_library)
        response = client.post(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 200

    def test_response_contains_message(self, client, user_jwt, approved_library):
        """Verify the response body includes a message field.
        Confirms the response shape matches ErrorOut schema."""
        response = client.post(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        body = response.json()
        assert "message" in body

    def test_404_for_nonexistent_library(self, client, user_jwt):
        """Verify a nonexistent slug returns 404.
        Favourites can only target libraries that exist."""
        response = client.post(
            self._url(slug="does-not-exist"),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 404

    def test_404_for_pending_library(self, client, user_jwt, user):
        """Verify a pending library cannot be favourited.
        Only approved libraries are eligible for favouriting."""
        pending = Library.objects.create(
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
            self._url(slug=pending.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 404

    def test_404_for_rejected_library(self, client, user_jwt, user):
        """Verify a rejected library cannot be favourited.
        Only approved libraries are eligible for favouriting."""
        rejected = Library.objects.create(
            name="Rejected Library",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Dante 2",
            city="Florence",
            country="IT",
            status=Library.Status.REJECTED,
            created_by=user,
        )
        response = client.post(
            self._url(slug=rejected.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 404

    def test_creates_favourite_in_database(self, client, user, user_jwt, approved_library):
        """Verify the favourite is persisted in the database.
        Confirms the model row exists after a successful request."""
        client.post(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert Favourite.objects.filter(user=user, library=approved_library).exists()

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_WRITE_REQUESTS=1,
    )
    def test_rate_limit_returns_429(self, client, user_jwt, approved_library):
        """Verify the endpoint returns 429 when rate limited.
        Write endpoints share a stricter rate limit tier."""
        client.post(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        response = client.post(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 429


@pytest.mark.django_db
class TestUnmarkFavourite:
    """Tests for DELETE /api/v1/libraries/{slug}/favourite endpoint."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def _url(self, slug):
        """Build the favourite endpoint URL for a given library slug.
        Centralises path construction for all test methods."""
        return f"/api/v1/libraries/{slug}/favourite"

    def test_requires_authentication(self, client, approved_library):
        """Verify the endpoint rejects unauthenticated requests with 401.
        JWT is mandatory for removing favourites."""
        response = client.delete(self._url(slug=approved_library.slug))
        assert response.status_code == 401

    def test_unmark_existing_favourite_returns_204(self, client, user, user_jwt, approved_library):
        """Verify removing an existing favourite returns 204.
        Successful deletion returns no content."""
        Favourite.objects.create(user=user, library=approved_library)
        response = client.delete(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 204

    def test_unmark_nonexistent_favourite_returns_204(self, client, user_jwt, approved_library):
        """Verify removing a non-existent favourite returns 204.
        The operation is idempotent for client convenience."""
        response = client.delete(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 204

    def test_404_for_nonexistent_library(self, client, user_jwt):
        """Verify a nonexistent slug returns 404.
        Cannot unmark a library that does not exist."""
        response = client.delete(
            self._url(slug="does-not-exist"),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 404

    def test_404_for_pending_library(self, client, user_jwt, user):
        """Verify a pending library cannot be unfavourited via the endpoint.
        Only approved libraries are accessible for favourite operations."""
        pending = Library.objects.create(
            name="Pending Library",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Dante 1",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=user,
        )
        response = client.delete(
            self._url(slug=pending.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 404

    def test_removes_favourite_from_database(self, client, user, user_jwt, approved_library):
        """Verify the favourite row is deleted from the database.
        Confirms the model row no longer exists after deletion."""
        Favourite.objects.create(user=user, library=approved_library)
        client.delete(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert not Favourite.objects.filter(user=user, library=approved_library).exists()

    def test_does_not_affect_other_users_favourites(self, client, user, user_jwt, approved_library):
        """Verify unfavouriting only removes the current user's favourite.
        Other users' favourites remain intact after deletion."""
        other_user = User.objects.create_user(username="otheruser", password="pass123")
        Favourite.objects.create(user=user, library=approved_library)
        Favourite.objects.create(user=other_user, library=approved_library)

        client.delete(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )

        assert not Favourite.objects.filter(user=user, library=approved_library).exists()
        assert Favourite.objects.filter(user=other_user, library=approved_library).exists()

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_WRITE_REQUESTS=1,
    )
    def test_rate_limit_returns_429(self, client, user_jwt, approved_library):
        """Verify the endpoint returns 429 when rate limited.
        Write endpoints share a stricter rate limit tier."""
        client.delete(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        response = client.delete(
            self._url(slug=approved_library.slug),
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 429


@pytest.mark.django_db
class TestListFavourites:
    """Tests for GET /api/v1/libraries/favourites endpoint."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    url = "/api/v1/libraries/favourites"

    def test_requires_authentication(self, client):
        """Verify the endpoint rejects unauthenticated requests with 401.
        JWT is mandatory for listing favourites."""
        response = client.get(self.url)
        assert response.status_code == 401

    def test_returns_empty_when_no_favourites(self, client, user_jwt):
        """Verify an empty favourites list returns correctly shaped response.
        Pagination metadata should indicate zero results."""
        response = client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["pagination"]["total"] == 0

    def test_returns_favourited_libraries(self, client, user, user_jwt, approved_library):
        """Verify favourited libraries appear in the response.
        The list should contain the library the user has favourited."""
        Favourite.objects.create(user=user, library=approved_library)
        response = client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["slug"] == approved_library.slug

    def test_excludes_unfavourited_libraries(
        self, client, user, user_jwt, approved_library, second_approved_library,
    ):
        """Verify only favourited libraries are included.
        Non-favourited libraries should not appear in the response."""
        Favourite.objects.create(user=user, library=approved_library)
        response = client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        body = response.json()
        slugs = [item["slug"] for item in body["items"]]
        assert approved_library.slug in slugs
        assert second_approved_library.slug not in slugs

    def test_excludes_unapproved_favourites(self, client, user, user_jwt):
        """Verify favourited libraries that lose approved status are excluded.
        Only currently approved libraries appear in the favourites list."""
        pending = Library.objects.create(
            name="Was Approved",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Ponte 5",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=user,
        )
        Favourite.objects.create(user=user, library=pending)
        response = client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        body = response.json()
        assert body["items"] == []

    def test_ordered_by_favourite_date_descending(
        self, client, user, user_jwt, approved_library, second_approved_library,
    ):
        """Verify favourites are ordered by when they were favourited, newest first.
        The most recently favourited library should appear first."""
        Favourite.objects.create(user=user, library=approved_library)
        Favourite.objects.create(user=user, library=second_approved_library)
        response = client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        body = response.json()
        assert body["items"][0]["slug"] == second_approved_library.slug
        assert body["items"][1]["slug"] == approved_library.slug

    def test_pagination_works(self, client, user, user_jwt, approved_library, second_approved_library):
        """Verify page_size limits the number of items returned.
        Pagination metadata should reflect the constrained page."""
        Favourite.objects.create(user=user, library=approved_library)
        Favourite.objects.create(user=user, library=second_approved_library)
        response = client.get(
            f"{self.url}?page_size=1",
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        body = response.json()
        assert len(body["items"]) == 1
        assert body["pagination"]["total"] == 2
        assert body["pagination"]["has_next"] is True

    def test_is_favourited_true_for_all_items(self, client, user, user_jwt, approved_library):
        """Verify all items in the favourites list have is_favourited true.
        Every library in the list is by definition a favourite."""
        Favourite.objects.create(user=user, library=approved_library)
        response = client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        body = response.json()
        for item in body["items"]:
            assert item["is_favourited"] is True

    def test_does_not_include_other_users_favourites(
        self, client, user_jwt, approved_library,
    ):
        """Verify only the authenticated user's favourites are returned.
        Other users' favourites should not leak into the response."""
        other_user = User.objects.create_user(username="otheruser", password="pass123")
        Favourite.objects.create(user=other_user, library=approved_library)
        response = client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        body = response.json()
        assert body["items"] == []

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_READ_REQUESTS=1,
    )
    def test_rate_limit_returns_429(self, client, user_jwt):
        """Verify the endpoint returns 429 when rate limited.
        Read endpoints share a rate limit tier."""
        client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        response = client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        assert response.status_code == 429


@pytest.mark.django_db
class TestIsFavouritedAnnotation:
    """Tests for is_favourited field on library detail, list, and latest endpoints."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_detail_is_favourited_true_when_favourited(
        self, client, user, user_jwt, approved_library,
    ):
        """Verify library detail shows is_favourited true for a favourited library.
        Authenticated users see their favourite status on individual libraries."""
        Favourite.objects.create(user=user, library=approved_library)
        response = client.get(
            f"/api/v1/libraries/{approved_library.slug}",
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        body = response.json()
        assert body["is_favourited"] is True

    def test_detail_is_favourited_false_when_not_favourited(
        self, client, user_jwt, approved_library,
    ):
        """Verify library detail shows is_favourited false for a non-favourited library.
        Authenticated users who haven't favourited see false."""
        response = client.get(
            f"/api/v1/libraries/{approved_library.slug}",
            HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        )
        body = response.json()
        assert body["is_favourited"] is False

    def test_detail_is_favourited_false_without_auth(self, client, approved_library):
        """Verify library detail shows is_favourited false for anonymous users.
        Unauthenticated requests always default to false."""
        response = client.get(f"/api/v1/libraries/{approved_library.slug}")
        body = response.json()
        assert body["is_favourited"] is False

    def test_list_includes_is_favourited_field(self, client, approved_library):
        """Verify library list items include the is_favourited field.
        The field should be present even for anonymous requests."""
        response = client.get("/api/v1/libraries/")
        body = response.json()
        assert len(body["items"]) > 0
        assert "is_favourited" in body["items"][0]

    def test_latest_includes_is_favourited_field(self, client, approved_library):
        """Verify latest libraries include the is_favourited field.
        The field should be present even for anonymous requests."""
        response = client.get("/api/v1/libraries/latest")
        body = response.json()
        assert len(body["items"]) > 0
        assert "is_favourited" in body["items"][0]
