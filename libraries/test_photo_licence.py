"""Tests for image origin, licence and author in the API and the export.

Covers the shared licence rules, the library and photo API responses, the
query count of the list endpoint and the GeoJSON export properties.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from ninja_jwt.tokens import RefreshToken

from libraries import library_export
from libraries.library_export import generate_library_export
from libraries.models import Library, LibraryPhoto
from libraries.photo_licence import CC_BY_SA_4_0, build_photo_licence_fields
from users.models import ContributorAgreementAcceptance

User = get_user_model()

SOURCE_URL = "https://example.org/images/original.jpg"
NO_LICENCE = {
    "photo_license": None,
    "photo_author": None,
}


def _accept_agreement(*, user: Any, version: str = "2026-01") -> None:
    """Record a contributor agreement acceptance for a user.
    Uses an arbitrary version because any version grants the licence."""
    ContributorAgreementAcceptance.objects.create(
        user=user,
        agreement_version=version,
        channel=ContributorAgreementAcceptance.Channel.WEB_CREDENTIAL_REGISTRATION,
    )


def _create_library(*, index: int = 1, **overrides: Any) -> Library:
    """Create an approved library with a main photo.
    Accepts keyword overrides for the photo origin fields under test."""
    defaults: dict[str, Any] = {
        "slug": f"licence-library-{index}",
        "name": f"Licence Library {index}",
        "photo": f"libraries/photos/licence-{index}.jpg",
        "location": Point(x=11.2 + index / 100, y=43.7, srid=4326),
        "address": f"Via Licence {index}",
        "city": "Florence",
        "country": "IT",
        "status": Library.Status.APPROVED,
    }
    defaults.update(overrides)
    return Library.objects.create(**defaults)


def _detail(*, client: Any, library: Library) -> dict[str, Any]:
    """Fetch the public detail response body for a library.
    Clears the cache first so rate limits never interfere."""
    cache.clear()
    response = client.get(f"/api/v1/libraries/{library.slug}")
    assert response.status_code == 200
    return response.json()


def _photo_fields(*, body: dict[str, Any]) -> dict[str, Any]:
    """Pick the four image licence fields from a response body.
    Keeps assertions focused on the contract under test."""
    return {
        name: body[name]
        for name in ("photo_origin", "photo_license", "photo_author", "photo_source_url")
    }


def _staff_headers() -> dict[str, str]:
    """Build a Bearer header for a new staff user.
    Lets tests call the moderation endpoints."""
    staff = User.objects.create_user(username="licence-staff", password="x", is_staff=True)
    return _headers(user=staff)


def _headers(*, user: Any) -> dict[str, str]:
    """Build a Bearer header for a user.
    Generates a valid JWT access token for authenticated calls."""
    token = str(RefreshToken.for_user(user).access_token)
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class TestBuildPhotoLicenceFields:
    """Unit tests for the shared licence rules."""

    def test_no_photo_returns_all_null(self) -> None:
        """Verify a library without a photo exposes no photo details.
        Even a stored origin is hidden when the photo is missing."""
        fields = build_photo_licence_fields(
            has_photo=False,
            origin="user",
            author_username="jane",
            author_has_agreement=True,
            source_url=SOURCE_URL,
        )

        assert fields == {
            "photo_origin": None,
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }

    def test_blank_origin_with_photo_is_unknown(self) -> None:
        """Verify a photo without a stored origin reports unknown.
        Keeps legacy rows distinguishable from libraries without a photo."""
        fields = build_photo_licence_fields(
            has_photo=True,
            origin="",
            author_username=None,
            author_has_agreement=False,
            source_url="",
        )

        assert fields["photo_origin"] == "unknown"
        assert fields["photo_license"] is None
        assert fields["photo_source_url"] is None

    def test_user_origin_without_author_is_never_licensed(self) -> None:
        """Verify an image is never CC BY-SA without an author.
        A deleted author leaves the origin but drops licence and author."""
        fields = build_photo_licence_fields(
            has_photo=True,
            origin="user",
            author_username=None,
            author_has_agreement=True,
            source_url="",
        )

        assert fields["photo_origin"] == "user"
        assert fields["photo_license"] is None
        assert fields["photo_author"] is None

    def test_external_origin_with_agreement_is_not_licensed(self) -> None:
        """Verify only user origin can be CC BY-SA.
        An external photo keeps its source URL and gets no licence."""
        fields = build_photo_licence_fields(
            has_photo=True,
            origin="external",
            author_username="jane",
            author_has_agreement=True,
            source_url=SOURCE_URL,
        )

        assert fields == {
            "photo_origin": "external",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": SOURCE_URL,
        }


@pytest.mark.django_db
class TestLibraryApiPhotoLicence:
    """Tests for the image fields on public library responses."""

    def test_user_photo_with_acceptance_is_cc_by_sa(self, client, user) -> None:
        """Verify an accepted contributor gets the licence and credit.
        The response names the author's username."""
        _accept_agreement(user=user)
        library = _create_library(photo_origin="user", photo_author=user)

        body = _detail(client=client, library=library)

        assert _photo_fields(body=body) == {
            "photo_origin": "user",
            "photo_license": CC_BY_SA_4_0,
            "photo_author": "testuser",
            "photo_source_url": None,
        }

    def test_any_agreement_version_counts(self, client, user) -> None:
        """Verify an acceptance of an older version still grants the licence.
        The agreement covers contributions made before or after acceptance."""
        _accept_agreement(user=user, version="2020-01")
        library = _create_library(photo_origin="user", photo_author=user)

        body = _detail(client=client, library=library)

        assert body["photo_license"] == CC_BY_SA_4_0

    def test_user_photo_without_acceptance_has_no_licence(self, client, user) -> None:
        """Verify a contributor without an acceptance gets no licence or credit.
        The origin stays user so reusers still see where the photo came from."""
        library = _create_library(photo_origin="user", photo_author=user)

        body = _detail(client=client, library=library)

        assert _photo_fields(body=body) == {
            "photo_origin": "user",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }

    def test_deleted_author_has_no_licence(self, client, user) -> None:
        """Verify deleting the author clears licence and author.
        The acceptance row survives with a null user, so it cannot grant a licence."""
        _accept_agreement(user=user)
        library = _create_library(photo_origin="user", photo_author=user)
        user.delete()

        body = _detail(client=client, library=library)

        assert _photo_fields(body=body) == {
            "photo_origin": "user",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }

    def test_moderation_preview_describes_staged_photo(self, client, user) -> None:
        """Verify the staff preview of a staged photo credits the library owner.
        The live photo's author and agreement must not leak into the preview."""
        live_author = User.objects.create_user(username="live-author", password="x")
        _accept_agreement(user=live_author)
        library = _create_library(
            photo_origin="user",
            photo_author=live_author,
            created_by=user,
            pending_changes={},
            pending_photo="libraries/pending_photos/staged.jpg",
        )

        cache.clear()
        response = client.get(
            f"/api/v1/libraries/moderation/{library.slug}", **_staff_headers()
        )

        assert response.status_code == 200
        assert _photo_fields(body=response.json()) == {
            "photo_origin": "user",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }

        _accept_agreement(user=user)
        cache.clear()
        response = client.get(
            f"/api/v1/libraries/moderation/{library.slug}",
            **_headers(user=User.objects.get(username="licence-staff")),
        )

        assert response.json()["photo_author"] == "testuser"
        assert response.json()["photo_license"] == CC_BY_SA_4_0

    def test_external_photo_exposes_source_url(self, client, user) -> None:
        """Verify an external photo shows its source URL and no licence.
        Reusers can follow the URL to the original image."""
        _accept_agreement(user=user)
        library = _create_library(
            photo_origin="external",
            photo_author=user,
            photo_source_url=SOURCE_URL,
        )

        body = _detail(client=client, library=library)

        assert _photo_fields(body=body) == {
            "photo_origin": "external",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": SOURCE_URL,
        }

    def test_unknown_origin_has_no_licence(self, client) -> None:
        """Verify a photo with an unknown origin has no licence information.
        Legacy photos with a blank origin report unknown as well."""
        unknown = _create_library(index=1, photo_origin="unknown")
        legacy = _create_library(index=2, photo_origin="")

        assert _photo_fields(body=_detail(client=client, library=unknown)) == {
            "photo_origin": "unknown",
            **NO_LICENCE,
            "photo_source_url": None,
        }
        assert _detail(client=client, library=legacy)["photo_origin"] == "unknown"

    def test_library_without_photo_has_all_null(self, client, user) -> None:
        """Verify a library without a photo has all four fields null.
        The stored origin is ignored when there is no image."""
        library = _create_library(photo="", photo_origin="user", photo_author=user)

        body = _detail(client=client, library=library)

        assert _photo_fields(body=body) == {
            "photo_origin": None,
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }

    def test_list_endpoint_query_count_does_not_grow(self, client, django_user_model) -> None:
        """Verify the list endpoint does not query per library.
        Compares the query count for one library and for several with user photos."""
        first_author = django_user_model.objects.create_user(username="author-0", password="x")
        _accept_agreement(user=first_author)
        _create_library(index=0, photo_origin="user", photo_author=first_author)

        cache.clear()
        with CaptureQueriesContext(connection) as single:
            response = client.get("/api/v1/libraries/")
        assert response.status_code == 200

        for index in range(1, 6):
            author = django_user_model.objects.create_user(
                username=f"author-{index}", password="x"
            )
            if index % 2:
                _accept_agreement(user=author)
            _create_library(index=index, photo_origin="user", photo_author=author)

        cache.clear()
        with CaptureQueriesContext(connection) as several:
            response = client.get("/api/v1/libraries/")
        assert response.status_code == 200
        assert len(response.json()["items"]) == 6
        assert len(several) == len(single)


