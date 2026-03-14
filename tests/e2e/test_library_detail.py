import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_detail_page_shows_library_info(
    live_server, page, mock_external_apis, single_library
):
    """Verify the detail page renders library name, address, and city.
    Confirms all key fields from the library model are displayed."""
    page.goto(f"{live_server.url}/library/{single_library.slug}/")

    heading = page.locator("h1")
    assert "Corner Library Firenze" in heading.text_content()

    body = page.locator("body")
    body_text = body.text_content()
    assert "Via Rosina 15" in body_text
    assert "Florence" in body_text


def test_detail_map_loads(live_server, page, mock_external_apis, single_library):
    """Verify the detail page Leaflet map initializes with a marker.
    Confirms the map container gains the leaflet-container class."""
    page.goto(f"{live_server.url}/library/{single_library.slug}/")

    leaflet_map = page.locator("#library-detail-map.leaflet-container")
    leaflet_map.wait_for(state="attached", timeout=10000)
    assert leaflet_map.count() == 1


def test_report_toggle_shows_form(
    live_server, authenticated_page, single_library
):
    """Verify clicking the report button reveals the report form.
    Confirms aria-expanded changes and the form section becomes visible."""
    authenticated_page.goto(
        f"{live_server.url}/library/{single_library.slug}/"
    )

    toggle = authenticated_page.locator("#report-form-toggle")
    assert toggle.get_attribute("aria-expanded") == "false"

    toggle.click()

    form_section = authenticated_page.locator("#report-form")
    form_section.wait_for(state="visible")

    assert toggle.get_attribute("aria-expanded") == "true"
    assert not form_section.evaluate("el => el.classList.contains('hidden')")


def test_photo_toggle_shows_form(
    live_server, authenticated_page, single_library
):
    """Verify clicking the photo button reveals the photo upload form.
    Confirms aria-expanded changes and the form section becomes visible."""
    authenticated_page.goto(
        f"{live_server.url}/library/{single_library.slug}/"
    )

    toggle = authenticated_page.locator("#photo-form-toggle")
    if not toggle.is_visible():
        pytest.skip("Photo form toggle not visible (no photo_form context)")

    assert toggle.get_attribute("aria-expanded") == "false"

    toggle.click()

    form_section = authenticated_page.locator("#photo-form")
    form_section.wait_for(state="visible")

    assert toggle.get_attribute("aria-expanded") == "true"
    assert not form_section.evaluate("el => el.classList.contains('hidden')")


def test_report_form_htmx_submit(
    live_server, authenticated_page, single_library
):
    """Verify submitting a report via HTMX shows a success message.
    Confirms the full report flow works end-to-end in the browser."""
    authenticated_page.goto(
        f"{live_server.url}/library/{single_library.slug}/"
    )

    authenticated_page.click("#report-form-toggle")
    authenticated_page.locator("#report-form").wait_for(state="visible")

    authenticated_page.select_option("#report-form select", value="damaged")
    authenticated_page.fill(
        "#report-form textarea", "The book shelf is broken."
    )

    authenticated_page.click("#report-form button[type='submit']")

    success_alert = authenticated_page.locator("#report-form-panel .alert-success")
    success_alert.wait_for(state="visible", timeout=10000)

    assert "report was submitted" in success_alert.text_content().lower()
