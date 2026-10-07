import pytest
from django.test import Client

from config.security import is_probe_path


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/.env.production",
        "/customer/.env",
        "/exapi/.env",
        "/.git/config",
        "/.aws/credentials",
        "/config/master.key",
        "/wp-config.php",
        "/wp-config.php.bak",
        "/wordpress/wp-login.php",
        "/config/credentials.yml.enc",
        "/secrets.yaml",
        "/.ENV",
    ],
)
def test_is_probe_path_matches_secret_file_probes(path: str) -> None:
    """Flag the scanner paths seen in Sentry and close variants.
    Ensures nginx, middleware and tracing agree on what to block."""
    assert is_probe_path(path=path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/.well-known/apple-developer-domain-association.txt",
        "/.well-known/acme-challenge/token",
        "/robots.txt",
        "/sitemap.xml",
        "/static/css/app.css",
        "/data/libraries/metadata.json",
        "/library/some-library-slug/",
        "/api/v1/libraries/",
        "/environment/",
        "/keys/master.keyring",
    ],
)
def test_is_probe_path_allows_real_routes(path: str) -> None:
    """Leave real pages, static files and .well-known paths alone.
    Guards against blocking legitimate traffic by accident."""
    assert is_probe_path(path=path) is False


def test_block_probe_paths_middleware_returns_bare_404() -> None:
    """Return an empty 404 for probe paths without rendering a template.
    Keeps blocked probes cheap and free of session or database work."""
    response = Client().get("/.env")

    assert response.status_code == 404
    assert response.content == b""
    assert "sessionid" not in response.cookies


@pytest.mark.django_db
def test_block_probe_paths_middleware_passes_real_requests() -> None:
    """Let normal requests reach their views through the middleware.
    Confirms the early 404 does not swallow legitimate routes."""
    response = Client().get("/robots.txt")

    assert response.status_code == 200
