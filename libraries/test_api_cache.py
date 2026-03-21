import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point

from libraries.models import Library

User = get_user_model()


@pytest.fixture
def cache_user(db):
    """Create a user for cache header test fixtures.
    Provides a baseline creator for library objects."""
    return User.objects.create_user(
        username="cacheuser",
        password="cachepass123",
    )


@pytest.fixture
def approved_library(cache_user):
    """Create an approved library for cache header tests.
    Provides a retrievable library for GET endpoint assertions."""
    return Library.objects.create(
        name="Cache Test Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city="Florence",
        country="IT",
        status=Library.Status.APPROVED,
        created_by=cache_user,
    )


@pytest.mark.django_db
class TestCacheControlHeaders:
    """Tests for Cache-Control headers on read-only API endpoints."""

    def test_list_libraries_cache_control(self, client, approved_library):
        """Verify GET /libraries/ returns 2-minute public cache headers.
        Confirms clients and proxies receive correct caching directives."""
        response = client.get("/api/v1/libraries/")

        assert response.status_code == 200
        cc = response["Cache-Control"]
        assert "public" in cc
        assert "max-age=120" in cc
        assert "s-maxage=120" in cc

    def test_latest_libraries_cache_control(self, client, approved_library):
        """Verify GET /libraries/latest returns 5-minute public cache headers.
        Confirms the latest endpoint has a longer TTL than list."""
        response = client.get("/api/v1/libraries/latest")

        assert response.status_code == 200
        cc = response["Cache-Control"]
        assert "public" in cc
        assert "max-age=300" in cc
        assert "s-maxage=300" in cc

    def test_library_detail_cache_control(self, client, approved_library):
        """Verify GET /libraries/{slug} returns 5-minute public cache headers.
        Confirms individual library responses are cacheable."""
        response = client.get(f"/api/v1/libraries/{approved_library.slug}")

        assert response.status_code == 200
        cc = response["Cache-Control"]
        assert "public" in cc
        assert "max-age=300" in cc
        assert "s-maxage=300" in cc

    def test_statistics_cache_control(self, client):
        """Verify GET /statistics/ returns 15-minute public cache headers.
        Confirms the statistics endpoint has the longest TTL."""
        response = client.get("/api/v1/statistics/")

        assert response.status_code == 200
        cc = response["Cache-Control"]
        assert "public" in cc
        assert "max-age=900" in cc
        assert "s-maxage=900" in cc
