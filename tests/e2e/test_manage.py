import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from playwright.sync_api import Page, expect

from libraries.models import Library, LibraryPhoto, Report
from tests.e2e.conftest import _make_test_image

User = get_user_model()

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def staff_user(db):
    """Create a staff user for manage interface tests."""
    return User.objects.create_user(
        username="staffuser",
        email="staff@example.com",
        password="StaffPass123!",
        is_staff=True,
    )


@pytest.fixture
def regular_user(db):
    """Create a non-staff user to test access control."""
    return User.objects.create_user(
        username="regularuser",
        email="regular@example.com",
        password="RegularPass123!",
    )


@pytest.fixture
def staff_page(page: Page, live_server, staff_user):
    """Provide a Playwright page logged in as a staff user."""
    page.goto(f"{live_server.url}/login/")
    page.fill("#id_username", "staffuser")
    page.fill("#id_password", "StaffPass123!")
    page.locator("form button[type='submit']").first.click()
    page.wait_for_url(f"{live_server.url}/")
    return page


@pytest.fixture
def sample_libraries(staff_user):
    """Create a mix of libraries with different statuses."""
    libs = []
    for i, status in enumerate([
        Library.Status.PENDING,
        Library.Status.APPROVED,
        Library.Status.REJECTED,
        Library.Status.PENDING,
    ]):
        lib = Library.objects.create(
            name=f"Test Library {i}",
            photo=_make_test_image(name=f"manage_lib_{i}.jpg"),
            location=Point(x=11.0 + i * 0.1, y=43.0, srid=4326),
            address=f"Via Manage {i}",
            city="Florence",
            country="IT",
            status=status,
            created_by=staff_user,
        )
        libs.append(lib)
    return libs


@pytest.fixture
def sample_report(staff_user, sample_libraries):
    """Create an open report on the first library."""
    return Report.objects.create(
        library=sample_libraries[1],
        created_by=staff_user,
        reason=Report.Reason.DAMAGED,
        details="The shelf is broken.",
    )


@pytest.fixture
def sample_photo(staff_user, sample_libraries):
    """Create a pending community photo."""
    return LibraryPhoto.objects.create(
        library=sample_libraries[1],
        photo=_make_test_image(name="community_photo.jpg"),
        caption="Nice spot",
        created_by=staff_user,
    )


def test_manage_redirects_non_staff(live_server, page, regular_user):
    """Verify non-staff users are redirected away from /manage/."""
    page.goto(f"{live_server.url}/login/")
    page.fill("#id_username", "regularuser")
    page.fill("#id_password", "RegularPass123!")
    page.locator("form button[type='submit']").first.click()
    page.wait_for_url(f"{live_server.url}/")

    page.goto(f"{live_server.url}/manage/")
    expect(page).not_to_have_url(f"{live_server.url}/manage/")


def test_manage_redirects_anonymous(live_server, page):
    """Verify anonymous users are redirected away from /manage/."""
    page.goto(f"{live_server.url}/manage/")
    expect(page).not_to_have_url(f"{live_server.url}/manage/")


def test_dashboard_loads_with_stat_cards(live_server, staff_page, sample_libraries):
    """Verify the dashboard renders stat cards and moderation queues."""
    staff_page.goto(f"{live_server.url}/manage/")

    expect(staff_page.locator("text=Total Approved")).to_be_visible()
    expect(staff_page.locator("text=Pending Libraries")).to_be_visible()


def test_dashboard_pending_libraries_are_clickable(
    live_server, staff_page, sample_libraries
):
    """Verify pending library items on dashboard link to detail pages."""
    staff_page.goto(f"{live_server.url}/manage/")

    pending_link = staff_page.locator("text=Test Library 0").first
    expect(pending_link).to_be_visible()
    pending_link.click()

    expect(staff_page).to_have_url(
        f"{live_server.url}/manage/libraries/{sample_libraries[0].pk}/"
    )


