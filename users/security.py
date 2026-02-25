from __future__ import annotations

import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest


def _get_client_identifier(*, request: HttpRequest) -> str:
    """Build a stable client identifier for throttling checks.
    Uses forwarded IP values with a remote-address fallback."""
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if isinstance(forwarded_for, str) and forwarded_for:
        first_hop = forwarded_for.split(",")[0].strip()
        if first_hop:
            return first_hop

    remote_address = request.META.get("REMOTE_ADDR")
    if isinstance(remote_address, str) and remote_address:
        return remote_address

    return "unknown"


def is_auth_rate_limited(
    *,
    request: HttpRequest,
    scope: str,
    max_attempts: int,
) -> tuple[bool, int]:
    """Check whether an auth request exceeds the configured attempt limit.
    Returns a tuple of rate-limit state and retry-after seconds."""
    if not settings.AUTH_RATE_LIMIT_ENABLED:
        return False, 0

    window_seconds = max(1, int(settings.AUTH_RATE_LIMIT_WINDOW_SECONDS))
    allowed_attempts = max(1, max_attempts)
    now_seconds = int(time.time())
    retry_after_seconds = max(1, window_seconds - (now_seconds % window_seconds))

    client_identifier = _get_client_identifier(request=request)
    window_bucket = now_seconds // window_seconds
    cache_key = f"auth-rate:{scope}:{client_identifier}:{window_bucket}"

    if cache.add(cache_key, 1, timeout=window_seconds + 1):
        attempt_count = 1
    else:
        try:
            attempt_count = int(cache.incr(cache_key))
        except ValueError:
            cache.set(cache_key, 1, timeout=window_seconds + 1)
            attempt_count = 1

    return attempt_count > allowed_attempts, retry_after_seconds
