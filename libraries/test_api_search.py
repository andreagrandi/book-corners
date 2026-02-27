import pytest
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.test import override_settings

from libraries.models import Library


@pytest.fixture
def make_library(user):
    """Factory fixture to create approved libraries with sensible defaults.
    Reduces boilerplate for tests that need multiple libraries."""

    def _make(*, name="Test Library", city="Paris", country="FR",
              postal_code="75001", lat=48.8566, lng=2.3522,
              description="", status=Library.Status.APPROVED):
        return Library.objects.create(
            name=name,
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=lng, y=lat, srid=4326),
            address="1 Rue Test",
            city=city,
            country=country,
            postal_code=postal_code,
            description=description,
            status=status,
            created_by=user,
        )

    return _make


@pytest.mark.django_db
class TestLibrarySearchTextFilter:
    """Tests for the q (text search) query parameter."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_q_matches_name(self, client, make_library):
        """Verify text search matches library name.
        Confirms the q parameter filters by name content."""
        make_library(name="Sunshine Corner Library")
        make_library(name="Oak Street Library")

        response = client.get("/api/v1/libraries/?q=Sunshine")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Sunshine Corner Library"

    def test_q_matches_description(self, client, make_library):
        """Verify text search matches library description.
        Confirms full-text search indexes the description field."""
        make_library(name="Library A", description="Beautiful garden reading spot")
        make_library(name="Library B", description="Urban book exchange")

        response = client.get("/api/v1/libraries/?q=garden")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Library A"

    def test_q_excludes_non_matching(self, client, make_library):
        """Verify text search excludes libraries that do not match.
        Confirms unrelated entries are filtered out."""
        make_library(name="Oak Library")

        response = client.get("/api/v1/libraries/?q=Nonexistent")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 0

    def test_q_excludes_pending(self, client, make_library):
        """Verify text search excludes pending libraries.
        Only approved entries should appear in search results."""
        make_library(name="Pending Place", status=Library.Status.PENDING)

        response = client.get("/api/v1/libraries/?q=Pending")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 0

    def test_empty_q_returns_all_approved(self, client, make_library):
        """Verify empty q returns all approved libraries.
        Confirms the text filter is a no-op when blank."""
        make_library(name="Library A")
        make_library(name="Library B")

        response = client.get("/api/v1/libraries/?q=")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_no_q_returns_all_approved(self, client, make_library):
        """Verify omitting q returns all approved libraries.
        Confirms default behavior is unfiltered."""
        make_library(name="Library A")
        make_library(name="Library B")

        response = client.get("/api/v1/libraries/")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2


@pytest.mark.django_db
class TestLibrarySearchFieldFilters:
    """Tests for city, country, and postal_code field filters."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_city_filter_case_insensitive(self, client, make_library):
        """Verify city filter is case-insensitive.
        Confirms icontains behavior on the city field."""
        make_library(name="Paris Lib", city="Paris")
        make_library(name="Berlin Lib", city="Berlin")

        response = client.get("/api/v1/libraries/?city=paris")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Paris Lib"

    def test_country_filter_exact(self, client, make_library):
        """Verify country filter uses exact case-insensitive match.
        Confirms iexact behavior on the country field."""
        make_library(name="French Lib", country="FR")
        make_library(name="German Lib", country="DE")

        response = client.get("/api/v1/libraries/?country=FR")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "French Lib"

    def test_postal_code_partial_match(self, client, make_library):
        """Verify postal code filter supports partial matching.
        Confirms icontains behavior on the postal_code field."""
        make_library(name="Lib 75001", postal_code="75001")
        make_library(name="Lib 10115", postal_code="10115")

        response = client.get("/api/v1/libraries/?postal_code=750")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Lib 75001"

    def test_combined_city_and_country(self, client, make_library):
        """Verify combining city and country filters narrows results.
        Confirms both filters are applied as AND conditions."""
        make_library(name="Paris FR", city="Paris", country="FR")
        make_library(name="Paris TX", city="Paris", country="US")
        make_library(name="Berlin DE", city="Berlin", country="DE")

        response = client.get("/api/v1/libraries/?city=Paris&country=FR")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Paris FR"

    def test_combined_text_and_field(self, client, make_library):
        """Verify combining text search with field filters.
        Confirms q and city are applied together as AND conditions."""
        make_library(name="Sunshine Library", city="Paris")
        make_library(name="Sunshine Library", city="Berlin")
        make_library(name="Oak Library", city="Paris")

        response = client.get("/api/v1/libraries/?q=Sunshine&city=Paris")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["city"] == "Paris"


