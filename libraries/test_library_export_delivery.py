"""Tests for authenticated browser and API library export delivery.
Exercises manifest safety, HTTP caching, and download availability behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.contrib.gis.geos import Point
from django.test import override_settings
from django.urls import reverse
from ninja_jwt.tokens import RefreshToken

from libraries.library_export import generate_library_export, library_export_directory
from libraries.library_export_delivery import (
    IMMUTABLE_LIBRARY_EXPORT_CACHE_CONTROL,
    LATEST_LIBRARY_EXPORT_CACHE_CONTROL,
    LibraryExportDelivery,
    get_library_export_delivery,
)
from libraries.models import Library


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def export_directory(settings, tmp_path) -> Path:
    """Configure isolated media storage for one export delivery test.
    Keeps artifacts and generated absolute URLs deterministic per test.
    """
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.SITE_URL = "https://bookcorners.example"
    return library_export_directory()


@pytest.fixture
def published_export(export_directory: Path) -> LibraryExportDelivery:
    """Generate one approved-library export for authenticated delivery tests.
    Provides a manifest-backed current GeoJSON and metadata artifact pair.
    """
    Library.objects.create(
        name="Export Delivery Library",
        description="A library included in the download.",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Export 1",
        city="Florence",
        country="IT",
        status=Library.Status.APPROVED,
    )
    generate_library_export()
    export = get_library_export_delivery()
    assert export is not None
    return export


@pytest.fixture
def user_jwt(user) -> str:
    """Create a JWT access token for authenticated export API calls.
    Uses the same bearer credential type as other protected endpoints.
    """
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


def _response_bytes(response) -> bytes:
    """Return a complete body from regular or streaming Django responses.
    Lets file-delivery tests inspect GeoJSON without changing production streams.
    """
    if response.streaming:
        return b"".join(response.streaming_content)
    return response.content


@pytest.mark.parametrize(
    "url",
    [
        "/data/libraries/",
        "/data/libraries/latest.geojson",
        "/data/libraries/metadata.json",
        "/data/libraries/libraries-example.geojson",
    ],
)
def test_web_export_routes_require_authentication(client, url: str) -> None:
    """Verify all browser export routes redirect anonymous visitors to login.
    Preserves the normal session gate before any file or manifest lookup.
    """
    response = client.get(url)

    assert response.status_code == 302
    assert response.url.startswith(f"{reverse('login')}?next=")


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/libraries/export/latest.geojson",
        "/api/v1/libraries/export/metadata.json",
        "/api/v1/libraries/export/libraries-example.geojson",
    ],
)
def test_api_export_routes_require_bearer_authentication(client, url: str) -> None:
    """Verify every API export route rejects requests without a JWT.
    Keeps programmatic downloads behind the established bearer-token contract.
    """
    response = client.get(url)

    assert response.status_code == 401


def test_web_latest_geojson_streams_private_download(
    client,
    published_export: LibraryExportDelivery,
    user,
) -> None:
    """Verify a signed-in user can download the current GeoJSON artifact.
    Confirms browser delivery has private caching and strong validators.
    """
    client.force_login(user)

    response = client.get(reverse("library_export_latest_geojson"))

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/geo+json"
    assert published_export.geojson.filename in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == LATEST_LIBRARY_EXPORT_CACHE_CONTROL
    assert response.headers["ETag"] == f'"{published_export.geojson.checksum}"'
    assert "Cookie" in response.headers["Vary"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
    assert json.loads(_response_bytes(response))["type"] == "FeatureCollection"


def test_api_latest_geojson_streams_private_download(
    client,
    published_export: LibraryExportDelivery,
    user_jwt: str,
) -> None:
    """Verify a JWT client can download the same current GeoJSON artifact.
    Confirms API access shares bytes while varying cache behavior by token header.
    """
    response = client.get(
        "/api/v1/libraries/export/latest.geojson",
        HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/geo+json"
    assert published_export.geojson.filename in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == LATEST_LIBRARY_EXPORT_CACHE_CONTROL
    assert response.headers["ETag"] == f'"{published_export.geojson.checksum}"'
    assert "Authorization" in response.headers["Vary"]
    assert json.loads(_response_bytes(response))["type"] == "FeatureCollection"


def test_api_metadata_and_immutable_artifacts_use_expected_headers(
    client,
    published_export: LibraryExportDelivery,
    user_jwt: str,
) -> None:
    """Verify API metadata and versioned URLs expose correct cache semantics.
    Keeps current aliases private and immutable artifact versions long-lived.
    """
    metadata_response = client.get(
        "/api/v1/libraries/export/metadata.json",
        HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
    )
    immutable_response = client.get(
        f"/api/v1/libraries/export/{published_export.geojson.filename}",
        HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
    )

    assert metadata_response.status_code == 200
    assert metadata_response.headers["Content-Type"] == "application/json"
    assert "inline" in metadata_response.headers["Content-Disposition"]
    assert metadata_response.headers["Cache-Control"] == LATEST_LIBRARY_EXPORT_CACHE_CONTROL
    assert json.loads(_response_bytes(metadata_response))["record_count"] == 1
    assert immutable_response.status_code == 200
    assert immutable_response.headers["Cache-Control"] == IMMUTABLE_LIBRARY_EXPORT_CACHE_CONTROL
    assert published_export.geojson.filename in immutable_response.headers["Content-Disposition"]


def test_authenticated_latest_aliases_support_conditional_requests(
    client,
    published_export: LibraryExportDelivery,
    user,
    user_jwt: str,
) -> None:
    """Verify browser and API clients receive 304 for unchanged artifacts.
    Uses the manifest checksums and publication timestamp as shared validators.
    """
    client.force_login(user)
    web_response = client.get(reverse("library_export_latest_geojson"))
    api_response = client.get(
        "/api/v1/libraries/export/latest.geojson",
        HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
    )

    conditional_web_response = client.get(
        reverse("library_export_latest_geojson"),
        HTTP_IF_NONE_MATCH=web_response.headers["ETag"],
    )
    conditional_api_response = client.get(
        "/api/v1/libraries/export/latest.geojson",
        HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
        HTTP_IF_MODIFIED_SINCE=api_response.headers["Last-Modified"],
    )

    assert conditional_web_response.status_code == 304
    assert conditional_web_response.headers["ETag"] == f'"{published_export.geojson.checksum}"'
    assert conditional_web_response.headers["Cache-Control"] == LATEST_LIBRARY_EXPORT_CACHE_CONTROL
    assert conditional_api_response.status_code == 304
    assert conditional_api_response.headers["ETag"] == f'"{published_export.geojson.checksum}"'
    assert "Authorization" in conditional_api_response.headers["Vary"]


def test_download_page_and_dashboard_expose_available_export(
    client,
    published_export: LibraryExportDelivery,
    user,
) -> None:
    """Verify signed-in users can discover the export through the dashboard.
    Renders plain-language download details before the GeoJSON file action.
    """
    client.force_login(user)

    dashboard_response = client.get(reverse("dashboard"))
    page_response = client.get(reverse("library_export_download"))

    dashboard_content = dashboard_response.content.decode()
    page_content = page_response.content.decode()
    assert dashboard_response.status_code == 200
    assert 'id="library-export-card"' in dashboard_content
    assert reverse("library_export_download") in dashboard_content
    assert page_response.status_code == 200
    assert "Download the complete catalogue of approved Book Corners libraries" in page_content
    assert str(published_export.record_count) in page_content
    assert reverse("library_export_latest_geojson") in page_content
    assert published_export.geojson.filename in page_content


def test_dashboard_hides_export_before_first_artifact(
    client,
    export_directory: Path,
    user,
) -> None:
    """Verify the dashboard does not link to an unavailable first export.
    Keeps the optional download card visible only when bytes can be served.
    """
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert 'id="library-export-card"' not in response.content.decode()


@override_settings(LIBRARY_EXPORT_DELIVERY_ENABLED=False)
def test_disabled_delivery_returns_not_found_and_hides_dashboard(
    client,
    published_export: LibraryExportDelivery,
    user,
    user_jwt: str,
) -> None:
    """Verify the emergency delivery switch removes web and API exposure.
    Leaves generated artifacts intact while hiding the dashboard entry point.
    """
    client.force_login(user)

    web_response = client.get(reverse("library_export_latest_geojson"))
    api_response = client.get(
        "/api/v1/libraries/export/latest.geojson",
        HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
    )
    dashboard_response = client.get(reverse("dashboard"))

    assert web_response.status_code == 404
    assert api_response.status_code == 404
    assert 'id="library-export-card"' not in dashboard_response.content.decode()


def test_missing_export_returns_clear_api_and_web_unavailable_responses(
    client,
    export_directory: Path,
    user,
    user_jwt: str,
) -> None:
    """Verify an enabled service reports a missing first artifact clearly.
    Keeps browser messaging friendly and API errors structured for clients.
    """
    client.force_login(user)

    page_response = client.get(reverse("library_export_download"))
    web_response = client.get(reverse("library_export_latest_geojson"))
    api_response = client.get(
        "/api/v1/libraries/export/latest.geojson",
        HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
    )

    assert page_response.status_code == 503
    assert "temporarily unavailable" in page_response.content.decode()
    assert web_response.status_code == 503
    assert "temporarily unavailable" in web_response.content.decode()
    assert api_response.status_code == 503
    assert api_response.json() == {
        "message": "Library export is temporarily unavailable.",
        "details": None,
    }


def test_unlisted_files_are_not_available_from_web_or_api(
    client,
    export_directory: Path,
    published_export: LibraryExportDelivery,
    user,
    user_jwt: str,
) -> None:
    """Verify retained-looking files outside the manifest stay inaccessible.
    Prevents arbitrary directory contents from becoming authenticated downloads.
    """
    unlisted_filename = "libraries-retained.geojson"
    (export_directory / unlisted_filename).write_text("{}", encoding="utf-8")
    client.force_login(user)

    web_response = client.get(
        reverse("library_export_artifact", kwargs={"filename": unlisted_filename})
    )
    api_response = client.get(
        f"/api/v1/libraries/export/{unlisted_filename}",
        HTTP_AUTHORIZATION=f"Bearer {user_jwt}",
    )

    assert web_response.status_code == 404
    assert api_response.status_code == 404
