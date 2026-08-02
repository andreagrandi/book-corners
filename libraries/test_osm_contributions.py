from datetime import timedelta

import httpx
import pytest
from django.contrib.gis.geos import Point
from django.db import IntegrityError
from django.test import override_settings
from django.utils import timezone

from libraries.models import (
    Library,
    OpenStreetMapContribution,
    OpenStreetMapContributionEvent,
)
from libraries.osm_contributions import (
    resolve_osm_duplicate,
    run_osm_duplicate_check,
    withdraw_osm_submission_permission,
)

OSM_TEST_SETTINGS = {
    "OSM_OVERPASS_URL": "https://overpass.test/api/interpreter",
    "OSM_USER_AGENT": "book-corners-tests/1.0 (https://example.test/contact)",
    "OSM_DUPLICATE_RADIUS_METERS": 100,
    "OSM_DUPLICATE_CHECK_MAX_AGE_SECONDS": 900,
}


@pytest.fixture
def eligible_osm_library(user) -> Library:
    """Create an approved opted-in direct user submission.
    Provides the valid baseline for OSM eligibility and checker tests."""
    return Library.objects.create(
        name="Giardino dei Libri",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Roma 5",
        city="Florence",
        country="IT",
        status=Library.Status.APPROVED,
        created_by=user,
        submission_origin=Library.SubmissionOrigin.USER,
        osm_submission_allowed=True,
        osm_submission_allowed_at=timezone.now(),
    )


@pytest.fixture
def configured_osm_settings(settings) -> None:
    """Configure the fake Overpass endpoint for checker tests.
    Keeps test traffic inside an injected HTTPX mock transport."""
    for setting_name, value in OSM_TEST_SETTINGS.items():
        setattr(settings, setting_name, value)


def _client_for_elements(
    *,
    elements: list[dict],
    requests: list[httpx.Request] | None = None,
) -> httpx.Client:
    """Build a fake HTTPX client returning an Overpass-style payload.
    Optionally captures requests for transport-boundary assertions."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return the configured fake Overpass response.
        Keeps every test isolated from production OpenStreetMap services."""
        if requests is not None:
            requests.append(request)
        return httpx.Response(
            status_code=200,
            json={"elements": elements},
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.django_db
class TestOpenStreetMapEligibility:
    """Tests for shared OpenStreetMap eligibility safeguards."""

    @pytest.mark.parametrize(
        ("changes", "expected_reason"),
        [
            (
                {"status": Library.Status.PENDING},
                "The library is not approved.",
            ),
            (
                {"submission_origin": Library.SubmissionOrigin.IMPORT},
                "The library was not submitted directly by a user.",
            ),
            (
                {"created_by": None},
                "The original submitter account is no longer linked.",
            ),
            (
                {
                    "osm_submission_allowed": False,
                    "osm_submission_allowed_at": None,
                },
                "The submitter did not allow OpenStreetMap submission.",
            ),
            (
                {"source": Library.OPENSTREETMAP_SOURCE},
                "The library originated from OpenStreetMap.",
            ),
            (
                {"pending_changes": {"name": "Updated name"}},
                "The library has an update awaiting moderation.",
            ),
        ],
    )
    def test_precheck_reports_the_first_exact_blocker(
        self,
        eligible_osm_library: Library,
        changes: dict,
        expected_reason: str,
    ) -> None:
        """Verify each durable eligibility condition has a clear reason.
        Keeps admin explanations aligned with the guarded checker."""
        for field_name, value in changes.items():
            setattr(eligible_osm_library, field_name, value)

        reason = eligible_osm_library.osm_precheck_ineligibility_reason()

        assert reason == expected_reason

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("source", "OSM"),
            ("source", "openstreetmap"),
            ("source", "OpenStreetMap import"),
            ("external_id", "node/12345"),
        ],
    )
    def test_osm_origin_requires_the_exact_canonical_source(
        self,
        eligible_osm_library: Library,
        field_name: str,
        value: str,
    ) -> None:
        """Verify similar values do not infer OpenStreetMap provenance.
        Keeps classification independent from aliases and external identifiers."""
        setattr(eligible_osm_library, field_name, value)

        assert eligible_osm_library._has_osm_origin() is False

    def test_contribution_requires_a_fresh_successful_check(
        self,
        eligible_osm_library: Library,
    ) -> None:
        """Verify unchecked and stale duplicate states block contribution.
        Allows only a recent successful no-match state through."""
        assert (
            eligible_osm_library.osm_contribution_ineligibility_reason(
                max_age_seconds=900
            )
            == "An OpenStreetMap duplicate check has not been completed."
        )
        contribution = OpenStreetMapContribution.objects.create(
            library=eligible_osm_library,
            status=OpenStreetMapContribution.Status.NO_MATCH,
            checked_at=timezone.now() - timedelta(seconds=901),
        )

        assert (
            eligible_osm_library.osm_contribution_ineligibility_reason(
                max_age_seconds=900
            )
            == "The latest OpenStreetMap duplicate check is stale."
        )

        contribution.checked_at = timezone.now()
        contribution.save(update_fields=["checked_at"])
        assert (
            eligible_osm_library.osm_contribution_ineligibility_reason(
                max_age_seconds=900
            )
            is None
        )

    def test_linked_osm_element_blocks_another_precheck(
        self,
        eligible_osm_library: Library,
    ) -> None:
        """Verify a recorded OSM feature makes the library ineligible.
        Prevents already-present or contributed records from re-entering checks."""
        OpenStreetMapContribution.objects.create(
            library=eligible_osm_library,
            status=OpenStreetMapContribution.Status.ALREADY_PRESENT,
            osm_element_type=OpenStreetMapContribution.ElementType.NODE,
            osm_element_id=123,
        )

        reason = eligible_osm_library.osm_precheck_ineligibility_reason()

        assert reason == "An existing OpenStreetMap feature is already linked."