@pytest.mark.django_db
class TestLibrarySearchPhotoFilter:
    """Tests for the has_photo query parameter."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_has_photo_true_returns_only_with_photo(self, client, make_library):
        """Verify has_photo=true returns only libraries with photos.
        Confirms libraries without photos are excluded."""
        make_library(name="With Photo")
        Library.objects.create(
            name="Without Photo",
            photo="",
            location=Point(x=2.3522, y=48.8566, srid=4326),
            address="1 Rue Test",
            city="Paris",
            country="FR",
            postal_code="75001",
            status=Library.Status.APPROVED,
            created_by=Library.objects.first().created_by,
        )

        response = client.get("/api/v1/libraries/?has_photo=true")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "With Photo"

    def test_has_photo_false_returns_only_without_photo(self, client, make_library):
        """Verify has_photo=false returns only libraries without photos.
        Confirms libraries with photos are excluded."""
        make_library(name="With Photo")
        Library.objects.create(
            name="Without Photo",
            photo="",
            location=Point(x=2.3522, y=48.8566, srid=4326),
            address="1 Rue Test",
            city="Paris",
            country="FR",
            postal_code="75001",
            status=Library.Status.APPROVED,
            created_by=Library.objects.first().created_by,
        )

        response = client.get("/api/v1/libraries/?has_photo=false")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Without Photo"

    def test_has_photo_omitted_returns_all(self, client, make_library):
        """Verify omitting has_photo returns all approved libraries.
        Confirms the filter is a no-op when not specified."""
        make_library(name="With Photo")
        Library.objects.create(
            name="Without Photo",
            photo="",
            location=Point(x=2.3522, y=48.8566, srid=4326),
            address="1 Rue Test",
            city="Paris",
            country="FR",
            postal_code="75001",
            status=Library.Status.APPROVED,
            created_by=Library.objects.first().created_by,
        )

        response = client.get("/api/v1/libraries/")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2


@pytest.mark.django_db
class TestLibrarySearchProximity:
    """Tests for lat/lng proximity search parameters."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_nearby_included(self, client, make_library):
        """Verify a library within default radius is included.
        Confirms proximity filter returns close results."""
        make_library(name="Nearby", lat=48.8566, lng=2.3522)

        response = client.get("/api/v1/libraries/?lat=48.8570&lng=2.3525")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Nearby"

    def test_distant_excluded(self, client, make_library):
        """Verify a library beyond default radius is excluded.
        Confirms proximity filter rejects distant results."""
        make_library(name="Far Away", lat=52.52, lng=13.405)

        response = client.get("/api/v1/libraries/?lat=48.8566&lng=2.3522")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 0

    def test_custom_radius(self, client, make_library):
        """Verify custom radius_km expands the search area.
        Confirms the radius parameter overrides the default."""
        make_library(name="Medium Distance", lat=48.95, lng=2.35)

        response = client.get("/api/v1/libraries/?lat=48.8566&lng=2.3522&radius_km=20")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_lat_only_ignored(self, client, make_library):
        """Verify lat without lng is ignored for proximity.
        Returns all approved libraries without distance filtering."""
        make_library(name="Library A")
        make_library(name="Library B", lat=52.52, lng=13.405)

        response = client.get("/api/v1/libraries/?lat=48.8566")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_lng_only_ignored(self, client, make_library):
        """Verify lng without lat is ignored for proximity.
        Returns all approved libraries without distance filtering."""
        make_library(name="Library A")
        make_library(name="Library B", lat=52.52, lng=13.405)

        response = client.get("/api/v1/libraries/?lng=2.3522")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_proximity_with_text_search(self, client, make_library):
        """Verify proximity combined with text search.
        Confirms both filters narrow results together."""
        make_library(name="Sunshine Corner", lat=48.8566, lng=2.3522)
        make_library(name="Oak Street", lat=48.8570, lng=2.3525)
        make_library(name="Sunshine Far", lat=52.52, lng=13.405)

        response = client.get("/api/v1/libraries/?q=Sunshine&lat=48.8566&lng=2.3522")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Sunshine Corner"


