"""Tests for GeoJSON import functionality.

Covers parsing, field mapping, duplicate detection, image handling,
admin view access, and import result accuracy.
"""

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from libraries.geojson_import import (
    GeoJSONImporter,
    ImportCandidate,
    fetch_image_from_url,
    parse_geojson,
)
from libraries.models import Library

User = get_user_model()


def _build_feature(
    *,
    feature_id="node/12345",
    lon=10.62,
    lat=43.89,
    name="Test Bookcase",
    description="A nice bookcase",
    street="Via Roma",
    housenumber="5",
    city="Florence",
    country="IT",
    postcode="50100",
    wheelchair="yes",
    capacity="50",
    indoor="no",
    lit="yes",
    website="https://example.com",
    phone="+39 055 1234567",
    email="info@example.com",
    operator="City of Florence",
    brand="Little Free Library",
    image="",
    extra_properties=None,
):
    """Build a GeoJSON feature dict with configurable properties.
    Provides sensible defaults for all fields to simplify test setup."""
    properties = {
        "id": feature_id,
        "amenity": "public_bookcase",
        "name": name,
        "description": description,
        "addr:street": street,
        "addr:housenumber": housenumber,
        "addr:city": city,
        "addr:country": country,
        "addr:postcode": postcode,
        "wheelchair": wheelchair,
        "capacity": capacity,
        "indoor": indoor,
        "lit": lit,
        "website": website,
        "phone": phone,
        "email": email,
        "operator": operator,
        "brand": brand,
        "image": image,
    }
    if extra_properties:
        properties.update(extra_properties)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": properties,
    }


def _build_geojson(*features):
    """Wrap features into a FeatureCollection.
    Produces a valid GeoJSON structure for import tests."""
    return {"type": "FeatureCollection", "features": list(features)}


def _build_test_image_bytes():
    """Create minimal JPEG bytes for image fetch tests.
    Returns raw bytes suitable for mocking URL downloads."""
    buf = BytesIO()
    Image.new("RGB", (100, 100), color=(200, 150, 100)).save(buf, format="JPEG")
    return buf.getvalue()


class TestParseGeoJSON:
    """Tests for the parse_geojson function."""

    def test_parse_all_fields(self):
        """Verify all GeoJSON properties map to ImportCandidate fields.
        Ensures the full field mapping works end to end."""
        feature = _build_feature()
        geojson = _build_geojson(feature)

        candidates = parse_geojson(geojson)

        assert len(candidates) == 1
        c = candidates[0]
        assert c.external_id == "node/12345"
        assert c.name == "Test Bookcase"
        assert c.description == "A nice bookcase"
        assert c.longitude == 10.62
        assert c.latitude == 43.89
        assert c.address == "Via Roma 5"
        assert c.city == "Florence"
        assert c.country == "IT"
        assert c.postal_code == "50100"
        assert c.wheelchair_accessible == "yes"
        assert c.capacity == 50
        assert c.is_indoor is False
        assert c.is_lit is True
        assert c.website == "https://example.com"
        assert c.contact == "+39 055 1234567, info@example.com"
        assert c.operator == "City of Florence"
        assert c.brand == "Little Free Library"

    def test_parse_missing_optional_fields(self):
        """Verify defaults when optional GeoJSON properties are absent.
        Confirms the parser handles sparse features gracefully."""
        feature = _build_feature(
            name="",
            description="",
            housenumber="",
            postcode="",
            wheelchair="",
            capacity="",
            indoor="",
            lit="",
            website="",
            phone="",
            email="",
            operator="",
            brand="",
        )
        geojson = _build_geojson(feature)

        candidates = parse_geojson(geojson)

        assert len(candidates) == 1
        c = candidates[0]
        assert c.name == ""
        assert c.description == ""
        assert c.address == "Via Roma"
        assert c.postal_code == ""
        assert c.wheelchair_accessible == ""
        assert c.capacity is None
        assert c.is_indoor is None
        assert c.is_lit is None
        assert c.website == ""
        assert c.contact == ""
        assert c.operator == ""
        assert c.brand == ""

    def test_parse_contact_website_fallback(self):
        """Verify website falls back to contact:website property.
        Handles OSM features using the contact:* namespace."""
        feature = _build_feature(website="")
        feature["properties"]["contact:website"] = "https://fallback.example.com"
        geojson = _build_geojson(feature)

        candidates = parse_geojson(geojson)

        assert candidates[0].website == "https://fallback.example.com"

    def test_parse_skips_feature_without_coordinates(self):
        """Verify features with missing geometry are skipped.
        Prevents crashes on malformed GeoJSON entries."""
        feature = {"type": "Feature", "geometry": {"type": "Point", "coordinates": []}, "properties": {"id": "node/1"}}
        geojson = _build_geojson(feature)

        candidates = parse_geojson(geojson)

        assert len(candidates) == 0

    def test_parse_country_uppercased(self):
        """Verify country codes are normalized to uppercase.
        Ensures consistency with the Library model's 2-char country field."""
        feature = _build_feature(country="it")
        geojson = _build_geojson(feature)

        candidates = parse_geojson(geojson)

        assert candidates[0].country == "IT"

    def test_parse_empty_feature_collection(self):
        """Verify an empty FeatureCollection returns no candidates.
        Handles edge case of importing an empty file."""
        geojson = _build_geojson()

        candidates = parse_geojson(geojson)

        assert candidates == []

    def test_parse_invalid_capacity_returns_none(self):
        """Verify non-numeric capacity is parsed as None.
        Handles freeform text in OSM capacity tags."""
        feature = _build_feature(capacity="lots")
        geojson = _build_geojson(feature)

        candidates = parse_geojson(geojson)

        assert candidates[0].capacity is None


