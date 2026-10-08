"""Tests for the photo credit line on the library detail page.

Covers each origin and licence case and checks that rendering the credit
never loads the author's password hash.
"""

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from libraries.models import Library
from users.models import ContributorAgreementAcceptance

User = get_user_model()

SOURCE_URL = "https://example.org/images/original.jpg"
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"


def _accept_agreement(*, user: Any) -> None:
    """Record a contributor agreement acceptance for a user.
    Any acceptance makes the user's uploads CC BY-SA."""
    ContributorAgreementAcceptance.objects.create(
        user=user,
        agreement_version="2026-01",
        channel=ContributorAgreementAcceptance.Channel.WEB_CREDENTIAL_REGISTRATION,
    )


def _create_library(**overrides: Any) -> Library:
    """Create an approved library with a main photo.
    Accepts keyword overrides for the photo origin fields under test."""
    defaults: dict[str, Any] = {
        "name": "Credit Library",
        "photo": "libraries/photos/credit.jpg",
        "location": Point(x=11.25, y=43.77, srid=4326),
        "address": "Via Credit 1",
        "city": "Florence",
        "country": "IT",
        "status": Library.Status.APPROVED,
    }
    defaults.update(overrides)
    return Library.objects.create(**defaults)


def _detail_html(*, client: Any, library: Library) -> str:
    """Fetch the rendered detail page for a library.
    Asserts a successful response so each test can focus on the credit."""
    response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
class TestLibraryDetailPhotoCredit:
    """Tests for the credit line under the main photo.
    Each origin and licence case renders its own credit or none."""

    def test_cc_by_sa_photo_credits_author_and_links_licence(self, client):
        """Verify a CC BY-SA photo names its author and links the licence.
        Visitors need both to reuse the photo correctly."""
        author = User.objects.create_user(username="photographer", password="pw")
        _accept_agreement(user=author)
        library = _create_library(
            photo_origin=Library.PhotoOrigin.USER,
            photo_author=author,
        )

        content = _detail_html(client=client, library=library)

        assert 'data-photo-credit="cc-by-sa"' in content
        assert "Photo by photographer" in content
        assert f'href="{LICENCE_URL}"' in content
        assert "CC BY-SA 4.0" in content

    def test_user_photo_without_agreement_has_no_credit(self, client):
        """Verify a user photo without an agreement shows no credit line.
        Without the agreement the photo has no licence to state."""
        author = User.objects.create_user(username="no-agreement", password="pw")
        library = _create_library(
            photo_origin=Library.PhotoOrigin.USER,
            photo_author=author,
        )

        content = _detail_html(client=client, library=library)

        assert "data-photo-credit" not in content
        assert "no-agreement" not in content

    def test_external_photo_links_source_url(self, client):
        """Verify an external photo with a known source links to it.
        Lets visitors see where the photo was published first."""
        library = _create_library(
            photo_origin=Library.PhotoOrigin.EXTERNAL,
            photo_source_url=SOURCE_URL,
        )

        content = _detail_html(client=client, library=library)

        assert 'data-photo-credit="external"' in content
        assert f'href="{SOURCE_URL}"' in content
        assert "Photo from an external source" in content

    def test_external_photo_without_source_shows_plain_credit(self, client):
        """Verify an external photo with no source shows unlinked text.
        Avoids rendering an empty link when the source is unknown."""
        library = _create_library(photo_origin=Library.PhotoOrigin.EXTERNAL)

        content = _detail_html(client=client, library=library)

        assert 'data-photo-credit="external"' in content
        assert "Photo from an external source" in content
        assert 'rel="noopener noreferrer nofollow"' not in content

    def test_unknown_photo_has_no_credit(self, client):
        """Verify a photo of unknown origin shows no credit line.
        Unknown origin gives nothing neutral and accurate to say."""
        library = _create_library(photo_origin=Library.PhotoOrigin.UNKNOWN)

        content = _detail_html(client=client, library=library)

        assert "data-photo-credit" not in content

    def test_library_without_photo_has_no_credit(self, client):
        """Verify the placeholder image gets no credit line.
        The placeholder is a site asset, not a contributed photo."""
        library = _create_library(photo="")

        content = _detail_html(client=client, library=library)

        assert "data-photo-credit" not in content

    def test_credit_does_not_load_author_password(self, client):
        """Verify resolving the credit never selects the author's password.
        Keeps password hashes out of public page rendering."""
        author = User.objects.create_user(username="private-author", password="pw")
        _accept_agreement(user=author)
        library = _create_library(
            photo_origin=Library.PhotoOrigin.USER,
            photo_author=author,
        )

        with CaptureQueriesContext(connection) as queries:
            content = _detail_html(client=client, library=library)

        password_queries = [
            query["sql"]
            for query in queries.captured_queries
            if '"users_user"."password"' in query["sql"]
        ]
        assert "Photo by private-author" in content
        assert password_queries == []
