from __future__ import annotations

import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest

from config.security import get_client_identifier


def is_api_rate_limited(
    *,
    request: HttpRequest,
    scope: str,
    max_requests: int,
) -> tuple[bool, int]:
    """Check whether an API request exceeds the configured rate limit.
    Returns a tuple of rate-limit state and retry-after seconds."""
    if not settings.API_RATE_LIMIT_ENABLED:
        return False, 0

    window_seconds = max(1, int(settings.API_RATE_LIMIT_WINDOW_SECONDS))
    allowed_requests = max(1, max_requests)
    now_seconds = int(time.time())
    retry_after_seconds = max(1, window_seconds - (now_seconds % window_seconds))

    client_identifier = get_client_identifier(request=request)
    window_bucket = now_seconds // window_seconds
    cache_key = f"api-rate:{scope}:{client_identifier}:{window_bucket}"

    if cache.add(cache_key, 1, timeout=window_seconds + 1):
        request_count = 1
    else:
        try:
            request_count = int(cache.incr(cache_key))
        except ValueError:
            cache.set(cache_key, 1, timeout=window_seconds + 1)
            request_count = 1

    return request_count > allowed_requests, retry_after_seconds
