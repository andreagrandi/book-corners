from io import BytesIO
from unittest.mock import patch

import pytest
from django.contrib.gis.geos import Point
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational

from libraries.geolocation import extract_gps_coordinates
from libraries.models import Library, Report


def _build_uploaded_photo(*, file_name: str = "library.jpg") -> SimpleUploadedFile:
    """Build an in-memory JPEG upload for form and endpoint tests.
    Keeps image fixtures deterministic without touching disk."""
    image_bytes = BytesIO()
    image = Image.new("RGB", (640, 480), color=(140, 165, 210))
    image.save(image_bytes, format="JPEG")
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
            created_by=user,
        )

        assert library.pk is not None
        assert library.name == "The Book Nook"
        assert library.status == Library.Status.PENDING
        assert library.slug == "florence-via-rosina-15-the-book-nook"
        assert library.created_at is not None
        assert library.updated_at is not None

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
        assert "Little Free Libraries" in content
        assert "href=\"/map/\"" in content
        assert "href=\"/search/\"" in content
        assert "href=\"/submit/\"" in content
        assert "href=\"/login/\"" in content
        assert "href=\"/register/\"" in content
        assert "https://unpkg.com/htmx.org@2.0.4" in content
        assert "id=\"latest-entries\"" in content
        assert "hx-get=\"/latest-entries/\"" in content
        assert "Latest entries" in content
        assert "built with Django, HTMX, and OpenStreetMap data" in content


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
        assert "Report this library" not in anonymous_content

        client.force_login(user)
        authenticated_response = client.get(detail_url)
        authenticated_content = authenticated_response.content.decode()
        assert authenticated_response.status_code == 200
        assert "Report this library" in authenticated_content
        assert "id=\"report-form\"" in authenticated_content


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