@pytest.mark.django_db
class TestOpenStreetMapStateModels:
    """Tests for current-state integrity and append-only audit records."""

    def test_osm_element_link_is_unique_across_libraries(
        self,
        eligible_osm_library: Library,
        user,
    ) -> None:
        """Verify one OSM feature cannot satisfy two Book Corners libraries.
        Enforces the cross-library idempotency boundary in the database."""
        OpenStreetMapContribution.objects.create(
            library=eligible_osm_library,
            status=OpenStreetMapContribution.Status.ALREADY_PRESENT,
            osm_element_type=OpenStreetMapContribution.ElementType.NODE,
            osm_element_id=321,
        )
        other_library = Library.objects.create(
            location=Point(x=11.26, y=43.77, srid=4326),
            city="Florence",
            country="IT",
            created_by=user,
        )

        with pytest.raises(IntegrityError):
            OpenStreetMapContribution.objects.create(
                library=other_library,
                status=OpenStreetMapContribution.Status.ALREADY_PRESENT,
                osm_element_type=OpenStreetMapContribution.ElementType.NODE,
                osm_element_id=321,
            )

    def test_audit_event_rejects_updates_and_direct_deletion(
        self,
        eligible_osm_library: Library,
        user,
    ) -> None:
        """Verify audit events are immutable after their initial insert.
        Preserves staff history through normal model operations."""
        contribution = OpenStreetMapContribution.objects.create(
            library=eligible_osm_library
        )
        event = OpenStreetMapContributionEvent.objects.create(
            contribution=contribution,
            event_type=OpenStreetMapContributionEvent.EventType.DUPLICATE_CHECK,
            outcome=OpenStreetMapContributionEvent.Outcome.SUCCESS,
            actor=user,
            details={"candidates": []},
        )

        event.details = {"changed": True}
        with pytest.raises(ValueError, match="immutable"):
            event.save()
        with pytest.raises(ValueError, match="cannot be deleted"):
            event.delete()


