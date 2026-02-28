"""Instagram posting client for library social media sharing."""

from __future__ import annotations

import logging
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.instagram.com"


def _get_access_token() -> str:
    """Return the current Instagram access token.
    Prefers the DB-stored token, falls back to the env var for bootstrap."""
    from libraries.models import InstagramToken

    token_row = InstagramToken.objects.order_by("-refreshed_at").first()
    if token_row:
        return token_row.access_token
    return settings.INSTAGRAM_ACCESS_TOKEN


def post_library(library, text: str, image_path: Path) -> str:
    """Post a library with photo to Instagram and return the permalink.
    Uses the two-step container publish flow via the Instagram Graph API."""
    user_id = settings.INSTAGRAM_USER_ID
    access_token = _get_access_token()
    base_url = getattr(settings, "SITE_URL", "http://localhost:8000").rstrip("/")
    image_url = f"{base_url}{library.photo.url}"

    # Step 1: Create media container
    container_response = requests.post(
        f"{GRAPH_API_URL}/{user_id}/media",
        data={
            "image_url": image_url,
            "caption": text,
            "access_token": access_token,
        },
        timeout=60,
    )
    container_response.raise_for_status()
    creation_id = container_response.json()["id"]

    # Step 2: Publish the container
    publish_response = requests.post(
        f"{GRAPH_API_URL}/{user_id}/media_publish",
        data={
            "creation_id": creation_id,
            "access_token": access_token,
        },
        timeout=60,
    )
    publish_response.raise_for_status()
    media_id = publish_response.json()["id"]

    # Step 3: Get the permalink
    permalink_response = requests.get(
        f"{GRAPH_API_URL}/{media_id}",
        params={
            "fields": "permalink",
            "access_token": access_token,
        },
        timeout=30,
    )
    permalink_response.raise_for_status()
    return permalink_response.json()["permalink"]
