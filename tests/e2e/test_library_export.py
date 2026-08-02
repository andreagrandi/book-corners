"""Browser coverage for authenticated library export discovery and download.
Verifies the dashboard path works for a signed-in non-technical user.
"""

from pathlib import Path

import pytest
from django.contrib.gis.geos import Point

from libraries.library_export import generate_library_export
from libraries.library_export_delivery import get_library_export_delivery
from libraries.models import Library


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_dashboard_opens_export_page_and_starts_geojson_download(
    authenticated_page,
    e2e_user,
    live_server,
    settings,
    tmp_path: Path,
) -> None:
    """Verify a signed-in user can find and download the complete catalogue.
    Covers the dashboard card, plain-language page, and browser file action.
    """
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.SITE_URL = "https://bookcorners.example"
    Library.objects.create(
        name="Browser Export Library",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Browser 1",
        city="Florence",
        country="IT",
        status=Library.Status.APPROVED,
        created_by=e2e_user,
    )
    result = generate_library_export()
    assert result.geojson_filename is not None
    export = get_library_export_delivery()
    assert export is not None

    authenticated_page.goto(f"{live_server.url}/dashboard/")
    authenticated_page.locator("#library-export-card").wait_for(state="visible")
    authenticated_page.get_by_role("link", name="View download").click()

    authenticated_page.wait_for_url("**/data/libraries/")
    assert authenticated_page.get_by_role("heading", name="Download library data").is_visible()
    with authenticated_page.expect_download() as download_info:
        authenticated_page.get_by_role("link", name="Download compressed GeoJSON").click()
    download = download_info.value
    assert download.suggested_filename == export.geojson_gzip.filename
