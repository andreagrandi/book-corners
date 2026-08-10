from threading import Event
from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache

from libraries import geolocation


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

    tile = leaflet_map.locator(".leaflet-tile").first
    tile.wait_for(state="attached", timeout=10000)
    assert tile.get_attribute("src").startswith("https://tile.openstreetmap.org/")
    assert tile.get_attribute("referrerpolicy") == "strict-origin-when-cross-origin"


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


def test_proximity_filter_deduplicates_concurrent_map_and_list_geocoding(
    live_server,
    page,
    mock_external_apis,
    single_library,
) -> None:
    """Verify browser proximity filtering shares one server geocoding operation.
    Exercises concurrent map/list HTTP requests through the live Django server."""
    cache.clear()
    follower_waiting = Event()
    real_wait_for_result = geolocation._wait_for_forward_geocode_result

    def delayed_geocode(*args: object, **kwargs: object) -> Mock:
        """Hold the elected geocoder until the concurrent request is waiting.
        Makes browser-triggered server request overlap deterministic."""
        assert follower_waiting.wait(timeout=5)
        return Mock(latitude=43.7696, longitude=11.2558)

    def observed_wait_for_result(
        *, cache_key: str, timeout_seconds: int
    ) -> tuple[float, float] | None:
        """Record when the concurrent request joins the in-flight lookup.
        Delegates result polling to the production cache-backed helper."""
        follower_waiting.set()
        return real_wait_for_result(
            cache_key=cache_key,
            timeout_seconds=timeout_seconds,
        )

    with (
        patch("libraries.geolocation.Nominatim") as mocked_nominatim,
        patch(
            "libraries.geolocation._wait_for_forward_geocode_result",
            side_effect=observed_wait_for_result,
        ),
    ):
        mocked_nominatim.return_value.geocode.side_effect = delayed_geocode
        page.goto(f"{live_server.url}/map/")
        page.locator("#id_near").fill("Florence")

        with page.expect_response(
            lambda response: (
                "/map/libraries.geojson?" in response.url
                and "near=Florence" in response.url
            ),
            timeout=15000,
        ) as map_response_info:
            with page.expect_response(
                lambda response: (
                    "/map/libraries/list/?" in response.url
                    and "near=Florence" in response.url
                ),
                timeout=15000,
            ) as list_response_info:
                page.get_by_role("button", name="Apply filters").click()

        map_response = map_response_info.value
        list_response = list_response_info.value
        map_payload = map_response.json()
        list_html = list_response.text()

        assert map_response.status == 200
        assert list_response.status == 200
        assert map_payload["meta"]["location_resolution_failed"] is False
        assert map_payload["meta"]["center"] == {
            "lat": 43.7696,
            "lng": 11.2558,
        }
        assert single_library.name in list_html
        assert "Could not resolve" not in list_html
        mocked_nominatim.assert_called_once()
        mocked_nominatim.return_value.geocode.assert_called_once()

    page.get_by_text(single_library.name).wait_for(state="visible", timeout=10000)
