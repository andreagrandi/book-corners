from unittest.mock import patch

import pytest
from django.contrib.gis.geos import Point
from django.urls import reverse
from django.utils import timezone

from libraries.models import (
    Library,
    OpenStreetMapContribution,
    OpenStreetMapContributionEvent,
)


def _create_admin_candidate(
    *,
    user,
    name: str,
    longitude: float,
) -> Library:
    """Create an approved opted-in direct submission for admin tests.
    Uses distinct names and coordinates to keep generated slugs stable."""
    return Library.objects.create(
        name=name,
        location=Point(x=longitude, y=43.7696, srid=4326),
        address="Via Roma 5",
        city="Florence",
        country="IT",
        status=Library.Status.APPROVED,
        created_by=user,
        submission_origin=Library.SubmissionOrigin.USER,
        osm_submission_allowed=True,
        osm_submission_allowed_at=timezone.now(),
    )


@pytest.mark.django_db
class TestOpenStreetMapAdminFilters:
    """Tests for operator-facing OSM candidate filters and explanations."""

    def test_osm_state_filters_group_unknown_absent_duplicate_and_present(
        self,
        admin_client,
        user,
    ) -> None:
        """Verify admin state filters expose all required workflow categories.
        Keeps missing state separate from checked and linked records."""
        unknown = _create_admin_candidate(
            user=user,
            name="Unknown candidate",
            longitude=11.2500,
        )
        absent = _create_admin_candidate(
            user=user,
            name="Absent candidate",
            longitude=11.2510,
        )
        possible = _create_admin_candidate(
            user=user,
            name="Duplicate candidate",
            longitude=11.2520,
        )
        present = _create_admin_candidate(
            user=user,
            name="Present candidate",
            longitude=11.2530,
        )
        OpenStreetMapContribution.objects.create(
            library=absent,
            status=OpenStreetMapContribution.Status.NO_MATCH,
            checked_at=timezone.now(),
        )
        OpenStreetMapContribution.objects.create(
            library=possible,
            status=OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE,
        )
        OpenStreetMapContribution.objects.create(
            library=present,
            status=OpenStreetMapContribution.Status.ALREADY_PRESENT,
            osm_element_type=OpenStreetMapContribution.ElementType.NODE,
            osm_element_id=1001,
        )
        url = reverse("admin:libraries_library_changelist")

        expected_ids = {
            "unknown": {unknown.pk},
            "absent": {absent.pk},
            "possible_duplicate": {possible.pk},
            "present": {present.pk},
        }
        for filter_value, ids in expected_ids.items():
            response = admin_client.get(url, {"osm_state": filter_value})
            result_ids = set(response.context["cl"].queryset.values_list("pk", flat=True))
            assert result_ids == ids

    def test_opt_in_and_provenance_filters_are_available(
        self,
        admin_client,
        user,
    ) -> None:
        """Verify built-in admin filters expose permission and provenance.
        Lets staff narrow the candidate list without inferring consent."""
        opted_in = _create_admin_candidate(
            user=user,
            name="Opted-in candidate",
            longitude=11.2540,
        )
        Library.objects.create(
            name="Legacy record",
            location=Point(x=11.2550, y=43.7696, srid=4326),
            city="Florence",
            country="IT",
        )
        url = reverse("admin:libraries_library_changelist")

        response = admin_client.get(url, {
            "osm_submission_allowed__exact": "1",
            "submission_origin__exact": Library.SubmissionOrigin.USER,
        })

        result_ids = set(response.context["cl"].queryset.values_list("pk", flat=True))
        assert result_ids == {opted_in.pk}

    def test_change_form_explains_exact_ineligibility_reason(
        self,
        admin_client,
        user,
    ) -> None:
        """Verify the Library admin renders the shared blocker explanation.
        Gives staff a precise reason rather than a generic disabled state."""
        library = _create_admin_candidate(
            user=user,
            name="Pending candidate",
            longitude=11.2560,
        )
        library.status = Library.Status.PENDING
        library.save(update_fields=["status", "updated_at"])
        url = reverse("admin:libraries_library_change", args=[library.pk])

        response = admin_client.get(url)

        assert response.status_code == 200
        assert b"The library is not approved." in response.content

    def test_change_form_shows_guarded_osm_actions(
        self,
        admin_client,
        user,
    ) -> None:
        """Verify eligible records expose check and withdrawal controls.
        Adds resolution only while a possible duplicate is unresolved."""
        library = _create_admin_candidate(
            user=user,
            name="Action candidate",
            longitude=11.2570,
        )
        url = reverse("admin:libraries_library_change", args=[library.pk])

        response = admin_client.get(url)

        assert b"Check OSM duplicates" in response.content
        assert b"Withdraw OSM permission" in response.content
        assert b"Resolve OSM duplicate" not in response.content

        OpenStreetMapContribution.objects.create(
            library=library,
            status=OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE,
            duplicate_candidates=[{
                "element_type": "node",
                "element_id": 1002,
                "distance_meters": 8.0,
                "tags": {"amenity": "public_bookcase"},
                "signals": ["nearby_public_bookcase"],
            }],
        )
        response = admin_client.get(url)
        assert b"Resolve OSM duplicate" in response.content


