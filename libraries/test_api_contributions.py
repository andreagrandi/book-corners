from typing import Any, cast

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.test import override_settings
from ninja_jwt.tokens import RefreshToken

from libraries.models import Library, LibraryPhoto, Report

User = get_user_model()
Libraries = cast(Any, getattr(Library, "objects"))
Reports = cast(Any, getattr(Report, "objects"))
LibraryPhotos = cast(Any, getattr(LibraryPhoto, "objects"))


def _auth_header(*, user: AbstractBaseUser) -> dict[str, str]:
    """Build a Bearer token header for a user.
    Generates a valid JWT access token for authenticated API calls."""
    refresh = cast(RefreshToken, RefreshToken.for_user(user=user))
    access_token = str(getattr(refresh, "access_token"))
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}


def _create_library(
    *,
    user: AbstractBaseUser,
    name: str,
    status: str,
    address: str,
    city: str = "Florence",
    country: str = "IT",
) -> Library:
    """Create a library contribution for API tests.
    Keeps location and media defaults consistent across contribution cases."""
    return Libraries.create(
        name=name,
        photo="libraries/photos/2026/02/test.jpg",
        photo_thumbnail="libraries/photos/thumbnails/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address=address,
        city=city,
        country=country,
        status=status,
        created_by=user,
    )


@pytest.mark.django_db
class TestContributionAuthentication:
    """Tests authentication requirements for contribution endpoints.
    Covers all current-user contribution list routes."""

    def setup_method(self) -> None:
        """Clear cache state before each authentication test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/libraries/mine",
            "/api/v1/libraries/mine/reports",
            "/api/v1/libraries/mine/photos",
        ],
    )
    def test_requires_authentication(self, client, url: str) -> None:
        """Verify contribution endpoints reject anonymous requests.
        JWT authentication is mandatory before user-scoped data is returned."""
        response = client.get(url)

        assert response.status_code == 401


@pytest.mark.django_db
class TestMyLibrariesEndpoint:
    """Tests for GET /api/v1/libraries/mine.
    Covers owner scoping, status exposure, and pagination."""

    url = "/api/v1/libraries/mine"

    def setup_method(self) -> None:
        """Clear cache state before each library contribution test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_returns_only_current_users_libraries_with_statuses(self, client, user) -> None:
        """Verify the endpoint returns only the caller's submitted libraries.
        All library moderation statuses should be surfaced to the owner."""
        other_user = User.objects.create_user(username="othercontributor", password="pass123")
        approved = _create_library(
            user=user,
            name="Approved Mine",
            status=Library.Status.APPROVED,
            address="Via Approved 1",
        )
        rejected = _create_library(
            user=user,
            name="Rejected Mine",
            status=Library.Status.REJECTED,
            address="Via Rejected 1",
        )
        pending = _create_library(
            user=user,
            name="Pending Mine",
            status=Library.Status.PENDING,
            address="Via Pending 1",
        )
        _create_library(
            user=other_user,
            name="Other User Library",
            status=Library.Status.PENDING,
            address="Via Other 1",
        )

        response = client.get(self.url, **_auth_header(user=user))

        body = response.json()
        slugs = {item["slug"] for item in body["items"]}
        statuses = {item["status"] for item in body["items"]}
        assert response.status_code == 200
        assert slugs == {approved.slug, rejected.slug, pending.slug}
        assert statuses == {
            Library.Status.APPROVED,
            Library.Status.REJECTED,
            Library.Status.PENDING,
        }
        assert body["items"][0]["slug"] == pending.slug
        assert body["pagination"]["total"] == 3

    def test_paginates_libraries(self, client, user) -> None:
        """Verify library contributions use standard pagination metadata.
        Page size should limit returned items while preserving total counts."""
        _create_library(
            user=user,
            name="First Mine",
            status=Library.Status.APPROVED,
            address="Via First 1",
        )
        _create_library(
            user=user,
            name="Second Mine",
            status=Library.Status.APPROVED,
            address="Via Second 1",
        )

        response = client.get(f"{self.url}?page_size=1", **_auth_header(user=user))

        body = response.json()
        assert response.status_code == 200
        assert len(body["items"]) == 1
        assert body["pagination"]["total"] == 2
        assert body["pagination"]["has_next"]


