import pytest
from django.core.cache import cache
from django.test import RequestFactory, override_settings

from libraries.api_security import is_api_rate_limited


@pytest.mark.django_db
class TestAPIRateLimiting:
    """Tests for the API rate limiting utility."""

    def setup_method(self):
        """Clear the cache before each test.
        Prevents rate limit state from leaking between tests."""
        cache.clear()

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
    )
    def test_allows_requests_under_limit(self):
        """Verify requests under the max count are not throttled.
        Confirms normal traffic passes through without blocking."""
        factory = RequestFactory()
        request = factory.get("/api/v1/libraries/")

        limited, _ = is_api_rate_limited(
            request=request, scope="test-read", max_requests=5,
        )

        assert limited is False

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
    )
    def test_blocks_requests_over_limit(self):
        """Verify requests exceeding the max count are throttled.
        Confirms the limiter returns True after the allowed count."""
        factory = RequestFactory()
        request = factory.get("/api/v1/libraries/")

        for _ in range(3):
            is_api_rate_limited(
                request=request, scope="test-block", max_requests=2,
            )

        limited, retry_after = is_api_rate_limited(
            request=request, scope="test-block", max_requests=2,
        )

        assert limited is True
        assert retry_after > 0

    @override_settings(API_RATE_LIMIT_ENABLED=False)
    def test_disabled_always_allows(self):
        """Verify disabled rate limiting never blocks requests.
        Confirms the feature flag bypasses all counting logic."""
        factory = RequestFactory()
        request = factory.get("/api/v1/libraries/")

        for _ in range(100):
            limited, _ = is_api_rate_limited(
                request=request, scope="test-disabled", max_requests=1,
            )

        assert limited is False

    @override_settings(
        API_RATE_LIMIT_ENABLED=True,
        API_RATE_LIMIT_WINDOW_SECONDS=300,
    )
    def test_independent_scope_counters(self):
        """Verify different scopes track counts independently.
        Confirms exhausting one scope does not affect another."""
        factory = RequestFactory()
        request = factory.get("/api/v1/libraries/")

        for _ in range(3):
            is_api_rate_limited(
                request=request, scope="scope-a", max_requests=2,
            )

        limited_a, _ = is_api_rate_limited(
            request=request, scope="scope-a", max_requests=2,
        )
        limited_b, _ = is_api_rate_limited(
            request=request, scope="scope-b", max_requests=2,
        )

        assert limited_a is True
        assert limited_b is False