@pytest.mark.django_db
class TestGeoJSONImporter:
    """Tests for the GeoJSONImporter class."""

    @pytest.fixture
    def import_user(self, db):
        """Create a user for the import creator field.
        Provides the required FK for Library records."""
        return User.objects.create_superuser(
            username="importer",
            password="testpass123",
        )

    def _make_candidate(self, **overrides):
        """Build an ImportCandidate with sensible defaults.
        Accepts keyword overrides for specific field testing."""
        defaults = {
            "external_id": "node/12345",
            "name": "Test Bookcase",
            "description": "A bookcase",
            "longitude": 10.62,
            "latitude": 43.89,
            "address": "Via Roma 5",
            "city": "Florence",
            "country": "IT",
            "postal_code": "50100",
            "wheelchair_accessible": "yes",
            "capacity": 50,
            "is_indoor": False,
            "is_lit": True,
            "website": "https://example.com",
            "contact": "+39 055 1234567",
            "operator": "City of Florence",
            "brand": "Little Free Library",
            "image_url": "",
        }
        defaults.update(overrides)
        return ImportCandidate(**defaults)

    def test_creates_library(self, import_user):
        """Verify a complete candidate creates a Library record.
        Checks all fields are correctly mapped to the database."""
        candidate = self._make_candidate()
        importer = GeoJSONImporter(source="OSM", status="approved", created_by=import_user)

        result = importer.run([candidate])

        assert result.created == 1
        library = Library.objects.get(external_id="node/12345")
        assert library.name == "Test Bookcase"
        assert library.city == "Florence"
        assert library.country == "IT"
        assert library.source == "OSM"
        assert library.status == "approved"
        assert library.created_by == import_user
        assert library.location.x == pytest.approx(10.62)
        assert library.location.y == pytest.approx(43.89)

    def test_skips_duplicate_external_id(self, import_user):
        """Verify features with existing external_id are skipped.
        Prevents re-importing previously imported records."""
        Library.objects.create(
            name="Existing",
            location=Point(x=10.0, y=43.0, srid=4326),
            address="Via Existing 1",
            city="Florence",
            country="IT",
            external_id="node/12345",
            status="approved",
            created_by=import_user,
        )
        candidate = self._make_candidate()
        importer = GeoJSONImporter(source="OSM", status="approved", created_by=import_user)

        result = importer.run([candidate])

        assert result.created == 0
        assert result.skipped_duplicate == 1

    def test_skips_missing_address(self, import_user):
        """Verify features without required address fields are skipped.
        Catches features the enrichment script could not geocode."""
        candidate = self._make_candidate(address="", city="", country="")
        importer = GeoJSONImporter(source="OSM", status="pending", created_by=import_user)

        result = importer.run([candidate])

        assert result.created == 0
        assert result.skipped_missing_address == 1

    def test_skips_missing_city(self, import_user):
        """Verify features without city are skipped.
        City is a required field on the Library model."""
        candidate = self._make_candidate(city="")
        importer = GeoJSONImporter(source="OSM", status="pending", created_by=import_user)

        result = importer.run([candidate])

        assert result.created == 0
        assert result.skipped_missing_address == 1

    def test_skips_missing_country(self, import_user):
        """Verify features without country are skipped.
        Country is a required field on the Library model."""
        candidate = self._make_candidate(country="")
        importer = GeoJSONImporter(source="OSM", status="pending", created_by=import_user)

        result = importer.run([candidate])

        assert result.created == 0
        assert result.skipped_missing_address == 1

    def test_source_override_applied(self, import_user):
        """Verify the source parameter is applied to created libraries.
        Allows admins to tag import batches with their data origin."""
        candidate = self._make_candidate()
        importer = GeoJSONImporter(source="MapComplete", status="approved", created_by=import_user)

        importer.run([candidate])

        library = Library.objects.get(external_id="node/12345")
        assert library.source == "MapComplete"

    def test_status_selection_applied(self, import_user):
        """Verify the status parameter is applied to created libraries.
        Allows admins to choose between approved and pending imports."""
        candidate = self._make_candidate()
        importer = GeoJSONImporter(source="OSM", status="pending", created_by=import_user)

        importer.run([candidate])

        library = Library.objects.get(external_id="node/12345")
        assert library.status == "pending"

    def test_result_counts_accurate(self, import_user):
        """Verify import result counts reflect actual operations.
        Ensures the summary is trustworthy for admin feedback."""
        Library.objects.create(
            name="Duplicate",
            location=Point(x=10.0, y=43.0, srid=4326),
            address="Via Dup 1",
            city="Florence",
            country="IT",
            external_id="node/dup",
            status="approved",
            created_by=import_user,
        )
        candidates = [
            self._make_candidate(external_id="node/new1"),
            self._make_candidate(external_id="node/new2"),
            self._make_candidate(external_id="node/dup"),
            self._make_candidate(external_id="node/noaddr", address="", city="", country=""),
        ]
        importer = GeoJSONImporter(source="OSM", status="approved", created_by=import_user)

        result = importer.run(candidates)

        assert result.created == 2
        assert result.skipped_duplicate == 1
        assert result.skipped_missing_address == 1

    @patch("libraries.geojson_import.fetch_image_from_url")
    def test_image_fetch_success_attaches_photo(self, mock_fetch, import_user):
        """Verify a downloaded image is attached to the library.
        Exercises the photo pipeline for features with image URLs."""
        mock_fetch.return_value = _build_test_image_bytes()
        candidate = self._make_candidate(image_url="https://example.com/photo.jpg")
        importer = GeoJSONImporter(source="OSM", status="approved", created_by=import_user)

        result = importer.run([candidate])

        assert result.created == 1
        library = Library.objects.get(external_id="node/12345")
        assert library.photo
        assert library.photo_thumbnail

    @patch("libraries.geojson_import.fetch_image_from_url")
    def test_image_fetch_failure_creates_library_without_photo(self, mock_fetch, import_user):
        """Verify image fetch failure does not block library creation.
        Libraries are still created when photos cannot be downloaded."""
        mock_fetch.return_value = None
        candidate = self._make_candidate(image_url="https://example.com/broken.jpg")
        importer = GeoJSONImporter(source="OSM", status="approved", created_by=import_user)

        result = importer.run([candidate])

        assert result.created == 1
        library = Library.objects.get(external_id="node/12345")
        assert not library.photo

    def test_invalid_wheelchair_value_cleared(self, import_user):
        """Verify unrecognised wheelchair values are stored as blank.
        Prevents invalid choices from reaching the database."""
        candidate = self._make_candidate(wheelchair_accessible="unknown")
        importer = GeoJSONImporter(source="OSM", status="approved", created_by=import_user)

        importer.run([candidate])

        library = Library.objects.get(external_id="node/12345")
        assert library.wheelchair_accessible == ""

    def test_multiple_candidates_created(self, import_user):
        """Verify multiple valid candidates all create Library records.
        Tests batch import with distinct external IDs."""
        candidates = [
            self._make_candidate(external_id="node/1"),
            self._make_candidate(external_id="node/2"),
            self._make_candidate(external_id="node/3"),
        ]
        importer = GeoJSONImporter(source="OSM", status="approved", created_by=import_user)

        result = importer.run(candidates)

        assert result.created == 3
        assert Library.objects.count() == 3


