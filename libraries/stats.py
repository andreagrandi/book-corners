from __future__ import annotations

from itertools import accumulate

import pycountry
from django.core.cache import cache
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import TruncDate, TruncMonth

from libraries.models import Library, LibraryPhoto

STATS_CACHE_KEY = "stats_page_data"
STATS_CACHE_TIMEOUT = 300  # 5 minutes

ADAPTIVE_GRANULARITY_THRESHOLD_DAYS = 90


def country_code_to_flag_emoji(*, country_code: str) -> str:
    """Convert an ISO 3166-1 alpha-2 code into its Unicode flag emoji.
    Uses regional indicator symbols to produce a flag glyph."""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_code.upper())


def get_countries() -> list[dict]:
    """Return all countries with at least one approved library.
    Ordered by count descending, without the top-10 limit."""
    approved_qs = Library.objects.filter(status=Library.Status.APPROVED)
    countries_raw = (
        approved_qs
        .values("country")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    countries = []
    for entry in countries_raw:
        code = entry["country"]
        py_country = pycountry.countries.get(alpha_2=code)
        country_name = py_country.name if py_country else code
        countries.append({
            "country_code": code,
            "country_name": country_name,
            "flag_emoji": country_code_to_flag_emoji(country_code=code),
            "count": entry["count"],
        })
    return countries


def build_stats_data() -> dict:
    """Compute aggregate statistics about approved libraries.
    Returns cached data with counts, country breakdown, and growth series."""
    cached = cache.get(STATS_CACHE_KEY)
    if cached is not None:
        return cached

    approved_qs = Library.objects.filter(status=Library.Status.APPROVED)

    total_approved = approved_qs.count()

    has_approved_community_photo = LibraryPhoto.objects.filter(
        library=OuterRef("pk"),
        status=LibraryPhoto.Status.APPROVED,
    )
    total_with_image = approved_qs.filter(
        ~Q(photo="") | Q(Exists(has_approved_community_photo))
    ).count()

    top_countries_raw = (
        approved_qs
        .values("country")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )
    top_countries = []
    for entry in top_countries_raw:
        code = entry["country"]
        py_country = pycountry.countries.get(alpha_2=code)
        country_name = py_country.name if py_country else code
        top_countries.append({
            "country_code": code,
            "country_name": country_name,
            "flag_emoji": country_code_to_flag_emoji(country_code=code),
            "count": entry["count"],
        })

    cumulative_series, granularity = _build_cumulative_series(queryset=approved_qs)

    data = {
        "total_approved": total_approved,
        "total_with_image": total_with_image,
        "top_countries": top_countries,
        "cumulative_series": cumulative_series,
        "granularity": granularity,
    }
    cache.set(STATS_CACHE_KEY, data, STATS_CACHE_TIMEOUT)
    return data


def _build_cumulative_series(
    *, queryset,
) -> tuple[list[dict[str, object]], str]:
    """Build a cumulative time series of library creation dates.
    Chooses daily or monthly granularity based on the date span."""
    first_library = queryset.order_by("created_at").only("created_at").first()
    if first_library is None:
        return [], "daily"

    from django.utils import timezone

    now = timezone.now()
    span_days = (now - first_library.created_at).days

    if span_days <= ADAPTIVE_GRANULARITY_THRESHOLD_DAYS:
        trunc_fn = TruncDate("created_at")
        granularity = "daily"
    else:
        trunc_fn = TruncMonth("created_at")
        granularity = "monthly"

    period_counts = (
        queryset
        .annotate(period=trunc_fn)
        .values("period")
        .annotate(count=Count("id"))
        .order_by("period")
    )

    counts = [entry["count"] for entry in period_counts]
    periods = [entry["period"] for entry in period_counts]
    cumulative = list(accumulate(counts))

    series = []
    for period, cum_count in zip(periods, cumulative):
        series.append({
            "period": str(period.date()) if hasattr(period, "date") else str(period),
            "cumulative_count": cum_count,
        })

    return series, granularity
