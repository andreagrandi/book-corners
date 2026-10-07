from __future__ import annotations

import re

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


# Paths that only scanners request: dotfiles and dot-directories other than
# .well-known, PHP scripts (the site runs no PHP), and common secret filenames.
# Keep in sync with deploy/nginx/block-probes.conf.
_PROBE_PATH_PATTERN = re.compile(
    r"(?:^|/)(?:"
    r"\.(?!well-known(?:/|$))[^/]+"
    r"|[^/]*\.php[^/]*"
    r"|master\.key|credentials\.ya?ml(?:\.enc)?|secrets\.ya?ml"
    r")(?:/|$)",
    re.IGNORECASE,
)


def is_probe_path(*, path: str) -> bool:
    """Report whether a request path matches known secret-file scanner probes.
    Lets middleware and Sentry sampling ignore traffic no real page uses."""
    return _PROBE_PATH_PATTERN.search(path) is not None
