from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.test import TestCase
from django.urls import reverse

from libraries.models import Library, Report

User = get_user_model()


class LibraryModelTest(TestCase):
    """Tests for the Library model."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
        )

    def test_create_library_with_all_fields(self) -> None:
        library = Library.objects.create(
            name="The Book Nook",
            description="A cozy little library on the corner.",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            postal_code="50123",
            created_by=self.user,
        )

        assert library.pk is not None
        assert library.name == "The Book Nook"
        assert library.status == Library.Status.PENDING
        assert library.slug == "florence-via-rosina-15-the-book-nook"
        assert library.created_at is not None
        assert library.updated_at is not None

    def test_create_library_without_name(self) -> None:
        library = Library.objects.create(
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=self.user,
        )

        assert library.pk is not None
        assert library.name == ""
        assert library.slug == "florence-via-rosina-15"

    def test_slug_uniqueness_adds_numeric_suffix(self) -> None:
        common_kwargs = {
            "photo": "libraries/photos/2026/02/test.jpg",
            "location": Point(x=11.2558, y=43.7696, srid=4326),
            "address": "Via Rosina 15",
            "city": "Florence",
            "country": "IT",
            "created_by": self.user,
        }

        library_1 = Library.objects.create(**common_kwargs)
        library_2 = Library.objects.create(**common_kwargs)

        assert library_1.slug == "florence-via-rosina-15"
        assert library_2.slug == "florence-via-rosina-15-2"

    def test_library_str_with_name(self) -> None:
        library = Library.objects.create(
            name="The Book Nook",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=self.user,
        )

        assert str(library) == "The Book Nook (Florence)"

    def test_library_str_without_name(self) -> None:
        library = Library.objects.create(
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=self.user,
        )

        assert str(library) == "Via Rosina 15, Florence"

    def test_slug_truncated_for_long_inputs(self) -> None:
        long_address = "A" * 255
        library = Library.objects.create(
            name="B" * 255,
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address=long_address,
            city="Florence",
            country="IT",
            created_by=self.user,
        )

        max_length = Library._meta.get_field("slug").max_length
        assert len(library.slug) <= max_length

    def test_slug_truncation_still_allows_uniqueness(self) -> None:
        long_address = "A" * 255
        common_kwargs = {
            "photo": "libraries/photos/2026/02/test.jpg",
            "location": Point(x=11.2558, y=43.7696, srid=4326),
            "address": long_address,
            "city": "Florence",
            "country": "IT",
            "created_by": self.user,
        }

        library_1 = Library.objects.create(**common_kwargs)
        library_2 = Library.objects.create(**common_kwargs)

        max_length = Library._meta.get_field("slug").max_length
        assert library_1.slug != library_2.slug
        assert len(library_1.slug) <= max_length
        assert len(library_2.slug) <= max_length

    def test_default_status_is_pending(self) -> None:
        library = Library.objects.create(
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=self.user,
        )

        assert library.status == Library.Status.PENDING


class ReportModelTest(TestCase):
    """Tests for the Report model."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
        )
        self.library = Library.objects.create(
            name="Test Library",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=self.user,
        )

    def test_create_report(self) -> None:
        report = Report.objects.create(
            library=self.library,
            created_by=self.user,
            reason=Report.Reason.DAMAGED,
            details="The library box is broken and books are getting wet.",
        )

        assert report.pk is not None
        assert report.reason == Report.Reason.DAMAGED
        assert report.status == Report.Status.OPEN
        assert report.created_at is not None
        assert report.photo == ""

    def test_create_report_with_photo(self) -> None:
        report = Report.objects.create(
            library=self.library,
            created_by=self.user,
            reason=Report.Reason.MISSING,
            details="The library is no longer at this location.",
            photo="reports/photos/2026/02/evidence.jpg",
        )

        assert report.pk is not None
        assert report.photo == "reports/photos/2026/02/evidence.jpg"

    def test_default_status_is_open(self) -> None:
        report = Report.objects.create(
            library=self.library,
            created_by=self.user,
            reason=Report.Reason.OTHER,
            details="Something else is wrong.",
        )

        assert report.status == Report.Status.OPEN

    def test_report_str(self) -> None:
        report = Report.objects.create(
            library=self.library,
            created_by=self.user,
            reason=Report.Reason.INAPPROPRIATE,
            details="Inappropriate content found.",
        )

        assert str(report) == "Report: Inappropriate - Test Library (Florence)"


class LibraryAdminTest(TestCase):
    """Tests for Library admin actions."""

    def setUp(self) -> None:
        self.admin_user = User.objects.create_superuser(
            username="admin",
            password="adminpass123",
        )
        self.client.force_login(self.admin_user)
        self.library = Library.objects.create(
            name="Pending Library",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=self.admin_user,
        )

    def test_approve_libraries_action(self) -> None:
        url = reverse("admin:libraries_library_changelist")
        response = self.client.post(url, {
            "action": "approve_libraries",
            "_selected_action": [self.library.pk],
        })

        assert response.status_code == 302
        self.library.refresh_from_db()
        assert self.library.status == Library.Status.APPROVED

    def test_reject_libraries_action(self) -> None:
        url = reverse("admin:libraries_library_changelist")
        response = self.client.post(url, {
            "action": "reject_libraries",
            "_selected_action": [self.library.pk],
        })

        assert response.status_code == 302
        self.library.refresh_from_db()
        assert self.library.status == Library.Status.REJECTED

    def test_bulk_approve_multiple_libraries(self) -> None:
        library_2 = Library.objects.create(
            photo="libraries/photos/2026/02/test2.jpg",
            location=Point(x=11.2600, y=43.7700, srid=4326),
            address="Via Roma 1",
            city="Florence",
            country="IT",
            created_by=self.admin_user,
        )

        url = reverse("admin:libraries_library_changelist")
        response = self.client.post(url, {
            "action": "approve_libraries",
            "_selected_action": [self.library.pk, library_2.pk],
        })

        assert response.status_code == 302
        self.library.refresh_from_db()
        library_2.refresh_from_db()
        assert self.library.status == Library.Status.APPROVED
        assert library_2.status == Library.Status.APPROVED


class ReportAdminTest(TestCase):
    """Tests for Report admin actions."""

    def setUp(self) -> None:
        self.admin_user = User.objects.create_superuser(
            username="admin",
            password="adminpass123",
        )
        self.client.force_login(self.admin_user)
        self.library = Library.objects.create(
            name="Test Library",
            photo="libraries/photos/2026/02/test.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            created_by=self.admin_user,
        )
        self.report = Report.objects.create(
            library=self.library,
            created_by=self.admin_user,
            reason=Report.Reason.DAMAGED,
            details="The library box is broken.",
        )

    def test_resolve_reports_action(self) -> None:
        url = reverse("admin:libraries_report_changelist")
        response = self.client.post(url, {
            "action": "resolve_reports",
            "_selected_action": [self.report.pk],
        })

        assert response.status_code == 302
        self.report.refresh_from_db()
        assert self.report.status == Report.Status.RESOLVED

    def test_dismiss_reports_action(self) -> None:
        url = reverse("admin:libraries_report_changelist")
        response = self.client.post(url, {
            "action": "dismiss_reports",
            "_selected_action": [self.report.pk],
        })

        assert response.status_code == 302
        self.report.refresh_from_db()
        assert self.report.status == Report.Status.DISMISSED
