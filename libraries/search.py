from __future__ import annotations

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import connection
from django.db.models import Q, QuerySet

from libraries.models import Library

DEFAULT_SEARCH_RADIUS_KM = 10


def apply_text_search(*, queryset: QuerySet[Library], query_text: str) -> QuerySet[Library]:
    """Filter libraries by text on name and description.
    Uses PostgreSQL full-text search with a safe fallback for non-Postgres DBs."""
    if connection.vendor != "postgresql":
        return queryset.filter(
            Q(name__icontains=query_text) | Q(description__icontains=query_text)
        )

    search_vector = SearchVector("name", weight="A") + SearchVector("description", weight="B")
    search_query = SearchQuery(query_text, search_type="plain")
    return (
        queryset
        .annotate(search=search_vector, rank=SearchRank(search_vector, search_query))
        .filter(search=search_query)
        .order_by("-rank", "-created_at")
    )


def apply_combined_search(*, queryset: QuerySet[Library], search_text: str) -> QuerySet[Library]:
    """Filter libraries by text across name, description, city, address, and postal code.
    Combines substring matching with PostgreSQL full-text ranking when available."""
    # icontains scans these columns without a supporting index. Acceptable at current
    # table size and consistent with the existing `q` FTS query (also unindexed). If
    # this becomes a hot path, swap to pg_trgm GIN indexes on the same five fields.
    text_filter = (
        Q(name__icontains=search_text)
        | Q(description__icontains=search_text)
        | Q(city__icontains=search_text)
        | Q(address__icontains=search_text)
        | Q(postal_code__icontains=search_text)
    )

    if connection.vendor != "postgresql":
        return queryset.filter(text_filter)

    search_vector = (
        SearchVector("name", weight="A")
        + SearchVector("description", weight="B")
        + SearchVector("city", weight="C")
        + SearchVector("address", weight="C")
    )
    search_query = SearchQuery(search_text, search_type="plain")
    return (
        queryset
        .annotate(combined_rank=SearchRank(search_vector, search_query))
        .filter(text_filter)
        .order_by("-combined_rank", "-created_at")
    )


def run_library_search(
    *,
    q: str = "",
    search: str = "",
    city: str = "",
    country: str = "",
    postal_code: str = "",
    lat: float | None = None,
    lng: float | None = None,
    radius_km: int | None = None,
    has_photo: bool | None = None,
) -> QuerySet[Library]:
    """Execute combined text, field, and proximity search on approved libraries.
    Returns a queryset filtered by the given parameters without geocoding."""
    queryset = Library.objects.filter(status=Library.Status.APPROVED).order_by("-created_at")

    if has_photo is True:
        queryset = queryset.exclude(photo="")
    elif has_photo is False:
        queryset = queryset.filter(photo="")

    if city:
        queryset = queryset.filter(city__icontains=city)
    if country:
        queryset = queryset.filter(country__iexact=country)
    if postal_code:
        queryset = queryset.filter(postal_code__icontains=postal_code)

    if search:
        queryset = apply_combined_search(queryset=queryset, search_text=search)
    elif q:
        queryset = apply_text_search(queryset=queryset, query_text=q)

    if lat is not None and lng is not None:
        center_point = Point(x=lng, y=lat, srid=4326)
        queryset = queryset.annotate(distance=Distance("location", center_point))
        if isinstance(radius_km, int) and radius_km > 0:
            queryset = queryset.filter(location__distance_lte=(center_point, D(km=radius_km)))
        queryset = queryset.order_by("distance", "-created_at")

    return queryset
