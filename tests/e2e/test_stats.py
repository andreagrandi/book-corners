import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_stats_page_renders_charts(
    live_server, page, mock_external_apis, approved_libraries
):
    """Verify the statistics page renders both Chart.js canvas elements.
    Confirms the countries bar chart and growth line chart are present."""
    page.goto(f"{live_server.url}/stats/")

    countries_canvas = page.locator("#countries-chart")
    countries_canvas.wait_for(state="attached")
    assert countries_canvas.count() == 1

    growth_canvas = page.locator("#growth-chart")
    growth_canvas.wait_for(state="attached")
    assert growth_canvas.count() == 1


def test_stats_page_shows_totals(
    live_server, page, mock_external_apis, approved_libraries
):
    """Verify the stat cards display correct non-zero counts.
    Confirms the total libraries stat reflects the test data."""
    page.goto(f"{live_server.url}/stats/")

    stat_values = page.locator(".stat-value")
    assert stat_values.count() >= 2

    total_text = stat_values.first.text_content().strip()
    assert total_text != "0"
