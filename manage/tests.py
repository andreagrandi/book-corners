from typing import Any

import pytest
from django.contrib.gis.geos import Point
from django.test import Client
from django.urls import reverse

from libraries.models import Library, LibraryPhoto


@pytest.fixture
def manage_library(user: Any) -> Library:
    """Create a library for manage edit tests.
    Provides a mutable record with a pending moderation state."""
    return Library.objects.create(
        name="Manage Edit Shelf",
        description="Original description.",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city="Florence",
        country="IT",
        postal_code="50123",
        status=Library.Status.PENDING,
        created_by=user,
    )


@pytest.mark.django_db
def test_library_edit_redirects_anonymous_users(
    client: Client,
    manage_library: Library,
) -> None:
    """Verify anonymous users cannot access the manage edit page.
    Confirms the staff gate is enforced before rendering the form."""
    response = client.get(
        reverse("manage:library_edit", kwargs={"pk": manage_library.pk})
    )

    assert response.status_code == 302
    assert response.url == reverse("login")


@pytest.mark.django_db
def test_library_edit_redirects_non_staff_users(
    client: Client,
    user: Any,
    manage_library: Library,
) -> None:
    """Verify regular users cannot access the manage edit page.
    Confirms authenticated non-staff users stay out of staff workflows."""
    client.force_login(user)

    response = client.get(
        reverse("manage:library_edit", kwargs={"pk": manage_library.pk})
    )

    assert response.status_code == 302
    assert response.url == reverse("login")


