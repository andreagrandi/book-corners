"""Tests for recording where each library's main photo came from.

Covers the model helper, the web and admin write paths, promotion of
community photos, pending-update approval and the Instagram crop.
"""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib import admin
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from django.test import Client, RequestFactory
from django.urls import reverse
from PIL import Image

from libraries.admin import LibraryAdmin
from libraries.image_processing import ensure_instagram_aspect_ratio
from libraries.models import Library, LibraryPhoto
from libraries.tests import _build_uploaded_photo


def _create_library(*, user: Any, **overrides: Any) -> Library:
    """Create a library with sensible defaults for photo origin tests.
    Accepts keyword overrides for the fields a test cares about."""
    defaults: dict[str, Any] = {
        "name": "Origin Shelf",
        "location": Point(x=11.2558, y=43.7696, srid=4326),
        "address": "Via Rosina 15",
        "city": "Florence",
        "country": "IT",
        "status": Library.Status.PENDING,
        "created_by": user,
    }
    defaults.update(overrides)
    return Library.objects.create(**defaults)


def _edit_payload(**overrides: Any) -> dict[str, Any]:
    """Build the owner edit form payload for a Florence library.
    Accepts keyword overrides such as a replacement photo."""
    payload: dict[str, Any] = {
        "name": "Origin Shelf",
        "description": "",
        "address": "Via Rosina 15",
        "city": "Florence",
        "country": "IT",
        "postal_code": "",
        "latitude": "43.7696",
        "longitude": "11.2558",
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestLibraryPhotoOriginModel:
    """Tests for the Library photo origin fields and save rules."""

    def test_set_photo_origin_sets_fields_without_saving(self, user: Any) -> None:
        """Verify the helper updates the three fields in memory only.
        Leaves persistence to the caller so update_fields stays explicit."""
        library = _create_library(user=user, photo="libraries/photos/2026/02/a.jpg")

        library.set_photo_origin(
            origin=Library.PhotoOrigin.EXTERNAL,
            author=user,
            source_url="https://example.com/a.jpg",
        )

        assert library.photo_origin == "external"
        assert library.photo_author == user
        assert library.photo_source_url == "https://example.com/a.jpg"
        library.refresh_from_db()
        assert library.photo_origin == ""

    def test_save_clears_origin_when_library_has_no_photo(self, user: Any) -> None:
        """Verify a library without a photo cannot keep origin data.
        Keeps the empty origin meaning no photo."""
        library = _create_library(user=user, photo="libraries/photos/2026/02/a.jpg")
        library.set_photo_origin(
            origin=Library.PhotoOrigin.USER,
            author=user,
            source_url="https://example.com/a.jpg",
        )
        library.save()

        library.photo = ""
        library.save()

        library.refresh_from_db()
        assert library.photo_origin == ""
        assert library.photo_author is None
        assert library.photo_source_url == ""

    def test_save_with_photo_in_update_fields_persists_origin(self, user: Any) -> None:
        """Verify the safety net adds origin fields to photo-only saves.
        Prevents callers from losing origin data by forgetting update_fields."""
        library = _create_library(user=user, photo="libraries/photos/2026/02/a.jpg")
        library.photo = "libraries/photos/2026/02/b.jpg"
        library.set_photo_origin(origin=Library.PhotoOrigin.USER, author=user)

        library.save(update_fields=["photo"])

        library.refresh_from_db()
        assert library.photo_origin == "user"
        assert library.photo_author == user

    def test_approving_photo_promotes_it_with_creator_as_author(
        self,
        user: Any,
        admin_user: Any,
    ) -> None:
        """Verify promotion records the community photo author.
        Uses the LibraryPhoto creator rather than the library owner."""
        library = _create_library(
            user=user,
            status=Library.Status.APPROVED,
            photo="libraries/photos/2026/02/old.jpg",
        )
        community_photo = LibraryPhoto.objects.create(
            library=library,
            created_by=admin_user,
            photo="libraries/user_photos/2026/02/community.jpg",
        )

        community_photo.status = LibraryPhoto.Status.APPROVED
        community_photo.save(update_fields=["status"])

        library.refresh_from_db()
        assert library.photo.name == "libraries/user_photos/2026/02/community.jpg"
        assert library.photo_origin == "user"
        assert library.photo_author == admin_user
        assert library.photo_source_url == ""

    def test_promote_to_library_primary_keeps_missing_author_empty(
        self,
        user: Any,
    ) -> None:
        """Verify a community photo without a creator gives no author.
        Covers photos whose uploader account was deleted."""
        library = _create_library(user=user, status=Library.Status.APPROVED)
        community_photo = LibraryPhoto.objects.create(
            library=library,
            created_by=None,
            photo="libraries/user_photos/2026/02/orphan.jpg",
        )

        community_photo.promote_to_library_primary()

        library.refresh_from_db()
        assert library.photo_origin == "user"
        assert library.photo_author is None

    def test_apply_pending_update_with_photo_records_creator_as_author(
        self,
        user: Any,
        settings: Any,
        tmp_path: Path,
    ) -> None:
        """Verify approving a staged photo records the owner as author.
        Only the library creator can propose updates."""
        settings.MEDIA_ROOT = tmp_path / "media"
        library = _create_library(
            user=user,
            status=Library.Status.APPROVED,
            photo=_build_uploaded_photo(file_name="live.jpg"),
        )
        library.set_photo_origin(origin=Library.PhotoOrigin.UNKNOWN)
        library.save(update_fields=["photo_origin", "photo_author", "photo_source_url"])
        library.stage_update(
            changes={},
            photo=_build_uploaded_photo(file_name="staged.jpg"),
        )

        library.apply_pending_update()

        library.refresh_from_db()
        assert "staged" in library.photo.name
        assert library.photo_origin == "user"
        assert library.photo_author == user

    def test_apply_pending_update_without_photo_keeps_origin(
        self,
        user: Any,
    ) -> None:
        """Verify approving text-only changes leaves the photo origin alone.
        The live photo did not change, so neither does its provenance."""
        library = _create_library(
            user=user,
            status=Library.Status.APPROVED,
            photo="libraries/photos/2026/02/live.jpg",
        )
        library.set_photo_origin(origin=Library.PhotoOrigin.EXTERNAL, source_url="https://example.com/x.jpg")
        library.save()
        library.stage_update(changes={"name": "Renamed Shelf"})

        library.apply_pending_update()

        library.refresh_from_db()
        assert library.name == "Renamed Shelf"
        assert library.photo_origin == "external"
        assert library.photo_source_url == "https://example.com/x.jpg"

    def test_instagram_crop_keeps_existing_origin(
        self,
        user: Any,
        settings: Any,
        tmp_path: Path,
    ) -> None:
        """Verify the in-place Instagram crop does not reset the origin.
        The cropped file is still the same photo from the same source."""
        settings.MEDIA_ROOT = tmp_path / "media"
        buffer = BytesIO()
        Image.new("RGB", (2000, 400), color=(140, 165, 210)).save(buffer, format="JPEG")
        library = _create_library(user=user)
        library.photo.save("wide.jpg", ContentFile(buffer.getvalue()), save=False)
        library.set_photo_origin(
            origin=Library.PhotoOrigin.EXTERNAL,
            source_url="https://example.com/wide.jpg",
        )
        library.save()

        ensure_instagram_aspect_ratio(library=library)

        library.refresh_from_db()
        assert library.photo_origin == "external"
        assert library.photo_source_url == "https://example.com/wide.jpg"


@pytest.mark.django_db
class TestWebSubmissionPhotoOrigin:
    """Tests for photo origin on website submit and pending edit."""

    def test_submit_records_user_origin_and_author(
        self,
        client: Client,
        user: Any,
    ) -> None:
        """Verify website submissions record the submitter as photo author.
        Sets the origin to user for the uploaded photo."""
        client.force_login(user)

        response = client.post(
            reverse("submit_library"),
            data={**_edit_payload(name="Origin Submit Shelf"), "photo": _build_uploaded_photo()},
        )

        assert response.status_code == 302
        library = Library.objects.get(name="Origin Submit Shelf")
        assert library.photo_origin == "user"
        assert library.photo_author == user
        assert library.photo_source_url == ""

    def test_pending_edit_with_new_photo_records_user_origin(
        self,
        client: Client,
        user: Any,
        settings: Any,
        tmp_path: Path,
    ) -> None:
        """Verify replacing a pending library photo resets origin to the owner.
        An earlier unknown origin must not survive the replacement."""
        settings.MEDIA_ROOT = tmp_path / "media"
        library = _create_library(user=user, photo="libraries/photos/2026/02/old.jpg")
        library.set_photo_origin(origin=Library.PhotoOrigin.UNKNOWN)
        library.save()
        client.force_login(user)

        response = client.post(
            reverse("edit_library", kwargs={"slug": library.slug}),
            data=_edit_payload(photo=_build_uploaded_photo(file_name="replacement.jpg")),
        )

        assert response.status_code == 302
        library.refresh_from_db()
        assert library.photo_origin == "user"
        assert library.photo_author == user

    def test_pending_edit_without_new_photo_keeps_origin(
        self,
        client: Client,
        user: Any,
    ) -> None:
        """Verify text-only edits do not touch the photo origin.
        Only a changed photo should change its provenance."""
        library = _create_library(user=user, photo="libraries/photos/2026/02/old.jpg")
        library.set_photo_origin(origin=Library.PhotoOrigin.UNKNOWN)
        library.save()
        client.force_login(user)

        response = client.post(
            reverse("edit_library", kwargs={"slug": library.slug}),
            data=_edit_payload(name="Edited Shelf"),
        )

        assert response.status_code == 302
        library.refresh_from_db()
        assert library.name == "Edited Shelf"
        assert library.photo_origin == "unknown"
        assert library.photo_author is None


@pytest.mark.django_db
class TestAdminPhotoOrigin:
    """Tests for photo origin handling in the Django admin."""

    def _save_through_admin(
        self,
        *,
        library: Library,
        admin_user: Any,
        changed_data: list[str],
    ) -> None:
        """Run the Library admin save_model hook with a fake form.
        Mimics which fields staff changed without rendering the full form."""
        request = RequestFactory().post("/")
        request.user = admin_user
        model_admin = LibraryAdmin(model=Library, admin_site=admin.site)
        model_admin.save_model(
            request=request,
            obj=library,
            form=SimpleNamespace(changed_data=changed_data),
            change=True,
        )

    def test_change_form_lists_origin_fields_after_thumbnail(self, admin_client: Client, user: Any) -> None:
        """Verify the admin change form exposes the three origin fields.
        Lets staff correct the origin by hand."""
        library = _create_library(user=user, photo="libraries/photos/2026/02/a.jpg")

        response = admin_client.get(reverse("admin:libraries_library_change", args=[library.pk]))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="photo_origin"' in content
        assert 'name="photo_author"' in content
        assert 'name="photo_source_url"' in content

    def test_replacing_photo_without_origin_edits_sets_unknown(
        self,
        admin_user: Any,
        user: Any,
    ) -> None:
        """Verify a staff photo upload resets the origin to unknown.
        Staff cannot be assumed to be the photographer."""
        library = _create_library(user=user, photo="libraries/photos/2026/02/a.jpg")
        library.set_photo_origin(origin=Library.PhotoOrigin.USER, author=user)
        library.save()
        library.photo = "libraries/photos/2026/02/b.jpg"

        self._save_through_admin(library=library, admin_user=admin_user, changed_data=["photo"])

        library.refresh_from_db()
        assert library.photo.name == "libraries/photos/2026/02/b.jpg"
        assert library.photo_origin == "unknown"
        assert library.photo_author is None
        assert library.photo_source_url == ""

    def test_replacing_photo_with_origin_edits_keeps_staff_values(
        self,
        admin_user: Any,
        user: Any,
    ) -> None:
        """Verify origin values typed by staff are not overwritten.
        Staff can set the real origin in the same save as the new photo."""
        library = _create_library(user=user, photo="libraries/photos/2026/02/a.jpg")
        library.photo = "libraries/photos/2026/02/b.jpg"
        library.set_photo_origin(
            origin=Library.PhotoOrigin.EXTERNAL,
            source_url="https://example.com/b.jpg",
        )

        self._save_through_admin(
            library=library,
            admin_user=admin_user,
            changed_data=["photo", "photo_origin", "photo_source_url"],
        )

        library.refresh_from_db()
        assert library.photo_origin == "external"
        assert library.photo_source_url == "https://example.com/b.jpg"

    def test_clearing_photo_clears_origin(self, admin_user: Any, user: Any) -> None:
        """Verify removing the photo in admin empties the origin fields.
        Saving clears them because the library no longer has a photo."""
        library = _create_library(user=user, photo="libraries/photos/2026/02/a.jpg")
        library.set_photo_origin(origin=Library.PhotoOrigin.USER, author=user)
        library.save()
        library.photo = ""

        self._save_through_admin(library=library, admin_user=admin_user, changed_data=["photo"])

        library.refresh_from_db()
        assert library.photo_origin == ""
        assert library.photo_author is None
        assert library.photo_source_url == ""

    def test_editing_other_fields_keeps_origin(self, admin_user: Any, user: Any) -> None:
        """Verify unrelated admin edits leave the photo origin untouched.
        Only a changed photo triggers the unknown fallback."""
        library = _create_library(user=user, photo="libraries/photos/2026/02/a.jpg")
        library.set_photo_origin(origin=Library.PhotoOrigin.USER, author=user)
        library.save()
        library.name = "Renamed In Admin"

        self._save_through_admin(library=library, admin_user=admin_user, changed_data=["name"])

        library.refresh_from_db()
        assert library.photo_origin == "user"
        assert library.photo_author == user

    def test_approve_photos_action_records_photo_author(
        self,
        admin_client: Client,
        admin_user: Any,
        user: Any,
    ) -> None:
        """Verify the admin approve action promotes with the photo creator.
        The first approved photo per library becomes the main photo."""
        library = _create_library(user=admin_user, photo="libraries/photos/2026/02/a.jpg")
        community_photo = LibraryPhoto.objects.create(
            library=library,
            created_by=user,
            photo="libraries/user_photos/2026/02/admin-approved.jpg",
        )

        response = admin_client.post(
            reverse("admin:libraries_libraryphoto_changelist"),
            {"action": "approve_photos", "_selected_action": [community_photo.pk]},
        )

        assert response.status_code == 302
        library.refresh_from_db()
        assert library.photo.name == "libraries/user_photos/2026/02/admin-approved.jpg"
        assert library.photo_origin == "user"
        assert library.photo_author == user
