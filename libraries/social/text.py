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


def build_bluesky_text(library, detail_url: str, *, max_length: int = 300):
    """Build a Bluesky TextBuilder with clickable links and hashtags.
    Returns an atproto TextBuilder instance with proper facets."""
    from atproto import client_utils

    plain_text = build_post_text(library, detail_url, max_length=max_length)
    builder = client_utils.TextBuilder()

    i = 0
    while i < len(plain_text):
        # Check if current position starts the URL
        if plain_text[i:].startswith(detail_url):
            builder.link(detail_url, detail_url)
            i += len(detail_url)
        # Check if current position starts a hashtag
        elif plain_text[i] == "#":
            end = i + 1
            while end < len(plain_text) and plain_text[end] not in (" ", "\n"):
                end += 1
            tag_text = plain_text[i:end]
            tag_value = tag_text[1:]  # strip the # for the tag facet
            builder.tag(tag_text, tag_value)
            i = end
        else:
            # Collect plain text until next special token
            end = i + 1
            while end < len(plain_text):
                if plain_text[end] == "#" or plain_text[end:].startswith(detail_url):
                    break
                end += 1
            builder.text(plain_text[i:end])
            i = end

    return builder
