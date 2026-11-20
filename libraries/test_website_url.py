import json

import pytest
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from django.urls import reverse
from ninja_jwt.tokens import RefreshToken

from libraries.geojson_import import parse_geojson
from libraries.models import Library
from libraries.tests import _build_uploaded_photo
from libraries.validators import (
    is_website_url,
    normalize_website_url,
    validate_website_url,
)

UNSAFE_WEBSITES = [
    "javascript:alert(document.cookie)",
    "JavaScript:alert(1)",
    " javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "ftp://example.org/file",
    "//example.org/path",
    "not a website",
]


@pytest.fixture
def user_jwt(user):
    """Generate a valid JWT access token for the default test user.
    Provides bearer credentials for authenticated API requests."""
    return str(RefreshToken.for_user(user).access_token)


def _create_library(*, user, status: str, website: str = "") -> Library:
    """Create a library owned by the test user with the given status.
    Gives each test a fresh row to submit edits against."""
    return Library.objects.create(
        name="Website Test Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city="Florence",
        country="IT",
        website=website,
        status=status,
        created_by=user,
    )


def _patch_website(*, client, slug: str, website: str, token: str):
    """Send a multipart PATCH that changes only the website field.
    Matches how mobile clients submit owner edits."""
    return client.patch(
        f"/api/v1/libraries/{slug}",
        data=encode_multipart(BOUNDARY, {"website": website}),
        content_type=MULTIPART_CONTENT,
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


class TestWebsiteUrlValidator:
    """Tests for the shared http and https website rule."""

    @pytest.mark.parametrize(
        "value",
        ["https://example.org", "http://example.org/path?q=1", "HTTPS://EXAMPLE.ORG"],
    )
    def test_http_and_https_urls_are_accepted(self, value):
        """Verify well-formed http and https URLs pass the rule.
        Keeps normal website links working."""
        assert is_website_url(value=value) is True
        validate_website_url(value)

    @pytest.mark.parametrize("value", UNSAFE_WEBSITES)
    def test_other_schemes_and_malformed_values_are_rejected(self, value):
        """Verify script schemes and non-URLs fail the rule.
        Blocks values that would run code or break the link."""
        assert is_website_url(value=value) is False
        with pytest.raises(ValidationError):
            validate_website_url(value)

    def test_validator_rejects_bare_domain(self):
        """Verify the validator itself does not guess a missing scheme.
        Callers add https:// with normalize_website_url first."""
        assert is_website_url(value="example.org") is False

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("example.org", "https://example.org"),
            (" www.example.org/path ", "https://www.example.org/path"),
            ("http://example.org", "http://example.org"),
            ("javascript:alert(1)", "javascript:alert(1)"),
            ("", ""),
        ],
    )
    def test_normalize_adds_https_only_when_scheme_is_missing(self, value, expected):
        """Verify normalization prefixes bare domains and keeps other schemes.
        Script schemes stay as they are so validation still rejects them."""
        assert normalize_website_url(value) == expected

    def test_empty_value_is_allowed_by_validator_but_not_a_url(self):
        """Verify the field stays optional while empty is not a link.
        Lets templates hide the link when no website is set."""
        validate_website_url("")
        assert is_website_url(value="") is False
        assert is_website_url(value=None) is False


@pytest.mark.django_db
class TestLibraryModelWebsite:
    """Tests for model level website validation."""

    def test_full_clean_rejects_javascript_website(self, user):
        """Verify model validation blocks script URLs.
        Covers the web form, admin and staff forms that call full_clean."""
        library = _create_library(user=user, status=Library.Status.PENDING)
        library.website = "javascript:alert(1)"

        with pytest.raises(ValidationError) as exc_info:
            library.full_clean()

        assert "website" in exc_info.value.message_dict

    def test_safe_website_hides_unsafe_stored_value(self, user):
        """Verify rows stored before validation expose no unsafe link.
        Protects pages that read existing data."""
        library = _create_library(
            user=user,
            status=Library.Status.APPROVED,
            website="javascript:alert(1)",
        )

        assert library.safe_website == ""

    def test_safe_website_returns_valid_url(self, user):
        """Verify valid websites pass through unchanged.
        Keeps normal links visible on the detail page."""
        library = _create_library(
            user=user,
            status=Library.Status.APPROVED,
            website="https://example.org/bookcase",
        )

        assert library.safe_website == "https://example.org/bookcase"


