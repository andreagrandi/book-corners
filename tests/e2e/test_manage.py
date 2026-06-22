import re

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.test import Client
from playwright.sync_api import Page, Response, expect

from libraries.models import Library, LibraryPhoto, Report
from tests.e2e.conftest import _make_test_image

User = get_user_model()


def _force_login_browser(page: Page, live_server, user):
    """Log a user in by injecting a session cookie from Django's test client.
    Avoids the login form so rate limiting is never triggered."""
    client = Client()
    client.force_login(user)
    session_cookie = client.cookies["sessionid"]
    page.goto(f"{live_server.url}/")
    page.context.add_cookies([{
        "name": "sessionid",
        "value": session_cookie.value,
        "domain": "localhost",
        "path": "/",
    }])


def _wait_for_htmx(page: Page) -> None:
    """Wait until HTMX is available on the page.
    Keeps browser tests synchronized before triggering HTMX actions."""
    page.wait_for_function("() => window.htmx !== undefined")


def _is_htmx_response(
    response: Response,
    *,
    url_prefix: str = "",
    url_suffix: str = "",
) -> bool:
    """Return whether a response came from an HTMX request.
    Allows tests to distinguish swaps from full-page navigation."""
    if response.request.headers.get("hx-request") != "true":
        return False
    if url_prefix and not response.url.startswith(url_prefix):
        return False
    if url_suffix and not response.url.endswith(url_suffix):
        return False
    return True


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
def staff_page(page: Page, live_server, staff_user, mock_external_apis):
    """Provide a Playwright page logged in as a staff user."""
    _force_login_browser(page, live_server, staff_user)
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
    _force_login_browser(page, live_server, regular_user)
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


def test_library_filters_submit_via_htmx(
    live_server,
    staff_page,
    sample_libraries,
) -> None:
    """Verify library filters update the table through HTMX.
    Confirms status filters swap only the table container."""
    staff_page.goto(f"{live_server.url}/manage/libraries/")
    _wait_for_htmx(staff_page)

    filter_form = staff_page.locator("form[hx-target='#library-table-container']")
    filter_form.locator("select[name='status']").select_option(
        Library.Status.APPROVED
    )

    with staff_page.expect_response(
        lambda response: _is_htmx_response(
            response,
            url_prefix=f"{live_server.url}/manage/libraries/?",
        )
    ):
        filter_form.locator("button[type='submit']").click()

    table = staff_page.locator("#library-table-container")
    expect(table.locator(f"#library-row-{sample_libraries[1].pk}")).to_be_visible()
    expect(table.locator(f"#library-row-{sample_libraries[0].pk}")).not_to_be_visible()
    expect(staff_page).to_have_url(
        re.compile(r".*/manage/libraries/\?.*status=approved")
    )


def test_library_approve_updates_status_with_htmx(
    live_server,
    staff_page,
    sample_libraries,
) -> None:
    """Verify the library approve button uses an HTMX row swap.
    Confirms the approved moderation state is persisted."""
    library_to_approve = sample_libraries[0]
    staff_page.goto(f"{live_server.url}/manage/libraries/")
    _wait_for_htmx(staff_page)

    approve_row = staff_page.locator(f"#library-row-{library_to_approve.pk}")
    approve_url = f"/manage/libraries/{library_to_approve.pk}/approve/"
    with staff_page.expect_response(
        lambda response: _is_htmx_response(
            response,
            url_suffix=approve_url,
        )
    ):
        approve_row.locator("button[title='Approve']").click()

    expect(approve_row.locator(".badge")).to_contain_text("Approved")
    library_to_approve.refresh_from_db()
    assert library_to_approve.status == Library.Status.APPROVED


def test_library_reject_updates_status_with_htmx(
    live_server,
    staff_page,
    sample_libraries,
) -> None:
    """Verify the library reject button uses an HTMX row swap.
    Confirms the rejected moderation state is persisted."""
    library_to_reject = sample_libraries[3]
    staff_page.goto(f"{live_server.url}/manage/libraries/")
    _wait_for_htmx(staff_page)

    reject_row = staff_page.locator(f"#library-row-{library_to_reject.pk}")
    reject_url = f"/manage/libraries/{library_to_reject.pk}/reject/"
    with staff_page.expect_response(
        lambda response: _is_htmx_response(
            response,
            url_suffix=reject_url,
        )
    ):
        reject_row.locator("button[title='Reject']").click()

    expect(reject_row.locator(".badge")).to_contain_text("Rejected")
    library_to_reject.refresh_from_db()
    assert library_to_reject.status == Library.Status.REJECTED


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


def test_library_edit_form_has_map_picker(live_server, staff_page, sample_libraries):
    """Verify the manage library edit form renders its map picker.
    Confirms clicking the map updates latitude and longitude fields."""
    lib = sample_libraries[0]
    staff_page.goto(f"{live_server.url}/manage/libraries/{lib.pk}/edit/")

    expect(staff_page.locator("h1:text('Edit library')")).to_be_visible()
    expect(staff_page.locator("text=Edit in Django Admin")).to_be_visible()
    map_element = staff_page.locator("#manage-library-map")
    expect(map_element).to_be_visible()
    staff_page.wait_for_function(
        "() => document.querySelector('#manage-library-map .leaflet-marker-icon') !== null"
    )

    initial_latitude = staff_page.locator("#id_latitude").input_value()
    initial_longitude = staff_page.locator("#id_longitude").input_value()
    map_element.click(position={"x": 80, "y": 80})

    staff_page.wait_for_function(
        """([initialLatitude, initialLongitude]) => {
          const latitude = document.getElementById("id_latitude").value;
          const longitude = document.getElementById("id_longitude").value;
          return latitude !== initialLatitude && longitude !== initialLongitude;
        }""",
        arg=[initial_latitude, initial_longitude],
    )


