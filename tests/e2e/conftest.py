import io

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from PIL import Image
from playwright.sync_api import Page, Route

from libraries.models import Library

User = get_user_model()

TRANSPARENT_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

PHOTON_MOCK_RESPONSE = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [11.2558, 43.7696]},
            "properties": {
                "name": "Via Rosina",
                "street": "Via Rosina",
                "housenumber": "15",
                "city": "Firenze",
                "postcode": "50123",
                "country": "Italy",
                "countrycode": "IT",
            },
        }
    ],
}

NOMINATIM_MOCK_RESPONSE = [
    {
        "lat": "43.7696",
        "lon": "11.2558",
        "display_name": "Via Rosina 15, 50123 Firenze, Italy",
    }
]


def _make_test_image(name="test.jpg"):
    """Create a minimal JPEG image as a Django ContentFile.
    Used to satisfy photo-required querysets in views."""
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color=(128, 128, 128)).save(buf, "JPEG")
    buf.seek(0)
    return ContentFile(buf.read(), name=name)


@pytest.fixture(scope="session")
def browser_context_args():
    """Set a fixed viewport size for consistent rendering across runs."""
    return {"viewport": {"width": 1280, "height": 720}}


@pytest.fixture
def e2e_user(db):
    """Create a test user for E2E browser tests.
    Provides a user that can log in through the real login form."""
    return User.objects.create_user(
        username="e2euser",
        email="e2e@example.com",
        password="E2eTestPass123!",
    )


@pytest.fixture
def approved_libraries(e2e_user):
    """Create a set of approved libraries for browse and map tests.
    Provides enough data for pagination and map marker display."""
    libraries = []
    for i in range(12):
        lib = Library.objects.create(
            name=f"Test Library {i}",
            photo=_make_test_image(name=f"test_lib_{i}.jpg"),
            location=Point(x=11.0 + i * 0.1, y=43.0 + i * 0.05, srid=4326),
            address=f"Via Test {i}",
            city="Florence",
            country="IT",
            status=Library.Status.APPROVED,
            created_by=e2e_user,
        )
        libraries.append(lib)
    return libraries


@pytest.fixture
def single_library(e2e_user):
    """Create a single approved library for detail page tests.
    Provides a library with all fields populated for full rendering."""
    return Library.objects.create(
        name="Corner Library Firenze",
        description="A cozy book exchange near the Duomo.",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city="Florence",
        country="IT",
        postal_code="50123",
        status=Library.Status.APPROVED,
        created_by=e2e_user,
    )


@pytest.fixture
def mock_external_apis(page: Page):
    """Intercept external API calls to keep tests deterministic.
    Mocks Photon, Nominatim, and OSM tile requests at the browser level."""

    def handle_tile_route(route: Route):
        """Respond with a transparent 1x1 PNG for map tiles."""
        route.fulfill(
            status=200,
            content_type="image/png",
            body=TRANSPARENT_PNG_1X1,
        )

    def handle_photon_route(route: Route):
        """Respond with canned Photon address suggestions."""
        route.fulfill(
            status=200,
            content_type="application/json",
            json=PHOTON_MOCK_RESPONSE,
        )

    def handle_nominatim_route(route: Route):
        """Respond with canned Nominatim geocoding results."""
        route.fulfill(
            status=200,
            content_type="application/json",
            json=NOMINATIM_MOCK_RESPONSE,
        )

    page.route("**/*.tile.openstreetmap.org/**", handle_tile_route)
    page.route("**/photon.komoot.io/api/**", handle_photon_route)
    page.route("**/nominatim.openstreetmap.org/**", handle_nominatim_route)


@pytest.fixture
def authenticated_page(page: Page, live_server, e2e_user, mock_external_apis):
    """Provide a Playwright page already logged in as e2e_user.
    Handles the login flow so individual tests start authenticated."""
    page.goto(f"{live_server.url}/login/")
    page.fill("#id_username", "e2euser")
    page.fill("#id_password", "E2eTestPass123!")
    page.locator("form button[type='submit']").first.click()
    page.wait_for_url(f"{live_server.url}/")
    return page