@pytest.mark.django_db
class TestApiSubmitWebsite:
    """Tests for website validation on POST /api/v1/libraries/."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def _submit(self, *, client, token: str, website: str):
        """Submit a valid library payload with the given website.
        Isolates the website field as the only variable."""
        return client.post(
            "/api/v1/libraries/",
            data={
                "name": "Website Submit Library",
                "address": "10 Rue de la Paix",
                "city": "Paris",
                "country": "FR",
                "latitude": "48.8698",
                "longitude": "2.3311",
                "website": website,
                "photo": _build_uploaded_photo(),
            },
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    @pytest.mark.parametrize("website", UNSAFE_WEBSITES)
    def test_unsafe_website_is_rejected(self, client, user_jwt, website, tmp_path, settings):
        """Verify the API refuses script and malformed website values.
        Closes the stored XSS path through API submissions."""
        settings.MEDIA_ROOT = tmp_path

        response = self._submit(client=client, token=user_jwt, website=website)

        assert response.status_code == 422
        assert "website" in json.dumps(response.json())
        assert not Library.objects.filter(name="Website Submit Library").exists()

    def test_https_website_is_stored(self, client, user_jwt, tmp_path, settings):
        """Verify a normal https website is still accepted.
        Keeps legitimate submissions working."""
        settings.MEDIA_ROOT = tmp_path

        response = self._submit(
            client=client,
            token=user_jwt,
            website=" https://example.org/bookcase ",
        )

        assert response.status_code == 201
        library = Library.objects.get(id=response.json()["id"])
        assert library.website == "https://example.org/bookcase"

    def test_bare_domain_website_gets_https(self, client, user_jwt, tmp_path, settings):
        """Verify a website typed without a scheme is stored with https://.
        Mobile clients send the field as typed, so this keeps them working."""
        settings.MEDIA_ROOT = tmp_path

        response = self._submit(client=client, token=user_jwt, website="example.org/bookcase")

        assert response.status_code == 201
        library = Library.objects.get(id=response.json()["id"])
        assert library.website == "https://example.org/bookcase"

    def test_empty_website_is_stored(self, client, user_jwt, tmp_path, settings):
        """Verify the website field stays optional.
        Submissions without a website keep working."""
        settings.MEDIA_ROOT = tmp_path

        response = self._submit(client=client, token=user_jwt, website="")

        assert response.status_code == 201
        assert Library.objects.get(id=response.json()["id"]).website == ""


@pytest.mark.django_db
class TestApiUpdateWebsite:
    """Tests for website validation on PATCH /api/v1/libraries/{slug}."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    def test_pending_library_rejects_javascript_website(self, client, user, user_jwt):
        """Verify owners cannot write a script URL to a pending library.
        This path saves directly without moderation."""
        library = _create_library(user=user, status=Library.Status.PENDING)

        response = _patch_website(
            client=client,
            slug=library.slug,
            website="javascript:alert(1)",
            token=user_jwt,
        )

        library.refresh_from_db()
        assert response.status_code == 422
        assert library.website == ""

    def test_approved_library_rejects_javascript_website(self, client, user, user_jwt):
        """Verify owners cannot stage a script URL for moderation.
        Staff approval would otherwise publish the value."""
        library = _create_library(
            user=user,
            status=Library.Status.APPROVED,
            website="https://example.org/old",
        )

        response = _patch_website(
            client=client,
            slug=library.slug,
            website="javascript:alert(1)",
            token=user_jwt,
        )

        library.refresh_from_db()
        assert response.status_code == 422
        assert library.website == "https://example.org/old"
        assert library.pending_changes is None

    def test_pending_library_accepts_https_website(self, client, user, user_jwt):
        """Verify owners can still set a normal website.
        Keeps legitimate edits working."""
        library = _create_library(user=user, status=Library.Status.PENDING)

        response = _patch_website(
            client=client,
            slug=library.slug,
            website="https://example.org/new",
            token=user_jwt,
        )

        library.refresh_from_db()
        assert response.status_code == 200
        assert library.website == "https://example.org/new"

    def test_pending_library_can_clear_website(self, client, user, user_jwt):
        """Verify owners can remove a website with an empty value.
        Clearing must not be treated as an invalid URL."""
        library = _create_library(
            user=user,
            status=Library.Status.PENDING,
            website="https://example.org/old",
        )

        response = _patch_website(client=client, slug=library.slug, website="", token=user_jwt)

        library.refresh_from_db()
        assert response.status_code == 200
        assert library.website == ""


@pytest.mark.django_db
class TestLibraryDetailWebsiteLink:
    """Tests for the website link on the public detail page."""

    def test_stored_javascript_website_renders_no_link(self, client, user):
        """Verify an unsafe stored value produces no anchor or text.
        Protects visitors from rows saved before validation existed."""
        library = _create_library(
            user=user,
            status=Library.Status.APPROVED,
            website="javascript:alert(1)",
        )

        response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))

        content = response.content.decode()
        assert response.status_code == 200
        assert "javascript:alert(1)" not in content
        assert "Website:" not in content

    def test_https_website_renders_link(self, client, user):
        """Verify a valid website still renders as a link.
        Keeps the detail page useful for normal data."""
        library = _create_library(
            user=user,
            status=Library.Status.APPROVED,
            website="https://example.org/bookcase",
        )

        response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))

        assert 'href="https://example.org/bookcase"' in response.content.decode()


class TestGeojsonImportWebsite:
    """Tests for website filtering in the GeoJSON import parser."""

    def _parse_website(self, *, properties: dict) -> str:
        """Parse one feature with the given properties and return its website.
        Keeps each case focused on the website tags."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "node/1",
                    "geometry": {"type": "Point", "coordinates": [11.25, 43.77]},
                    "properties": {"@id": "node/1", **properties},
                }
            ],
        }
        return parse_geojson(geojson)[0].website

    def test_javascript_website_tag_is_dropped(self):
        """Verify imported OSM data cannot carry a script URL.
        OSM tags are public input and need the same rule."""
        assert self._parse_website(properties={"website": "javascript:alert(1)"}) == ""

    def test_https_website_tag_is_kept(self):
        """Verify valid OSM website tags still import.
        Keeps normal imported data intact."""
        assert self._parse_website(properties={"website": "https://example.org"}) == "https://example.org"

    def test_bare_domain_website_tag_gets_https(self):
        """Verify OSM values without a scheme are imported with https://.
        They would otherwise render as relative links on the site."""
        assert self._parse_website(properties={"website": "example.org"}) == "https://example.org"

    def test_free_text_website_tag_is_dropped(self):
        """Verify OSM values that are not URLs are not imported.
        Text such as notes cannot become a working link."""
        assert self._parse_website(properties={"website": "not yet available"}) == ""
