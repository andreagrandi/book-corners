from io import BytesIO
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, override_settings
from django.urls import reverse
from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational

from libraries.geolocation import extract_gps_coordinates
from libraries.image_processing import (
    MIN_ASPECT_RATIO,
    MAX_ASPECT_RATIO,
    _crop_to_aspect_ratio_bounds,
    ensure_instagram_aspect_ratio,
)
from libraries.models import Library, LibraryPhoto, MAX_LIBRARY_PHOTOS_PER_USER, Report

User = get_user_model()


def _build_uploaded_photo(
    *,
    file_name: str = "library.jpg",
    width: int = 640,
    height: int = 480,
    quality: int = 95,
) -> SimpleUploadedFile:
    """Build an in-memory JPEG upload for form and endpoint tests.
    Keeps image fixtures deterministic without touching disk."""
    image_bytes = BytesIO()
    image = Image.new("RGB", (width, height), color=(140, 165, 210))
    image.save(image_bytes, format="JPEG", quality=quality)
    image_bytes.seek(0)

    return SimpleUploadedFile(
        name=file_name,
        content=image_bytes.getvalue(),
        content_type="image/jpeg",
    )


def _decimal_to_dms(value: float) -> tuple[IFDRational, IFDRational, IFDRational]:
    """Convert decimal degrees into EXIF DMS rationals for test images.
    Produces stable GPS metadata for EXIF extraction coverage."""
    absolute_value = abs(value)
    degrees = int(absolute_value)
    minutes_float = (absolute_value - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60

    return (
        IFDRational(degrees, 1),
        IFDRational(minutes, 1),
        IFDRational(int(round(seconds * 10000)), 10000),
    )


def _build_uploaded_photo_with_gps(
    *,
    latitude: float,
    longitude: float,
    file_name: str = "library-with-gps.jpg",
) -> SimpleUploadedFile:
    """Build an in-memory JPEG upload that contains EXIF GPS tags.
    Lets tests exercise backend photo geolocation without fixture files."""
    image_bytes = BytesIO()
    image = Image.new("RGB", (640, 480), color=(140, 165, 210))

    exif = Image.Exif()
    exif[ExifTags.Base.GPSInfo] = {
        ExifTags.GPS.GPSLatitudeRef: "N" if latitude >= 0 else "S",
        ExifTags.GPS.GPSLatitude: _decimal_to_dms(latitude),
        ExifTags.GPS.GPSLongitudeRef: "E" if longitude >= 0 else "W",
        ExifTags.GPS.GPSLongitude: _decimal_to_dms(longitude),
    }

    image.save(image_bytes, format="JPEG", exif=exif)
    image_bytes.seek(0)

    return SimpleUploadedFile(
        name=file_name,
        content=image_bytes.getvalue(),
        content_type="image/jpeg",
    )


class TestPhotoGeolocationUtilities:
    def test_extract_gps_coordinates_returns_decimal_coordinates(self):
        """Verify EXIF GPS tags are converted to decimal coordinates.
        Covers happy-path parsing for geotagged photo uploads."""
        photo = _build_uploaded_photo_with_gps(latitude=43.7696, longitude=11.2558)

        coordinates = extract_gps_coordinates(photo)

        assert coordinates is not None
        latitude, longitude = coordinates
        assert latitude == pytest.approx(43.7696, abs=1e-4)
        assert longitude == pytest.approx(11.2558, abs=1e-4)

    def test_extract_gps_coordinates_returns_none_without_metadata(self):
        """Verify extraction returns None when GPS EXIF tags are missing.
        Covers fallback behavior for photos without location metadata."""
        photo = _build_uploaded_photo()

        coordinates = extract_gps_coordinates(photo)

        assert coordinates is None


@pytest.fixture
def library(user):
    """Create a reusable library fixture for model and report tests.
    Provides baseline location and address data shared across scenarios."""
    return Library.objects.create(
        name="Test Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city="Florence",
        country="IT",
        created_by=user,
    )


@pytest.fixture
def admin_library(admin_user):
    """Create a pending library fixture owned by an admin user.
    Supports moderation action tests that update library status."""
    return Library.objects.create(
        name="Pending Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city="Florence",
        country="IT",
        created_by=admin_user,
    )


@pytest.fixture
def admin_report(admin_library, admin_user):
    """Create an open report fixture linked to the admin library.
    Supports report moderation tests for resolve and dismiss actions."""
    return Report.objects.create(
        library=admin_library,
        created_by=admin_user,
        reason=Report.Reason.DAMAGED,
        details="The library box is broken.",
    )


@pytest.mark.django_db
class TestLibraryModel:
    """Tests for the Library model."""

    def test_create_library_with_all_fields(self, user):
        """Verify create library with all fields.
        Confirms the expected behavior stays stable."""
        library = Library.objects.create(
            name="The Book Nook",
            description="A cozy little library on the corner.",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            postal_code="50123",
            wheelchair_accessible=Library.WheelchairAccess.YES,
            capacity=30,
            is_indoor=False,
            is_lit=True,
            website="https://example.org/nook",
            contact="info@example.org",
            source="OpenStreetMap",
            operator="City Library Association",
            brand="Little Free Library",
            created_by=user,
        )

        assert library.pk is not None
        assert library.name == "The Book Nook"
        assert library.status == Library.Status.PENDING
        assert library.slug == "florence-via-rosina-15-the-book-nook"
        assert library.created_at is not None
        assert library.updated_at is not None
        assert library.wheelchair_accessible == "yes"
        assert library.capacity == 30
        assert library.is_indoor is False
        assert library.is_lit is True
        assert library.website == "https://example.org/nook"
        assert library.contact == "info@example.org"
        assert library.source == "OpenStreetMap"
        assert library.operator == "City Library Association"
        assert library.brand == "Little Free Library"

    def test_wheelchair_accessible_choices_validation(self, user):
        """Verify wheelchair_accessible accepts only valid choices.
        Rejects arbitrary strings that fall outside the defined enum."""
        library = Library(
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            wheelchair_accessible="invalid",
            created_by=user,
        )
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            library.full_clean()

    def test_new_metadata_fields_default_to_blank_or_null(self, user):
        """Verify new metadata fields default correctly.
        Ensures existing rows are unaffected by the migration."""
        library = Library.objects.create(
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=user,
        )

        assert library.wheelchair_accessible == ""
        assert library.capacity is None
        assert library.is_indoor is None
        assert library.is_lit is None
        assert library.website == ""
        assert library.contact == ""
        assert library.source == ""
        assert library.operator == ""
        assert library.brand == ""

    def test_create_library_without_name(self, user):
        """Verify create library without name.
        Confirms the expected behavior stays stable."""
        library = Library.objects.create(
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=user,
        )

        assert library.pk is not None
        assert library.name == ""
        assert library.slug == "florence-via-rosina-15"

    def test_card_photo_url_falls_back_to_main_photo_without_thumbnail(self, user):
        """Verify card image URL falls back to the main photo when needed.
        Preserves rendering compatibility for existing rows without thumbnails."""
        library = Library.objects.create(
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=user,
        )

        assert library.photo_thumbnail == ""
        assert library.card_photo_url.endswith("/libraries/photos/2026/02/test.jpg")

    def test_slug_uniqueness_adds_numeric_suffix(self, user):
        """Verify slug uniqueness adds numeric suffix.
        Confirms the expected behavior stays stable."""
        common_kwargs = {
            "photo": "libraries/photos/2026/02/test.jpg",
            "location": Point(x=11.2558, y=43.7696, srid=4326),
            "address": "Via Rosina 15",
            "city": "Florence",
            "country": "IT",
            "created_by": user,
        }

        library_1 = Library.objects.create(**common_kwargs)
        library_2 = Library.objects.create(**common_kwargs)

        assert library_1.slug == "florence-via-rosina-15"
        assert library_2.slug == "florence-via-rosina-15-2"

    def test_library_str_with_name(self, user):
        """Verify library str with name.
        Confirms the expected behavior stays stable."""
        library = Library.objects.create(
            name="The Book Nook",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=user,
        )

        assert str(library) == "The Book Nook (Florence)"

    def test_library_str_without_name(self, user):
        """Verify library str without name.
        Confirms the expected behavior stays stable."""
        library = Library.objects.create(
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=user,
        )

        assert str(library) == "Via Rosina 15, Florence"

    def test_slug_truncated_for_long_inputs(self, user):
        """Verify slug truncated for long inputs.
        Confirms the expected behavior stays stable."""
        long_address = "A" * 255
        library = Library.objects.create(
            name="B" * 255,
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address=long_address,
            city="Florence",
            country="IT",
            created_by=user,
        )

        max_length = Library._meta.get_field("slug").max_length
        assert len(library.slug) <= max_length

    def test_slug_truncation_still_allows_uniqueness(self, user):
        """Verify slug truncation still allows uniqueness.
        Confirms the expected behavior stays stable."""
        long_address = "A" * 255
        common_kwargs = {
            "photo": "libraries/photos/2026/02/test.jpg",
            "location": Point(x=11.2558, y=43.7696, srid=4326),
            "address": long_address,
            "city": "Florence",
            "country": "IT",
            "created_by": user,
        }

        library_1 = Library.objects.create(**common_kwargs)
        library_2 = Library.objects.create(**common_kwargs)

        max_length = Library._meta.get_field("slug").max_length
        assert library_1.slug != library_2.slug
        assert len(library_1.slug) <= max_length
        assert len(library_2.slug) <= max_length

    def test_slug_falls_back_to_uuid_when_slugify_produces_empty_string(self, user):
        """Verify a non-empty slug is generated when city and address slugify to nothing.
        Prevents NoReverseMatch errors in sitemaps and URL resolution."""
        library = Library.objects.create(
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="",
            city="",
            country="IT",
            created_by=user,
        )

        assert library.slug != ""
        assert len(library.slug) == 8

    def test_default_status_is_pending(self, user):
        """Verify default status is pending.
        Confirms the expected behavior stays stable."""
        library = Library.objects.create(
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=user,
        )

        assert library.status == Library.Status.PENDING


@pytest.mark.django_db
class TestReportModel:
    """Tests for the Report model."""

    def test_create_report(self, library, user):
        """Verify create report.
        Confirms the expected behavior stays stable."""
        report = Report.objects.create(
            library=library,
            created_by=user,
            reason=Report.Reason.DAMAGED,
            details="The library box is broken and books are getting wet.",
        )

        assert report.pk is not None
        assert report.reason == Report.Reason.DAMAGED
        assert report.status == Report.Status.OPEN
        assert report.created_at is not None
        assert report.photo == ""

    def test_create_report_with_photo(self, library, user):
        """Verify create report with photo.
        Confirms the expected behavior stays stable."""
        report = Report.objects.create(
            library=library,
            created_by=user,
            reason=Report.Reason.MISSING,
            details="The library is no longer at this location.",
            photo="reports/photos/2026/02/evidence.jpg",
        )

        assert report.pk is not None
        assert report.photo == "reports/photos/2026/02/evidence.jpg"

    def test_default_status_is_open(self, library, user):
        """Verify default status is open.
        Confirms the expected behavior stays stable."""
        report = Report.objects.create(
            library=library,
            created_by=user,
            reason=Report.Reason.OTHER,
            details="Something else is wrong.",
        )

        assert report.status == Report.Status.OPEN

    def test_report_str(self, library, user):
        """Verify report str.
        Confirms the expected behavior stays stable."""
        report = Report.objects.create(
            library=library,
            created_by=user,
            reason=Report.Reason.INAPPROPRIATE,
            details="Inappropriate content found.",
        )

        assert str(report) == "Report: Inappropriate - Test Library (Florence)"


@pytest.mark.django_db
class TestLibraryAdmin:
    """Tests for Library admin actions."""

    def test_approve_libraries_action(self, admin_client, admin_library):
        """Verify approve libraries action.
        Confirms the expected behavior stays stable."""
        url = reverse("admin:libraries_library_changelist")
        response = admin_client.post(url, {
            "action": "approve_libraries",
            "_selected_action": [admin_library.pk],
        })

        assert response.status_code == 302
        admin_library.refresh_from_db()
        assert admin_library.status == Library.Status.APPROVED

    def test_reject_libraries_action(self, admin_client, admin_library):
        """Verify reject libraries action.
        Confirms the expected behavior stays stable."""
        url = reverse("admin:libraries_library_changelist")
        response = admin_client.post(url, {
            "action": "reject_libraries",
            "_selected_action": [admin_library.pk],
        })

        assert response.status_code == 302
        admin_library.refresh_from_db()
        assert admin_library.status == Library.Status.REJECTED

    def test_bulk_approve_multiple_libraries(self, admin_client, admin_library, admin_user):
        """Verify bulk approve multiple libraries.
        Confirms the expected behavior stays stable."""
        library_2 = Library.objects.create(
            photo="libraries/photos/2026/02/test2.jpg",
            location=Point(x=11.2600, y=43.7700, srid=4326),
            address="Via Roma 1",
            city="Florence",
            country="IT",
            created_by=admin_user,
        )

        url = reverse("admin:libraries_library_changelist")
        response = admin_client.post(url, {
            "action": "approve_libraries",
            "_selected_action": [admin_library.pk, library_2.pk],
        })

        assert response.status_code == 302
        admin_library.refresh_from_db()
        library_2.refresh_from_db()
        assert admin_library.status == Library.Status.APPROVED
        assert library_2.status == Library.Status.APPROVED


@pytest.mark.django_db
class TestLibraryApprovalNotification:
    """Tests for email notifications when libraries are approved."""

    def test_notify_library_approved_sends_email(self, user):
        """Verify approval notification sends email with correct content.
        Checks subject, recipient, public link, and thank-you message."""
        from django.core import mail
        from libraries.notifications import notify_library_approved

        user.email = "submitter@example.com"
        user.save()
        library = Library.objects.create(
            name="Corner Shelf",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=user,
        )

        notify_library_approved(library)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.subject == "Your library is now live on Book Corners!"
        assert email.to == ["submitter@example.com"]
        assert email.from_email == "no-reply@bookcorners.org"
        assert library.slug in email.body
        assert "Thank you" in email.body

    def test_notify_library_approved_skips_no_email(self, user):
        """Verify no email is sent when the submitter has no email address.
        Ensures the function exits gracefully without raising."""
        from django.core import mail
        from libraries.notifications import notify_library_approved

        user.email = ""
        user.save()
        library = Library.objects.create(
            name="Silent Shelf",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Roma 1",
            city="Florence",
            country="IT",
            created_by=user,
        )

        notify_library_approved(library)

        assert len(mail.outbox) == 0

    def test_approve_action_sends_notification(self, admin_client, admin_user):
        """Verify bulk approve action sends notification for pending libraries.
        Confirms email is triggered by the admin action, not just the function."""
        from django.core import mail

        admin_user.email = "admin@example.com"
        admin_user.save()
        library = Library.objects.create(
            name="Bulk Shelf",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Roma 2",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=admin_user,
        )

        url = reverse("admin:libraries_library_changelist")
        admin_client.post(url, {
            "action": "approve_libraries",
            "_selected_action": [library.pk],
        })

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["admin@example.com"]

    def test_save_model_sends_notification_on_approval(self, admin_client, admin_user):
        """Verify single-edit approval triggers notification email.
        Covers the save_model override path for status transitions."""
        from django.core import mail

        admin_user.email = "admin@example.com"
        admin_user.save()
        library = Library.objects.create(
            name="Edit Shelf",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Roma 3",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=admin_user,
        )

        change_url = reverse("admin:libraries_library_change", args=[library.pk])
        response = admin_client.get(change_url)
        assert response.status_code == 200

        form_data = {
            "name": library.name,
            "description": "",
            "address": library.address,
            "city": library.city,
            "country": library.country,
            "postal_code": "",
            "location": library.location.wkt,
            "status": Library.Status.APPROVED,
            "created_by": admin_user.pk,
            "wheelchair_accessible": "",
            "website": "",
            "contact": "",
            "source": "",
            "operator": "",
            "brand": "",
            "external_id": "",
            "user_photos-TOTAL_FORMS": "0",
            "user_photos-INITIAL_FORMS": "0",
            "user_photos-MIN_NUM_FORMS": "0",
            "user_photos-MAX_NUM_FORMS": "1000",
            "_save": "Save",
        }
        admin_client.post(change_url, form_data)

        library.refresh_from_db()
        assert library.status == Library.Status.APPROVED
        assert len(mail.outbox) == 1
        assert "now live" in mail.outbox[0].subject

    def test_save_model_no_notification_when_already_approved(self, admin_client, admin_user):
        """Verify no duplicate email when editing an already-approved library.
        Prevents notification spam on non-transition saves."""
        from django.core import mail

        admin_user.email = "admin@example.com"
        admin_user.save()
        library = Library.objects.create(
            name="Already Approved Shelf",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Roma 4",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=admin_user,
        )

        change_url = reverse("admin:libraries_library_change", args=[library.pk])
        form_data = {
            "name": library.name,
            "description": "",
            "address": library.address,
            "city": library.city,
            "country": library.country,
            "postal_code": "",
            "location": library.location.wkt,
            "status": Library.Status.APPROVED,
            "created_by": admin_user.pk,
            "wheelchair_accessible": "",
            "website": "",
            "contact": "",
            "source": "",
            "operator": "",
            "brand": "",
            "external_id": "",
            "user_photos-TOTAL_FORMS": "0",
            "user_photos-INITIAL_FORMS": "0",
            "user_photos-MIN_NUM_FORMS": "0",
            "user_photos-MAX_NUM_FORMS": "1000",
            "_save": "Save",
        }
        admin_client.post(change_url, form_data)

        assert len(mail.outbox) == 0


@pytest.mark.django_db
class TestReportAdmin:
    """Tests for Report admin actions."""

    def test_resolve_reports_action(self, admin_client, admin_report):
        """Verify resolve reports action.
        Confirms the expected behavior stays stable."""
        url = reverse("admin:libraries_report_changelist")
        response = admin_client.post(url, {
            "action": "resolve_reports",
            "_selected_action": [admin_report.pk],
        })

        assert response.status_code == 302
        admin_report.refresh_from_db()
        assert admin_report.status == Report.Status.RESOLVED

    def test_dismiss_reports_action(self, admin_client, admin_report):
        """Verify dismiss reports action.
        Confirms the expected behavior stays stable."""
        url = reverse("admin:libraries_report_changelist")
        response = admin_client.post(url, {
            "action": "dismiss_reports",
            "_selected_action": [admin_report.pk],
        })

        assert response.status_code == 302
        admin_report.refresh_from_db()
        assert admin_report.status == Report.Status.DISMISSED


@pytest.mark.django_db
class TestStatsPageView:
    """Tests for the public statistics page view."""

    def test_stats_page_returns_200(self, client):
        """Verify the stats page is publicly accessible.
        Confirms unauthenticated users can view statistics."""
        response = client.get(reverse("stats_page"))

        assert response.status_code == 200

    def test_stats_page_empty_state(self, client):
        """Verify the stats page renders with zero libraries.
        Confirms graceful handling when no data exists."""
        response = client.get(reverse("stats_page"))

        content = response.content.decode()
        assert "0" in content

    def test_stats_page_counts_only_approved(self, client, user):
        """Verify totals count only approved libraries.
        Confirms pending and rejected entries are excluded."""
        Library.objects.create(
            name="Approved",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="Pending",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 16",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=user,
        )

        response = client.get(reverse("stats_page"))

        assert response.context["stats"]["total_approved"] == 1

    def test_stats_page_counts_libraries_with_primary_photo(self, client, user):
        """Verify image count includes libraries with a primary photo.
        Confirms primary photo presence is detected."""
        Library.objects.create(
            name="With Photo",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="No Photo",
            photo="",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 16",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(reverse("stats_page"))

        assert response.context["stats"]["total_with_image"] == 1

    def test_stats_page_counts_libraries_with_community_photo(self, client, user):
        """Verify image count includes libraries with approved community photos.
        Confirms LibraryPhoto presence contributes to the with-image count."""
        lib = Library.objects.create(
            name="Community Photo Lib",
            photo="",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 17",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        LibraryPhoto.objects.create(
            library=lib,
            created_by=user,
            photo="libraries/user_photos/2026/02/community.jpg",
            status=LibraryPhoto.Status.APPROVED,
        )

        response = client.get(reverse("stats_page"))

        assert response.context["stats"]["total_with_image"] == 1

    def test_stats_page_country_labels_include_flags(self, client, user):
        """Verify top countries include flag emoji and country name.
        Confirms display-friendly labels for chart rendering."""
        Library.objects.create(
            name="German Lib",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=13.405, y=52.52, srid=4326),
            address="Unter den Linden 1",
            city="Berlin",
            country="DE",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(reverse("stats_page"))

        countries = response.context["stats"]["top_countries"]
        assert len(countries) == 1
        assert countries[0]["country_code"] == "DE"
        assert countries[0]["country_name"] == "Germany"
        assert countries[0]["flag_emoji"] == "\U0001F1E9\U0001F1EA"

    def test_stats_page_daily_granularity_for_recent_data(self, client, user):
        """Verify daily granularity when all data is within 90 days.
        Confirms adaptive time series resolution."""
        Library.objects.create(
            name="Recent Lib",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(reverse("stats_page"))

        assert response.context["stats"]["granularity"] == "daily"

    def test_stats_page_contains_chart_js_script(self, client):
        """Verify the stats page loads Chart.js from CDN.
        Confirms the charting library is available for rendering."""
        response = client.get(reverse("stats_page"))

        content = response.content.decode()
        assert "chart.js" in content


class TestTailwindIntegration:
    def test_style_preview_template_renders_daisyui_classes(self, client):
        """Verify style preview template renders daisyui classes.
        Confirms the expected behavior stays stable."""
        response = client.get(reverse("style_preview"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "btn btn-primary" in content
        assert "/static/css/app.css" in content


@pytest.mark.django_db
class TestHomepageTemplate:
    def test_homepage_uses_base_template_layout(self, client):
        """Verify homepage uses base template layout.
        Confirms the expected behavior stays stable."""
        response = client.get(reverse("home"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Book Corners" in content
        assert "href=\"/about/\"" in content
        assert "href=\"/map/\"" in content
        assert "href=\"/map/?view=list\"" in content
        assert "href=\"/submit/\"" in content
        assert "href=\"/login/\"" in content
        assert "href=\"/register/\"" in content
        assert "https://unpkg.com/htmx.org@2.0.4" in content
        assert "id=\"latest-entries\"" in content
        assert "hx-get=\"/latest-entries/\"" in content
        assert "name=\"near\"" in content
        assert "Advanced search" in content
        assert "Latest entries" in content
        assert "Made with ❤️ by" in content
        assert "https://www.andreagrandi.it" in content
        assert "https://github.com/andreagrandi/book-corners" in content
        assert "href=\"/privacy/\"" in content
        assert "https://developers.bookcorners.org/" in content
        assert "https://stats.uptimerobot.com/y3eynRaqP2" in content


@pytest.mark.django_db
class TestAboutPageTemplate:
    def test_about_page_renders_logo_content_and_actions(self, client):
        """Verify about page renders logo, story cards, and call to actions.
        Confirms visitors can quickly understand and navigate the project."""
        response = client.get(reverse("about_page"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "About Book Corners" in content
        assert "Book Corners logo" in content
        assert "How to contribute" in content
        assert "About the creator" in content
        assert "Andrea speaking at PyCon Italy" in content
        assert "Add a new library in your neighborhood" in content
        assert "href=\"/map/\"" in content
        assert "href=\"/submit/\"" in content
        assert "https://github.com/andreagrandi/book-corners" in content


@pytest.mark.django_db
class TestSeoMetadata:
    def test_public_pages_render_custom_meta_descriptions(self, client):
        """Verify public pages expose custom meta descriptions for SEO.
        Ensures each page-level template override is visible in HTML output."""
        expected_descriptions = {
            reverse("home"): "Discover and share little free libraries in your neighborhood with Book Corners.",
            reverse("about_page"): "Learn the mission behind Book Corners and how to contribute new neighborhood library entries.",
            reverse("map_page"): "Explore the Book Corners map to find little free libraries near you and across nearby cities.",
            reverse("login"): "Log in to Book Corners to submit libraries, report issues, and manage your contributions.",
            reverse("register"): "Create a Book Corners account to add new libraries and keep local entries up to date.",
            reverse("submit_library_confirmation"): "Your library submission was received and is now waiting for moderation approval on Book Corners.",
        }

        for url, description in expected_descriptions.items():
            response = client.get(url)
            content = response.content.decode()
            assert response.status_code == 200
            assert f'<meta name="description" content="{description}">' in content

    def test_authenticated_pages_render_custom_meta_descriptions(self, client, user):
        """Verify authenticated pages expose custom meta descriptions.
        Keeps private user pages consistent with SEO metadata standards."""
        client.force_login(user)
        expected_descriptions = {
            reverse("dashboard"): "Review your submitted libraries and track each moderation status from your dashboard.",
            reverse("submit_library"): "Submit a little free library to Book Corners with a photo, location, and address details.",
        }

        for url, description in expected_descriptions.items():
            response = client.get(url)
            content = response.content.decode()
            assert response.status_code == 200
            assert f'<meta name="description" content="{description}">' in content

    def test_library_detail_page_renders_open_graph_metadata(self, client, user):
        """Verify library detail pages include Open Graph metadata tags.
        Ensures social sharing cards can render title, description, and image."""
        library = Library.objects.create(
            name="Canal Shelf",
            description="Waterproof little free library with family picks.",
            photo="libraries/photos/2026/02/detail-og.jpg",
            location=Point(x=4.9041, y=52.3676, srid=4326),
            address="Prinsengracht 140",
            city="Amsterdam",
            country="NL",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))

        content = response.content.decode()
        assert response.status_code == 200
        assert '<meta property="og:type" content="article">' in content
        assert '<meta property="og:title" content="Canal Shelf - Book Corners">' in content
        assert (
            '<meta property="og:description" content="Waterproof little free library with family picks.">'
            in content
        )
        assert '<meta property="og:image" content="http://testserver/' in content
        assert "detail-og.jpg" in content

    def test_sitemap_lists_public_pages_and_approved_library_details(self, client, user):
        """Verify sitemap lists static pages and approved library detail URLs.
        Prevents pending entries from being exposed to search engine crawlers."""
        from django.contrib.sites.models import Site

        Site.objects.update_or_create(id=1, defaults={"domain": "testserver", "name": "testserver"})
        approved_library = Library.objects.create(
            name="Indexed Shelf",
            photo="libraries/photos/2026/02/indexed.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        pending_library = Library.objects.create(
            name="Pending Shelf",
            photo="libraries/photos/2026/02/pending-index.jpg",
            location=Point(x=11.2658, y=43.7796, srid=4326),
            address="Via dei Neri 3",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=user,
        )

        response = client.get(reverse("sitemap"))

        content = response.content.decode()
        approved_url = f"http://testserver{reverse('library_detail', kwargs={'slug': approved_library.slug})}"
        pending_url = f"http://testserver{reverse('library_detail', kwargs={'slug': pending_library.slug})}"
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/xml")
        assert "<urlset" in content
        assert "http://testserver/" in content
        assert "http://testserver/about/" in content
        assert "http://testserver/map/" in content
        assert approved_url in content
        assert pending_url not in content

    def test_robots_txt_includes_sitemap_location(self, client):
        """Verify robots.txt allows crawling and points to the sitemap URL.
        Helps crawlers discover indexed pages using a standard robots location."""
        response = client.get(reverse("robots_txt"))

        content = response.content.decode()
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/plain")
        assert "User-agent: *" in content
        assert "Allow: /" in content
        assert "Sitemap: http://testserver/sitemap.xml" in content


class TestErrorPages:
    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_custom_404_template_is_rendered_for_missing_page(self, client):
        """Verify unknown URLs return the custom 404 template.
        Ensures users receive a friendly fallback page with recovery links."""
        response = client.get("/missing-page-for-404-test/")

        content = response.content.decode()
        assert response.status_code == 404
        assert "Page not found" in content
        assert "Back to homepage" in content
        assert "Explore map" in content
        assert f'href="{reverse("home")}"' in content
        assert f'href="{reverse("map_page")}"' in content

    def test_error_handlers_are_wired_to_custom_views(self):
        """Verify project URL config references custom 404 and 500 handlers.
        Confirms Django uses the dedicated error views in production mode."""
        from config import urls as project_urls

        assert project_urls.handler404 == "config.error_views.page_not_found"
        assert project_urls.handler500 == "config.error_views.server_error"

    def test_server_error_view_renders_custom_500_template(self):
        """Verify custom 500 view returns the branded error page.
        Confirms the server-error handler renders friendly recovery guidance."""
        from config.error_views import server_error

        request = RequestFactory().get("/trigger-server-error/")
        response = server_error(request)

        content = response.content.decode()
        assert response.status_code == 500
        assert "Something went wrong" in content
        assert "Back to homepage" in content
        assert "Explore map" in content


@pytest.mark.django_db
class TestHealthEndpoint:
    def test_health_returns_ok_when_database_is_reachable(self, client):
        """Verify health endpoint confirms app and database are responsive.
        Used by Dokku zero-downtime checks and uptime monitoring."""
        response = client.get("/health/")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestCsrfProtection:
    def test_csrf_middleware_is_enabled(self):
        """Verify CSRF middleware remains enabled in global middleware.
        Protects state-changing form submissions from cross-site request forgery."""
        assert "django.middleware.csrf.CsrfViewMiddleware" in django_settings.MIDDLEWARE

    @pytest.mark.django_db
    def test_post_forms_render_csrf_tokens(self, client, user):
        """Verify user-facing POST forms include CSRF tokens in markup.
        Ensures template-level CSRF protection is active across key flows."""
        login_response = client.get(reverse("login"))
        register_response = client.get(reverse("register"))

        assert login_response.status_code == 200
        assert register_response.status_code == 200
        assert "csrfmiddlewaretoken" in login_response.content.decode()
        assert "csrfmiddlewaretoken" in register_response.content.decode()

        library = Library.objects.create(
            name="CSRF Check Shelf",
            photo="libraries/photos/2026/02/csrf-check.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 21",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        client.force_login(user)

        home_response = client.get(reverse("home"))
        submit_response = client.get(reverse("submit_library"))
        detail_response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))

        assert home_response.status_code == 200
        assert submit_response.status_code == 200
        assert detail_response.status_code == 200
        assert "csrfmiddlewaretoken" in home_response.content.decode()
        assert "csrfmiddlewaretoken" in submit_response.content.decode()
        assert "csrfmiddlewaretoken" in detail_response.content.decode()


@pytest.mark.django_db
class TestHomepageLatestEntries:
    def test_latest_entries_partial_includes_only_approved_libraries(self, client, user):
        """Verify latest entries partial includes only approved libraries.
        Confirms the expected behavior stays stable."""
        approved = Library.objects.create(
            name="Approved Library",
            description="Visible on homepage",
            photo="libraries/photos/2026/02/approved.jpg",
            location=Point(x=2.3522, y=48.8566, srid=4326),
            address="Rue de Rivoli 11",
            city="Paris",
            country="FR",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="Pending Library",
            description="Should not be visible",
            photo="libraries/photos/2026/02/pending.jpg",
            location=Point(x=2.3400, y=48.8500, srid=4326),
            address="Rue Oberkampf 8",
            city="Paris",
            country="FR",
            status=Library.Status.PENDING,
            created_by=user,
        )

        response = client.get(reverse("latest_entries"))

        content = response.content.decode()
        detail_url = reverse("library_detail", kwargs={"slug": approved.slug})
        assert response.status_code == 200
        assert approved.name in content
        assert "Pending Library" not in content
        assert "id=\"latest-entries-grid\"" in content
        assert f"href=\"{detail_url}\"" in content

    def test_latest_entries_excludes_libraries_without_photo(self, client, user):
        """Verify approved libraries without a photo are excluded from latest entries.
        Confirms only libraries with real images appear on the homepage."""
        Library.objects.create(
            name="Has Photo",
            photo="libraries/photos/2026/02/real.jpg",
            location=Point(x=2.3522, y=48.8566, srid=4326),
            address="Rue de Rivoli 5",
            city="Paris",
            country="FR",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="No Photo",
            location=Point(x=2.3400, y=48.8500, srid=4326),
            address="Rue Oberkampf 3",
            city="Paris",
            country="FR",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(reverse("latest_entries"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Has Photo" in content
        assert "No Photo" not in content

    def test_latest_entries_partial_renders_load_more_when_next_page_exists(self, client, user):
        """Verify latest entries partial renders load more when next page exists.
        Confirms the expected behavior stays stable."""
        for index in range(10):
            Library.objects.create(
                name=f"Library {index}",
                description=f"Description for library {index}",
                photo="libraries/photos/2026/02/test.jpg",
                location=Point(x=11.2558 + index * 0.0005, y=43.7696 + index * 0.0005, srid=4326),
                address=f"Via Rosina {index + 1}",
                city="Florence",
                country="IT",
                status=Library.Status.APPROVED,
                created_by=user,
            )

        first_response = client.get(reverse("latest_entries"))
        second_response = client.get(reverse("latest_entries"), {"page": 2})

        first_content = first_response.content.decode()
        second_content = second_response.content.decode()

        assert first_response.status_code == 200
        assert "Load more" in first_content
        assert "hx-target=\"#latest-entries-grid\"" in first_content

        assert second_response.status_code == 200
        assert "hx-swap-oob=\"outerHTML\"" in second_content
        assert "You have reached the latest approved entries." in second_content


@pytest.mark.django_db
class TestMapPageView:
    def test_map_page_renders_leaflet_cluster_and_filter_controls(self, client):
        """Verify map page renders map shell, filters, and cluster assets.
        Confirms the dedicated map interface is available to visitors."""
        response = client.get(reverse("map_page"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "id=\"map-filters-form\"" in content
        assert "id=\"libraries-map\"" in content
        assert "id=\"map-list-results\"" in content
        assert "leaflet@1.9.4" in content
        assert "leaflet.markercluster@1.5.3" in content
        assert reverse("map_libraries_geojson") in content
        assert reverse("map_libraries_list") in content
        assert "data-view-mode=\"split\"" in content

    def test_map_page_persists_explicit_view_mode_from_querystring(self, client):
        """Verify map page preserves an explicit requested view mode.
        Lets server-rendered links open directly in list or map focus."""
        response = client.get(reverse("map_page"), {"view": "list"})

        content = response.content.decode()
        assert response.status_code == 200
        assert 'data-initial-view="list"' in content

    def test_map_geojson_endpoint_returns_only_approved_libraries(self, client, user):
        """Verify map GeoJSON endpoint excludes non-approved libraries.
        Keeps public map markers limited to moderated approved entries."""
        approved_library = Library.objects.create(
            name="Approved on Map",
            photo="libraries/photos/2026/02/map-approved.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="Pending on Map",
            photo="libraries/photos/2026/02/map-pending.jpg",
            location=Point(x=12.4964, y=41.9028, srid=4326),
            address="Via del Corso 1",
            city="Rome",
            country="IT",
            status=Library.Status.PENDING,
            created_by=user,
        )

        response = client.get(reverse("map_libraries_geojson"))

        payload = response.json()
        assert response.status_code == 200
        assert payload["type"] == "FeatureCollection"
        assert payload["meta"]["count"] == 1
        assert len(payload["features"]) == 1

        feature = payload["features"][0]
        assert feature["type"] == "Feature"
        assert feature["properties"]["id"] == approved_library.id
        assert feature["properties"]["slug"] == approved_library.slug
        assert feature["properties"]["name"] == "Approved on Map"
        assert feature["properties"]["detail_url"] == reverse(
            "library_detail",
            kwargs={"slug": approved_library.slug},
        )
        assert feature["geometry"]["type"] == "Point"
        assert feature["geometry"]["coordinates"] == pytest.approx([11.2558, 43.7696], abs=1e-6)

    def test_map_geojson_endpoint_applies_structured_filters(self, client, user):
        """Verify map GeoJSON endpoint filters by city and country fields.
        Ensures live map filtering can narrow marker sets quickly."""
        matching_library = Library.objects.create(
            name="Florence Filtered Shelf",
            photo="libraries/photos/2026/02/map-filter-match.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="Berlin Filtered Shelf",
            photo="libraries/photos/2026/02/map-filter-other.jpg",
            location=Point(x=13.4050, y=52.5200, srid=4326),
            address="Unter den Linden 1",
            city="Berlin",
            country="DE",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(
            reverse("map_libraries_geojson"),
            {
                "city": "Flor",
                "country": "IT",
            },
        )

        payload = response.json()
        assert response.status_code == 200
        assert payload["meta"]["count"] == 1
        assert len(payload["features"]) == 1
        assert payload["features"][0]["properties"]["id"] == matching_library.id

    @patch("libraries.views.forward_geocode_place")
    def test_map_geojson_endpoint_filters_by_near_radius_without_full_page_reload(
        self,
        mocked_forward_geocode,
        client,
        user,
    ):
        """Verify map endpoint supports nearby filtering using geocoded centers.
        Returns marker subsets and center metadata for dynamic map updates."""
        mocked_forward_geocode.return_value = (51.5074, -0.1278)
        Library.objects.create(
            name="Central London Shelf",
            photo="libraries/photos/2026/02/map-nearby-1.jpg",
            location=Point(x=-0.1270, y=51.5070, srid=4326),
            address="Whitehall",
            city="London",
            country="GB",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="Westminster Shelf",
            photo="libraries/photos/2026/02/map-nearby-2.jpg",
            location=Point(x=-0.1410, y=51.5010, srid=4326),
            address="Birdcage Walk",
            city="Westminster",
            country="GB",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="Cambridge Shelf",
            photo="libraries/photos/2026/02/map-nearby-3.jpg",
            location=Point(x=0.1218, y=52.2053, srid=4326),
            address="King's Parade",
            city="Cambridge",
            country="GB",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(
            reverse("map_libraries_geojson"),
            {
                "near": "London",
                "radius_km": "20",
            },
        )

        payload = response.json()
        names = {feature["properties"]["name"] for feature in payload["features"]}
        assert response.status_code == 200
        assert "Central London Shelf" in names
        assert "Westminster Shelf" in names
        assert "Cambridge Shelf" not in names
        assert payload["meta"]["location_resolution_failed"] is False
        assert payload["meta"]["near_query"] == "London"
        assert payload["meta"]["center"] is not None
        assert payload["meta"]["center"]["lat"] == pytest.approx(51.5074, abs=1e-6)
        assert payload["meta"]["center"]["lng"] == pytest.approx(-0.1278, abs=1e-6)

    def test_map_geojson_endpoint_applies_optional_bounds_filter(self, client, user):
        """Verify map GeoJSON endpoint can restrict markers to viewport bounds.
        Improves marker payload size when users pan or zoom around the map."""
        inside_library = Library.objects.create(
            name="Inside Bounds Shelf",
            photo="libraries/photos/2026/02/map-bounds-inside.jpg",
            location=Point(x=2.3522, y=48.8566, srid=4326),
            address="Rue de Rivoli 1",
            city="Paris",
            country="FR",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="Outside Bounds Shelf",
            photo="libraries/photos/2026/02/map-bounds-outside.jpg",
            location=Point(x=13.4050, y=52.5200, srid=4326),
            address="Unter den Linden 3",
            city="Berlin",
            country="DE",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(
            reverse("map_libraries_geojson"),
            {
                "min_lat": "48.5000",
                "min_lng": "2.1000",
                "max_lat": "49.1000",
                "max_lng": "2.7000",
            },
        )

        payload = response.json()
        assert response.status_code == 200
        assert payload["meta"]["bounds_applied"] is True
        assert payload["meta"]["count"] == 1
        assert payload["features"][0]["properties"]["id"] == inside_library.id


@pytest.mark.django_db
class TestMapGeoJSONClustering:
    """Tests for server-side GeoJSON clustering at low zoom levels."""

    def test_clustered_response_at_low_zoom(self, client, user):
        """Verify low-zoom requests return clustered features instead of individual points.
        Reduces payload size dramatically for wide-area map views."""
        for i in range(5):
            Library.objects.create(
                name=f"Cluster Shelf {i}",
                location=Point(x=11.25 + i * 0.001, y=43.77, srid=4326),
                address=f"Via Test {i}",
                city="Florence",
                country="IT",
                status=Library.Status.APPROVED,
                created_by=user,
            )

        response = client.get(
            reverse("map_libraries_geojson"),
            {"zoom": "5"},
        )

        payload = response.json()
        assert response.status_code == 200
        assert payload["meta"]["clustered"] is True
        assert len(payload["features"]) < 5
        feature = payload["features"][0]
        assert feature["properties"]["cluster"] is True
        assert feature["properties"]["point_count"] == 5
        bbox = feature["properties"]["bbox"]
        assert bbox is not None
        assert len(bbox) == 4
        assert bbox[0] <= bbox[2]
        assert bbox[1] <= bbox[3]

    def test_individual_points_at_high_zoom(self, client, user):
        """Verify high-zoom requests return individual library features with detail URLs.
        Preserves full marker popups when users are zoomed into a city."""
        library = Library.objects.create(
            name="High Zoom Shelf",
            photo="libraries/photos/2026/02/high-zoom.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(
            reverse("map_libraries_geojson"),
            {
                "zoom": "14",
                "min_lat": "43.5",
                "min_lng": "11.0",
                "max_lat": "44.0",
                "max_lng": "11.5",
            },
        )

        payload = response.json()
        assert response.status_code == 200
        assert payload["meta"].get("clustered") is not True
        assert len(payload["features"]) == 1
        feature = payload["features"][0]
        assert feature["properties"]["slug"] == library.slug
        assert "detail_url" in feature["properties"]

    def test_search_filters_bypass_clustering(self, client, user):
        """Verify search filters at low zoom bypass clustering and return individual results.
        Users expect precise matches when actively filtering."""
        Library.objects.create(
            name="Filtered Shelf",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Test 1",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(
            reverse("map_libraries_geojson"),
            {"zoom": "5", "city": "Florence"},
        )

        payload = response.json()
        assert response.status_code == 200
        assert payload["meta"].get("clustered") is not True

    def test_no_zoom_falls_back_to_individual(self, client, user):
        """Verify omitting zoom parameter returns individual points for backwards compatibility.
        Ensures direct URL hits and old cached pages still work."""
        Library.objects.create(
            name="No Zoom Shelf",
            photo="libraries/photos/2026/02/no-zoom.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Test 1",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(reverse("map_libraries_geojson"))

        payload = response.json()
        assert response.status_code == 200
        assert payload["meta"].get("clustered") is not True
        assert len(payload["features"]) == 1
        assert "slug" in payload["features"][0]["properties"]


@pytest.mark.django_db
class TestClusteringGridSize:
    """Unit tests for zoom-to-grid-size mapping."""

    def test_grid_size_decreases_with_increasing_zoom(self):
        """Verify that higher zoom levels use smaller grid cells.
        Ensures clusters get progressively finer as users zoom in."""
        from libraries.clustering import get_grid_size_for_zoom

        previous_size = get_grid_size_for_zoom(0)
        for zoom in range(1, 12):
            current_size = get_grid_size_for_zoom(zoom)
            assert current_size < previous_size, (
                f"Grid size at zoom {zoom} ({current_size}) should be "
                f"smaller than zoom {zoom - 1} ({previous_size})"
            )
            previous_size = current_size

    def test_negative_zoom_returns_largest_grid(self):
        """Verify negative zoom falls back to the largest grid cell size.
        Handles edge cases from unexpected client values."""
        from libraries.clustering import get_grid_size_for_zoom, ZOOM_GRID_SIZE

        assert get_grid_size_for_zoom(-1) == ZOOM_GRID_SIZE[0]

    def test_zoom_above_table_returns_smallest_grid(self):
        """Verify zoom levels above the table use the smallest grid cell size.
        Prevents missing-key errors for high zoom values."""
        from libraries.clustering import get_grid_size_for_zoom, ZOOM_GRID_SIZE

        assert get_grid_size_for_zoom(20) == ZOOM_GRID_SIZE[11]


@pytest.mark.django_db
class TestMapListEndpointFilters:
    def test_list_endpoint_search_by_keywords_matches_approved_name_and_description(self, client, user):
        """Verify keyword filtering shows approved libraries in list mode.
        Excludes pending entries while keeping keyword matching behavior."""
        matching_library = Library.objects.create(
            name="Canal Stories Shelf",
            description="Weekly fiction swaps by the water.",
            photo="libraries/photos/2026/02/search-keyword-1.jpg",
            location=Point(x=4.9041, y=52.3676, srid=4326),
            address="Prinsengracht 140",
            city="Amsterdam",
            country="NL",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="Garden Exchange",
            description="Children books and picture books.",
            photo="libraries/photos/2026/02/search-keyword-2.jpg",
            location=Point(x=4.9100, y=52.3600, srid=4326),
            address="Nieuwe Spiegelstraat 9",
            city="Amsterdam",
            country="NL",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="Canal Hidden Shelf",
            description="Pending moderation.",
            photo="libraries/photos/2026/02/search-keyword-3.jpg",
            location=Point(x=4.9200, y=52.3500, srid=4326),
            address="Keizersgracht 1",
            city="Amsterdam",
            country="NL",
            status=Library.Status.PENDING,
            created_by=user,
        )

        response = client.get(reverse("map_libraries_list"), {"q": "Canal"})

        content = response.content.decode()
        assert response.status_code == 200
        assert matching_library.name in content
        assert "Garden Exchange" not in content
        assert "Canal Hidden Shelf" not in content

    def test_list_endpoint_filters_city_country_and_postal_code(self, client, user):
        """Verify structured filters can be combined in list mode.
        Narrows list cards by city, country, and postal code values."""
        matching_library = Library.objects.create(
            name="Florence Central Shelf",
            description="Community favorite.",
            photo="libraries/photos/2026/02/search-filter-1.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            postal_code="50123",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        Library.objects.create(
            name="Rome Shelf",
            description="Different city.",
            photo="libraries/photos/2026/02/search-filter-2.jpg",
            location=Point(x=12.4964, y=41.9028, srid=4326),
            address="Via dei Fori Imperiali 1",
            city="Rome",
            country="IT",
            postal_code="00184",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(
            reverse("map_libraries_list"),
            {
                "city": "Flor",
                "country": "IT",
                "postal_code": "501",
            },
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert matching_library.name in content
        assert "Rome Shelf" not in content

    @patch("libraries.views.forward_geocode_place")
    def test_list_endpoint_near_filter_returns_nearby_results_within_radius(
        self,
        mocked_forward_geocode,
        client,
        user,
    ):
        """Verify place-like search includes nearby non-matching city names.
        Confirms radius filtering extends results beyond strict city text."""
        mocked_forward_geocode.return_value = (51.5074, -0.1278)
        london_library = Library.objects.create(
            name="Central London Shelf",
            description="Close to the Thames.",
            photo="libraries/photos/2026/02/search-near-1.jpg",
            location=Point(x=-0.1270, y=51.5070, srid=4326),
            address="Whitehall",
            city="London",
            country="GB",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        nearby_non_london_city = Library.objects.create(
            name="Westminster Pocket Shelf",
            description="A short walk from the center.",
            photo="libraries/photos/2026/02/search-near-2.jpg",
            location=Point(x=-0.1410, y=51.5010, srid=4326),
            address="Birdcage Walk",
            city="Westminster",
            country="GB",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        far_library = Library.objects.create(
            name="Cambridge Shelf",
            description="Too far for a 20 km search.",
            photo="libraries/photos/2026/02/search-near-3.jpg",
            location=Point(x=0.1218, y=52.2053, srid=4326),
            address="King's Parade",
            city="Cambridge",
            country="GB",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(
            reverse("map_libraries_list"),
            {"near": "London", "radius_km": "20"},
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert london_library.name in content
        assert nearby_non_london_city.name in content
        assert far_library.name not in content

    @patch("libraries.views.forward_geocode_place")
    def test_list_endpoint_falls_back_to_keywords_when_geocoding_fails(
        self,
        mocked_forward_geocode,
        client,
        user,
    ):
        """Verify unresolved place searches still return keyword matches.
        Preserves useful results when place geocoding fails."""
        mocked_forward_geocode.return_value = None
        matching_library = Library.objects.create(
            name="London Stories Library",
            description="A traveling collection.",
            photo="libraries/photos/2026/02/search-fallback-1.jpg",
            location=Point(x=2.3522, y=48.8566, srid=4326),
            address="Rue de Rivoli 20",
            city="Paris",
            country="FR",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(reverse("map_libraries_list"), {"near": "London"})

        content = response.content.decode()
        assert response.status_code == 200
        assert matching_library.name in content
        assert "Could not resolve \"London\"" in content

    def test_list_endpoint_shows_empty_state_when_no_results_match(self, client, user):
        """Verify no-match filter combinations render a clear empty state.
        Helps users understand they need to broaden their filters."""
        Library.objects.create(
            name="Florence Shelf",
            description="Classic novels.",
            photo="libraries/photos/2026/02/search-empty-1.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(reverse("map_libraries_list"), {"q": "nonexistent-search-term"})

        content = response.content.decode()
        assert response.status_code == 200
        assert "No libraries found" in content

    def test_list_endpoint_paginates_results_with_explicit_page_selection(self, client, user):
        """Verify list endpoint paginates large result sets with page controls.
        Supports non-infinite navigation while preserving current filter state."""
        for index in range(13):
            Library.objects.create(
                name=f"Paged List Shelf {index}",
                photo="libraries/photos/2026/02/map-list-page.jpg",
                location=Point(x=11.2558 + index * 0.0003, y=43.7696 + index * 0.0003, srid=4326),
                address=f"Via Rosina {index + 1}",
                city="Florence",
                country="IT",
                status=Library.Status.APPROVED,
                created_by=user,
            )

        first_page_response = client.get(reverse("map_libraries_list"))
        second_page_response = client.get(reverse("map_libraries_list"), {"page": "2"})

        first_page_content = first_page_response.content.decode()
        second_page_content = second_page_response.content.decode()

        assert first_page_response.status_code == 200
        assert "Page 1 of 2" in first_page_content
        assert 'data-list-page="2"' in first_page_content

        assert second_page_response.status_code == 200
        assert "Page 2 of 2" in second_page_content
        assert 'data-list-page="1"' in second_page_content
        assert "Paged List Shelf 0" in second_page_content


@pytest.mark.django_db
class TestLibraryDetailView:
    def test_approved_library_detail_renders_expected_content(self, client, user):
        """Verify approved library detail renders expected content.
        Confirms the expected behavior stays stable."""
        library = Library.objects.create(
            name="Canal Book Corner",
            description="Waterproof little free library with kid-friendly picks.",
            photo="libraries/photos/2026/02/detail.jpg",
            location=Point(x=4.9041, y=52.3676, srid=4326),
            address="Prinsengracht 140",
            city="Amsterdam",
            country="NL",
            postal_code="1015",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Canal Book Corner" in content
        assert "Waterproof little free library with kid-friendly picks." in content
        assert "Prinsengracht 140" in content
        assert "City:</span> Amsterdam" in content
        assert "Country:</span> NL" in content
        assert "id=\"library-detail-map\"" in content
        assert "leaflet@1.9.4" in content

    def test_pending_library_detail_returns_404(self, client, user):
        """Verify pending library detail returns 404.
        Confirms the expected behavior stays stable."""
        library = Library.objects.create(
            name="Pending Detail",
            description="Not visible yet.",
            photo="libraries/photos/2026/02/pending-detail.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via dei Neri 15",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=user,
        )

        response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))

        assert response.status_code == 404

    def test_creator_can_view_pending_library_detail(self, client, user):
        """Verify creators can view their own pending library detail pages.
        Keeps dashboard detail links usable before moderation approval."""
        library = Library.objects.create(
            name="Pending by Creator",
            description="Waiting for moderation.",
            photo="libraries/photos/2026/02/pending-owner.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via del Corso 9",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=user,
        )

        client.force_login(user)
        response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Pending by Creator" in content

    def test_pending_library_detail_returns_404_for_authenticated_non_owner(self, client, user):
        """Verify pending library detail stays private for non-owners.
        Prevents authenticated users from accessing others' pending entries."""
        owner = User.objects.create_user(
            username="owneruser",
            password="OwnerPass123!",
        )
        viewer = User.objects.create_user(
            username="vieweruser",
            password="ViewerPass123!",
        )
        library = Library.objects.create(
            name="Pending Private",
            description="Not visible to other users.",
            photo="libraries/photos/2026/02/pending-private.jpg",
            location=Point(x=11.2668, y=43.7806, srid=4326),
            address="Via del Proconsolo 12",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=owner,
        )

        client.force_login(viewer)
        response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))

        assert response.status_code == 404

    def test_nonexistent_library_slug_returns_404(self, client):
        """Verify nonexistent library slug returns 404.
        Confirms the expected behavior stays stable."""
        response = client.get(reverse("library_detail", kwargs={"slug": "does-not-exist"}))

        assert response.status_code == 404

    def test_report_button_is_only_visible_to_authenticated_users(self, client, user):
        """Verify report button is only visible to authenticated users.
        Confirms the expected behavior stays stable."""
        library = Library.objects.create(
            name="Reportable Library",
            description="A popular little free library.",
            photo="libraries/photos/2026/02/reportable.jpg",
            location=Point(x=2.3522, y=48.8566, srid=4326),
            address="Rue de Rivoli 20",
            city="Paris",
            country="FR",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        detail_url = reverse("library_detail", kwargs={"slug": library.slug})

        anonymous_response = client.get(detail_url)
        anonymous_content = anonymous_response.content.decode()
        assert anonymous_response.status_code == 200
        assert "Report an issue" not in anonymous_content

        client.force_login(user)
        authenticated_response = client.get(detail_url)
        authenticated_content = authenticated_response.content.decode()
        assert authenticated_response.status_code == 200
        assert "Report an issue" in authenticated_content
        assert "id=\"report-form-toggle\"" in authenticated_content
        assert "aria-expanded=\"false\"" in authenticated_content
        assert "id=\"report-form\"" in authenticated_content
        assert "class=\"mt-6 hidden rounded-box border border-dashed border-base-300 bg-base-100 p-5\"" in authenticated_content
        assert f'hx-post="{reverse("submit_library_report", kwargs={"slug": library.slug})}"' in authenticated_content
        assert "name=\"reason\"" in authenticated_content
        assert "name=\"details\"" in authenticated_content


@pytest.mark.django_db
class TestLibraryReportSubmission:
    def test_inline_report_submission_creates_report_and_returns_success_partial(self, client, user):
        """Verify inline report submissions persist and return a success partial.
        Confirms HTMX report flow completes without a full page reload."""
        reporter = User.objects.create_user(
            username="reporteruser",
            password="ReporterPass123!",
        )
        library = Library.objects.create(
            name="Inline Report Shelf",
            description="Library that can be reported.",
            photo="libraries/photos/2026/02/report-inline.jpg",
            location=Point(x=9.1900, y=45.4642, srid=4326),
            address="Via Torino 10",
            city="Milan",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        client.force_login(reporter)
        response = client.post(
            reverse("submit_library_report", kwargs={"slug": library.slug}),
            data={
                "reason": Report.Reason.DAMAGED,
                "details": "The front door is broken and cannot close.",
            },
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "alert alert-success" in content
        assert "pending moderation review" in content

        report = Report.objects.get()
        assert report.library == library
        assert report.created_by == reporter
        assert report.reason == Report.Reason.DAMAGED
        assert report.status == Report.Status.OPEN

    def test_inline_report_submission_returns_validation_errors_without_creating_report(
        self,
        client,
        user,
    ):
        """Verify invalid report payloads return inline form errors.
        Keeps users on the detail page while preserving validation feedback."""
        library = Library.objects.create(
            name="Validation Report Shelf",
            description="Library used for report validation tests.",
            photo="libraries/photos/2026/02/report-validation.jpg",
            location=Point(x=12.4964, y=41.9028, srid=4326),
            address="Via del Corso 20",
            city="Rome",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        client.force_login(user)
        response = client.post(
            reverse("submit_library_report", kwargs={"slug": library.slug}),
            data={
                "reason": Report.Reason.OTHER,
            },
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        assert response.status_code == 422
        assert "Please review the highlighted fields" in content
        assert "This field is required." in content
        assert Report.objects.count() == 0

    def test_inline_report_submission_rejects_non_image_photo_upload(self, client, user):
        """Verify report flow rejects non-image uploads.
        Prevents arbitrary files from entering report photo storage."""
        library = Library.objects.create(
            name="Invalid Report Upload Shelf",
            description="Library used for invalid report upload checks.",
            photo="libraries/photos/2026/02/report-invalid-upload.jpg",
            location=Point(x=12.4964, y=41.9028, srid=4326),
            address="Via del Corso 20",
            city="Rome",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        invalid_photo = SimpleUploadedFile(
            name="invalid-report-photo.txt",
            content=b"not-an-image",
            content_type="text/plain",
        )

        client.force_login(user)
        response = client.post(
            reverse("submit_library_report", kwargs={"slug": library.slug}),
            data={
                "reason": Report.Reason.OTHER,
                "details": "This report should fail because photo is invalid.",
                "photo": invalid_photo,
            },
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        assert response.status_code == 422
        assert "Upload a valid image" in content
        assert Report.objects.count() == 0

    def test_inline_report_submission_rejects_details_longer_than_max_length(self, client, user):
        """Verify report details are constrained by form-level length rules.
        Ensures user-provided text fields remain bounded for moderation data."""
        library = Library.objects.create(
            name="Long Report Details Shelf",
            description="Library used for report details length checks.",
            photo="libraries/photos/2026/02/report-long-details.jpg",
            location=Point(x=12.4964, y=41.9028, srid=4326),
            address="Via del Corso 20",
            city="Rome",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        client.force_login(user)
        response = client.post(
            reverse("submit_library_report", kwargs={"slug": library.slug}),
            data={
                "reason": Report.Reason.OTHER,
                "details": "x" * 2001,
            },
            HTTP_HX_REQUEST="true",
        )

        content = response.content.decode()
        assert response.status_code == 422
        assert "Ensure this value has at most 2000 characters" in content
        assert Report.objects.count() == 0

    def test_inline_report_submission_requires_authentication(self, client, user):
        """Verify report submissions require authentication.
        Prevents anonymous users from creating moderation reports."""
        library = Library.objects.create(
            name="Protected Report Shelf",
            description="Requires login before reporting.",
            photo="libraries/photos/2026/02/report-auth.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 5",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.post(
            reverse("submit_library_report", kwargs={"slug": library.slug}),
            data={
                "reason": Report.Reason.INCORRECT_INFO,
                "details": "Address and city do not match.",
            },
        )

        assert response.status_code == 302
        assert response.url.startswith(f"{reverse('login')}?next=")
        assert Report.objects.count() == 0

    def test_inline_report_submission_returns_404_for_unowned_pending_library(self, client, user):
        """Verify report endpoint denies access to non-visible pending libraries.
        Keeps private pending entries protected from unrelated users."""
        owner = User.objects.create_user(
            username="pendingowner",
            password="PendingPass123!",
        )
        pending_library = Library.objects.create(
            name="Pending Hidden Shelf",
            description="Not visible to other users yet.",
            photo="libraries/photos/2026/02/report-hidden.jpg",
            location=Point(x=13.4050, y=52.5200, srid=4326),
            address="Friedrichstrasse 7",
            city="Berlin",
            country="DE",
            status=Library.Status.PENDING,
            created_by=owner,
        )

        client.force_login(user)
        response = client.post(
            reverse("submit_library_report", kwargs={"slug": pending_library.slug}),
            data={
                "reason": Report.Reason.MISSING,
                "details": "I cannot find this library anymore.",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 404
        assert Report.objects.count() == 0

    def test_inline_report_submission_result_is_visible_in_admin_changelist(
        self,
        client,
        admin_client,
        admin_user,
        user,
    ):
        """Verify reports submitted from detail pages are visible in admin.
        Confirms moderation team can manage newly submitted reports."""
        library = Library.objects.create(
            name="Admin Visible Report Shelf",
            description="Report entries should appear in the admin list.",
            photo="libraries/photos/2026/02/report-admin.jpg",
            location=Point(x=-0.1276, y=51.5072, srid=4326),
            address="Baker Street 221B",
            city="London",
            country="GB",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        client.force_login(user)
        submit_response = client.post(
            reverse("submit_library_report", kwargs={"slug": library.slug}),
            data={
                "reason": Report.Reason.INAPPROPRIATE,
                "details": "The description includes inappropriate content.",
            },
            HTTP_HX_REQUEST="true",
        )

        assert submit_response.status_code == 200
        assert Report.objects.count() == 1

        admin_client.force_login(admin_user)
        admin_response = admin_client.get(reverse("admin:libraries_report_changelist"))
        admin_content = admin_response.content.decode()
        assert admin_response.status_code == 200
        assert "Admin Visible Report Shelf" in admin_content
        assert "Inappropriate" in admin_content


@pytest.mark.django_db
class TestUserDashboardView:
    def test_dashboard_requires_authentication(self, client):
        """Verify dashboard requires authentication before rendering.
        Protects user-specific submissions from anonymous visitors."""
        response = client.get(reverse("dashboard"))

        assert response.status_code == 302
        assert response.url.startswith(f"{reverse('login')}?next=")

    def test_dashboard_lists_only_current_user_submissions_with_status_badges(self, client, user):
        """Verify dashboard shows only current user submissions and statuses.
        Ensures moderation state and detail links are visible per submission."""
        other_user = User.objects.create_user(
            username="someoneelse",
            password="AnotherPass123!",
        )
        pending_library = Library.objects.create(
            name="Pending Shelf",
            photo="libraries/photos/2026/02/pending-dashboard.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=user,
        )
        approved_library = Library.objects.create(
            name="Approved Shelf",
            photo="libraries/photos/2026/02/approved-dashboard.jpg",
            location=Point(x=11.2568, y=43.7706, srid=4326),
            address="Via de' Benci 10",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )
        rejected_library = Library.objects.create(
            name="Rejected Shelf",
            photo="libraries/photos/2026/02/rejected-dashboard.jpg",
            location=Point(x=11.2578, y=43.7716, srid=4326),
            address="Via Ghibellina 25",
            city="Florence",
            country="IT",
            status=Library.Status.REJECTED,
            created_by=user,
        )
        other_users_library = Library.objects.create(
            name="Private Shelf",
            photo="libraries/photos/2026/02/private-dashboard.jpg",
            location=Point(x=11.2588, y=43.7726, srid=4326),
            address="Via dei Neri 7",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=other_user,
        )

        client.force_login(user)
        response = client.get(reverse("dashboard"))

        content = response.content.decode()
        assert response.status_code == 200
        assert pending_library.name in content
        assert approved_library.name in content
        assert rejected_library.name in content
        assert other_users_library.name not in content
        assert "badge-warning" in content
        assert "badge-success" in content
        assert "badge-error" in content
        assert f'href="{reverse("library_detail", kwargs={"slug": pending_library.slug})}"' in content
        assert f'href="{reverse("library_detail", kwargs={"slug": approved_library.slug})}"' in content
        assert f'href="{reverse("library_detail", kwargs={"slug": rejected_library.slug})}"' in content


@pytest.mark.django_db
class TestSubmitLibraryView:
    def test_submit_view_requires_authentication(self, client):
        """Verify anonymous users are redirected before opening submit form.
        Protects login gating on the library submission flow."""
        response = client.get(reverse("submit_library"))

        assert response.status_code == 302
        assert response.url.startswith(f"{reverse('login')}?next=")

    def test_submit_view_renders_map_and_country_selector(self, client, user):
        """Verify the submit page renders map controls and country selector.
        Covers the core UI required to choose and refine coordinates."""
        client.force_login(user)

        response = client.get(reverse("submit_library"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "id=\"submit-library-map\"" in content
        assert "id=\"id_country\"" in content
        assert "tom-select" in content
        assert "id=\"id_latitude\"" in content
        assert "id=\"id_longitude\"" in content
        assert "Center from address" in content
        assert "id=\"address-suggestions\"" in content
        assert "photon.komoot.io/api" in content
        assert "Name (optional)" in content
        assert "Description (optional)" in content
        assert "Postal code (optional)" in content

        country_position = content.find(">Country<")
        city_position = content.find(">City<")
        address_position = content.find(">Address<")
        postal_code_position = content.find(">Postal code (optional)<")
        assert country_position < city_position < address_position < postal_code_position

    def test_authenticated_submit_creates_pending_library_and_redirects_to_confirmation(self, client, user):
        """Verify valid submissions create pending libraries and redirect.
        Covers persistence rules for new user-submitted library entries."""
        client.force_login(user)

        response = client.post(
            reverse("submit_library"),
            data={
                "photo": _build_uploaded_photo(),
                "name": "Canal Shelf",
                "description": "Small library near the water.",
                "address": "Prinsengracht 150",
                "city": "Amsterdam",
                "country": "NL",
                "postal_code": "1015",
                "latitude": "52.3676",
                "longitude": "4.9041",
            },
        )

        assert response.status_code == 302
        assert response.url == reverse("submit_library_confirmation")

        library = Library.objects.get(name="Canal Shelf")
        assert library.status == Library.Status.PENDING
        assert library.created_by == user
        assert library.country == "NL"
        assert library.location.y == pytest.approx(52.3676, abs=1e-6)
        assert library.location.x == pytest.approx(4.9041, abs=1e-6)
        assert library.photo.name
        assert library.photo_thumbnail.name

    def test_submit_upload_resizes_photo_compresses_and_generates_thumbnail(
        self,
        client,
        user,
        settings,
        tmp_path,
    ):
        """Verify uploaded photos are resized and compressed before storage.
        Ensures submit flow stores both optimized main and thumbnail images."""
        settings.MEDIA_ROOT = tmp_path / "media"
        client.force_login(user)

        uploaded_photo = _build_uploaded_photo(
            file_name="large-upload.jpg",
            width=3600,
            height=2400,
            quality=98,
        )
        original_payload_size = uploaded_photo.size

        response = client.post(
            reverse("submit_library"),
            data={
                "photo": uploaded_photo,
                "name": "Optimized Shelf",
                "description": "Large upload that should be optimized.",
                "address": "Via dei Banchi 3",
                "city": "Florence",
                "country": "IT",
                "postal_code": "50123",
                "latitude": "43.7696",
                "longitude": "11.2558",
            },
        )

        assert response.status_code == 302
        assert response.url == reverse("submit_library_confirmation")

        library = Library.objects.get(name="Optimized Shelf")
        assert library.photo.name
        assert library.photo_thumbnail.name
        assert library.photo.size < original_payload_size

        with Image.open(library.photo.path) as optimized_image:
            assert max(optimized_image.size) <= 1600
            assert optimized_image.format == "JPEG"

        with Image.open(library.photo_thumbnail.path) as thumbnail_image:
            assert thumbnail_image.width <= 400
            assert thumbnail_image.height >= 1
            assert thumbnail_image.format == "JPEG"

    def test_submit_rejects_non_image_photo_upload(self, client, user):
        """Verify submit flow rejects uploads that are not valid images.
        Prevents arbitrary file types from being persisted as photo payloads."""
        client.force_login(user)
        invalid_photo = SimpleUploadedFile(
            name="invalid-photo.txt",
            content=b"not-an-image",
            content_type="text/plain",
        )

        response = client.post(
            reverse("submit_library"),
            data={
                "photo": invalid_photo,
                "name": "Invalid Upload Shelf",
                "description": "Should fail due to non-image upload.",
                "address": "Via dei Banchi 3",
                "city": "Florence",
                "country": "IT",
                "postal_code": "50123",
                "latitude": "43.7696",
                "longitude": "11.2558",
            },
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Upload a valid image" in content
        assert not Library.objects.filter(name="Invalid Upload Shelf").exists()

    @override_settings(MAX_LIBRARY_PHOTO_UPLOAD_BYTES=1024)
    def test_submit_rejects_photo_larger_than_max_size(self, client, user):
        """Verify submit flow enforces the maximum configured photo size.
        Protects the application from oversized upload payloads."""
        client.force_login(user)

        response = client.post(
            reverse("submit_library"),
            data={
                "photo": _build_uploaded_photo(file_name="too-large.jpg"),
                "name": "Too Large Upload Shelf",
                "description": "Should fail due to size restrictions.",
                "address": "Via dei Banchi 3",
                "city": "Florence",
                "country": "IT",
                "postal_code": "50123",
                "latitude": "43.7696",
                "longitude": "11.2558",
            },
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Photo must be at most 1024 bytes." in content
        assert not Library.objects.filter(name="Too Large Upload Shelf").exists()

    def test_submit_rejects_description_longer_than_max_length(self, client, user):
        """Verify submit flow validates description length limits.
        Ensures text inputs are bounded to prevent oversized payloads."""
        client.force_login(user)
        long_description = "x" * 2001

        response = client.post(
            reverse("submit_library"),
            data={
                "photo": _build_uploaded_photo(file_name="long-description.jpg"),
                "name": "Long Description Shelf",
                "description": long_description,
                "address": "Via dei Banchi 3",
                "city": "Florence",
                "country": "IT",
                "postal_code": "50123",
                "latitude": "43.7696",
                "longitude": "11.2558",
            },
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Ensure this value has at most 2000 characters" in content
        assert not Library.objects.filter(name="Long Description Shelf").exists()

    def test_photo_metadata_endpoint_requires_authentication(self, client):
        """Verify the photo metadata endpoint is protected by login.
        Prevents unauthenticated EXIF and geocoding lookups."""
        response = client.post(
            reverse("submit_library_photo_metadata"),
            data={"photo": _build_uploaded_photo()},
        )

        assert response.status_code == 302
        assert response.url.startswith(f"{reverse('login')}?next=")

    @patch("libraries.views.reverse_geocode_coordinates")
    def test_photo_metadata_endpoint_returns_prefill_payload_when_exif_exists(
        self,
        mocked_reverse_geocode,
        client,
        user,
    ):
        """Verify geotagged photos return EXIF and prefill address payload.
        Covers the JSON contract used by submit-form auto-prefill."""
        client.force_login(user)
        mocked_reverse_geocode.return_value = {
            "address": "Via Rosina 15",
            "city": "Florence",
            "country": "IT",
            "postal_code": "50123",
        }

        response = client.post(
            reverse("submit_library_photo_metadata"),
            data={
                "photo": _build_uploaded_photo_with_gps(
                    latitude=43.7696,
                    longitude=11.2558,
                ),
            },
        )

        payload = response.json()
        assert response.status_code == 200
        assert payload["gps_found"] is True
        assert payload["geocoded"] is True
        assert payload["address"] == "Via Rosina 15"
        assert payload["city"] == "Florence"
        assert payload["country"] == "IT"
        assert payload["postal_code"] == "50123"
        assert payload["latitude"] == pytest.approx(43.7696, abs=1e-4)
        assert payload["longitude"] == pytest.approx(11.2558, abs=1e-4)

        assert mocked_reverse_geocode.call_count == 1
        called_kwargs = mocked_reverse_geocode.call_args.kwargs
        assert called_kwargs["latitude"] == pytest.approx(43.7696, abs=1e-4)
        assert called_kwargs["longitude"] == pytest.approx(11.2558, abs=1e-4)

    @patch("libraries.views.reverse_geocode_coordinates")
    def test_photo_metadata_endpoint_returns_no_gps_when_photo_has_no_exif(
        self,
        mocked_reverse_geocode,
        client,
        user,
    ):
        """Verify non-geotagged photos return a no-GPS metadata response.
        Covers graceful fallback when EXIF coordinates are unavailable."""
        client.force_login(user)

        response = client.post(
            reverse("submit_library_photo_metadata"),
            data={"photo": _build_uploaded_photo()},
        )

        assert response.status_code == 200
        assert response.json() == {"gps_found": False}
        mocked_reverse_geocode.assert_not_called()

    def test_submit_confirmation_page_renders_continue_button(self, client):
        """Verify the confirmation page renders success copy and continue link.
        Covers the final step of the submission user journey."""
        response = client.get(reverse("submit_library_confirmation"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Submission received" in content
        assert "reviewed and approved as soon as possible" in content
        assert f"href=\"{reverse('home')}\"" in content
        assert "Continue" in content


@pytest.fixture
def approved_library(user):
    """Create an approved library fixture for photo submission tests.
    Provides an approved library that accepts community photo uploads."""
    return Library.objects.create(
        name="Approved Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city="Florence",
        country="IT",
        status=Library.Status.APPROVED,
        created_by=user,
    )


@pytest.fixture
def admin_library_photo(admin_library, admin_user):
    """Create a pending library photo fixture for moderation tests.
    Provides a photo attached to the admin library for admin action tests."""
    return LibraryPhoto.objects.create(
        library=admin_library,
        created_by=admin_user,
        photo="libraries/user_photos/2026/02/community.jpg",
        caption="A nice photo",
    )


@pytest.mark.django_db
class TestLibraryPhotoModel:
    """Tests for the LibraryPhoto model."""

    def test_create_library_photo(self, approved_library, user):
        """Verify creating a library photo persists all fields.
        Confirms the model stores photo, caption, and relationships correctly."""
        library_photo = LibraryPhoto.objects.create(
            library=approved_library,
            created_by=user,
            photo="libraries/user_photos/2026/02/test.jpg",
            caption="Great library",
        )

        assert library_photo.pk is not None
        assert library_photo.library == approved_library
        assert library_photo.created_by == user
        assert library_photo.caption == "Great library"
        assert library_photo.created_at is not None

    def test_default_status_is_pending(self, approved_library, user):
        """Verify new library photos default to pending status.
        Ensures all community photos go through moderation first."""
        library_photo = LibraryPhoto.objects.create(
            library=approved_library,
            created_by=user,
            photo="libraries/user_photos/2026/02/test.jpg",
        )

        assert library_photo.status == LibraryPhoto.Status.PENDING

    def test_str_with_caption(self, approved_library, user):
        """Verify string representation uses caption when available.
        Keeps admin and log output descriptive for captioned photos."""
        library_photo = LibraryPhoto.objects.create(
            library=approved_library,
            created_by=user,
            photo="libraries/user_photos/2026/02/test.jpg",
            caption="Summer view",
        )

        assert str(library_photo) == f"Summer view - {approved_library}"

    def test_str_without_caption(self, approved_library, user):
        """Verify string representation falls back to generic label.
        Keeps admin output clear for photos without captions."""
        library_photo = LibraryPhoto.objects.create(
            library=approved_library,
            created_by=user,
            photo="libraries/user_photos/2026/02/test.jpg",
        )

        assert str(library_photo) == f"Photo - {approved_library}"

    def test_card_photo_url_falls_back_to_main_photo(self, approved_library, user):
        """Verify card photo URL falls back to main photo without thumbnail.
        Preserves rendering compatibility for photos without thumbnails."""
        library_photo = LibraryPhoto.objects.create(
            library=approved_library,
            created_by=user,
            photo="libraries/user_photos/2026/02/test.jpg",
        )

        assert library_photo.photo_thumbnail == ""
        assert library_photo.card_photo_url.endswith("/libraries/user_photos/2026/02/test.jpg")

    def test_card_photo_url_returns_empty_string_without_photo(self, approved_library, user):
        """Verify card photo URL returns empty string when no photo is set.
        Prevents template rendering errors for edge-case records."""
        library_photo = LibraryPhoto(
            library=approved_library,
            created_by=user,
        )

        assert library_photo.card_photo_url == ""


@pytest.mark.django_db
class TestLibraryPhotoAdmin:
    """Tests for LibraryPhoto admin actions."""

    def test_approve_photos_action(self, admin_client, admin_library_photo):
        """Verify approve photos action updates status to approved.
        Confirms the admin moderation workflow for community photos."""
        url = reverse("admin:libraries_libraryphoto_changelist")
        response = admin_client.post(url, {
            "action": "approve_photos",
            "_selected_action": [admin_library_photo.pk],
        })

        assert response.status_code == 302
        admin_library_photo.refresh_from_db()
        assert admin_library_photo.status == LibraryPhoto.Status.APPROVED

    def test_reject_photos_action(self, admin_client, admin_library_photo):
        """Verify reject photos action updates status to rejected.
        Confirms rejected photos are removed from public display."""
        url = reverse("admin:libraries_libraryphoto_changelist")
        response = admin_client.post(url, {
            "action": "reject_photos",
            "_selected_action": [admin_library_photo.pk],
        })

        assert response.status_code == 302
        admin_library_photo.refresh_from_db()
        assert admin_library_photo.status == LibraryPhoto.Status.REJECTED

    def test_set_as_primary_photo_action(self, admin_client, admin_library_photo):
        """Verify set as primary copies photo to the library record.
        Confirms admin can promote a community photo to the primary slot."""
        url = reverse("admin:libraries_libraryphoto_changelist")
        response = admin_client.post(url, {
            "action": "set_as_primary_photo",
            "_selected_action": [admin_library_photo.pk],
        })

        assert response.status_code == 302
        admin_library_photo.refresh_from_db()
        assert admin_library_photo.status == LibraryPhoto.Status.APPROVED
        library = admin_library_photo.library
        library.refresh_from_db()
        assert library.photo == admin_library_photo.photo

    def test_set_as_primary_rejects_multiple_selection(self, admin_client, admin_library_photo, admin_user, admin_library):
        """Verify set as primary rejects when multiple photos are selected.
        Enforces single-selection constraint for primary photo promotion."""
        second_photo = LibraryPhoto.objects.create(
            library=admin_library,
            created_by=admin_user,
            photo="libraries/user_photos/2026/02/second.jpg",
        )
        url = reverse("admin:libraries_libraryphoto_changelist")
        response = admin_client.post(url, {
            "action": "set_as_primary_photo",
            "_selected_action": [admin_library_photo.pk, second_photo.pk],
        })

        assert response.status_code == 302
        admin_library.refresh_from_db()
        assert admin_library.photo == "libraries/photos/2026/02/test.jpg"


@pytest.mark.django_db
class TestLibraryPhotoSubmission:
    """Tests for community photo submission via web form."""

    def test_submit_photo_happy_path(self, client, user, approved_library):
        """Verify authenticated users can submit a photo to an approved library.
        Confirms the full submission flow creates a pending photo record."""
        client.force_login(user)
        photo = _build_uploaded_photo(file_name="community.jpg")
        url = reverse("submit_library_photo", kwargs={"slug": approved_library.slug})

        response = client.post(url, {"photo": photo, "caption": "Nice library"})

        assert response.status_code == 200
        assert LibraryPhoto.objects.filter(
            library=approved_library,
            created_by=user,
            caption="Nice library",
            status=LibraryPhoto.Status.PENDING,
        ).exists()

    def test_submit_photo_requires_authentication(self, client, approved_library):
        """Verify unauthenticated users are redirected to login.
        Confirms the login_required decorator protects the endpoint."""
        photo = _build_uploaded_photo(file_name="community.jpg")
        url = reverse("submit_library_photo", kwargs={"slug": approved_library.slug})

        response = client.post(url, {"photo": photo})

        assert response.status_code == 302
        assert "/login/" in response.url

    def test_submit_photo_only_for_approved_libraries(self, client, user):
        """Verify photo submission is rejected for non-approved libraries.
        Confirms pending libraries do not accept community photos."""
        pending_library = Library.objects.create(
            name="Pending Library",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Roma 1",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=user,
        )
        client.force_login(user)
        photo = _build_uploaded_photo(file_name="community.jpg")
        url = reverse("submit_library_photo", kwargs={"slug": pending_library.slug})

        response = client.post(url, {"photo": photo})

        assert response.status_code == 404

    def test_submit_photo_enforces_per_user_limit(self, client, user, approved_library):
        """Verify per-user photo limit is enforced per library.
        Confirms users cannot exceed the maximum photo submission count."""
        client.force_login(user)
        for i in range(MAX_LIBRARY_PHOTOS_PER_USER):
            LibraryPhoto.objects.create(
                library=approved_library,
                created_by=user,
                photo=f"libraries/user_photos/2026/02/existing-{i}.jpg",
            )

        photo = _build_uploaded_photo(file_name="one-too-many.jpg")
        url = reverse("submit_library_photo", kwargs={"slug": approved_library.slug})
        response = client.post(url, {"photo": photo})

        assert response.status_code == 422
        content = response.content.decode()
        assert f"at most {MAX_LIBRARY_PHOTOS_PER_USER}" in content

    def test_submit_photo_method_not_allowed_for_get(self, client, user, approved_library):
        """Verify GET requests return 405 for the photo submission endpoint.
        Confirms the view only accepts POST requests."""
        client.force_login(user)
        url = reverse("submit_library_photo", kwargs={"slug": approved_library.slug})

        response = client.get(url)

        assert response.status_code == 405

    def test_gallery_shows_approved_photos(self, client, user, approved_library):
        """Verify approved photos appear in the library detail gallery.
        Confirms the gallery renders approved community photos."""
        LibraryPhoto.objects.create(
            library=approved_library,
            created_by=user,
            photo="libraries/user_photos/2026/02/visible.jpg",
            caption="Visible photo",
            status=LibraryPhoto.Status.APPROVED,
        )

        url = reverse("library_detail", kwargs={"slug": approved_library.slug})
        response = client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        assert "Community photos" in content
        assert "Visible photo" in content

    def test_gallery_hides_pending_photos(self, client, user, approved_library):
        """Verify pending photos do not appear in the library detail gallery.
        Confirms only approved photos are publicly visible."""
        LibraryPhoto.objects.create(
            library=approved_library,
            created_by=user,
            photo="libraries/user_photos/2026/02/hidden.jpg",
            caption="Hidden photo",
            status=LibraryPhoto.Status.PENDING,
        )

        url = reverse("library_detail", kwargs={"slug": approved_library.slug})
        response = client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        assert "Hidden photo" not in content

    def test_add_photo_button_visible_for_authenticated_users(self, client, user, approved_library):
        """Verify the add photo button is visible for authenticated users.
        Confirms the UI entry point for photo submission appears when logged in."""
        client.force_login(user)
        url = reverse("library_detail", kwargs={"slug": approved_library.slug})

        response = client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        assert "Add a photo" in content

    def test_add_photo_button_hidden_for_anonymous_users(self, client, user, approved_library):
        """Verify the add photo button is hidden for anonymous users.
        Confirms only logged-in users see the photo submission entry point."""
        url = reverse("library_detail", kwargs={"slug": approved_library.slug})

        response = client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        assert "Add a photo" not in content

    def test_rejected_photos_do_not_count_toward_limit(self, client, user, approved_library):
        """Verify rejected photos are excluded from the per-user limit.
        Confirms users can resubmit after their photos are rejected."""
        client.force_login(user)
        for i in range(MAX_LIBRARY_PHOTOS_PER_USER):
            LibraryPhoto.objects.create(
                library=approved_library,
                created_by=user,
                photo=f"libraries/user_photos/2026/02/rejected-{i}.jpg",
                status=LibraryPhoto.Status.REJECTED,
            )

        photo = _build_uploaded_photo(file_name="fresh-submission.jpg")
        url = reverse("submit_library_photo", kwargs={"slug": approved_library.slug})
        response = client.post(url, {"photo": photo})

        assert response.status_code == 200
        assert LibraryPhoto.objects.filter(
            library=approved_library,
            created_by=user,
            status=LibraryPhoto.Status.PENDING,
        ).exists()


@pytest.mark.django_db
class TestLibraryPhotoOptional:
    """Tests for the optional Library.photo field."""

    def test_create_library_without_photo(self, user):
        """Verify a library can be created without a photo.
        Confirms imported libraries with no image are valid."""
        library = Library.objects.create(
            name="No Photo Library",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=user,
        )

        assert library.pk is not None
        assert library.photo == ""
        assert "library-placeholder.png" in library.card_photo_url

    def test_detail_page_renders_placeholder_without_photo(self, client, user):
        """Verify library detail page shows a placeholder when library has no photo.
        Confirms the UI layout stays consistent for imported libraries."""
        library = Library.objects.create(
            name="Photoless Shelf",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=user,
        )

        response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Photoless Shelf" in content
        assert "library-placeholder.png" in content


@pytest.mark.django_db
class TestLibraryPhotoInline:
    """Tests for the LibraryPhoto inline on the Library admin page."""

    def test_inline_visible_on_library_change_page(self, admin_client, admin_library):
        """Verify community photos inline appears on the library change page.
        Confirms admins can see and manage photos from the library edit form."""
        url = reverse("admin:libraries_library_change", args=[admin_library.pk])

        response = admin_client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        assert "library_photos" in content or "user_photos" in content


@pytest.mark.django_db
class TestApprovePhotosAutoPromotion:
    """Tests for auto-promotion of approved photos to library primary."""

    def test_approve_promotes_to_primary_when_library_has_no_photo(self, admin_client, admin_user):
        """Verify approving a photo auto-promotes it when library lacks a primary.
        Confirms libraries without photos gain a primary on first approval."""
        library = Library.objects.create(
            name="No Photo Library",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Roma 10",
            city="Florence",
            country="IT",
            created_by=admin_user,
        )
        community_photo = LibraryPhoto.objects.create(
            library=library,
            created_by=admin_user,
            photo="libraries/user_photos/2026/02/promote-me.jpg",
            photo_thumbnail="libraries/user_photos/thumbnails/2026/02/promote-me.jpg",
        )

        url = reverse("admin:libraries_libraryphoto_changelist")
        admin_client.post(url, {
            "action": "approve_photos",
            "_selected_action": [community_photo.pk],
        })

        community_photo.refresh_from_db()
        assert community_photo.status == LibraryPhoto.Status.APPROVED

        library.refresh_from_db()
        assert library.photo == "libraries/user_photos/2026/02/promote-me.jpg"
        assert library.photo_thumbnail == "libraries/user_photos/thumbnails/2026/02/promote-me.jpg"

    def test_approve_does_not_overwrite_existing_primary_photo(self, admin_client, admin_library, admin_user):
        """Verify approving a photo does not overwrite an existing primary.
        Confirms libraries with photos keep their original primary image."""
        original_photo = admin_library.photo
        community_photo = LibraryPhoto.objects.create(
            library=admin_library,
            created_by=admin_user,
            photo="libraries/user_photos/2026/02/community-new.jpg",
        )

        url = reverse("admin:libraries_libraryphoto_changelist")
        admin_client.post(url, {
            "action": "approve_photos",
            "_selected_action": [community_photo.pk],
        })

        community_photo.refresh_from_db()
        assert community_photo.status == LibraryPhoto.Status.APPROVED

        admin_library.refresh_from_db()
        assert admin_library.photo == original_photo


@pytest.mark.django_db
class TestExtractStreet:
    """Tests for the _extract_street helper."""

    def test_strips_trailing_house_number(self):
        """Verify trailing house number is removed from address.
        Ensures street name extraction handles the common case."""
        from libraries.management.commands.find_duplicates import _extract_street

        assert _extract_street("Via Roma 10") == "via roma"

    def test_strips_trailing_house_number_with_letter(self):
        """Verify alphanumeric house numbers like '10A' are stripped.
        Covers European address suffixes."""
        from libraries.management.commands.find_duplicates import _extract_street

        assert _extract_street("Via Roma 10A") == "via roma"

    def test_returns_full_street_without_number(self):
        """Verify addresses without house numbers are returned as-is.
        Covers street-only inputs."""
        from libraries.management.commands.find_duplicates import _extract_street

        assert _extract_street("Via Roma") == "via roma"

    def test_normalises_whitespace_and_case(self):
        """Verify leading/trailing whitespace is trimmed and case lowered.
        Ensures consistent comparison across messy inputs."""
        from libraries.management.commands.find_duplicates import _extract_street

        assert _extract_street("  Via Milano 5  ") == "via milano"


@pytest.mark.django_db
class TestFindDuplicateGroupsProximity:
    """Tests for street-aware proximity and the use_proximity flag."""

    def test_different_streets_within_radius_are_not_duplicates(self, user):
        """Verify two libraries on different streets within 100m are not grouped.
        Prevents false positives from proximity-only matching."""
        from libraries.management.commands.find_duplicates import find_duplicate_groups

        Library.objects.create(
            name="Lib A",
            location=Point(x=11.25580, y=43.76960, srid=4326),
            address="Via Roma 10",
            city="Florence",
            country="IT",
            created_by=user,
        )
        Library.objects.create(
            name="Lib B",
            location=Point(x=11.25590, y=43.76965, srid=4326),
            address="Via Milano 5",
            city="Florence",
            country="IT",
            created_by=user,
        )

        groups = find_duplicate_groups()
        assert groups == []

    def test_same_street_within_radius_are_duplicates(self, user):
        """Verify two libraries on the same street within 100m are grouped.
        Confirms street-aware proximity still catches real duplicates."""
        from libraries.management.commands.find_duplicates import find_duplicate_groups

        Library.objects.create(
            name="Lib A",
            location=Point(x=11.25580, y=43.76960, srid=4326),
            address="Via Roma 10",
            city="Florence",
            country="IT",
            created_by=user,
        )
        Library.objects.create(
            name="Lib B",
            location=Point(x=11.25590, y=43.76965, srid=4326),
            address="Via Roma 12",
            city="Florence",
            country="IT",
            created_by=user,
        )

        groups = find_duplicate_groups()
        assert len(groups) == 1

    def test_use_proximity_false_skips_proximity_pass(self, user):
        """Verify use_proximity=False only matches by exact address.
        Libraries nearby on same street should not be grouped without proximity."""
        from libraries.management.commands.find_duplicates import find_duplicate_groups

        Library.objects.create(
            name="Lib A",
            location=Point(x=11.25580, y=43.76960, srid=4326),
            address="Via Roma 10",
            city="Florence",
            country="IT",
            created_by=user,
        )
        Library.objects.create(
            name="Lib B",
            location=Point(x=11.25590, y=43.76965, srid=4326),
            address="Via Roma 12",
            city="Florence",
            country="IT",
            created_by=user,
        )

        groups = find_duplicate_groups(use_proximity=False)
        assert groups == []


@pytest.mark.django_db
class TestGeoJSONImportStreetAwareProximity:
    """Tests for street-aware proximity in GeoJSON import."""

    def test_import_skips_nearby_same_street(self, user):
        """Verify import flags a candidate on the same street as a nearby library.
        Confirms true duplicates are still caught during import."""
        from libraries.geojson_import import GeoJSONImporter, ImportCandidate

        Library.objects.create(
            name="Existing",
            location=Point(x=11.25580, y=43.76960, srid=4326),
            address="Via Roma 10",
            city="Florence",
            country="IT",
            created_by=user,
        )

        candidate = ImportCandidate(
            external_id="new-1",
            name="New Lib",
            description="",
            longitude=11.25590,
            latitude=43.76965,
            address="Via Roma 12",
            city="Florence",
            country="IT",
            postal_code="",
            wheelchair_accessible="",
            capacity=None,
            is_indoor=None,
            is_lit=None,
            website="",
            contact="",
            operator="",
            brand="",
            image_url="",
        )

        importer = GeoJSONImporter(source="test", status="pending", created_by=user)
        result = importer.run([candidate])

        assert result.skipped_duplicate_location == 1
        assert result.created == 0

    def test_import_allows_nearby_different_street(self, user):
        """Verify import does not flag a candidate on a different street nearby.
        Prevents false duplicate rejection for genuinely separate libraries."""
        from libraries.geojson_import import GeoJSONImporter, ImportCandidate

        Library.objects.create(
            name="Existing",
            location=Point(x=11.25580, y=43.76960, srid=4326),
            address="Via Roma 10",
            city="Florence",
            country="IT",
            created_by=user,
        )

        candidate = ImportCandidate(
            external_id="new-2",
            name="New Lib",
            description="",
            longitude=11.25590,
            latitude=43.76965,
            address="Via Milano 5",
            city="Florence",
            country="IT",
            postal_code="",
            wheelchair_accessible="",
            capacity=None,
            is_indoor=None,
            is_lit=None,
            website="",
            contact="",
            operator="",
            brand="",
            image_url="",
        )

        importer = GeoJSONImporter(source="test", status="pending", created_by=user)
        result = importer.run([candidate])

        assert result.skipped_duplicate_location == 0
        assert result.created == 1


class TestAspectRatioCrop:
    """Tests for the Instagram aspect ratio cropping function."""

    def test_too_wide_image_is_cropped(self):
        """Verify a landscape image beyond 1.91:1 is center-cropped in width.
        Ensures the output ratio equals the maximum allowed bound."""
        image = Image.new("RGB", (1000, 400))
        result = _crop_to_aspect_ratio_bounds(image=image)

        assert result.width == round(400 * MAX_ASPECT_RATIO)
        assert result.height == 400

    def test_too_tall_image_is_cropped(self):
        """Verify a portrait image below 4:5 is center-cropped in height.
        Ensures the output ratio equals the minimum allowed bound."""
        image = Image.new("RGB", (400, 1000))
        result = _crop_to_aspect_ratio_bounds(image=image)

        assert result.width == 400
        assert result.height == round(400 / MIN_ASPECT_RATIO)

    def test_normal_image_is_unchanged(self):
        """Verify an image within bounds passes through unmodified.
        Confirms no unnecessary cropping for compliant aspect ratios."""
        image = Image.new("RGB", (640, 480))
        result = _crop_to_aspect_ratio_bounds(image=image)

        assert result.size == (640, 480)

    def test_exact_min_boundary_is_unchanged(self):
        """Verify an image at exactly the 4:5 lower bound is not cropped.
        Confirms boundary values are treated as within range."""
        width, height = 800, 1000  # ratio = 0.8 = 4/5
        image = Image.new("RGB", (width, height))
        result = _crop_to_aspect_ratio_bounds(image=image)

        assert result.size == (width, height)

    def test_exact_max_boundary_is_unchanged(self):
        """Verify an image at exactly the 1.91:1 upper bound is not cropped.
        Confirms boundary values are treated as within range."""
        width, height = 191, 100  # ratio = 1.91
        image = Image.new("RGB", (width, height))
        result = _crop_to_aspect_ratio_bounds(image=image)

        assert result.size == (width, height)


@pytest.mark.django_db
class TestEnsureInstagramAspectRatio:
    """Integration test for Instagram-specific aspect ratio cropping."""

    def test_too_wide_photo_is_cropped_in_place(self, settings, tmp_path, user):
        """Verify a too-wide library photo is cropped before Instagram posting.
        Ensures the stored photo satisfies Instagram's ratio constraints."""
        settings.MEDIA_ROOT = tmp_path / "media"

        image = Image.new("RGB", (2000, 400), color=(140, 165, 210))
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=85)

        library = Library.objects.create(
            name="Wide Shelf",
            address="Via Roma 1",
            city="Florence",
            country="IT",
            created_by=user,
            location=Point(x=11.2558, y=43.7696, srid=4326),
        )
        library.photo.save("wide.jpg", ContentFile(buf.getvalue()), save=True)

        ensure_instagram_aspect_ratio(library=library)

        with Image.open(library.photo.path) as stored_image:
            ratio = stored_image.width / stored_image.height
            assert MIN_ASPECT_RATIO <= ratio <= MAX_ASPECT_RATIO

    def test_normal_photo_is_not_modified(self, settings, tmp_path, user):
        """Verify a photo within bounds is left untouched.
        Prevents unnecessary re-encoding of compliant images."""
        settings.MEDIA_ROOT = tmp_path / "media"

        image = Image.new("RGB", (640, 480), color=(140, 165, 210))
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=85)
        original_bytes = buf.getvalue()

        library = Library.objects.create(
            name="Normal Shelf",
            address="Via Roma 2",
            city="Florence",
            country="IT",
            created_by=user,
            location=Point(x=11.2558, y=43.7696, srid=4326),
        )
        library.photo.save("normal.jpg", ContentFile(original_bytes), save=True)

        ensure_instagram_aspect_ratio(library=library)

        with open(library.photo.path, "rb") as f:
            assert f.read() == original_bytes
