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


def run_library_search(
    *,
    q: str = "",
    city: str = "",
    country: str = "",
    postal_code: str = "",
    lat: float | None = None,
    lng: float | None = None,
    radius_km: int | None = None,
) -> QuerySet[Library]:
    """Execute combined text, field, and proximity search on approved libraries.
    Returns a queryset filtered by the given parameters without geocoding."""
    queryset = Library.objects.filter(status=Library.Status.APPROVED).order_by("-created_at")

    if city:
        queryset = queryset.filter(city__icontains=city)
    if country:
        queryset = queryset.filter(country__iexact=country)
    if postal_code:
        queryset = queryset.filter(postal_code__icontains=postal_code)

    if q:
        queryset = apply_text_search(queryset=queryset, query_text=q)

    if lat is not None and lng is not None:
        effective_radius = radius_km if isinstance(radius_km, int) and radius_km > 0 else DEFAULT_SEARCH_RADIUS_KM
        center_point = Point(x=lng, y=lat, srid=4326)
        queryset = (
            queryset
            .annotate(distance=Distance("location", center_point))
            .filter(location__distance_lte=(center_point, D(km=effective_radius)))
            .order_by("distance", "-created_at")
        )

    return queryset
