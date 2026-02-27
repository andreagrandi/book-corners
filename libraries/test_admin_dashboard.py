import pytest
from django.contrib.gis.geos import Point
from django.urls import reverse

from libraries.models import Library, LibraryPhoto, Report


@pytest.fixture
def _pending_library(user):
    """Create a pending library for moderation tests.
    Returns a library with default pending status."""
    return Library.objects.create(
        name="Test Library",
        address="123 Main St",
        city="Springfield",
        country="US",
        location=Point(-89.6501, 39.7817),
        status=Library.Status.PENDING,
        created_by=user,
    )


@pytest.fixture
def _approved_library(user):
    """Create an approved library excluded from moderation queues.
    Verifies that non-pending items are filtered out."""
    return Library.objects.create(
        name="Approved Library",
        address="456 Oak Ave",
        city="Springfield",
        country="US",
        location=Point(-89.6502, 39.7818),
        status=Library.Status.APPROVED,
        created_by=user,
    )


@pytest.fixture
def _open_report(_approved_library, user):
    """Create an open report for moderation tests.
    Linked to the approved library fixture."""
    return Report.objects.create(
        library=_approved_library,
        created_by=user,
        reason=Report.Reason.DAMAGED,
        details="The door is broken",
        status=Report.Status.OPEN,
    )


@pytest.fixture
def _resolved_report(_approved_library, user):
    """Create a resolved report excluded from moderation queues.
    Verifies that resolved items are filtered out."""
    return Report.objects.create(
        library=_approved_library,
        created_by=user,
        reason=Report.Reason.MISSING,
        details="Already fixed",
        status=Report.Status.RESOLVED,
    )


@pytest.fixture
def _pending_photo(_approved_library, user):
    """Create a pending photo for moderation tests.
    Uses a path-only assignment to skip image processing."""
    return LibraryPhoto.objects.create(
        library=_approved_library,
        created_by=user,
        photo="test/photo.jpg",
        status=LibraryPhoto.Status.PENDING,
    )


@pytest.fixture
def _approved_photo(_approved_library, user):
    """Create an approved photo excluded from moderation queues.
    Verifies that non-pending photos are filtered out."""
    return LibraryPhoto.objects.create(
        library=_approved_library,
        created_by=user,
        photo="test/approved.jpg",
        status=LibraryPhoto.Status.APPROVED,
    )


class TestAdminDashboard:
    """Tests for the custom admin moderation dashboard."""

    def test_admin_index_returns_200(self, admin_client):
        """Verify the admin index page loads successfully.
        Confirms the custom admin site is wired up correctly."""
        response = admin_client.get(reverse("admin:index"))
        assert response.status_code == 200

    def test_moderation_context_present(self, admin_client):
        """Verify the moderation dict is in template context.
        Ensures the custom index() override injects the data."""
        response = admin_client.get(reverse("admin:index"))
        assert "moderation" in response.context

    def test_pending_library_counted(self, admin_client, _pending_library):
        """Verify pending libraries appear in the moderation count.
        Creates one pending library and checks the count is 1."""
        response = admin_client.get(reverse("admin:index"))
        moderation = response.context["moderation"]
        assert moderation["pending_libraries_count"] == 1

    def test_approved_library_excluded(self, admin_client, _approved_library):
        """Verify approved libraries are excluded from moderation.
        Only pending items should appear in the queue."""
        response = admin_client.get(reverse("admin:index"))
        moderation = response.context["moderation"]
        assert moderation["pending_libraries_count"] == 0

    def test_open_report_counted(self, admin_client, _open_report):
        """Verify open reports appear in the moderation count.
        Creates one open report and checks the count is 1."""
        response = admin_client.get(reverse("admin:index"))
        moderation = response.context["moderation"]
        assert moderation["open_reports_count"] == 1

    def test_resolved_report_excluded(self, admin_client, _resolved_report):
        """Verify resolved reports are excluded from moderation.
        Only open items should appear in the queue."""
        response = admin_client.get(reverse("admin:index"))
        moderation = response.context["moderation"]
        assert moderation["open_reports_count"] == 0

    def test_pending_photo_counted(self, admin_client, _pending_photo):
        """Verify pending photos appear in the moderation count.
        Creates one pending photo and checks the count is 1."""
        response = admin_client.get(reverse("admin:index"))
        moderation = response.context["moderation"]
        assert moderation["pending_photos_count"] == 1

    def test_approved_photo_excluded(self, admin_client, _approved_photo):
        """Verify approved photos are excluded from moderation.
        Only pending items should appear in the queue."""
        response = admin_client.get(reverse("admin:index"))
        moderation = response.context["moderation"]
        assert moderation["pending_photos_count"] == 0

    def test_total_count_sums_correctly(
        self, admin_client, _pending_library, _open_report, _pending_photo
    ):
        """Verify total_count is the sum of all three queues.
        Creates one item per queue and expects total of 3."""
        response = admin_client.get(reverse("admin:index"))
        moderation = response.context["moderation"]
        assert moderation["total_count"] == 3

    def test_moderation_heading_rendered(self, admin_client):
        """Verify the Moderation Queue heading appears in HTML.
        Confirms the custom template renders the dashboard section."""
        response = admin_client.get(reverse("admin:index"))
        assert b"Moderation Queue" in response.content

    def test_filtered_changelist_links_present(self, admin_client):
        """Verify the View all links point to filtered changelists.
        Each card should link to the relevant admin list with status filter."""
        response = admin_client.get(reverse("admin:index"))
        content = response.content.decode()
        assert "status__exact=pending" in content
        assert "status__exact=open" in content

    def test_app_list_still_renders(self, admin_client):
        """Verify the standard Django app list is preserved.
        The dashboard should augment, not replace, the default index."""
        response = admin_client.get(reverse("admin:index"))
        assert b"app-libraries" in response.content or b"Libraries" in response.content
