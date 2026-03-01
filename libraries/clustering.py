from __future__ import annotations

from django.contrib.gis.geos import Polygon
from django.db import connection

from libraries.models import Library

CLUSTER_ZOOM_THRESHOLD = 12

ZOOM_GRID_SIZE: dict[int, float] = {
    0: 40.0,
    1: 30.0,
    2: 20.0,
    3: 15.0,
    4: 10.0,
    5: 6.0,
    6: 3.0,
    7: 1.5,
    8: 0.8,
    9: 0.4,
    10: 0.2,
    11: 0.1,
}


def get_grid_size_for_zoom(zoom: int) -> float:
    """Return the ST_SnapToGrid cell size in degrees for a given zoom level.
    Falls back to the smallest grid size for zoom levels above the table."""
    if zoom in ZOOM_GRID_SIZE:
        return ZOOM_GRID_SIZE[zoom]
    if zoom < 0:
        return ZOOM_GRID_SIZE[0]
    return ZOOM_GRID_SIZE[11]


def _parse_box2d(box2d_str: str | None) -> list[float] | None:
    """Parse a PostGIS BOX2D string like 'BOX(xmin ymin,xmax ymax)' into [min_lng, min_lat, max_lng, max_lat]."""
    if not box2d_str:
        return None
    try:
        inner = box2d_str.replace("BOX(", "").rstrip(")")
        min_part, max_part = inner.split(",")
        min_lng, min_lat = min_part.split()
        max_lng, max_lat = max_part.split()
        return [float(min_lng), float(min_lat), float(max_lng), float(max_lat)]
    except (ValueError, AttributeError):
        return None


def build_clustered_features(
    *, zoom: int, bounds: Polygon | None = None
) -> list[dict[str, object]]:
    """Aggregate approved libraries into clusters using PostGIS ST_SnapToGrid.
    Returns lightweight GeoJSON features with cluster counts and sample metadata."""
    grid_size = get_grid_size_for_zoom(zoom)

    where_clauses = ["status = 'approved'"]
    params: list[object] = []

    if bounds is not None:
        where_clauses.append("ST_Within(location, ST_GeomFromEWKT(%s))")
        params.append(bounds.ewkt)

    where_sql = " AND ".join(where_clauses)
    table_name = Library._meta.db_table

    sql = f"""
        SELECT
            ST_X(ST_Centroid(ST_Collect(location))) AS lng,
            ST_Y(ST_Centroid(ST_Collect(location))) AS lat,
            COUNT(*) AS point_count,
            MIN(city) AS sample_city,
            MIN(country) AS sample_country,
            ST_Extent(location) AS extent
        FROM {table_name}
        WHERE {where_sql}
        GROUP BY ST_SnapToGrid(location, %s)
        ORDER BY point_count DESC
    """
    params.append(grid_size)

    features: list[dict[str, object]] = []
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        for row in cursor.fetchall():
            lng, lat, point_count, sample_city, sample_country, extent = row
            bbox = _parse_box2d(extent)
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(lng), float(lat)],
                },
                "properties": {
                    "cluster": True,
                    "point_count": point_count,
                    "sample_city": sample_city or "",
                    "sample_country": sample_country or "",
                    "bbox": bbox,
                },
            })

    return features