@pytest.mark.django_db
class TestOpenStreetMapAdminActions:
    """Tests for guarded OSM check, resolution, and withdrawal endpoints."""

    def test_check_action_records_no_match_without_an_osm_write(
        self,
        admin_client,
        admin_user,
        user,
        settings,
    ) -> None:
        """Verify the admin check action persists a read-only result.
        Uses a fake payload and leaves all write audit fields empty."""
        settings.OSM_OVERPASS_URL = "https://overpass.test/api/interpreter"
        settings.OSM_USER_AGENT = "book-corners-tests/1.0"
        settings.OSM_DUPLICATE_RADIUS_METERS = 100
        library = _create_admin_candidate(
            user=user,
            name="Checked candidate",
            longitude=11.2580,
        )
        url = reverse(
            "admin:libraries_library_osm_check",
            args=[library.pk],
        )

        with patch(
            "libraries.osm_contributions._fetch_overpass_payload",
            return_value={"elements": []},
        ):
            response = admin_client.post(url)

        assert response.status_code == 302
        contribution = library.osm_contribution
        assert contribution.status == OpenStreetMapContribution.Status.NO_MATCH
        assert contribution.osm_element_id is None
        assert contribution.changeset_id is None
        assert contribution.contributed_at is None
        assert contribution.contributed_by is None
        event = contribution.events.get()
        assert event.actor == admin_user
        assert event.event_type == OpenStreetMapContributionEvent.EventType.DUPLICATE_CHECK

    def test_resolution_page_records_different_feature_reason(
        self,
        admin_client,
        admin_user,
        user,
    ) -> None:
        """Verify the admin resolution form records a staff explanation.
        Returns the candidate to unchecked so a fresh check is mandatory."""
        library = _create_admin_candidate(
            user=user,
            name="Resolution candidate",
            longitude=11.2590,
        )
        contribution = OpenStreetMapContribution.objects.create(
            library=library,
            status=OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE,
            duplicate_candidates=[{
                "element_type": "node",
                "element_id": 1003,
                "distance_meters": 9.0,
                "tags": {"amenity": "public_bookcase"},
                "signals": ["nearby_public_bookcase"],
            }],
        )
        url = reverse(
            "admin:libraries_library_osm_resolve",
            args=[library.pk],
        )

        get_response = admin_client.get(url)
        response = admin_client.post(url, {
            "candidate": "node:1003",
            "resolution": "different_feature",
            "reason": "A wall separates the two independently stocked bookcases.",
        })

        assert get_response.status_code == 200
        assert get_response.context["candidates"][0]["element_id"] == 1003
        assert b"No blocking candidate is available to resolve." not in get_response.content
        assert b"node/1003" in get_response.content
        assert response.status_code == 302
        contribution.refresh_from_db()
        assert contribution.status == OpenStreetMapContribution.Status.UNCHECKED
        event = contribution.events.get()
        assert event.actor == admin_user
        assert event.details == {
            "resolution": "different_feature",
            "element_type": "node",
            "element_id": 1003,
            "reason": "A wall separates the two independently stocked bookcases.",
        }

    def test_resolution_rejects_a_candidate_not_in_the_snapshot(
        self,
        admin_client,
        user,
    ) -> None:
        """Verify forged candidate identifiers cannot be resolved.
        Keeps the stored possible-duplicate warning unchanged."""
        library = _create_admin_candidate(
            user=user,
            name="Forged resolution candidate",
            longitude=11.2600,
        )
        contribution = OpenStreetMapContribution.objects.create(
            library=library,
            status=OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE,
            duplicate_candidates=[{
                "element_type": "node",
                "element_id": 1004,
                "distance_meters": 7.0,
                "tags": {"amenity": "public_bookcase"},
                "signals": ["nearby_public_bookcase"],
            }],
        )
        url = reverse(
            "admin:libraries_library_osm_resolve",
            args=[library.pk],
        )

        response = admin_client.post(url, {
            "candidate": "node:9999",
            "resolution": "same_feature",
            "reason": "Forged value",
        })

        assert response.status_code == 200
        assert b"not current" in response.content
        contribution.refresh_from_db()
        assert (
            contribution.status
            == OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
        )
        assert contribution.events.count() == 0

    def test_withdraw_action_clears_permission_and_records_actor(
        self,
        admin_client,
        admin_user,
        user,
    ) -> None:
        """Verify the guarded admin withdrawal is one-way and audited.
        Confirms generic model editing is not needed for consent removal."""
        library = _create_admin_candidate(
            user=user,
            name="Withdrawal candidate",
            longitude=11.2610,
        )
        url = reverse(
            "admin:libraries_library_osm_withdraw",
            args=[library.pk],
        )

        response = admin_client.post(url)

        assert response.status_code == 302
        library.refresh_from_db()
        assert library.osm_submission_allowed is False
        assert library.osm_submission_allowed_at is None
        event = library.osm_contribution.events.get()
        assert event.actor == admin_user
        assert (
            event.event_type
            == OpenStreetMapContributionEvent.EventType.PERMISSION_WITHDRAWN
        )