def test_report_list_loads(live_server, staff_page, sample_report):
    """Verify the report list renders with the sample report."""
    staff_page.goto(f"{live_server.url}/manage/reports/")

    expect(staff_page.locator("h1:text('Reports')")).to_be_visible()
    expect(staff_page.locator("table tbody tr").first).to_be_visible()


def test_report_resolve_updates_status_with_htmx(
    live_server,
    staff_page,
    sample_report,
) -> None:
    """Verify the report resolve button uses an HTMX row swap.
    Confirms the resolved moderation state is persisted."""
    staff_page.goto(f"{live_server.url}/manage/reports/")
    _wait_for_htmx(staff_page)

    resolve_row = staff_page.locator(f"#report-row-{sample_report.pk}")
    resolve_url = f"/manage/reports/{sample_report.pk}/resolve/"
    with staff_page.expect_response(
        lambda response: _is_htmx_response(
            response,
            url_suffix=resolve_url,
        )
    ):
        resolve_row.locator("button[title='Resolve']").click()

    expect(resolve_row.locator(".badge")).to_contain_text("Resolved")
    sample_report.refresh_from_db()
    assert sample_report.status == Report.Status.RESOLVED


def test_report_dismiss_updates_status_with_htmx(
    live_server,
    staff_page,
    sample_report,
) -> None:
    """Verify the report dismiss button uses an HTMX row swap.
    Confirms the dismissed moderation state is persisted."""
    staff_page.goto(f"{live_server.url}/manage/reports/")
    _wait_for_htmx(staff_page)

    dismiss_row = staff_page.locator(f"#report-row-{sample_report.pk}")
    dismiss_url = f"/manage/reports/{sample_report.pk}/dismiss/"
    with staff_page.expect_response(
        lambda response: _is_htmx_response(
            response,
            url_suffix=dismiss_url,
        )
    ):
        dismiss_row.locator("button[title='Dismiss']").click()

    expect(dismiss_row.locator(".badge")).to_contain_text("Dismissed")
    sample_report.refresh_from_db()
    assert sample_report.status == Report.Status.DISMISSED


def test_photo_grid_loads(live_server, staff_page, sample_libraries):
    """Verify the photo grid renders with library photos."""
    staff_page.goto(f"{live_server.url}/manage/photos/")

    expect(staff_page.locator("h1:text('Photos')")).to_be_visible()
    expect(staff_page.locator("select[name='type']")).to_be_visible()


def test_photo_filters_submit_via_htmx(
    live_server,
    staff_page,
    sample_photo,
) -> None:
    """Verify community photo filters update the grid through HTMX.
    Confirms filtering can hide primary library photos."""
    staff_page.goto(f"{live_server.url}/manage/photos/")
    _wait_for_htmx(staff_page)

    filter_form = staff_page.locator("form[hx-target='#photo-grid-container']")
    filter_form.locator("select[name='status']").select_option(
        LibraryPhoto.Status.PENDING
    )
    filter_form.locator("select[name='type']").select_option("community")

    with staff_page.expect_response(
        lambda response: _is_htmx_response(
            response,
            url_prefix=f"{live_server.url}/manage/photos/?",
        )
    ):
        filter_form.locator("button[type='submit']").click()

    grid = staff_page.locator("#photo-grid-container")
    expect(grid.locator(f"#photo-card-{sample_photo.pk}-community")).to_be_visible()
    expect(grid.get_by_text("Primary")).not_to_be_visible()


def test_photo_approve_updates_status_with_htmx(
    live_server,
    staff_page,
    sample_photo,
) -> None:
    """Verify the community photo approve button uses an HTMX card swap.
    Confirms the approved moderation state is persisted."""
    staff_page.goto(f"{live_server.url}/manage/photos/?status=pending&type=community")
    _wait_for_htmx(staff_page)

    approve_card = staff_page.locator(f"#photo-card-{sample_photo.pk}-community")
    expect(approve_card).to_be_visible()
    approve_url = f"/manage/photos/{sample_photo.pk}/approve/"
    with staff_page.expect_response(
        lambda response: _is_htmx_response(
            response,
            url_suffix=approve_url,
        )
    ):
        approve_card.get_by_role("button", name="Approve").click()

    expect(
        approve_card.locator(".badge").filter(has_text="Approved")
    ).to_be_visible()
    sample_photo.refresh_from_db()
    assert sample_photo.status == LibraryPhoto.Status.APPROVED


def test_photo_reject_updates_status_with_htmx(
    live_server,
    staff_page,
    sample_photo,
) -> None:
    """Verify the community photo reject button uses an HTMX card swap.
    Confirms the rejected moderation state is persisted."""
    staff_page.goto(f"{live_server.url}/manage/photos/?status=pending&type=community")
    _wait_for_htmx(staff_page)

    reject_card = staff_page.locator(f"#photo-card-{sample_photo.pk}-community")
    expect(reject_card).to_be_visible()
    reject_url = f"/manage/photos/{sample_photo.pk}/reject/"
    with staff_page.expect_response(
        lambda response: _is_htmx_response(
            response,
            url_suffix=reject_url,
        )
    ):
        reject_card.get_by_role("button", name="Reject").click()

    expect(
        reject_card.locator(".badge").filter(has_text="Rejected")
    ).to_be_visible()
    sample_photo.refresh_from_db()
    assert sample_photo.status == LibraryPhoto.Status.REJECTED


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
    _force_login_browser(page, live_server, regular_user)
    page.goto(f"{live_server.url}/")

    manage_link = page.locator(".navbar-end a[href='/manage/']")
    expect(manage_link).not_to_be_visible()
