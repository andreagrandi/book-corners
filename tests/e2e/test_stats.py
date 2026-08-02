from datetime import UTC, datetime

import pytest
from django.core.cache import cache
from django.utils import timezone

from libraries.models import Library


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


def test_growth_chart_uses_accurate_period_end_dates(
    live_server, page, mock_external_apis, approved_libraries
):
    """Verify the chart labels monthly totals at period-end and ends today.
    Keeps cumulative values attached to the dates they represent."""
    Library.objects.filter(pk=approved_libraries[0].pk).update(
        created_at=datetime(2026, 2, 28, 12, tzinfo=UTC),
    )
    Library.objects.filter(pk=approved_libraries[1].pk).update(
        created_at=datetime(2026, 3, 1, 12, tzinfo=UTC),
    )
    cache.clear()

    page.goto(f"{live_server.url}/stats/")
    page.wait_for_function(
        'typeof Chart !== "undefined" && Chart.getChart("growth-chart") !== undefined'
    )
    chart_data = page.evaluate(
        """() => {
            const chart = Chart.getChart("growth-chart");
            return {
                labels: chart.data.labels,
                counts: chart.data.datasets[0].data,
                beginAtZero: chart.options.scales.y.beginAtZero,
                minimum: chart.options.scales.y.min
            };
        }"""
    )

    assert "2026-02-01" not in chart_data["labels"]
    assert chart_data["labels"][0] == "2026-03-31"
    assert chart_data["labels"][-1] == timezone.localdate().isoformat()
    assert chart_data["beginAtZero"] is False
    assert chart_data["minimum"] == chart_data["counts"][0]
