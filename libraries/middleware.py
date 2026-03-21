from django.utils.cache import patch_cache_control

# Cache-Control rules for read-only API endpoints.
# Each rule is (path, exact_match, max_age, s_maxage).
# Rules are evaluated in order; first match wins.
# Only applied to GET requests that return 2xx responses.
_API_CACHE_RULES = [
    ("/api/v1/statistics/", True, 900, 900),
    ("/api/v1/libraries/latest", True, 300, 300),
    ("/api/v1/libraries/", True, 120, 120),
]

# Detail endpoints: prefix match for slugs under /api/v1/libraries/{slug}
_LIBRARY_DETAIL_PREFIX = "/api/v1/libraries/"
_LIBRARY_DETAIL_CACHE = (300, 300)

# Paths that must never be cached (write endpoints).
_API_NO_CACHE_PREFIXES = [
    "/api/v1/auth/",
]


def _find_cache_rule(path):
    """Find the matching cache rule for a request path.
    Returns (max_age, s_maxage) or None if no rule matches."""
    for rule_path, exact, max_age, s_maxage in _API_CACHE_RULES:
        if exact and path == rule_path:
            return max_age, s_maxage

    # Match library detail paths: /api/v1/libraries/{slug}
    if path.startswith(_LIBRARY_DETAIL_PREFIX) and path != _LIBRARY_DETAIL_PREFIX:
        slug_part = path[len(_LIBRARY_DETAIL_PREFIX):].rstrip("/")
        # Only match single-segment slugs (no sub-resources like /report, /photo)
        if slug_part and "/" not in slug_part:
            return _LIBRARY_DETAIL_CACHE

    return None


class APICacheControlMiddleware:
    """Add Cache-Control headers to read-only API responses.
    Matches GET requests against configured path rules and patches the response."""

    def __init__(self, get_response):
        """Store the next middleware or view in the chain.
        Standard Django middleware initialisation."""
        self.get_response = get_response

    def __call__(self, request):
        """Process the request and conditionally add cache headers.
        Only applies to successful GET requests matching configured API paths."""
        response = self.get_response(request)

        if request.method != "GET":
            return response

        if not 200 <= response.status_code < 300:
            return response

        for prefix in _API_NO_CACHE_PREFIXES:
            if request.path.startswith(prefix):
                return response

        rule = _find_cache_rule(path=request.path)
        if rule:
            max_age, s_maxage = rule
            patch_cache_control(response, public=True, max_age=max_age, s_maxage=s_maxage)

        return response