@pytest.mark.django_db
@pytest.mark.usefixtures("configured_osm_settings")
class TestOpenStreetMapDuplicateChecker:
    """Tests for the read-only OpenStreetMap duplicate checker."""

    def test_no_match_records_successful_check_and_audit_event(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify an empty Overpass result records a current no-match state.
        Confirms the request remains a read-only nearby-feature query."""
        captured_requests: list[httpx.Request] = []
        client = _client_for_elements(
            elements=[],
            requests=captured_requests,
        )

        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=client,
            sleep=lambda delay: None,
        )

        assert contribution.status == OpenStreetMapContribution.Status.NO_MATCH
        assert contribution.checked_at is not None
        assert contribution.duplicate_candidates == []
        assert contribution.events.count() == 1
        event = contribution.events.get()
        assert event.event_type == OpenStreetMapContributionEvent.EventType.DUPLICATE_CHECK
        assert event.outcome == OpenStreetMapContributionEvent.Outcome.SUCCESS
        assert len(captured_requests) == 1
        request = captured_requests[0]
        assert request.method == "POST"
        assert request.url == httpx.URL(OSM_TEST_SETTINGS["OSM_OVERPASS_URL"])
        assert request.headers["user-agent"] == OSM_TEST_SETTINGS["OSM_USER_AGENT"]
        assert b"nwr%28around%3A100%2C43.7696000%2C11.2558000%29" in request.content
        assert b"public_bookcase" in request.content

    def test_nearby_public_bookcase_records_sanitized_blocking_warning(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify close public bookcases become possible duplicates.
        Stores allowlisted public tags while excluding contact and image data."""
        client = _client_for_elements(elements=[{
            "type": "node",
            "id": 101,
            "lat": 43.76965,
            "lon": 11.2558,
            "tags": {
                "amenity": "public_bookcase",
                "name": "Another Bookcase",
                "contact:email": "private@example.test",
                "image": "https://example.test/photo.jpg",
            },
        }])

        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=client,
            sleep=lambda delay: None,
        )

        assert (
            contribution.status
            == OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
        )
        assert contribution.duplicate_candidates == [{
            "element_type": "node",
            "element_id": 101,
            "distance_meters": pytest.approx(5.6, abs=0.2),
            "tags": {
                "amenity": "public_bookcase",
                "name": "Another Bookcase",
            },
            "signals": ["nearby_public_bookcase"],
        }]
        stored_details = contribution.events.get().details
        assert "private@example.test" not in str(stored_details)
        assert "photo.jpg" not in str(stored_details)

    def test_name_address_and_operator_signals_are_detected(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify normalized name, structured address, and operator matching.
        Covers the design signals beyond the close-distance rule."""
        eligible_osm_library.operator = "Comune di Firenze"
        eligible_osm_library.save(update_fields=["operator", "updated_at"])
        client = _client_for_elements(elements=[{
            "type": "way",
            "id": 202,
            "center": {"lat": 43.7700, "lon": 11.2558},
            "tags": {
                "amenity": "public_bookcase",
                "name": "GIARDINO DEI LIBRI!",
                "addr:street": "Via Roma",
                "addr:housenumber": "5",
                "operator": "comune di firenze",
            },
        }])

        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=client,
            sleep=lambda delay: None,
        )

        candidate = contribution.duplicate_candidates[0]
        assert candidate["signals"] == [
            "same_name",
            "same_address",
            "same_brand_or_operator",
        ]

    def test_secondary_feature_is_kept_as_nonblocking_warning(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify nearby libraries remain visible without proving a duplicate.
        Keeps missing tags from becoming false positive evidence."""
        client = _client_for_elements(elements=[{
            "type": "relation",
            "id": 303,
            "center": {"lat": 43.7697, "lon": 11.2558},
            "tags": {"amenity": "library", "name": "Public Library"},
        }])

        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=client,
            sleep=lambda delay: None,
        )

        assert contribution.status == OpenStreetMapContribution.Status.NO_MATCH
        assert contribution.duplicate_candidates[0]["signals"] == []

    def test_existing_link_on_another_library_is_a_blocking_signal(
        self,
        eligible_osm_library: Library,
        admin_user,
        user,
    ) -> None:
        """Verify linked OSM identifiers warn even without matching tags.
        Prevents one OSM feature from being reused across library records."""
        existing_library = Library.objects.create(
            location=Point(x=11.0, y=43.0, srid=4326),
            city="Florence",
            country="IT",
            created_by=user,
        )
        OpenStreetMapContribution.objects.create(
            library=existing_library,
            status=OpenStreetMapContribution.Status.ALREADY_PRESENT,
            osm_element_type=OpenStreetMapContribution.ElementType.WAY,
            osm_element_id=404,
        )
        client = _client_for_elements(elements=[{
            "type": "way",
            "id": 404,
            "center": {"lat": 43.7697, "lon": 11.2558},
            "tags": {"amenity": "library"},
        }])

        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=client,
            sleep=lambda delay: None,
        )

        assert (
            contribution.status
            == OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
        )
        assert contribution.duplicate_candidates[0]["signals"] == ["already_linked"]

    def test_failed_check_retries_and_preserves_previous_candidates(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify transient failures retry and do not erase warning evidence.
        Stores only a stable safe failure category after bounded attempts."""
        contribution = OpenStreetMapContribution.objects.create(
            library=eligible_osm_library,
            status=OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE,
            duplicate_candidates=[{
                "element_type": "node",
                "element_id": 505,
                "distance_meters": 10.0,
                "tags": {"amenity": "public_bookcase"},
                "signals": ["nearby_public_bookcase"],
            }],
        )
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            """Return a retryable fake server failure.
            Lets the test assert bounded attempts without external traffic."""
            nonlocal request_count
            request_count += 1
            return httpx.Response(status_code=503, request=request)

        delays: list[float] = []
        client = httpx.Client(transport=httpx.MockTransport(handler))

        updated = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=client,
            sleep=delays.append,
        )

        assert updated.pk == contribution.pk
        assert updated.status == OpenStreetMapContribution.Status.FAILED
        assert updated.last_error_code == "service_error"
        assert updated.last_error_retryable is True
        assert updated.duplicate_candidates == contribution.duplicate_candidates
        assert request_count == 3
        assert delays == [1, 2]
        failure_event = updated.events.order_by("-created_at").first()
        assert failure_event is not None
        assert failure_event.details == {
            "error_code": "service_error",
            "error_message": "The OpenStreetMap duplicate service is unavailable.",
            "retryable": True,
        }

        rechecked = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=_client_for_elements(elements=[]),
            sleep=lambda delay: None,
        )
        assert (
            rechecked.status
            == OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
        )
        assert rechecked.duplicate_candidates[0]["element_id"] == 505

    def test_unresolved_warning_is_not_silently_cleared(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify a disappeared candidate remains blocked until resolution.
        Prevents a later empty response from silently removing staff review."""
        initial_client = _client_for_elements(elements=[{
            "type": "node",
            "id": 606,
            "lat": 43.76965,
            "lon": 11.2558,
            "tags": {"amenity": "public_bookcase"},
        }])
        run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=initial_client,
            sleep=lambda delay: None,
        )

        updated = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=_client_for_elements(elements=[]),
            sleep=lambda delay: None,
        )

        assert updated.status == OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
        assert updated.duplicate_candidates[0]["element_id"] == 606

    def test_overpass_remark_is_a_failure_not_a_false_no_match(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify a successful HTTP response can still report query failure.
        Prevents Overpass runtime remarks from becoming false absence evidence."""

        def handler(request: httpx.Request) -> httpx.Response:
            """Return a fake Overpass runtime-error payload.
            Reproduces a service failure delivered with HTTP 200."""
            return httpx.Response(
                status_code=200,
                json={
                    "remark": "runtime error: Query timed out",
                    "elements": [],
                },
                request=request,
            )

        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleep=lambda delay: None,
        )

        assert contribution.status == OpenStreetMapContribution.Status.FAILED
        assert contribution.last_error_code == "service_error"
        assert contribution.last_error_retryable is True
        assert "Query timed out" not in contribution.last_error_message

    def test_malformed_element_is_a_failure_not_a_false_no_match(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify incomplete nearby elements invalidate the whole check.
        Prevents malformed candidate data from becoming absence evidence."""
        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=_client_for_elements(elements=[{
                "type": "node",
                "id": 607,
                "tags": {"amenity": "public_bookcase"},
            }]),
            sleep=lambda delay: None,
        )

        assert contribution.status == OpenStreetMapContribution.Status.FAILED
        assert contribution.last_error_code == "malformed_response"
        assert contribution.last_error_retryable is False

    def test_rate_limit_respects_retry_after_before_succeeding(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify read-only retries honor a bounded Retry-After value.
        Stops after a successful response without a fourth request."""
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            """Return two rate limits followed by an empty success.
            Lets the test inspect retry timing without sleeping."""
            nonlocal request_count
            request_count += 1
            if request_count < 3:
                return httpx.Response(
                    status_code=429,
                    headers={"Retry-After": "7"},
                    request=request,
                )
            return httpx.Response(
                status_code=200,
                json={"elements": []},
                request=request,
            )

        delays: list[float] = []
        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleep=delays.append,
        )

        assert contribution.status == OpenStreetMapContribution.Status.NO_MATCH
        assert request_count == 3
        assert delays == [7.0, 7.0]

    @override_settings(OSM_OVERPASS_URL="", OSM_USER_AGENT="")
    def test_missing_configuration_records_visible_failure(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify disabled checker configuration fails safely and visibly.
        Avoids any network request when endpoint metadata is absent."""
        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=None,
            sleep=lambda delay: None,
        )

        assert contribution.status == OpenStreetMapContribution.Status.FAILED
        assert contribution.last_error_code == "not_configured"
        assert contribution.last_error_retryable is False


