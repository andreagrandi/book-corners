from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from libraries.models import Library


class StaticViewSitemap(Sitemap):
    """Sitemap entries for public static pages.
    Helps crawlers discover core landing pages quickly."""

    changefreq = "weekly"
    priority = 0.8

    def items(self) -> list[str]:
        """Return URL names for static routes to index.
        Keeps sitemap generation explicit and predictable."""
        return [
            "home",
            "about_page",
            "map_page",
            "privacy_page",
            "stats_page",
        ]

    def location(self, item: str) -> str:
        """Resolve each static URL name into a concrete path.
        Ensures the sitemap always reflects current route definitions."""
        return reverse(item)


class LibrarySitemap(Sitemap):
    """Sitemap entries for approved library detail pages.
    Limits indexing to publicly visible library records."""

    changefreq = "weekly"
    priority = 0.7

    def items(self):
        """Return approved libraries ordered by latest update.
        Keeps crawler focus on active and publicly visible content."""
        return Library.objects.filter(status=Library.Status.APPROVED).order_by("-updated_at")

    def lastmod(self, obj: Library):
        """Return the last updated timestamp for a library entry.
        Lets crawlers optimize recrawl cadence for changed records."""
        return obj.updated_at

    def location(self, item: Library) -> str:
        """Resolve a library sitemap item to its detail route.
        Uses slug-based URLs that match the public detail page pattern."""
        return reverse("library_detail", kwargs={"slug": item.slug})
