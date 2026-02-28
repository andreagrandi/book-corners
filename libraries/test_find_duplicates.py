"""Tests for the find_duplicates management command and admin view.

Covers address-based grouping, proximity grouping, group merging,
filtering, auto-delete behavior, and the admin duplicate finder page.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.urls import reverse

from libraries.management.commands.find_duplicates import find_duplicate_groups
from libraries.models import Library

User = get_user_model()


@pytest.fixture
def lib_user(db):
    """Create a user for library ownership in tests.
    Provides the required FK for Library records."""
    return User.objects.create_user(username="libuser", password="testpass123")


def _create_library(user, *, name="Test", address="Via Roma 5", city="Florence",
                    country="IT", lon=11.25, lat=43.77, external_id="", status="approved"):
    """Create a library with sensible defaults for duplicate detection tests.
    Returns the created Library instance."""
    return Library.objects.create(
        name=name,
        location=Point(x=lon, y=lat, srid=4326),
        address=address,
        city=city,
        country=country,
        external_id=external_id,
        status=status,
        created_by=user,
    )


@pytest.mark.django_db
class TestFindDuplicateGroups:
    """Tests for the find_duplicate_groups function."""

    def test_address_match_grouping(self, lib_user):
        """Verify libraries with identical city+address form a group.
        The most common duplicate scenario across OSM exports."""
        _create_library(lib_user, name="A", address="Via Roma 5", city="Florence", lon=11.0, lat=43.0)
        _create_library(lib_user, name="B", address="Via Roma 5", city="Florence", lon=12.0, lat=44.0)

        groups = find_duplicate_groups()

        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_address_match_case_insensitive(self, lib_user):
        """Verify address matching ignores case.
        Different OSM tools may export addresses with different casing."""
        _create_library(lib_user, name="A", address="VIA ROMA 5", city="FLORENCE", lon=11.0, lat=43.0)
        _create_library(lib_user, name="B", address="via roma 5", city="florence", lon=12.0, lat=44.0)

        groups = find_duplicate_groups()

        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_proximity_grouping(self, lib_user):
        """Verify nearby libraries with different addresses form a group.
        Catches duplicates with slightly different address text."""
        _create_library(lib_user, name="A", address="Via Roma 5", city="Florence", lon=11.25, lat=43.77)
        _create_library(lib_user, name="B", address="Via Roma 5/A", city="Florence", lon=11.2501, lat=43.7701)

        groups = find_duplicate_groups()

        assert len(groups) == 1

    def test_group_merging(self, lib_user):
        """Verify overlapping matches merge into a single group.
        If A matches B by address and B matches C by proximity, all three group."""
        _create_library(lib_user, name="A", address="Via Test 1", city="Rome", lon=12.49, lat=41.89, external_id="a")
        _create_library(lib_user, name="B", address="Via Test 1", city="Rome", lon=12.4901, lat=41.8901, external_id="b")
        _create_library(lib_user, name="C", address="Via Test 1bis", city="Rome", lon=12.4902, lat=41.8901, external_id="c")

        groups = find_duplicate_groups()

        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_different_city_not_grouped(self, lib_user):
        """Verify same street in different cities are not grouped.
        Prevents false positives from common street names."""
        _create_library(lib_user, name="A", address="Via Roma 5", city="Florence", lon=11.25, lat=43.77)
        _create_library(lib_user, name="B", address="Via Roma 5", city="Milan", lon=9.19, lat=45.46)

        groups = find_duplicate_groups()

        assert len(groups) == 0

    def test_far_apart_not_grouped(self, lib_user):
        """Verify libraries far apart with different addresses are not grouped.
        Only genuine duplicates should be flagged."""
        _create_library(lib_user, name="A", address="Via Roma 5", city="Florence", lon=11.25, lat=43.77)
        _create_library(lib_user, name="B", address="Via Milano 10", city="Florence", lon=11.30, lat=43.80)

        groups = find_duplicate_groups()

        assert len(groups) == 0

    def test_city_filter(self, lib_user):
        """Verify city filter restricts detection scope.
        Allows targeted scanning of specific cities."""
        _create_library(lib_user, name="A", address="Via Roma 5", city="Florence", lon=11.0, lat=43.0)
        _create_library(lib_user, name="B", address="Via Roma 5", city="Florence", lon=12.0, lat=44.0)
        _create_library(lib_user, name="C", address="Via Milano 1", city="Milan", lon=9.0, lat=45.0)
        _create_library(lib_user, name="D", address="Via Milano 1", city="Milan", lon=9.5, lat=45.5)

        groups = find_duplicate_groups(city="Florence")

        assert len(groups) == 1
        cities = {lib.city for lib in groups[0]}
        assert cities == {"Florence"}

    def test_country_filter(self, lib_user):
        """Verify country filter restricts detection scope.
        Allows targeted scanning of specific countries."""
        _create_library(lib_user, name="A", address="Via Roma 5", city="Florence", country="IT", lon=11.0, lat=43.0)
        _create_library(lib_user, name="B", address="Via Roma 5", city="Florence", country="IT", lon=12.0, lat=44.0)
        _create_library(lib_user, name="C", address="Rue de Paris 1", city="Paris", country="FR", lon=2.0, lat=48.0)
        _create_library(lib_user, name="D", address="Rue de Paris 1", city="Paris", country="FR", lon=2.5, lat=48.5)

        groups = find_duplicate_groups(country="IT")

        assert len(groups) == 1

    def test_no_duplicates_returns_empty(self, lib_user):
        """Verify unique libraries produce no groups.
        Avoids false positives in clean datasets."""
        _create_library(lib_user, name="A", address="Via Roma 5", city="Florence", lon=11.25, lat=43.77)
        _create_library(lib_user, name="B", address="Via Milano 10", city="Milan", lon=9.19, lat=45.46)

        groups = find_duplicate_groups()

        assert len(groups) == 0

    def test_auto_delete_keeps_oldest(self, lib_user):
        """Verify deletion removes newer duplicates and keeps the oldest.
        Simulates the auto-delete workflow from the management command."""
        lib_a = _create_library(lib_user, name="Oldest", address="Via Roma 5", city="Florence", lon=11.0, lat=43.0)
        _create_library(lib_user, name="Newer", address="Via Roma 5", city="Florence", lon=12.0, lat=44.0)

        groups = find_duplicate_groups()
        assert len(groups) == 1

        # Simulate auto-delete: keep first (oldest), delete rest
        for group in groups:
            for lib in group[1:]:
                lib.delete()

        assert Library.objects.count() == 1
        assert Library.objects.first().pk == lib_a.pk


@pytest.mark.django_db
class TestAdminFindDuplicatesView:
    """Tests for the admin find duplicates view."""

    def test_get_returns_200_for_admin(self, admin_client):
        """Verify the find duplicates page loads for admin users.
        Ensures the custom admin URL is correctly registered."""
        url = reverse("admin:libraries_library_find_duplicates")

        response = admin_client.get(url)

        assert response.status_code == 200

    def test_get_rejects_anonymous(self, client):
        """Verify anonymous users cannot access the find duplicates page.
        Confirms admin-only access control is enforced."""
        url = reverse("admin:libraries_library_find_duplicates")

        response = client.get(url)

        assert response.status_code == 302

    def test_scan_shows_results(self, admin_client, admin_user):
        """Verify scanning returns grouped duplicate results.
        End-to-end test of the admin duplicate finder."""
        _create_library(admin_user, name="A", address="Via Roma 5", city="Florence", lon=11.0, lat=43.0)
        _create_library(admin_user, name="B", address="Via Roma 5", city="Florence", lon=12.0, lat=44.0)
        url = reverse("admin:libraries_library_find_duplicates")

        response = admin_client.get(url, {"radius": "100", "city": "", "country": ""})

        assert response.status_code == 200
        assert response.context["scanned"] is True
        assert len(response.context["groups"]) == 1

    def test_post_deletes_selected(self, admin_client, admin_user):
        """Verify POST with delete_ids removes the selected libraries.
        Tests the deletion workflow from the admin page."""
        lib_a = _create_library(admin_user, name="Keep", address="Via Roma 5", city="Florence", lon=11.0, lat=43.0)
        lib_b = _create_library(admin_user, name="Delete", address="Via Roma 5", city="Florence", lon=12.0, lat=44.0)
        url = reverse("admin:libraries_library_find_duplicates")

        response = admin_client.post(url, {"delete_ids": [str(lib_b.pk)]})

        assert response.status_code == 200
        assert Library.objects.count() == 1
        assert Library.objects.first().pk == lib_a.pk

    def test_find_duplicates_link_on_changelist(self, admin_client):
        """Verify the Find Duplicates link appears on the library changelist.
        Ensures admins can discover the feature."""
        url = reverse("admin:libraries_library_changelist")

        response = admin_client.get(url)

        assert response.status_code == 200
        assert b"Find Duplicates" in response.content