@pytest.mark.django_db
class TestLibrarySearchPagination:
    """Tests for search result pagination."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_search_results_paginate(self, client, make_library):
        """Verify search results are paginated correctly.
        Confirms page and page_size work with search filters."""
        for i in range(5):
            make_library(name=f"Sunshine Library {i}", city="Paris")

        response = client.get("/api/v1/libraries/?q=Sunshine&page=1&page_size=2")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 2
        assert body["pagination"]["total"] == 5
        assert body["pagination"]["has_next"] is True

    def test_field_filter_results_paginate(self, client, make_library):
        """Verify field-filtered results paginate correctly.
        Confirms pagination works when combined with city filter."""
        for i in range(4):
            make_library(name=f"Berlin Lib {i}", city="Berlin", country="DE")
        make_library(name="Paris Lib", city="Paris", country="FR")

        response = client.get("/api/v1/libraries/?city=Berlin&page=1&page_size=2")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 2
        assert body["pagination"]["total"] == 4


@pytest.mark.django_db
class TestLatestLibrariesEndpoint:
    """Tests for GET /api/v1/libraries/latest endpoint."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_default_limit_ten(self, client, make_library):
        """Verify default limit returns up to 10 libraries.
        Confirms the endpoint applies the default limit when omitted."""
        for i in range(15):
            make_library(name=f"Library {i}")

        response = client.get("/api/v1/libraries/latest")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 10

    def test_custom_limit(self, client, make_library):
        """Verify custom limit parameter is respected.
        Confirms the endpoint returns the requested number of items."""
        for i in range(10):
            make_library(name=f"Library {i}")

        response = client.get("/api/v1/libraries/latest?limit=3")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 3

    def test_limit_above_50_returns_422(self, client, db):
        """Verify limit above 50 is rejected with 422.
        Confirms Ninja validation enforces the upper bound."""
        response = client.get("/api/v1/libraries/latest?limit=51")

        assert response.status_code == 422

    def test_limit_below_1_returns_422(self, client, db):
        """Verify limit below 1 is rejected with 422.
        Confirms Ninja validation enforces the lower bound."""
        response = client.get("/api/v1/libraries/latest?limit=0")

        assert response.status_code == 422

    def test_only_approved(self, client, make_library):
        """Verify only approved libraries appear in latest.
        Confirms pending and rejected entries are excluded."""
        make_library(name="Approved", status=Library.Status.APPROVED)
        make_library(name="Pending", status=Library.Status.PENDING)
        make_library(name="Rejected", status=Library.Status.REJECTED)

        response = client.get("/api/v1/libraries/latest")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Approved"

    def test_newest_first(self, client, make_library):
        """Verify latest returns libraries in newest-first order.
        Confirms created_at descending sort is applied."""
        lib_a = make_library(name="Older")
        lib_b = make_library(name="Newer")

        response = client.get("/api/v1/libraries/latest")

        assert response.status_code == 200
        items = response.json()["items"]
        assert items[0]["name"] == "Newer"
        assert items[1]["name"] == "Older"

    def test_empty_database(self, client, db):
        """Verify latest returns empty list when no libraries exist.
        Confirms graceful handling of zero-row queries."""
        response = client.get("/api/v1/libraries/latest")

        assert response.status_code == 200
        assert response.json()["items"] == []

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_READ_REQUESTS=2,
    )
    def test_rate_limit_returns_429(self, client, db):
        """Verify the latest endpoint returns 429 when rate limited.
        Confirms excessive requests are throttled with a retry hint."""
        for _ in range(3):
            client.get("/api/v1/libraries/latest")

        response = client.get("/api/v1/libraries/latest")

        assert response.status_code == 429
        body = response.json()
        assert "Too many requests" in body["message"]

    def test_has_photo_true_excludes_photoless(self, client, make_library):
        """Verify has_photo=true on latest excludes libraries without photos.
        Confirms the filter works on the latest endpoint."""
        make_library(name="With Photo")
        Library.objects.create(
            name="Without Photo",
            photo="",
            location=Point(x=2.3522, y=48.8566, srid=4326),
            address="1 Rue Test",
            city="Paris",
            country="FR",
            postal_code="75001",
            status=Library.Status.APPROVED,
            created_by=Library.objects.first().created_by,
        )

        response = client.get("/api/v1/libraries/latest?has_photo=true")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "With Photo"

    def test_no_pagination_metadata(self, client, make_library):
        """Verify the latest response has no pagination metadata.
        Confirms the flat list shape without navigation fields."""
        make_library(name="Library A")

        response = client.get("/api/v1/libraries/latest")

        body = response.json()
        assert "pagination" not in body
        assert "items" in body
