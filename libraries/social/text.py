"""Shared post text builder for social media platforms."""

from __future__ import annotations

COUNTRY_NAMES = {
    "AT": "Austria",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "CH": "Switzerland",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK": "Denmark",
    "EE": "Estonia",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "HR": "Croatia",
    "HU": "Hungary",
    "IE": "Ireland",
    "IT": "Italy",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "NL": "Netherlands",
    "NO": "Norway",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SE": "Sweden",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "US": "United States",
}

BASE_HASHTAGS = ["#BookCorners", "#FreeBooks", "#Books", "#StreetLibrary"]


def _country_name(country_code: str) -> str:
    """Look up a full country name from a two-letter ISO code.
    Falls back to the raw code when not found in the lookup table."""
    return COUNTRY_NAMES.get(country_code.upper(), country_code)


def build_post_text(library, detail_url: str, *, max_length: int = 300) -> str:
    """Build social media post text with description, location, link, and hashtags.
    Truncates description and fills hashtags to fit within max_length."""
    country_name = _country_name(library.country)
    location_line = f"\U0001f4cd {library.city}, {country_name}"

    city_tag = f"#{library.city.replace(' ', '')}"
    country_tag = f"#{country_name.replace(' ', '')}"
    extra_hashtags = [city_tag, country_tag]

    all_hashtags = BASE_HASHTAGS + [
        tag for tag in extra_hashtags if tag not in BASE_HASHTAGS
    ]

    # Build the fixed parts (location + url)
    fixed_parts = f"\n\n{location_line}\n\n{detail_url}"

    # Fill hashtags up to max_length
    hashtag_line = ""
    for tag in all_hashtags:
        candidate = f"{hashtag_line} {tag}".strip()
        # Check if adding description + fixed + hashtags fits
        test_text = f"x{fixed_parts}\n\n{candidate}"
        if len(test_text) <= max_length:
            hashtag_line = candidate

    # Calculate budget for description
    suffix = f"{fixed_parts}\n\n{hashtag_line}" if hashtag_line else fixed_parts
    description_budget = max_length - len(suffix)

    description = library.description or library.name or library.address
    if len(description) > description_budget:
        description = description[: description_budget - 1].rstrip() + "\u2026"

    parts = [description, location_line, detail_url]
    if hashtag_line:
        parts.append(hashtag_line)

    return "\n\n".join(parts)
