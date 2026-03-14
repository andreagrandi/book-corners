import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_map_page_loads_with_leaflet(
    live_server, page, mock_external_apis, approved_libraries
):
    """Verify the map page renders and Leaflet initializes.
    Confirms the map container gains the leaflet-container class."""
    page.goto(f"{live_server.url}/map/")

    map_panel = page.locator("#map-results-panel")
    map_panel.wait_for(state="visible")

    leaflet_map = page.locator("#libraries-map.leaflet-container")
    leaflet_map.wait_for(state="attached", timeout=10000)
    assert leaflet_map.count() == 1


def test_map_view_mode_switching(
    live_server, page, mock_external_apis, approved_libraries
):
    """Verify clicking view mode buttons toggles between map, list, and split.
    Confirms panel visibility changes when switching views."""
    page.goto(f"{live_server.url}/map/")

    page.locator("#libraries-map.leaflet-container").wait_for(
        state="attached", timeout=10000
    )

    page.click("[data-view-mode='list']")
    page.wait_for_timeout(500)

    list_panel = page.locator("#list-results-panel")
    assert list_panel.is_visible()

    page.click("[data-view-mode='map']")
    page.wait_for_timeout(500)

    map_panel = page.locator("#map-results-panel")
    assert map_panel.is_visible()


def test_map_geojson_loads_on_render(
    live_server, page, mock_external_apis, approved_libraries
):
    """Verify the map fetches GeoJSON data on initial page load.
    Confirms a network request to the GeoJSON endpoint completes."""
    with page.expect_response(
        lambda response: "libraries.geojson" in response.url, timeout=15000
    ) as response_info:
        page.goto(f"{live_server.url}/map/")

    response = response_info.value
    assert response.status == 200


def test_map_list_view_shows_libraries(
    live_server, page, mock_external_apis, approved_libraries
):
    """Verify switching to list view displays library items.
    Confirms the list results container populates with content."""
    page.goto(f"{live_server.url}/map/")

    page.locator("#libraries-map.leaflet-container").wait_for(
        state="attached", timeout=10000
    )

    page.click("[data-view-mode='list']")

    list_container = page.locator("#map-list-results")
    list_container.wait_for(state="visible", timeout=10000)

    page.wait_for_timeout(2000)

    assert list_container.inner_html().strip() != ""
