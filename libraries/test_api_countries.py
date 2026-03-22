import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.test import override_settings

from libraries.models import Library

User = get_user_model()


@pytest.fixture
def countries_user(db):
    """Create a user for countries endpoint test fixtures.
    Provides a baseline creator for library objects."""
    return User.objects.create_user(
        username="countriesuser",
        password="countriespass123",
    )


def _create_library(*, user, country="IT", city="Florence", status=Library.Status.APPROVED):
    """Create a library with minimal required fields.
    Reduces boilerplate across countries test cases."""
    return Library.objects.create(
        name=f"Lib in {city}",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city=city,
        country=country,
        status=status,
        created_by=user,
    )


@pytest.mark.django_db
class TestCountriesEndpoint:
    """Tests for the GET /api/v1/libraries/countries/ endpoint."""

    def test_returns_all_countries_with_approved_libraries(self, client, countries_user):
        """Verify the endpoint returns every country that has approved libraries.
        Confirms no top-N limit is applied."""
        countries = ["IT", "DE", "FR", "ES", "GB", "US", "NL", "BE", "AT", "CH", "PT", "SE"]
        for code in countries:
            _create_library(user=countries_user, country=code, city=f"City-{code}")

        response = client.get("/api/v1/libraries/countries/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 12

    def test_empty_db_returns_empty_list(self, client, db):
        """Verify the endpoint returns an empty items list when no libraries exist.
        Confirms graceful handling of the zero-data case."""
        response = client.get("/api/v1/libraries/countries/")

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_excludes_pending_and_rejected_libraries(self, client, countries_user):
        """Verify only approved libraries contribute to country counts.
        Confirms non-approved statuses are filtered out."""
        _create_library(user=countries_user, country="IT", city="Florence")
        _create_library(user=countries_user, country="DE", city="Berlin", status=Library.Status.PENDING)
        _create_library(user=countries_user, country="FR", city="Paris", status=Library.Status.REJECTED)

        response = client.get("/api/v1/libraries/countries/")

        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["country_code"] == "IT"

    def test_ordered_by_count_descending(self, client, countries_user):
        """Verify countries are ordered by library count, most first.
        Confirms the descending sort contract."""
        for _ in range(3):
            _create_library(user=countries_user, country="DE", city="Berlin")
        _create_library(user=countries_user, country="IT", city="Florence")

        response = client.get("/api/v1/libraries/countries/")

        items = response.json()["items"]
        assert items[0]["country_code"] == "DE"
        assert items[0]["count"] == 3
        assert items[1]["country_code"] == "IT"
        assert items[1]["count"] == 1

    def test_country_entry_has_expected_fields(self, client, countries_user):
        """Verify each country entry contains code, name, flag, and count.
        Confirms the schema contract for the country list."""
        _create_library(user=countries_user, country="FR", city="Paris")

        response = client.get("/api/v1/libraries/countries/")

        item = response.json()["items"][0]
        assert item["country_code"] == "FR"
        assert item["country_name"] == "France"
        assert item["flag_emoji"] == "\U0001F1EB\U0001F1F7"
        assert item["count"] == 1

    def test_public_access_without_authentication(self, client, db):
        """Verify the endpoint is publicly accessible without auth.
        Confirms no JWT or session is required."""
        response = client.get("/api/v1/libraries/countries/")

        assert response.status_code == 200

    @override_settings(API_RATE_LIMIT_ENABLED=True, API_RATE_LIMIT_READ_REQUESTS=1, API_RATE_LIMIT_WINDOW_SECONDS=60)
    def test_rate_limiting_returns_429(self, client, countries_user):
        """Verify the endpoint enforces rate limiting.
        Confirms excessive requests receive a 429 response."""
        client.get("/api/v1/libraries/countries/")

        response = client.get("/api/v1/libraries/countries/")

        assert response.status_code == 429

    def test_cache_control_header(self, client, countries_user):
        """Verify GET /libraries/countries/ returns 1-hour public cache headers.
        Confirms the country list has a long TTL since it changes infrequently."""
        _create_library(user=countries_user, country="IT", city="Florence")

        response = client.get("/api/v1/libraries/countries/")

        assert response.status_code == 200
        cc = response["Cache-Control"]
        assert "public" in cc
        assert "max-age=3600" in cc
        assert "s-maxage=3600" in cc
