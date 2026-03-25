import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_homepage_loads_with_hero_and_nav(live_server, page, mock_external_apis):
    """Verify the homepage renders with hero section and navigation links.
    Confirms the base template, hero heading, and navbar are functional."""
    page.goto(f"{live_server.url}/")

    heading = page.locator("h1")
    assert heading.is_visible()

    navbar = page.locator(".navbar")
    assert navbar.is_visible()

    assert page.locator(".navbar-end a[href='/map/']").is_visible()


def test_homepage_latest_entries_htmx_load(
    live_server, page, mock_external_apis, approved_libraries
):
    """Verify HTMX loads the latest entries grid on page load.
    Confirms the card grid populates with library cards after HTMX triggers."""
    page.goto(f"{live_server.url}/")

    latest_section = page.locator("#latest-entries")
    latest_section.wait_for(state="attached", timeout=10000)

    first_card = latest_section.locator("article.card").first
    first_card.wait_for(state="visible", timeout=10000)

    cards = latest_section.locator("article.card")
    assert cards.count() > 0


def test_homepage_load_more_pagination(
    live_server, page, mock_external_apis, approved_libraries
):
    """Verify the Load more button appends additional entries via HTMX.
    Confirms pagination works without a full page reload."""
    page.goto(f"{live_server.url}/")

    grid = page.locator("#latest-entries-grid")
    grid.wait_for(state="visible")

    initial_count = grid.locator(".card").count()

    load_more = page.locator("#latest-entries-pagination button")
    if load_more.is_visible():
        load_more.click()
        grid.locator(f".card:nth-child({initial_count + 1})").wait_for(
            state="visible", timeout=10000,
        )

        new_count = grid.locator(".card").count()
        assert new_count > initial_count
