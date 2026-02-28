"""Bluesky posting client for library social media sharing."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from atproto import Client, models
from django.conf import settings

logger = logging.getLogger(__name__)


def post_library(library, text: str, image_path: Path) -> str:
    """Post a library with photo to Bluesky and return the post URL.
    Uploads the image blob first, then creates the post record."""
    client = Client()
    client.login(settings.BLUESKY_HANDLE, settings.BLUESKY_APP_PASSWORD)

    with open(image_path, "rb") as f:
        img_data = f.read()

    upload = client.com.atproto.repo.upload_blob(img_data)
    images = [
        models.AppBskyEmbedImages.Image(
            alt=str(library),
            image=upload.blob,
        )
    ]
    embed = models.AppBskyEmbedImages.Main(images=images)

    response = client.com.atproto.repo.create_record(
        models.ComAtprotoRepoCreateRecord.Data(
            repo=client.me.did,
            collection="app.bsky.feed.post",
            record=models.AppBskyFeedPost.Main(
                created_at=datetime.now(tz=timezone.utc).isoformat(),
                text=text,
                embed=embed,
            ),
        )
    )

    # Build post URL from handle and rkey
    rkey = response.uri.split("/")[-1]
    handle = settings.BLUESKY_HANDLE
    return f"https://bsky.app/profile/{handle}/post/{rkey}"