@pytest.mark.django_db
def test_staff_can_open_library_edit_form(
    client: Client,
    admin_user: Any,
    manage_library: Library,
) -> None:
    """Verify staff can open the manage edit form.
    Confirms the page renders the native edit UI and map picker."""
    client.force_login(admin_user)

    response = client.get(
        reverse("manage:library_edit", kwargs={"pk": manage_library.pk})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Edit library" in content
    assert "manage-library-map" in content
    assert "Edit in Django Admin" in content


@pytest.mark.django_db
def test_staff_can_edit_library_details_and_location(
    client: Client,
    admin_user: Any,
    manage_library: Library,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify staff can save core details and location from manage.
    Confirms cache invalidation and moderation notifications are reused."""
    invalidations = []
    approved_notifications = []

    def fake_invalidate_library_caches() -> None:
        """Record cache invalidation calls during the edit save.
        Keeps the test focused on view orchestration."""
        invalidations.append(True)

    def fake_notify_library_approved(library: Library) -> None:
        """Record approval notifications during the edit save.
        Avoids sending mail while asserting moderation side effects."""
        approved_notifications.append(library.pk)

    monkeypatch.setattr(
        "manage.views.libraries._invalidate_library_caches",
        fake_invalidate_library_caches,
    )
    monkeypatch.setattr(
        "manage.views.libraries.notify_library_approved",
        fake_notify_library_approved,
    )
    client.force_login(admin_user)

    response = client.post(
        reverse("manage:library_edit", kwargs={"pk": manage_library.pk}),
        data={
            "name": "Updated Manage Shelf",
            "description": "Updated by staff.",
            "address": "Via Roma 20",
            "city": "Florence",
            "country": "IT",
            "postal_code": "50100",
            "latitude": "43.780000",
            "longitude": "11.260000",
            "wheelchair_accessible": Library.WheelchairAccess.YES,
            "capacity": "42",
            "is_indoor": "false",
            "is_lit": "true",
            "website": "https://example.com/library",
            "contact": "Front desk",
            "source": "manual",
            "operator": "City Library",
            "brand": "Book Corners",
            "external_id": "manual-70",
            "status": Library.Status.APPROVED,
            "rejection_reason": "",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse(
        "manage:library_detail", kwargs={"pk": manage_library.pk}
    )
    manage_library.refresh_from_db()
    assert manage_library.name == "Updated Manage Shelf"
    assert manage_library.description == "Updated by staff."
    assert manage_library.address == "Via Roma 20"
    assert manage_library.postal_code == "50100"
    assert manage_library.wheelchair_accessible == Library.WheelchairAccess.YES
    assert manage_library.capacity == 42
    assert manage_library.is_indoor is False
    assert manage_library.is_lit is True
    assert manage_library.website == "https://example.com/library"
    assert manage_library.contact == "Front desk"
    assert manage_library.source == "manual"
    assert manage_library.operator == "City Library"
    assert manage_library.brand == "Book Corners"
    assert manage_library.external_id == "manual-70"
    assert manage_library.status == Library.Status.APPROVED
    assert manage_library.location.y == pytest.approx(43.78, abs=1e-6)
    assert manage_library.location.x == pytest.approx(11.26, abs=1e-6)
    assert invalidations == [True]
    assert approved_notifications == [manage_library.pk]


@pytest.mark.django_db
def test_pending_moderation_stat_links_to_photo_queue_when_only_photos_are_pending(
    admin_client: Client,
    manage_library: Library,
    user: Any,
) -> None:
    """Verify a photo-only moderation total links to the pending photo queue.
    Prevents the aggregate dashboard card from opening an empty library list."""
    manage_library.status = Library.Status.APPROVED
    manage_library.save(update_fields=["status", "updated_at"])
    LibraryPhoto.objects.create(
        library=manage_library,
        created_by=user,
        photo="libraries/user_photos/dashboard-pending.jpg",
    )

    response = admin_client.get(reverse("manage:dashboard"))

    photo_list_url = reverse("manage:photo_list")
    assert response.status_code == 200
    assert response.context["pending_moderation_url"] == (
        f"{photo_list_url}?status={LibraryPhoto.Status.PENDING}"
        "&type=community"
    )


@pytest.mark.django_db
def test_library_detail_links_pending_photos_to_moderation_actions(
    admin_client: Client,
    manage_library: Library,
    user: Any,
) -> None:
    """Verify pending photo cards expose approve-as-main and reject actions.
    Gives moderators both decisions without leaving the library detail page."""
    pending_photo = LibraryPhoto.objects.create(
        library=manage_library,
        created_by=user,
        photo="libraries/user_photos/detail-pending.jpg",
    )

    response = admin_client.get(
        reverse("manage:library_detail", kwargs={"pk": manage_library.pk})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert f'id="photo-card-{pending_photo.pk}-community"' in content
    assert "Approve as main photo" in content
    assert reverse(
        "manage:photo_approve", kwargs={"pk": pending_photo.pk}
    ) in content
    assert reverse(
        "manage:photo_reject", kwargs={"pk": pending_photo.pk}
    ) in content


@pytest.mark.django_db
def test_library_detail_compares_staged_changes_with_live_values(
    admin_client: Client,
    manage_library: Library,
) -> None:
    """Verify moderators see only proposed fields beside current values.
    Makes clear that the approved library remains live during review."""
    manage_library.status = Library.Status.APPROVED
    manage_library.save(update_fields=["status", "updated_at"])
    manage_library.stage_update(
        changes={
            "name": "Proposed Manage Shelf",
            "city": "Prato",
        }
    )

    response = admin_client.get(
        reverse("manage:library_detail", kwargs={"pk": manage_library.pk})
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Pending changes" in content
    assert "The approved library remains live" in content
    assert "Manage Edit Shelf" in content
    assert "Proposed Manage Shelf" in content
    assert "Approve changes" in content
    assert "Reject changes" in content


@pytest.mark.django_db
def test_staff_approves_staged_changes_without_changing_library_identity(
    admin_client: Client,
    manage_library: Library,
) -> None:
    """Verify manage approval applies staged fields to the live library.
    Preserves its approved status, primary key, and public slug."""
    manage_library.status = Library.Status.APPROVED
    manage_library.save(update_fields=["status", "updated_at"])
    original_slug = manage_library.slug
    manage_library.stage_update(changes={"name": "Approved Manage Change"})

    response = admin_client.post(
        reverse("manage:library_approve", kwargs={"pk": manage_library.pk})
    )

    manage_library.refresh_from_db()
    assert response.status_code == 302
    assert manage_library.name == "Approved Manage Change"
    assert manage_library.slug == original_slug
    assert manage_library.status == Library.Status.APPROVED
    assert manage_library.pending_changes is None


@pytest.mark.django_db
def test_staff_bulk_approve_restores_rejected_library(
    admin_client: Client,
    manage_library: Library,
) -> None:
    """Verify manage bulk approval restores a rejected library.
    Keeps bulk approval behavior aligned with the single-library action."""
    manage_library.status = Library.Status.REJECTED
    manage_library.save(update_fields=["status", "updated_at"])

    response = admin_client.post(
        reverse("manage:library_bulk_action"),
        data={
            "action": "approve",
            "selected": [manage_library.pk],
        },
    )

    manage_library.refresh_from_db()
    assert response.status_code == 302
    assert manage_library.status == Library.Status.APPROVED


@pytest.mark.django_db
def test_staff_rejects_staged_changes_without_rejecting_live_library(
    admin_client: Client,
    manage_library: Library,
) -> None:
    """Verify manage rejection discards only proposed library edits.
    Keeps the existing approved record publicly available."""
    manage_library.status = Library.Status.APPROVED
    manage_library.save(update_fields=["status", "updated_at"])
    manage_library.stage_update(changes={"name": "Rejected Manage Change"})

    response = admin_client.post(
        reverse("manage:library_reject", kwargs={"pk": manage_library.pk}),
        data={"rejection_reason": "The proposed name is inaccurate."},
    )

    manage_library.refresh_from_db()
    assert response.status_code == 302
    assert manage_library.name == "Manage Edit Shelf"
    assert manage_library.status == Library.Status.APPROVED
    assert manage_library.pending_changes is None


@pytest.mark.django_db
def test_staff_can_choose_main_photo_and_reject_another_from_library_detail(
    admin_client: Client,
    manage_library: Library,
    user: Any,
) -> None:
    """Verify library-detail moderation can choose one photo and reject another.
    Confirms the selected photo becomes primary and both statuses are persisted."""
    selected_photo = LibraryPhoto.objects.create(
        library=manage_library,
        created_by=user,
        photo="libraries/user_photos/selected-main.jpg",
    )
    rejected_photo = LibraryPhoto.objects.create(
        library=manage_library,
        created_by=user,
        photo="libraries/user_photos/rejected-alternative.jpg",
    )

    approve_response = admin_client.post(
        reverse("manage:photo_approve", kwargs={"pk": selected_photo.pk}),
        data={"return_to_library": "1"},
    )
    reject_response = admin_client.post(
        reverse("manage:photo_reject", kwargs={"pk": rejected_photo.pk}),
        data={"return_to_library": "1"},
    )

    library_detail_url = reverse(
        "manage:library_detail", kwargs={"pk": manage_library.pk}
    )
    assert approve_response.status_code == 302
    assert approve_response.url == f"{library_detail_url}#community-photos"
    assert reject_response.status_code == 302
    assert reject_response.url == f"{library_detail_url}#community-photos"
    selected_photo.refresh_from_db()
    rejected_photo.refresh_from_db()
    manage_library.refresh_from_db()
    assert selected_photo.status == LibraryPhoto.Status.APPROVED
    assert rejected_photo.status == LibraryPhoto.Status.REJECTED
    assert manage_library.photo.name == selected_photo.photo.name