def test_library_list_loads_with_filters(live_server, staff_page, sample_libraries):
    """Verify the library list renders with filter controls and table rows."""
    staff_page.goto(f"{live_server.url}/manage/libraries/")

    expect(staff_page.locator("h1:text('Libraries')")).to_be_visible()
    expect(staff_page.locator("table tbody tr").first).to_be_visible()
    expect(staff_page.locator("select[name='status']")).to_be_visible()


def test_library_list_filter_by_status(live_server, staff_page, sample_libraries):
    """Verify filtering by status shows correct results."""
    staff_page.goto(f"{live_server.url}/manage/libraries/?status=pending")

    rows = staff_page.locator("table tbody tr")
    expect(rows.first).to_be_visible()

    for row in rows.all():
        expect(row.locator(".badge")).to_contain_text("Pending")


def test_library_detail_shows_info(live_server, staff_page, sample_libraries):
    """Verify the library detail page renders core fields."""
    lib = sample_libraries[0]
    staff_page.goto(f"{live_server.url}/manage/libraries/{lib.pk}/")

    expect(staff_page.locator(f"h1:text('{lib.name}')")).to_be_visible()
    expect(staff_page.locator(".drawer-content >> text=Florence").first).to_be_visible()
    expect(staff_page.locator("h2:text('Details')")).to_be_visible()


def test_library_detail_has_breadcrumbs(live_server, staff_page, sample_libraries):
    """Verify breadcrumbs appear on the library detail page."""
    lib = sample_libraries[0]
    staff_page.goto(f"{live_server.url}/manage/libraries/{lib.pk}/")

    breadcrumbs = staff_page.locator(".breadcrumbs")
    expect(breadcrumbs).to_be_visible()
    expect(breadcrumbs.locator("text=Libraries")).to_be_visible()


def test_report_list_loads(live_server, staff_page, sample_report):
    """Verify the report list renders with the sample report."""
    staff_page.goto(f"{live_server.url}/manage/reports/")

    expect(staff_page.locator("h1:text('Reports')")).to_be_visible()
    expect(staff_page.locator("table tbody tr").first).to_be_visible()


def test_photo_grid_loads(live_server, staff_page, sample_libraries):
    """Verify the photo grid renders with library photos."""
    staff_page.goto(f"{live_server.url}/manage/photos/")

    expect(staff_page.locator("h1:text('Photos')")).to_be_visible()
    expect(staff_page.locator("select[name='type']")).to_be_visible()


def test_user_list_loads(live_server, staff_page):
    """Verify the user list renders with at least the staff user."""
    staff_page.goto(f"{live_server.url}/manage/users/")

    expect(staff_page.locator("h1:text('Users')")).to_be_visible()
    expect(staff_page.locator("table >> text=staffuser")).to_be_visible()


def test_user_detail_shows_info(live_server, staff_page, staff_user):
    """Verify the user detail page renders account info."""
    staff_page.goto(f"{live_server.url}/manage/users/{staff_user.pk}/")

    expect(staff_page.locator("h1:text('staffuser')")).to_be_visible()
    expect(staff_page.locator("h2:text('Account')")).to_be_visible()


def test_manage_link_visible_for_staff(live_server, staff_page):
    """Verify the Manage navbar link appears for staff users."""
    staff_page.goto(f"{live_server.url}/")

    manage_link = staff_page.locator(".navbar-end a[href='/manage/']")
    expect(manage_link).to_be_visible()


def test_manage_link_hidden_for_regular_user(live_server, page, regular_user):
    """Verify the Manage navbar link is hidden for non-staff users."""
    page.goto(f"{live_server.url}/login/")
    page.fill("#id_username", "regularuser")
    page.fill("#id_password", "RegularPass123!")
    page.locator("form button[type='submit']").first.click()
    page.wait_for_url(f"{live_server.url}/")

    manage_link = page.locator(".navbar-end a[href='/manage/']")
    expect(manage_link).not_to_be_visible()
