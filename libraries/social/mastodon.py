"""Mastodon posting client for library social media sharing."""

from __future__ import annotations

import logging
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def post_library(library, text: str, image_path: Path) -> str:
    """Post a library with photo to Mastodon and return the status URL.
    Uploads the image first, then creates a status with the media attached."""
    instance_url = settings.MASTODON_INSTANCE_URL.rstrip("/")
    access_token = settings.MASTODON_ACCESS_TOKEN
    headers = {"Authorization": f"Bearer {access_token}"}

    # Upload media
    with open(image_path, "rb") as f:
        media_response = requests.post(
            f"{instance_url}/api/v2/media",
            headers=headers,
            files={"file": (image_path.name, f, "image/jpeg")},
            data={"description": str(library)},
            timeout=60,
        )
    media_response.raise_for_status()
    media_id = media_response.json()["id"]

    # Create status
    status_response = requests.post(
        f"{instance_url}/api/v1/statuses",
        headers=headers,
        json={
            "status": text,
            "media_ids": [media_id],
            "visibility": "public",
        },
        timeout=30,
    )
    status_response.raise_for_status()
    return status_response.json()["url"]