@pytest.mark.django_db
class TestPhotoApiLicence:
    """Tests for the image fields on community photo responses."""

    def _create_photo(self, *, user: Any) -> LibraryPhoto:
        """Create a community photo uploaded by a user.
        Attaches it to a fresh approved library."""
        library = _create_library(index=50)
        return LibraryPhoto.objects.create(
            library=library,
            created_by=user,
            photo="libraries/user_photos/licence.jpg",
        )

    def test_moderation_photo_with_acceptance(self, client, user) -> None:
        """Verify the moderation list licenses photos of accepted contributors.
        The staff response names the uploader as author."""
        _accept_agreement(user=user)
        self._create_photo(user=user)

        response = client.get(
            "/api/v1/libraries/moderation/photos", **_staff_headers()
        )

        assert response.status_code == 200
        assert _photo_fields(body=response.json()["items"][0]) == {
            "photo_origin": "user",
            "photo_license": CC_BY_SA_4_0,
            "photo_author": "testuser",
            "photo_source_url": None,
        }

    def test_moderation_photo_without_acceptance(self, client, user) -> None:
        """Verify the moderation list gives no licence without an acceptance.
        The origin stays user."""
        self._create_photo(user=user)

        response = client.get(
            "/api/v1/libraries/moderation/photos", **_staff_headers()
        )

        assert _photo_fields(body=response.json()["items"][0]) == {
            "photo_origin": "user",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }

    def test_moderation_photo_update_response_includes_licence(self, client, user) -> None:
        """Verify the moderation update response carries the licence fields.
        Covers the single-object path through the shared annotation."""
        _accept_agreement(user=user)
        photo = self._create_photo(user=user)

        response = client.patch(
            f"/api/v1/libraries/moderation/photos/{photo.pk}",
            data={"status": "rejected"},
            content_type="application/json",
            **_staff_headers(),
        )

        assert response.status_code == 200
        assert response.json()["photo_license"] == CC_BY_SA_4_0

    def test_moderation_photo_with_deleted_uploader(self, client, user) -> None:
        """Verify a photo whose uploader was deleted has no licence.
        Origin stays user and the author is null."""
        _accept_agreement(user=user)
        self._create_photo(user=user)
        user.delete()

        response = client.get(
            "/api/v1/libraries/moderation/photos", **_staff_headers()
        )

        assert _photo_fields(body=response.json()["items"][0]) == {
            "photo_origin": "user",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }

    def test_contribution_photos_with_and_without_acceptance(self, client, user) -> None:
        """Verify the contributions list reflects the uploader's acceptance.
        The same uploader is unlicensed before and licensed after accepting."""
        self._create_photo(user=user)

        before = client.get("/api/v1/libraries/mine/photos", **_headers(user=user))
        _accept_agreement(user=user)
        after = client.get("/api/v1/libraries/mine/photos", **_headers(user=user))

        assert _photo_fields(body=before.json()["items"][0]) == {
            "photo_origin": "user",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }
        assert _photo_fields(body=after.json()["items"][0]) == {
            "photo_origin": "user",
            "photo_license": CC_BY_SA_4_0,
            "photo_author": "testuser",
            "photo_source_url": None,
        }


