import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.test import Client
from django.utils import timezone
from playwright.sync_api import Page, expect

from libraries.models import (
    Library,
    OpenStreetMapContribution,
    OpenStreetMapContributionEvent,
)

User = get_user_model()

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def _force_login_browser(page: Page, live_server, user) -> None:
    """Log a superuser into Django admin through a test session cookie.
    Avoids coupling the workflow test to the login form."""
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


@pytest.fixture
def osm_admin_user(db):
    """Create a superuser for the Django admin OSM workflow.
    Provides all required model view and action permissions."""
    return User.objects.create_superuser(
        username="osmadmin",
        password="OsmAdminPass123!",
    )


@pytest.fixture
def osm_admin_page(
    page: Page,
    live_server,
    osm_admin_user,
    mock_external_apis,
) -> Page:
    """Provide a browser page authenticated in Django admin.
    Keeps external map resources mocked by the shared E2E fixture."""
    _force_login_browser(page, live_server, osm_admin_user)
    return page


@pytest.fixture
def unresolved_osm_library(osm_admin_user) -> Library:
    """Create an opted-in library with one blocking OSM candidate.
    Supplies the complete state required by the resolution page."""
    library = Library.objects.create(
        name="Browser OSM Candidate",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Roma 5",
        city="Florence",
        country="IT",
        status=Library.Status.APPROVED,
        created_by=osm_admin_user,
        submission_origin=Library.SubmissionOrigin.USER,
        osm_submission_allowed=True,
        osm_submission_allowed_at=timezone.now(),
    )
    OpenStreetMapContribution.objects.create(
        library=library,
        status=OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE,
        checked_at=timezone.now(),
        duplicate_candidates=[{
            "element_type": "node",
            "element_id": 1003,
            "distance_meters": 9.0,
            "tags": {"amenity": "public_bookcase"},
            "signals": ["nearby_public_bookcase"],
        }],
    )
    return library


def test_admin_resolves_osm_candidate_as_a_different_feature(
    live_server,
    osm_admin_page: Page,
    unresolved_osm_library: Library,
) -> None:
    """Verify the rendered admin resolution workflow persists its decision.
    Confirms OSM IDs stay unlocalized and a fresh check becomes mandatory."""
    change_url = (
        f"{live_server.url}/admin/libraries/library/"
        f"{unresolved_osm_library.pk}/change/"
    )
    osm_admin_page.goto(change_url)

    resolution_link = osm_admin_page.get_by_role(
        "link",
        name="Resolve OSM duplicate",
    )
    expect(resolution_link).to_be_visible()
    resolution_link.click()

    expect(osm_admin_page.get_by_text("node/1003")).to_be_visible()
    candidate = osm_admin_page.locator("input[name='candidate']")
    expect(candidate).to_have_value("node:1003")
    candidate.check()
    osm_admin_page.locator(
        "input[name='resolution'][value='different_feature']"
    ).check()
    osm_admin_page.locator("textarea[name='reason']").fill(
        "A wall separates two independently stocked bookcases."
    )
    osm_admin_page.get_by_role("button", name="Record resolution").click()

    expect(osm_admin_page).to_have_url(change_url)
    expect(
        osm_admin_page.get_by_text(
            "Run a fresh duplicate check before continuing.",
            exact=False,
        )
    ).to_be_visible()
    contribution = unresolved_osm_library.osm_contribution
    contribution.refresh_from_db()
    assert contribution.status == OpenStreetMapContribution.Status.UNCHECKED
    event = contribution.events.get()
    assert (
        event.event_type
        == OpenStreetMapContributionEvent.EventType.DUPLICATE_RESOLUTION
    )
