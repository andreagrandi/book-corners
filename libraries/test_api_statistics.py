import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.test import override_settings

from libraries.models import Library, LibraryPhoto

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the Django cache before each test.
    Prevents stale cached statistics from leaking between tests."""
    cache.clear()


@pytest.fixture
def stats_user(db):
    """Create a user for statistics test fixtures.
    Provides a baseline creator for library objects."""
    return User.objects.create_user(
        username="statsuser",
        password="statspass123",
    )


def _create_approved_library(*, user, country="IT", city="Florence", photo="libraries/photos/2026/02/test.jpg"):
    """Create an approved library with minimal required fields.
    Reduces boilerplate across statistics test cases."""
    return Library.objects.create(
        name=f"Lib in {city}",
        photo=photo,
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city=city,
        country=country,
        status=Library.Status.APPROVED,
        created_by=user,
    )


@pytest.mark.django_db
class TestStatisticsEndpoint:
    """Tests for the GET /api/v1/statistics/ endpoint."""

    def test_returns_200_with_expected_shape(self, client, stats_user):
        """Verify the response contains all top-level statistics fields.
        Confirms the API contract matches the documented schema."""
        _create_approved_library(user=stats_user)

        response = client.get("/api/v1/statistics/")

        assert response.status_code == 200
        data = response.json()
        assert "total_approved" in data
        assert "total_with_image" in data
        assert "top_countries" in data
        assert "cumulative_series" in data
        assert "granularity" in data

    def test_counts_only_approved_libraries(self, client, stats_user):
        """Verify totals exclude pending and rejected libraries.
        Confirms only approved entries contribute to counts."""
        _create_approved_library(user=stats_user)
        Library.objects.create(
            name="Pending Lib",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 16",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=stats_user,
        )

        response = client.get("/api/v1/statistics/")

        assert response.json()["total_approved"] == 1

    def test_image_count_includes_primary_photo(self, client, stats_user):
        """Verify libraries with a primary photo are counted as having images.
        Confirms primary photo field contributes to image totals."""
        _create_approved_library(user=stats_user, photo="libraries/photos/2026/02/test.jpg")
        _create_approved_library(user=stats_user, photo="", city="Rome")

        response = client.get("/api/v1/statistics/")

        assert response.json()["total_with_image"] == 1

    def test_image_count_includes_community_photos(self, client, stats_user):
        """Verify libraries with approved community photos count as having images.
        Confirms LibraryPhoto contributes to with-image totals."""
        lib = _create_approved_library(user=stats_user, photo="")
        LibraryPhoto.objects.create(
            library=lib,
            created_by=stats_user,
            photo="libraries/user_photos/2026/02/community.jpg",
            status=LibraryPhoto.Status.APPROVED,
        )

        response = client.get("/api/v1/statistics/")

        assert response.json()["total_with_image"] == 1

    def test_top_countries_limited_to_10(self, client, stats_user):
        """Verify top_countries returns at most 10 entries.
        Confirms the ranking is capped regardless of data volume."""
        countries = ["IT", "DE", "FR", "ES", "GB", "US", "NL", "BE", "AT", "CH", "PT"]
        for code in countries:
            _create_approved_library(user=stats_user, country=code, city=f"City-{code}")

        response = client.get("/api/v1/statistics/")

        assert len(response.json()["top_countries"]) == 10

    def test_cumulative_series_values_are_non_decreasing(self, client, stats_user):
        """Verify cumulative series is monotonically non-decreasing.
        Confirms the running total only grows over time."""
        for i in range(3):
            _create_approved_library(user=stats_user, city=f"City-{i}")

        response = client.get("/api/v1/statistics/")

        series = response.json()["cumulative_series"]
        counts = [point["cumulative_count"] for point in series]
        assert counts == sorted(counts)
        assert counts[-1] == 3

    def test_empty_state_returns_zeros(self, client):
        """Verify the endpoint handles an empty database gracefully.
        Confirms zero totals and empty lists when no libraries exist."""
        response = client.get("/api/v1/statistics/")

        data = response.json()
        assert data["total_approved"] == 0
        assert data["total_with_image"] == 0
        assert data["top_countries"] == []
        assert data["cumulative_series"] == []

    def test_granularity_field_is_present(self, client, stats_user):
        """Verify the granularity field is included in the response.
        Confirms clients can determine time series resolution."""
        _create_approved_library(user=stats_user)

        response = client.get("/api/v1/statistics/")

        assert response.json()["granularity"] in ("daily", "monthly")

    @override_settings(API_RATE_LIMIT_ENABLED=True, API_RATE_LIMIT_READ_REQUESTS=1, API_RATE_LIMIT_WINDOW_SECONDS=60)
    def test_rate_limiting_returns_429(self, client, stats_user):
        """Verify the endpoint enforces rate limiting.
        Confirms excessive requests receive a 429 response."""
        client.get("/api/v1/statistics/")

        response = client.get("/api/v1/statistics/")

        assert response.status_code == 429

    def test_country_stat_includes_flag_and_name(self, client, stats_user):
        """Verify each country entry has code, name, flag, and count.
        Confirms the schema contract for country statistics."""
        _create_approved_library(user=stats_user, country="DE", city="Berlin")

        response = client.get("/api/v1/statistics/")

        country = response.json()["top_countries"][0]
        assert country["country_code"] == "DE"
        assert country["country_name"] == "Germany"
        assert country["flag_emoji"] == "\U0001F1E9\U0001F1EA"
        assert country["count"] == 1

    def test_public_access_without_authentication(self, client):
        """Verify the endpoint is publicly accessible without auth.
        Confirms no JWT or session is required."""
        response = client.get("/api/v1/statistics/")

        assert response.status_code == 200
