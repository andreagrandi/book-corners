import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_submit_requires_login(live_server, page, mock_external_apis):
    """Verify the submit page redirects anonymous users to login.
    Confirms the login-required decorator is enforced."""
    page.goto(f"{live_server.url}/submit/")

    page.wait_for_url(f"**/login/**")
    assert "/login/" in page.url


def test_submit_page_renders_form_and_map(
    live_server, authenticated_page
):
    """Verify the submit page shows the form and interactive map.
    Confirms both the form fields and Leaflet map initialize."""
    authenticated_page.goto(f"{live_server.url}/submit/")

    assert authenticated_page.locator("#id_photo").is_visible()
    assert authenticated_page.locator("#id_address").is_visible()
    assert authenticated_page.locator("#id_city").is_visible()
    assert authenticated_page.locator("#id_country").is_visible()

    leaflet_map = authenticated_page.locator(
        "#submit-library-map.leaflet-container"
    )
    leaflet_map.wait_for(state="attached", timeout=10000)
    assert leaflet_map.count() == 1


def test_submit_autocomplete_shows_suggestions(
    live_server, authenticated_page
):
    """Verify typing in the address field shows autocomplete suggestions.
    Confirms Photon API mock returns results rendered as a dropdown."""
    authenticated_page.goto(f"{live_server.url}/submit/")

    authenticated_page.locator(
        "#submit-library-map.leaflet-container"
    ).wait_for(state="attached", timeout=10000)

    authenticated_page.fill("#id_address", "Via Rosina")

    suggestions = authenticated_page.locator("#address-suggestions")
    suggestions.wait_for(state="visible", timeout=5000)

    items = suggestions.locator("li")
    assert items.count() > 0


def test_submit_form_happy_path(live_server, authenticated_page, tmp_path):
    """Verify a complete library submission succeeds end-to-end.
    Fills all fields, sets coordinates via geocode, and submits."""
    authenticated_page.goto(f"{live_server.url}/submit/")

    authenticated_page.locator(
        "#submit-library-map.leaflet-container"
    ).wait_for(state="attached", timeout=10000)

    image_path = tmp_path / "test_photo.jpg"
    _create_minimal_jpeg(image_path)
    authenticated_page.set_input_files("#id_photo", str(image_path))

    authenticated_page.fill("#id_name", "Test Submission Library")
    authenticated_page.fill("#id_city", "Firenze")
    authenticated_page.fill("#id_address", "Via Rosina 15")
    authenticated_page.fill("#id_postal_code", "50123")

    authenticated_page.evaluate("""() => {
        const lat = document.getElementById('id_latitude');
        const lng = document.getElementById('id_longitude');
        if (lat) lat.value = '43.7696';
        if (lng) lng.value = '11.2558';
    }""")

    country_select = authenticated_page.locator("#id_country")
    if country_select.evaluate("el => el.tomselect !== undefined"):
        authenticated_page.evaluate("""() => {
            const el = document.getElementById('id_country');
            if (el && el.tomselect) {
                el.tomselect.setValue('IT', true);
            }
        }""")
    else:
        authenticated_page.select_option("#id_country", value="IT")

    authenticated_page.locator(
        ".card-body button[type='submit']"
    ).click()

    authenticated_page.wait_for_url("**/submit/confirmation/**", timeout=15000)
    assert "/submit/confirmation/" in authenticated_page.url


def _create_minimal_jpeg(path):
    """Create a minimal valid JPEG file for photo upload testing.
    Produces a tiny 1x1 JPEG that passes image validation."""
    from PIL import Image

    img = Image.new("RGB", (100, 100), color=(128, 128, 128))
    img.save(str(path), "JPEG")
