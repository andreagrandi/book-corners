from __future__ import annotations

from django.http import HttpRequest


def get_client_identifier(*, request: HttpRequest) -> str:
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
