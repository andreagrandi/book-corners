#!/usr/bin/env python3
"""Enrich a GeoJSON export with reverse-geocoded address fields.

Standalone script — no Django required. Calls Nominatim for each feature
missing addr:street or addr:city and writes results back into the file.
"""

import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "book-corners-enrichment/1.0"
REQUEST_DELAY = 1.1  # seconds between requests (Nominatim policy: 1 req/sec)
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0  # seconds


def _needs_enrichment(properties: dict) -> bool:
    """Check whether a feature is missing address data."""
    return not properties.get("addr:street") or not properties.get("addr:city")


def _reverse_geocode(lat: float, lon: float) -> dict[str, str]:
    """Call Nominatim reverse geocode and return addr:* fields."""
    params = (
        f"?lat={lat}&lon={lon}&format=jsonv2&addressdetails=1"
        f"&accept-language=en"
    )
    url = NOMINATIM_URL + params
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise RuntimeError(f"Failed after {MAX_RETRIES} retries") from exc
        else:
            break

    address = data.get("address", {})
    street = (
        address.get("road")
        or address.get("pedestrian")
        or address.get("residential")
        or address.get("footway")
        or address.get("path")
        or ""
    )
    house_number = address.get("house_number", "")
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or address.get("municipality")
        or address.get("suburb")
        or address.get("county")
        or ""
    )
    country_code = address.get("country_code", "").upper()
    postcode = address.get("postcode", "")

    result = {}
    if street:
        full_street = f"{street} {house_number}".strip() if house_number else street
        result["addr:street"] = full_street
    if city:
        result["addr:city"] = city
    if country_code:
        result["addr:country"] = country_code
    if postcode:
        result["addr:postcode"] = postcode

    return result


def enrich(input_path: str) -> None:
    """Load GeoJSON, enrich features missing address data, save to new file."""
    path = Path(input_path)
    if not path.exists():
        print(f"Error: file not found: {path}")
        sys.exit(1)

    with open(path, encoding="utf-8") as fh:
        geojson = json.load(fh)

    features = geojson.get("features", [])
    total = len(features)
    enriched_count = 0
    skipped_count = 0
    failed_count = 0

    print(f"Loaded {total} features from {path.name}")

    for idx, feature in enumerate(features, start=1):
        properties = feature.get("properties", {})
        feature_id = properties.get("id", "unknown")

        if not _needs_enrichment(properties):
            skipped_count += 1
            continue

        coords = feature.get("geometry", {}).get("coordinates", [])
        if len(coords) < 2:
            print(f"[{idx}/{total}] ({idx / total * 100:.1f}%) Skipping {feature_id} — no coordinates")
            failed_count += 1
            continue

        lon, lat = coords[0], coords[1]

        try:
            addr_fields = _reverse_geocode(lat=lat, lon=lon)
        except Exception as exc:
            print(f"[{idx}/{total}] ({idx / total * 100:.1f}%) FAILED {feature_id} — {exc}")
            failed_count += 1
            time.sleep(REQUEST_DELAY)
            continue

        for key, value in addr_fields.items():
            if key not in properties or not properties[key]:
                properties[key] = value

        street_display = addr_fields.get("addr:street", "?")
        city_display = addr_fields.get("addr:city", "?")
        country_display = addr_fields.get("addr:country", "?")
        print(
            f"[{idx}/{total}] ({idx / total * 100:.1f}%) "
            f"Enriching {feature_id} — {street_display}, {city_display}, {country_display}"
        )
        enriched_count += 1
        time.sleep(REQUEST_DELAY)

    output_path = path.parent / f"enriched_{path.name}"
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(geojson, fh, ensure_ascii=False, indent=2)

    print(f"\nDone! Enriched: {enriched_count}, Skipped (already had data): {skipped_count}, Failed: {failed_count}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <geojson_file>")
        sys.exit(1)
    enrich(sys.argv[1])
