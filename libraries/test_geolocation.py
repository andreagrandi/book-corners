from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache
from django.db import connections
from geopy.exc import GeocoderRateLimited

from libraries import geolocation
from libraries.geolocation import forward_geocode_place

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def clear_forward_geocode_cache() -> Iterator[None]:
    """Clear geocoding cache entries around every test.
    Keeps single-flight and negative-cache scenarios isolated."""
    cache.clear()
    yield
    cache.clear()


@patch("libraries.geolocation.Nominatim")
def test_forward_geocode_reuses_cache_for_normalized_query(
    mocked_nominatim: Mock,
) -> None:
    """Verify equivalent place queries share one successful cache entry.
    Normalizes whitespace, casing, and country codes before cache lookup."""
    mocked_location = Mock(latitude=51.5074, longitude=-0.1278)
    mocked_nominatim.return_value.geocode.return_value = mocked_location

    first_result = forward_geocode_place(
        place_query="  Central   London  ",
        user_agent="book-corners-tests",
        timeout_seconds=5,
        country_code=" GB ",
    )
    second_result = forward_geocode_place(
        place_query="central london",
        user_agent="book-corners-tests",
        timeout_seconds=5,
        country_code="gb",
    )

    assert first_result == (51.5074, -0.1278)
    assert second_result == first_result
    mocked_nominatim.assert_called_once()
    mocked_nominatim.return_value.geocode.assert_called_once_with(
        "Central London",
        exactly_one=True,
        language="en",
        addressdetails=False,
        country_codes="gb",
    )


@patch("libraries.geolocation.Nominatim")
def test_forward_geocode_disables_transport_retries(
    mocked_nominatim: Mock,
) -> None:
    """Verify Nominatim uses an HTTP adapter with retries disabled.
    Prevents one rate-limited operation from producing repeated requests."""
    mocked_nominatim.return_value.geocode.return_value = None

    forward_geocode_place(
        place_query="London",
        user_agent="book-corners-tests",
        timeout_seconds=5,
    )

    adapter_factory = mocked_nominatim.call_args.kwargs["adapter_factory"]
    adapter = adapter_factory(proxies=None, ssl_context=None)
    try:
        https_adapter = adapter.session.get_adapter("https://")
        assert https_adapter.max_retries.total == 0
    finally:
        adapter.session.close()


@patch("libraries.geolocation.Nominatim")
def test_concurrent_forward_geocode_calls_share_one_upstream_operation(
    mocked_nominatim: Mock,
) -> None:
    """Verify concurrent callers share one in-flight geocoding operation.
    Both callers receive the successful coordinates published through cache."""
    geocode_started = Event()
    release_geocode = Event()
    follower_waiting = Event()

    real_wait_for_result = geolocation._wait_for_forward_geocode_result

    def delayed_geocode(*args: object, **kwargs: object) -> Mock:
        """Hold the mocked upstream call until both callers contend.
        Makes the single-flight lock behavior deterministic."""
        geocode_started.set()
        assert release_geocode.wait(timeout=2)
        return Mock(latitude=51.5074, longitude=-0.1278)

    def observed_wait_for_result(
        *, cache_key: str, timeout_seconds: int
    ) -> tuple[float, float] | None:
        """Record when a caller waits for the elected geocoder.
        Delegates polling to the real cache-backed wait helper."""
        follower_waiting.set()
        return real_wait_for_result(
            cache_key=cache_key,
            timeout_seconds=timeout_seconds,
        )

    def threaded_forward_geocode(
        *, place_query: str, country_code: str
    ) -> tuple[float, float] | None:
        """Run forward geocoding and close the worker's database connections.
        Prevents thread-local cache connections from leaking into test teardown."""
        try:
            return forward_geocode_place(
                place_query=place_query,
                user_agent="book-corners-tests",
                timeout_seconds=5,
                country_code=country_code,
            )
        finally:
            connections.close_all()

    mocked_nominatim.return_value.geocode.side_effect = delayed_geocode

    with patch(
        "libraries.geolocation._wait_for_forward_geocode_result",
        side_effect=observed_wait_for_result,
    ):
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                threaded_forward_geocode,
                place_query="Central London",
                country_code="GB",
            )
            assert geocode_started.wait(timeout=2)
            second_future = executor.submit(
                threaded_forward_geocode,
                place_query=" central   london ",
                country_code="gb",
            )
            assert follower_waiting.wait(timeout=2)
            release_geocode.set()

            first_result = first_future.result(timeout=2)
            second_result = second_future.result(timeout=2)

    assert first_result == (51.5074, -0.1278)
    assert second_result == first_result
    mocked_nominatim.assert_called_once()
    mocked_nominatim.return_value.geocode.assert_called_once()


@patch("libraries.geolocation.Nominatim")
def test_rate_limited_forward_geocode_is_cached_briefly(
    mocked_nominatim: Mock,
) -> None:
    """Verify HTTP rate limiting creates a short-lived failure cache entry.
    Immediate matching requests use fallback without another upstream call."""
    mocked_nominatim.return_value.geocode.side_effect = GeocoderRateLimited(
        "HTTP 429",
        retry_after=30,
    )

    first_result = forward_geocode_place(
        place_query="London",
        user_agent="book-corners-tests",
        timeout_seconds=5,
    )
    second_result = forward_geocode_place(
        place_query="london",
        user_agent="book-corners-tests",
        timeout_seconds=5,
    )

    assert first_result is None
    assert second_result is None
    mocked_nominatim.assert_called_once()
    mocked_nominatim.return_value.geocode.assert_called_once()
