"""Bluesky posting client for library social media sharing."""

from __future__ import annotations

import logging
from pathlib import Path

from atproto import Client
from django.conf import settings

from libraries.social.text import build_bluesky_text

logger = logging.getLogger(__name__)


def post_library(library, text: str, image_path: Path) -> str:
    """Post a library with photo to Bluesky and return the post URL.
    Builds a TextBuilder with facets so links and hashtags are clickable."""
    client = Client()
    client.login(settings.BLUESKY_HANDLE, settings.BLUESKY_APP_PASSWORD)

    with open(image_path, "rb") as f:
        img_data = f.read()

    # Reconstruct the detail URL from the plain text
    detail_url = _extract_url(text)
    rich_text = build_bluesky_text(library, detail_url)

    response = client.send_image(
        text=rich_text,
        image=img_data,
        image_alt=str(library),
    )

    rkey = response.uri.split("/")[-1]
    handle = settings.BLUESKY_HANDLE
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def _extract_url(text: str) -> str:
    """Extract the first https:// URL from post text.
    Used to identify the detail link for facet annotation."""
    for word in text.split():
        if word.startswith("https://"):
            return word
    return ""