@pytest.mark.django_db
@pytest.mark.usefixtures("configured_osm_settings")
class TestOpenStreetMapStaffDecisions:
    """Tests for audited duplicate resolution and permission withdrawal."""

    def test_different_feature_resolution_requires_a_fresh_check(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify a rejected candidate is audited and returns to unchecked.
        A fresh check can then pass while retaining the public warning."""
        client = _client_for_elements(elements=[{
            "type": "node",
            "id": 707,
            "lat": 43.76965,
            "lon": 11.2558,
            "tags": {"amenity": "public_bookcase"},
        }])
        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=client,
            sleep=lambda delay: None,
        )

        resolved = resolve_osm_duplicate(
            contribution=contribution,
            actor=admin_user,
            resolution="different_feature",
            element_type="node",
            element_id=707,
            reason="The mapped bookcase is on the opposite side of the courtyard.",
        )

        assert resolved.status == OpenStreetMapContribution.Status.UNCHECKED
        refreshed = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=client,
            sleep=lambda delay: None,
        )
        assert refreshed.status == OpenStreetMapContribution.Status.NO_MATCH
        resolution_event = refreshed.events.filter(
            event_type=OpenStreetMapContributionEvent.EventType.DUPLICATE_RESOLUTION
        ).get()
        assert resolution_event.actor == admin_user
        assert resolution_event.details["resolution"] == "different_feature"

    def test_same_feature_resolution_links_existing_element(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify confirmed duplicate resolution records an OSM feature.
        Marks the library already present without performing an external write."""
        contribution = run_osm_duplicate_check(
            library=eligible_osm_library,
            actor=admin_user,
            client=_client_for_elements(elements=[{
                "type": "way",
                "id": 808,
                "center": {"lat": 43.76965, "lon": 11.2558},
                "tags": {"amenity": "public_bookcase"},
            }]),
            sleep=lambda delay: None,
        )

        resolved = resolve_osm_duplicate(
            contribution=contribution,
            actor=admin_user,
            resolution="same_feature",
            element_type="way",
            element_id=808,
            reason="The coordinates and physical description are identical.",
        )

        assert resolved.status == OpenStreetMapContribution.Status.ALREADY_PRESENT
        assert resolved.osm_element_type == "way"
        assert resolved.osm_element_id == 808
        assert resolved.changeset_id is None
        assert resolved.contributed_at is None

    def test_permission_withdrawal_is_one_way_and_audited(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify staff can only withdraw an unconsumed true permission.
        Clears its timestamp and records the acting administrator."""
        contribution = withdraw_osm_submission_permission(
            library=eligible_osm_library,
            actor=admin_user,
        )

        eligible_osm_library.refresh_from_db()
        assert eligible_osm_library.osm_submission_allowed is False
        assert eligible_osm_library.osm_submission_allowed_at is None
        event = contribution.events.get()
        assert (
            event.event_type
            == OpenStreetMapContributionEvent.EventType.PERMISSION_WITHDRAWN
        )
        assert event.actor == admin_user

        with pytest.raises(ValueError, match="not currently granted"):
            withdraw_osm_submission_permission(
                library=eligible_osm_library,
                actor=admin_user,
            )

    def test_permission_cannot_be_withdrawn_after_feature_link(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify a completed existing-feature link blocks withdrawal.
        Preserves the recorded permission used for the terminal decision."""
        OpenStreetMapContribution.objects.create(
            library=eligible_osm_library,
            status=OpenStreetMapContribution.Status.ALREADY_PRESENT,
            osm_element_type=OpenStreetMapContribution.ElementType.NODE,
            osm_element_id=909,
        )

        with pytest.raises(ValueError, match="after linking or submission"):
            withdraw_osm_submission_permission(
                library=eligible_osm_library,
                actor=admin_user,
            )

    def test_resolution_rejects_private_contact_details(
        self,
        eligible_osm_library: Library,
        admin_user,
    ) -> None:
        """Verify staff explanations cannot persist private contact details.
        Enforces audit sanitization in addition to the form warning."""
        contribution = OpenStreetMapContribution.objects.create(
            library=eligible_osm_library,
            status=OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE,
            duplicate_candidates=[{
                "element_type": "node",
                "element_id": 1001,
                "distance_meters": 5.0,
                "tags": {"amenity": "public_bookcase"},
                "signals": ["nearby_public_bookcase"],
            }],
        )

        with pytest.raises(ValueError, match="private contact details"):
            resolve_osm_duplicate(
                contribution=contribution,
                actor=admin_user,
                resolution="different_feature",
                element_type="node",
                element_id=1001,
                reason="Confirmed by private-owner@example.test",
            )

        assert contribution.events.count() == 0
