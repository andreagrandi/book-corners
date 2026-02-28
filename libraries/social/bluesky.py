"""Bluesky posting client for library social media sharing."""

from __future__ import annotations

import logging
from pathlib import Path

from atproto import Client
from django.conf import settings

logger = logging.getLogger(__name__)


def post_library(library, text: str, image_path: Path) -> str:
    """Post a library with photo to Bluesky and return the post URL.
    Uses the high-level send_image method for simplicity."""
    client = Client()
    client.login(settings.BLUESKY_HANDLE, settings.BLUESKY_APP_PASSWORD)

    with open(image_path, "rb") as f:
        img_data = f.read()

    response = client.send_image(
        text=text,
        image=img_data,
        image_alt=str(library),
    )

    rkey = response.uri.split("/")[-1]
    handle = settings.BLUESKY_HANDLE
    return f"https://bsky.app/profile/{handle}/post/{rkey}"
