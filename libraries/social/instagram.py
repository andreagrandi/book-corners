"""Instagram posting client for library social media sharing."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.instagram.com"
CONTAINER_POLL_INTERVAL = 5  # seconds between status checks
CONTAINER_POLL_MAX_ATTEMPTS = 12  # up to 60 seconds total


def _raise_with_detail(response: requests.Response) -> None:
    """Raise an exception with the Instagram API error message included.
    The default raise_for_status only shows the HTTP status code."""
    if response.ok:
        return
    try:
        detail = response.json().get("error", {}).get("message", response.text)
    except Exception:
        detail = response.text
    raise RuntimeError(
        f"Instagram API {response.status_code}: {detail} "
        f"(URL: {response.url})"
    )


def _get_access_token() -> str:
    """Return the current Instagram access token.
    Prefers the DB-stored token, falls back to the env var for bootstrap."""
    from libraries.models import InstagramToken

    token_row = InstagramToken.objects.order_by("-refreshed_at").first()
    if token_row:
        return token_row.access_token
    return settings.INSTAGRAM_ACCESS_TOKEN


def _wait_for_container(container_id: str, access_token: str) -> None:
    """Poll the container status until it is ready to publish.
    Raises RuntimeError if the container fails or times out."""
    for attempt in range(CONTAINER_POLL_MAX_ATTEMPTS):
        response = requests.get(
            f"{GRAPH_API_URL}/{container_id}",
            params={
                "fields": "status_code",
                "access_token": access_token,
            },
            timeout=30,
        )
        response.raise_for_status()
        status = response.json().get("status_code")
        logger.info("Container %s status: %s (attempt %d)", container_id, status, attempt + 1)

        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container {container_id} failed with status: {status}")

        time.sleep(CONTAINER_POLL_INTERVAL)

    raise RuntimeError(f"Instagram container {container_id} timed out waiting to become FINISHED")


def post_library(library, text: str, image_path: Path) -> str:
    """Post a library with photo to Instagram and return the permalink.
    Uses the two-step container publish flow via the Instagram Graph API."""
    from libraries.image_processing import ensure_instagram_aspect_ratio

    ensure_instagram_aspect_ratio(library=library)

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
    _raise_with_detail(container_response)
    creation_id = container_response.json()["id"]
    logger.info("Created Instagram container: %s", creation_id)

    # Step 2: Wait for container to be ready
    _wait_for_container(creation_id, access_token)

    # Step 3: Publish the container
    publish_response = requests.post(
        f"{GRAPH_API_URL}/{user_id}/media_publish",
        data={
            "creation_id": creation_id,
            "access_token": access_token,
        },
        timeout=60,
    )
    _raise_with_detail(publish_response)
    media_id = publish_response.json()["id"]
    logger.info("Published Instagram media: %s", media_id)

    # Step 4: Get the permalink
    permalink_response = requests.get(
        f"{GRAPH_API_URL}/{media_id}",
        params={
            "fields": "permalink",
            "access_token": access_token,
        },
        timeout=30,
    )
    _raise_with_detail(permalink_response)
    return permalink_response.json()["permalink"]