@pytest.mark.django_db
class TestMyReportsEndpoint:
    """Tests for GET /api/v1/libraries/mine/reports.
    Covers owner scoping, report status exposure, and pagination."""

    url = "/api/v1/libraries/mine/reports"

    def setup_method(self) -> None:
        """Clear cache state before each report contribution test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_returns_only_current_users_reports_with_statuses(self, client, user) -> None:
        """Verify the endpoint returns only the caller's submitted reports.
        Report reason, status, and library summary should be included."""
        other_user = User.objects.create_user(username="otherreporter", password="pass123")
        library = _create_library(
            user=user,
            name="Reported Library",
            status=Library.Status.APPROVED,
            address="Via Reported 1",
        )
        open_report = Reports.create(
            library=library,
            created_by=user,
            reason=Report.Reason.DAMAGED,
            details="Door is damaged.",
            status=Report.Status.OPEN,
        )
        resolved_report = Reports.create(
            library=library,
            created_by=user,
            reason=Report.Reason.MISSING,
            details="It moved.",
            status=Report.Status.RESOLVED,
        )
        dismissed_report = Reports.create(
            library=library,
            created_by=user,
            reason=Report.Reason.OTHER,
            details="Duplicate report.",
            status=Report.Status.DISMISSED,
        )
        Reports.create(
            library=library,
            created_by=other_user,
            reason=Report.Reason.INAPPROPRIATE,
            details="Other user's report.",
            status=Report.Status.OPEN,
        )

        response = client.get(self.url, **_auth_header(user=user))

        body = response.json()
        ids = {item["id"] for item in body["items"]}
        statuses = {item["status"] for item in body["items"]}
        first_item = body["items"][0]
        assert response.status_code == 200
        assert ids == {open_report.id, resolved_report.id, dismissed_report.id}
        assert statuses == {
            Report.Status.OPEN,
            Report.Status.RESOLVED,
            Report.Status.DISMISSED,
        }
        assert first_item["reason"] in {
            Report.Reason.DAMAGED,
            Report.Reason.MISSING,
            Report.Reason.OTHER,
        }
        assert first_item["library"]["slug"] == library.slug
        assert first_item["library"]["name"] == library.name
        assert first_item["library"]["city"] == library.city
        assert first_item["library"]["country"] == library.country
        assert body["pagination"]["total"] == 3

    def test_paginates_reports(self, client, user) -> None:
        """Verify report contributions use standard pagination metadata.
        Page size should limit returned items while preserving total counts."""
        library = _create_library(
            user=user,
            name="Pagination Report Library",
            status=Library.Status.APPROVED,
            address="Via Report Page 1",
        )
        for index in range(2):
            Reports.create(
                library=library,
                created_by=user,
                reason=Report.Reason.OTHER,
                details=f"Report {index}",
                status=Report.Status.OPEN,
            )

        response = client.get(f"{self.url}?page_size=1", **_auth_header(user=user))

        body = response.json()
        assert response.status_code == 200
        assert len(body["items"]) == 1
        assert body["pagination"]["total"] == 2
        assert body["pagination"]["has_next"]


@pytest.mark.django_db
class TestMyPhotosEndpoint:
    """Tests for GET /api/v1/libraries/mine/photos.
    Covers owner scoping, photo status exposure, and pagination."""

    url = "/api/v1/libraries/mine/photos"

    def setup_method(self) -> None:
        """Clear cache state before each photo contribution test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_returns_only_current_users_photos_with_statuses(self, client, user) -> None:
        """Verify the endpoint returns only the caller's submitted photos.
        Caption, status, thumbnail, and library summary should be included."""
        other_user = User.objects.create_user(username="otherphotographer", password="pass123")
        library = _create_library(
            user=user,
            name="Photo Library",
            status=Library.Status.APPROVED,
            address="Via Photo 1",
        )
        pending_photo = LibraryPhotos.create(
            library=library,
            created_by=user,
            photo="libraries/user_photos/2026/02/pending.jpg",
            photo_thumbnail="libraries/user_photos/thumbnails/2026/02/pending.jpg",
            caption="Pending photo",
            status=LibraryPhoto.Status.PENDING,
        )
        approved_photo = LibraryPhotos.create(
            library=library,
            created_by=user,
            photo="libraries/user_photos/2026/02/approved.jpg",
            photo_thumbnail="libraries/user_photos/thumbnails/2026/02/approved.jpg",
            caption="Approved photo",
            status=LibraryPhoto.Status.APPROVED,
        )
        rejected_photo = LibraryPhotos.create(
            library=library,
            created_by=user,
            photo="libraries/user_photos/2026/02/rejected.jpg",
            photo_thumbnail="libraries/user_photos/thumbnails/2026/02/rejected.jpg",
            caption="Rejected photo",
            status=LibraryPhoto.Status.REJECTED,
        )
        LibraryPhotos.create(
            library=library,
            created_by=other_user,
            photo="libraries/user_photos/2026/02/other.jpg",
            caption="Other user's photo",
            status=LibraryPhoto.Status.PENDING,
        )

        response = client.get(self.url, **_auth_header(user=user))

        body = response.json()
        ids = {item["id"] for item in body["items"]}
        statuses = {item["status"] for item in body["items"]}
        first_item = body["items"][0]
        assert response.status_code == 200
        assert ids == {pending_photo.id, approved_photo.id, rejected_photo.id}
        assert statuses == {
            LibraryPhoto.Status.PENDING,
            LibraryPhoto.Status.APPROVED,
            LibraryPhoto.Status.REJECTED,
        }
        assert first_item["caption"] in {
            pending_photo.caption,
            approved_photo.caption,
            rejected_photo.caption,
        }
        assert first_item["thumbnail_url"]
        assert first_item["library"]["slug"] == library.slug
        assert first_item["library"]["city"] == library.city
        assert body["pagination"]["total"] == 3

    def test_paginates_photos(self, client, user) -> None:
        """Verify photo contributions use standard pagination metadata.
        Page size should limit returned items while preserving total counts."""
        library = _create_library(
            user=user,
            name="Pagination Photo Library",
            status=Library.Status.APPROVED,
            address="Via Photo Page 1",
        )
        for index in range(2):
            LibraryPhotos.create(
                library=library,
                created_by=user,
                photo=f"libraries/user_photos/2026/02/photo-{index}.jpg",
                caption=f"Photo {index}",
                status=LibraryPhoto.Status.PENDING,
            )

        response = client.get(f"{self.url}?page_size=1", **_auth_header(user=user))

        body = response.json()
        assert response.status_code == 200
        assert len(body["items"]) == 1
        assert body["pagination"]["total"] == 2
        assert body["pagination"]["has_next"]


@pytest.mark.django_db
class TestContributionRateLimit:
    """Tests read rate limiting for contribution endpoints.
    Covers all current-user contribution list routes."""

    def setup_method(self) -> None:
        """Clear cache state before each rate limit test.
        Prevents previous requests from consuming the configured limit."""
        cache.clear()

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
        API_RATE_LIMIT_READ_REQUESTS=1,
    )
    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/libraries/mine",
            "/api/v1/libraries/mine/reports",
            "/api/v1/libraries/mine/photos",
        ],
    )
    def test_rate_limit_returns_429(self, client, user, url: str) -> None:
        """Verify contribution endpoints return 429 when rate limited.
        Current-user contribution lists use the shared read request tier."""
        client.get(url, **_auth_header(user=user))
        response = client.get(url, **_auth_header(user=user))

        assert response.status_code == 429