@pytest.fixture
def export_directory(settings, tmp_path) -> Path:
    """Configure an isolated media directory for one export test.
    Uses a stable public base URL so photo URLs are deterministic."""
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.SITE_URL = "https://bookcorners.example"
    return Path(settings.MEDIA_ROOT) / library_export.EXPORT_DIRECTORY_NAME


def _export_properties(*, export_directory: Path) -> dict[str, dict[str, Any]]:
    """Generate the export and return feature properties keyed by slug.
    Reads the active GeoJSON file selected by the manifest."""
    generate_library_export()
    manifest = json.loads((export_directory / "latest.json").read_text(encoding="utf-8"))
    filename = manifest["export"]["geojson"]["filename"]
    payload = json.loads((export_directory / filename).read_text(encoding="utf-8"))
    return {
        feature["properties"]["slug"]: feature["properties"]
        for feature in payload["features"]
    }


@pytest.mark.django_db(transaction=True)
class TestExportPhotoLicence:
    """Tests for the image properties in the GeoJSON export."""

    def test_every_case_is_exported(self, export_directory: Path, django_user_model) -> None:
        """Verify the export covers user, external, unknown and missing photos.
        Each library has one case from the acceptance criteria."""
        accepted = django_user_model.objects.create_user(username="accepted", password="x")
        _accept_agreement(user=accepted, version="2020-01")
        not_accepted = django_user_model.objects.create_user(username="plain", password="x")
        deleted = django_user_model.objects.create_user(username="gone", password="x")
        _accept_agreement(user=deleted)
        _create_library(index=1, photo_origin="user", photo_author=accepted)
        _create_library(index=2, photo_origin="user", photo_author=not_accepted)
        _create_library(index=3, photo_origin="user", photo_author=deleted)
        _create_library(
            index=4,
            photo_origin="external",
            photo_source_url=SOURCE_URL,
        )
        _create_library(index=5, photo_origin="unknown")
        _create_library(index=6, photo="", photo_origin="user", photo_author=accepted)
        deleted.delete()

        properties = _export_properties(export_directory=export_directory)

        def fields(index: int) -> dict[str, Any]:
            """Pick the four image properties of one exported library.
            Keeps the assertions below short."""
            return _photo_fields(body=properties[f"licence-library-{index}"])

        assert fields(1) == {
            "photo_origin": "user",
            "photo_license": CC_BY_SA_4_0,
            "photo_author": "accepted",
            "photo_source_url": None,
        }
        assert fields(2) == {
            "photo_origin": "user",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }
        assert fields(3) == fields(2)
        assert fields(4) == {
            "photo_origin": "external",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": SOURCE_URL,
        }
        assert fields(5) == {
            "photo_origin": "unknown",
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }
        assert fields(6) == {
            "photo_origin": None,
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }

    def test_schema_and_metadata_describe_images(self, export_directory: Path) -> None:
        """Verify the schema version, header and metadata describe image licensing.
        The ODbL block stays and the old photo notice is replaced."""
        _create_library()

        generate_library_export()
        manifest = json.loads((export_directory / "latest.json").read_text(encoding="utf-8"))
        export = manifest["export"]
        metadata = json.loads(
            (export_directory / export["metadata"]["filename"]).read_text(encoding="utf-8")
        )
        header = (export_directory / export["geojson"]["filename"]).read_bytes().splitlines()[0]

        assert library_export.EXPORT_SCHEMA_VERSION == 2
        assert metadata["metadata_version"] == 3
        assert metadata["schema"]["version"] == 2
        assert b'"book_corners_schema_version":2' in header
        assert metadata["license"]["name"].endswith("ODbL) v1.0")
        assert "photo_notice" not in metadata
        assert set(metadata["images"]) == {"license", "null_license", "hosting"}
        assert "per image" in metadata["images"]["license"]
        assert "no licence information" in metadata["images"]["null_license"]
        assert "your own hosting" in metadata["images"]["hosting"]
        for name in ("photo_origin", "photo_license", "photo_author", "photo_source_url"):
            assert metadata["schema"]["definition"]["properties"][name]["type"] == [
                "string",
                "null",
            ]
