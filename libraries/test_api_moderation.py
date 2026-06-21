import pytest
from django.contrib.gis.geos import Point
from django.core.cache import cache
from ninja_jwt.tokens import RefreshToken

from libraries.models import Library, LibraryPhoto, Report


def _auth_header(*, user):
    """Build a Bearer token header for a user.
    Generates a valid JWT access token for authenticated API calls."""
    access_token = str(RefreshToken.for_user(user).access_token)
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}


@pytest.fixture
def pending_library(user):
    """Create a pending library for moderation API tests.
    Provides a staff-visible submission awaiting review."""
    return Library.objects.create(
        name="Pending Moderation Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Moderazione 15",
        city="Florence",
        country="IT",
        source="manual",
        status=Library.Status.PENDING,
        created_by=user,
    )


@pytest.fixture
def approved_library(user):
    """Create an approved library for moderation API tests.
    Provides a live entry for all-status listing and reports."""
    return Library.objects.create(
        name="Approved Moderation Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=2.3522, y=48.8566, srid=4326),
        address="1 Rue Approved",
        city="Paris",
        country="FR",
        source="OpenStreetMap",
        status=Library.Status.APPROVED,
        created_by=user,
    )


@pytest.fixture
def rejected_library(user):
    """Create a rejected library for all-status moderation tests.
    Ensures staff lists can include entries hidden from public APIs."""
    return Library.objects.create(
        name="Rejected Moderation Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=13.405, y=52.52, srid=4326),
        address="Unter den Rejects 1",
        city="Berlin",
        country="DE",
        status=Library.Status.REJECTED,
        created_by=user,
    )


@pytest.fixture
def open_report(user, approved_library):
    """Create an open user report for moderation API tests.
    Provides report data that staff clients can list and update."""
    return Report.objects.create(
        library=approved_library,
        created_by=user,
        reason=Report.Reason.DAMAGED,
        details="The door hinge is broken.",
        status=Report.Status.OPEN,
    )


@pytest.fixture
def dismissed_report(user, approved_library):
    """Create a dismissed report for report filtering tests.
    Ensures status filters can exclude non-open reports."""
    return Report.objects.create(
        library=approved_library,
        created_by=user,
        reason=Report.Reason.OTHER,
        details="Already handled.",
        status=Report.Status.DISMISSED,
    )


@pytest.fixture
def pending_photo(user, approved_library):
    """Create a pending community photo for moderation API tests.
    Provides a photo submission that staff clients can approve or reject."""
    return LibraryPhoto.objects.create(
        library=approved_library,
        created_by=user,
        photo="libraries/user_photos/2026/02/photo.jpg",
        caption="Front view",
        status=LibraryPhoto.Status.PENDING,
    )


@pytest.fixture
def rejected_photo(user, approved_library):
    """Create a rejected community photo for photo filtering tests.
    Ensures status filters can exclude rejected photo submissions."""
    return LibraryPhoto.objects.create(
        library=approved_library,
        created_by=user,
        photo="libraries/user_photos/2026/02/rejected.jpg",
        caption="Blurry view",
        status=LibraryPhoto.Status.REJECTED,
    )


