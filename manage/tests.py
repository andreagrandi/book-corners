from typing import Any

import pytest
from django.contrib.gis.geos import Point
from django.test import Client
from django.urls import reverse

from libraries.models import Library


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