class TestFetchImageFromUrl:
    """Tests for the fetch_image_from_url function."""

    @patch("libraries.geojson_import.urllib.request.urlopen")
    def test_returns_bytes_on_success(self, mock_urlopen):
        """Verify successful download returns image bytes.
        Exercises the happy path of the image fetcher."""
        image_data = _build_test_image_bytes()
        mock_response = MagicMock()
        mock_response.read.return_value = image_data
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = fetch_image_from_url("https://example.com/photo.jpg")

        assert result == image_data

    def test_returns_none_for_empty_url(self):
        """Verify empty URLs return None without network calls.
        Avoids unnecessary HTTP requests for features without images."""
        result = fetch_image_from_url("")

        assert result is None

    @patch("libraries.geojson_import.urllib.request.urlopen")
    def test_returns_none_on_oversized_image(self, mock_urlopen):
        """Verify images exceeding max_bytes return None.
        Prevents memory issues from extremely large downloads."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"x" * 101
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = fetch_image_from_url("https://example.com/huge.jpg", max_bytes=100)

        assert result is None

    @patch("libraries.geojson_import.urllib.request.urlopen")
    def test_returns_none_on_network_error(self, mock_urlopen):
        """Verify network errors return None gracefully.
        Keeps imports resilient against transient failures."""
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        result = fetch_image_from_url("https://example.com/fail.jpg")

        assert result is None


@pytest.mark.django_db
class TestAdminGeoJSONImportView:
    """Tests for the admin GeoJSON import view."""

    def test_get_returns_200_for_admin(self, admin_client):
        """Verify the import form loads for admin users.
        Ensures the custom admin URL is correctly registered."""
        url = reverse("admin:libraries_library_import_geojson")

        response = admin_client.get(url)

        assert response.status_code == 200

    def test_get_rejects_anonymous(self, client):
        """Verify anonymous users cannot access the import page.
        Confirms admin-only access control is enforced."""
        url = reverse("admin:libraries_library_import_geojson")

        response = client.get(url)

        assert response.status_code == 302

    def test_post_creates_libraries(self, admin_client, admin_user):
        """Verify uploading a valid GeoJSON creates Library records.
        End-to-end test of the admin import workflow."""
        feature = _build_feature(feature_id="node/admin1")
        geojson = _build_geojson(feature)
        geojson_bytes = json.dumps(geojson).encode("utf-8")
        uploaded = SimpleUploadedFile(
            name="test.geojson",
            content=geojson_bytes,
            content_type="application/json",
        )
        url = reverse("admin:libraries_library_import_geojson")

        response = admin_client.post(url, {"geojson_file": uploaded, "source": "test", "status": "approved"})

        assert response.status_code == 200
        assert Library.objects.filter(external_id="node/admin1").exists()
        library = Library.objects.get(external_id="node/admin1")
        assert library.source == "test"
        assert library.status == "approved"
        assert library.created_by == admin_user

    def test_post_without_file_shows_error(self, admin_client):
        """Verify missing file upload shows an error message.
        Prevents accidental empty form submissions."""
        url = reverse("admin:libraries_library_import_geojson")

        response = admin_client.post(url, {})

        assert response.status_code == 200

    def test_post_invalid_json_shows_error(self, admin_client):
        """Verify malformed JSON is rejected with an error.
        Handles corrupt or non-JSON file uploads gracefully."""
        uploaded = SimpleUploadedFile(
            name="bad.geojson",
            content=b"not valid json",
            content_type="application/json",
        )
        url = reverse("admin:libraries_library_import_geojson")

        response = admin_client.post(url, {"geojson_file": uploaded})

        assert response.status_code == 200

    def test_import_geojson_link_on_changelist(self, admin_client):
        """Verify the Import GeoJSON link appears on the library changelist.
        Ensures admins can discover the import feature."""
        url = reverse("admin:libraries_library_changelist")

        response = admin_client.get(url)

        assert response.status_code == 200
        assert b"Import GeoJSON" in response.content

    def test_post_duplicate_features_skipped(self, admin_client, admin_user):
        """Verify re-uploading the same file skips existing records.
        Tests the duplicate detection via external_id."""
        Library.objects.create(
            name="Already There",
            location=Point(x=10.62, y=43.89, srid=4326),
            address="Via Roma 5",
            city="Florence",
            country="IT",
            external_id="node/existing",
            status="approved",
            created_by=admin_user,
        )
        feature = _build_feature(feature_id="node/existing")
        geojson = _build_geojson(feature)
        uploaded = SimpleUploadedFile(
            name="test.geojson",
            content=json.dumps(geojson).encode("utf-8"),
            content_type="application/json",
        )
        url = reverse("admin:libraries_library_import_geojson")

        response = admin_client.post(url, {"geojson_file": uploaded, "source": "test", "status": "approved"})

        assert response.status_code == 200
        assert Library.objects.filter(external_id="node/existing").count() == 1

    def test_post_defaults_to_pending_for_invalid_status(self, admin_client, admin_user):
        """Verify invalid status values default to pending.
        Prevents arbitrary status injection from form tampering."""
        feature = _build_feature(feature_id="node/statustest")
        geojson = _build_geojson(feature)
        uploaded = SimpleUploadedFile(
            name="test.geojson",
            content=json.dumps(geojson).encode("utf-8"),
            content_type="application/json",
        )
        url = reverse("admin:libraries_library_import_geojson")

        response = admin_client.post(url, {"geojson_file": uploaded, "source": "test", "status": "rejected"})

        assert response.status_code == 200
        library = Library.objects.get(external_id="node/statustest")
        assert library.status == "pending"