@pytest.mark.django_db
class TestModerationSummaryEndpoint:
    """Tests for GET /api/v1/libraries/moderation/summary.
    Covers staff-only access and dashboard count fields."""

    def setup_method(self):
        """Clear cache state before each moderation summary test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_non_staff_receives_403(self, client, user):
        """Verify regular users cannot view moderation summary counts.
        Staff dashboard data should stay restricted to staff accounts."""
        response = client.get(
            "/api/v1/libraries/moderation/summary",
            **_auth_header(user=user),
        )

        assert response.status_code == 403
        assert response.json()["message"] == "Staff access required."

    def test_staff_receives_dashboard_counts(
        self, client, admin_user, pending_library, approved_library, open_report, pending_photo
    ):
        """Verify staff users receive moderation dashboard counts.
        Counts mirror the manage dashboard queue totals."""
        response = client.get(
            "/api/v1/libraries/moderation/summary",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        assert response.status_code == 200
        assert body["pending_libraries_count"] == 1
        assert body["open_reports_count"] == 1
        assert body["pending_photos_count"] == 1
        assert body["total_pending"] == 3
        assert body["total_libraries"] == 1
        assert body["total_users"] >= 2


@pytest.mark.django_db
class TestLibraryModerationListEndpoint:
    """Tests for GET /api/v1/libraries/moderation.
    Covers staff-only all-status library listing and filters."""

    def setup_method(self):
        """Clear cache state before each moderation list test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_requires_authentication(self, client):
        """Verify the moderation list requires JWT auth.
        Anonymous users should not reach staff authorization checks."""
        response = client.get("/api/v1/libraries/moderation")

        assert response.status_code == 401

    def test_non_staff_receives_403(self, client, user):
        """Verify non-staff users receive a structured 403 response.
        Authenticated regular users must not access moderation data."""
        response = client.get(
            "/api/v1/libraries/moderation",
            **_auth_header(user=user),
        )

        assert response.status_code == 403
        assert response.json()["message"] == "Staff access required."

    def test_staff_can_list_all_library_statuses(
        self, client, admin_user, pending_library, approved_library, rejected_library
    ):
        """Verify staff users can list libraries across all statuses.
        The moderation list includes entries public APIs hide."""
        response = client.get(
            "/api/v1/libraries/moderation",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        slugs = {item["slug"] for item in body["items"]}
        assert response.status_code == 200
        assert slugs == {
            pending_library.slug,
            approved_library.slug,
            rejected_library.slug,
        }
        assert body["pagination"]["total"] == 3

    def test_staff_can_filter_libraries_by_status(
        self, client, admin_user, pending_library, approved_library, rejected_library
    ):
        """Verify staff users can filter libraries by moderation status.
        The pending filter should exclude approved and rejected entries."""
        response = client.get(
            "/api/v1/libraries/moderation?status=pending",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        assert response.status_code == 200
        assert [item["slug"] for item in body["items"]] == [pending_library.slug]

    def test_pending_convenience_endpoint_lists_pending_only(
        self, client, admin_user, pending_library, approved_library, rejected_library
    ):
        """Verify the pending convenience route returns pending submissions.
        Keeps the iOS dashboard shortcut available for queue loading."""
        response = client.get(
            "/api/v1/libraries/moderation/pending",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        assert response.status_code == 200
        assert [item["slug"] for item in body["items"]] == [pending_library.slug]
        assert body["items"][0]["created_by"]["username"] == "testuser"

    def test_staff_can_search_libraries(
        self, client, admin_user, pending_library, approved_library
    ):
        """Verify staff users can search moderation libraries by text.
        Search matches name, address, and city like the manage UI."""
        response = client.get(
            "/api/v1/libraries/moderation?q=Moderazione",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        assert response.status_code == 200
        assert [item["slug"] for item in body["items"]] == [pending_library.slug]


@pytest.mark.django_db
class TestLibraryModerationDetailEndpoint:
    """Tests for GET /api/v1/libraries/moderation/{slug}.
    Covers staff visibility for entries hidden from public APIs."""

    def setup_method(self):
        """Clear cache state before each moderation detail test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_staff_can_get_rejected_library(self, client, admin_user, rejected_library):
        """Verify staff users can retrieve rejected library details.
        Public detail endpoints intentionally hide rejected entries."""
        response = client.get(
            f"/api/v1/libraries/moderation/{rejected_library.slug}",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        assert response.status_code == 200
        assert body["slug"] == rejected_library.slug
        assert body["status"] == Library.Status.REJECTED

    def test_non_staff_receives_403(self, client, user, pending_library):
        """Verify regular users cannot use staff moderation detail.
        Owner visibility remains limited to the public detail endpoint."""
        response = client.get(
            f"/api/v1/libraries/moderation/{pending_library.slug}",
            **_auth_header(user=user),
        )

        assert response.status_code == 403


@pytest.mark.django_db
class TestLibraryModerationUpdateEndpoint:
    """Tests for PATCH /api/v1/libraries/moderation/{slug}.
    Covers staff status updates and authorization failures."""

    def setup_method(self):
        """Clear cache state before each moderation update test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_non_staff_receives_403(self, client, user, pending_library):
        """Verify non-staff users cannot update moderation status.
        The endpoint should return a clear structured 403 response."""
        response = client.patch(
            f"/api/v1/libraries/moderation/{pending_library.slug}",
            data={"status": "approved"},
            content_type="application/json",
            **_auth_header(user=user),
        )

        pending_library.refresh_from_db()
        assert response.status_code == 403
        assert response.json()["message"] == "Staff access required."
        assert pending_library.status == Library.Status.PENDING

    def test_staff_can_approve_pending_library(self, client, admin_user, pending_library):
        """Verify staff users can approve a pending library.
        Approval should persist and return the updated moderation status."""
        response = client.patch(
            f"/api/v1/libraries/moderation/{pending_library.slug}",
            data={"status": "approved"},
            content_type="application/json",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        pending_library.refresh_from_db()
        assert response.status_code == 200
        assert body["status"] == Library.Status.APPROVED
        assert pending_library.status == Library.Status.APPROVED

    def test_staff_can_reject_pending_library_with_reason(
        self, client, admin_user, pending_library
    ):
        """Verify staff users can reject a pending library.
        Rejection should persist the optional reason for submitter feedback."""
        response = client.patch(
            f"/api/v1/libraries/moderation/{pending_library.slug}",
            data={
                "status": "rejected",
                "rejection_reason": "Duplicate submission.",
            },
            content_type="application/json",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        pending_library.refresh_from_db()
        assert response.status_code == 200
        assert body["status"] == Library.Status.REJECTED
        assert body["rejection_reason"] == "Duplicate submission."
        assert pending_library.status == Library.Status.REJECTED
        assert pending_library.rejection_reason == "Duplicate submission."

    def test_staff_can_return_library_to_pending(self, client, admin_user, approved_library):
        """Verify staff users can move a library back to pending.
        The API exposes all model moderation statuses for staff clients."""
        response = client.patch(
            f"/api/v1/libraries/moderation/{approved_library.slug}",
            data={"status": "pending"},
            content_type="application/json",
            **_auth_header(user=admin_user),
        )

        approved_library.refresh_from_db()
        assert response.status_code == 200
        assert approved_library.status == Library.Status.PENDING

    def test_invalid_status_returns_422(self, client, admin_user, pending_library):
        """Verify unsupported moderation statuses are rejected.
        Staff clients may only use model-supported status values."""
        response = client.patch(
            f"/api/v1/libraries/moderation/{pending_library.slug}",
            data={"status": "archived"},
            content_type="application/json",
            **_auth_header(user=admin_user),
        )

        pending_library.refresh_from_db()
        assert response.status_code == 422
        assert pending_library.status == Library.Status.PENDING

    def test_missing_library_returns_404(self, client, admin_user):
        """Verify missing library slugs return a structured 404.
        Staff access should not change not-found behavior."""
        response = client.patch(
            "/api/v1/libraries/moderation/does-not-exist",
            data={"status": "approved"},
            content_type="application/json",
            **_auth_header(user=admin_user),
        )

        assert response.status_code == 404
        assert response.json()["message"] == "Not found."


@pytest.mark.django_db
class TestReportModerationEndpoint:
    """Tests for report moderation API endpoints.
    Covers staff report listing, filtering, and status updates."""

    def setup_method(self):
        """Clear cache state before each report moderation test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_staff_can_list_reports(
        self, client, admin_user, open_report, dismissed_report
    ):
        """Verify staff users can list user-submitted reports.
        Report payloads include reporter and target library context."""
        response = client.get(
            "/api/v1/libraries/moderation/reports",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        assert response.status_code == 200
        assert {item["id"] for item in body["items"]} == {
            open_report.id,
            dismissed_report.id,
        }
        assert body["items"][0]["library"]["slug"]
        assert body["items"][0]["created_by"]["username"] == "testuser"

    def test_staff_can_filter_reports_by_status(
        self, client, admin_user, open_report, dismissed_report
    ):
        """Verify staff users can filter reports by status.
        Open report queues should exclude dismissed reports."""
        response = client.get(
            "/api/v1/libraries/moderation/reports?status=open",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        assert response.status_code == 200
        assert [item["id"] for item in body["items"]] == [open_report.id]

    def test_staff_can_resolve_report(self, client, admin_user, open_report):
        """Verify staff users can update report status.
        Resolving a report should persist the new status."""
        response = client.patch(
            f"/api/v1/libraries/moderation/reports/{open_report.id}",
            data={"status": "resolved"},
            content_type="application/json",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        open_report.refresh_from_db()
        assert response.status_code == 200
        assert body["status"] == Report.Status.RESOLVED
        assert open_report.status == Report.Status.RESOLVED

    def test_non_staff_cannot_list_reports(self, client, user, open_report):
        """Verify regular users cannot list moderation reports.
        User-submitted reports should only be visible to staff clients."""
        response = client.get(
            "/api/v1/libraries/moderation/reports",
            **_auth_header(user=user),
        )

        assert response.status_code == 403


@pytest.mark.django_db
class TestPhotoModerationEndpoint:
    """Tests for community photo moderation API endpoints.
    Covers staff photo listing, filtering, and status updates."""

    def setup_method(self):
        """Clear cache state before each photo moderation test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_staff_can_list_photos(
        self, client, admin_user, pending_photo, rejected_photo
    ):
        """Verify staff users can list community photo submissions.
        Photo payloads include submitter and parent library context."""
        response = client.get(
            "/api/v1/libraries/moderation/photos",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        assert response.status_code == 200
        assert {item["id"] for item in body["items"]} == {
            pending_photo.id,
            rejected_photo.id,
        }
        assert body["items"][0]["library"]["slug"]
        assert body["items"][0]["created_by"]["username"] == "testuser"

    def test_staff_can_filter_photos_by_status(
        self, client, admin_user, pending_photo, rejected_photo
    ):
        """Verify staff users can filter photos by status.
        Pending photo queues should exclude rejected photos."""
        response = client.get(
            "/api/v1/libraries/moderation/photos?status=pending",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        assert response.status_code == 200
        assert [item["id"] for item in body["items"]] == [pending_photo.id]

    def test_staff_can_approve_photo(self, client, admin_user, pending_photo):
        """Verify staff users can approve community photos.
        The model promotes approved photos to the library primary image."""
        response = client.patch(
            f"/api/v1/libraries/moderation/photos/{pending_photo.id}",
            data={"status": "approved"},
            content_type="application/json",
            **_auth_header(user=admin_user),
        )

        body = response.json()
        pending_photo.refresh_from_db()
        pending_photo.library.refresh_from_db()
        assert response.status_code == 200
        assert body["status"] == LibraryPhoto.Status.APPROVED
        assert pending_photo.status == LibraryPhoto.Status.APPROVED
        assert pending_photo.library.photo == pending_photo.photo

    def test_non_staff_cannot_list_photos(self, client, user, pending_photo):
        """Verify regular users cannot list moderation photos.
        User-submitted photos should only be visible to staff clients."""
        response = client.get(
            "/api/v1/libraries/moderation/photos",
            **_auth_header(user=user),
        )

        assert response.status_code == 403
