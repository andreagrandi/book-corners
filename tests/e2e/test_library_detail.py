from pathlib import Path

import pytest
from PIL import Image
from pillow_heif import from_pillow

from libraries.image_processing import LIBRARY_PHOTO_TARGET_BYTES
from libraries.models import Library, Report


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


def test_rejected_library_report_with_photo_submits_on_mobile(
    live_server,
    authenticated_page,
    single_library,
    tmp_path,
):
    """Verify mobile owners can attach a photo when reporting a rejected library.
    Covers multipart HTMX submission for the rejection appeal workflow."""
    single_library.status = Library.Status.REJECTED
    single_library.rejection_reason = "Please provide a clearer photo."
    single_library.save(update_fields=["rejection_reason", "status"])
    image_path = tmp_path / "appeal-photo.heic"
    _create_heic_upload(image_path, target_size_bytes=6 * 1024 * 1024)

    authenticated_page.set_viewport_size({"width": 390, "height": 844})
    authenticated_page.goto(
        f"{live_server.url}/library/{single_library.slug}/"
    )
    authenticated_page.click("#report-form-toggle")
    authenticated_page.locator("#report-form").wait_for(state="visible")
    assert authenticated_page.get_by_text(
        "Maximum file size: 10 MB.",
        exact=False,
    ).is_visible()
    authenticated_page.select_option("#report-form select", value="other")
    authenticated_page.fill(
        "#report-form textarea",
        "This clearer photo shows the existing library.",
    )
    authenticated_page.set_input_files(
        "#report-form input[type='file']",
        str(image_path),
    )

    with authenticated_page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and f"/library/{single_library.slug}/report/" in response.url
        )
    ) as response_info:
        authenticated_page.click("#report-form button[type='submit']")

    assert response_info.value.status == 200
    success_alert = authenticated_page.locator(
        "#report-form-panel .alert-success"
    )
    success_alert.wait_for(state="visible", timeout=10000)
    report = Report.objects.get(library=single_library)
    assert report.photo.name.endswith(".jpg")
    assert report.photo.size <= LIBRARY_PHOTO_TARGET_BYTES


def test_report_form_shows_oversized_photo_error_on_mobile(
    live_server,
    authenticated_page,
    single_library,
    tmp_path,
):
    """Verify oversized mobile attachments render the server validation error.
    Preserves the 422 response without making the submit button look inert."""
    oversized_path = tmp_path / "oversized-photo.jpg"
    _create_oversized_jpeg(oversized_path)

    authenticated_page.set_viewport_size({"width": 390, "height": 844})
    authenticated_page.goto(
        f"{live_server.url}/library/{single_library.slug}/"
    )
    authenticated_page.click("#report-form-toggle")
    authenticated_page.locator("#report-form").wait_for(state="visible")
    authenticated_page.select_option("#report-form select", value="other")
    authenticated_page.fill(
        "#report-form textarea",
        "This oversized attachment should produce a visible validation error.",
    )
    authenticated_page.set_input_files(
        "#report-form input[type='file']",
        str(oversized_path),
    )

    with authenticated_page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and f"/library/{single_library.slug}/report/" in response.url
        )
    ) as response_info:
        authenticated_page.click("#report-form button[type='submit']")

    assert response_info.value.status == 422
    error_alert = authenticated_page.locator("#report-form-panel .alert-error")
    error_alert.wait_for(state="visible", timeout=10000)
    assert "highlighted fields" in error_alert.text_content().lower()
    assert authenticated_page.get_by_text(
        "Photo must be 10MB or smaller.",
        exact=False,
    ).is_visible()


def _create_heic_upload(path: Path, *, target_size_bytes: int) -> None:
    """Create a valid HEIC at the requested upload size.
    Reproduces phone photos that exceeded the previous 5 MB limit."""
    image = Image.new("RGB", (100, 100), color=(128, 128, 128))
    from_pillow(image).save(path, quality=90)
    current_payload = path.read_bytes()
    path.write_bytes(
        current_payload + (b"\0" * (target_size_bytes - len(current_payload)))
    )


def _create_oversized_jpeg(path: Path) -> None:
    """Create a valid JPEG just above the report upload limit.
    Reproduces the mobile photo size failure without a large binary fixture."""
    Image.new("RGB", (100, 100), color=(128, 128, 128)).save(path, "JPEG")
    target_size = (10 * 1024 * 1024) + 1
    current_payload = path.read_bytes()
    path.write_bytes(current_payload + (b"\0" * (target_size - len(current_payload))))
