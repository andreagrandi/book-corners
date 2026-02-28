"""Background tasks for asynchronous library operations.

Decouples slow I/O (image fetching) from request handling via django.tasks.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from django.tasks import task

from libraries.geojson_import import GeoJSONImporter, fetch_image_from_url, parse_geojson
from libraries.image_processing import build_library_photo_files
from libraries.models import Library

logger = logging.getLogger(__name__)


@task()
def run_geojson_import(geojson_data: dict[str, Any], source: str, status: str, user_id: int) -> None:
    """Run a full GeoJSON import in the background.
    Parses features, creates libraries, and logs the result summary."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("User %d not found, aborting GeoJSON import", user_id)
        return

    candidates = parse_geojson(geojson_data)
    importer = GeoJSONImporter(source=source, status=status, created_by=user)
    result = importer.run(candidates)

    logger.info(
        "GeoJSON import complete: %d created, %d skipped, %d errors",
        result.created,
        result.total_skipped,
        result.total_errors,
    )


@task()
def fetch_and_attach_library_image(library_id: int, image_url: str) -> None:
    """Fetch an image from a URL and attach it to a Library record.
    Runs as a background task so the import request returns immediately."""
    try:
        library = Library.objects.get(pk=library_id)
    except Library.DoesNotExist:
        logger.warning("Library %d not found, skipping image fetch", library_id)
        return

    image_data = fetch_image_from_url(image_url)
    if not image_data:
        return

    try:
        image_file = BytesIO(image_data)
        main_image, thumbnail_image = build_library_photo_files(
            image_file=image_file,
            original_name=image_url.rsplit("/", maxsplit=1)[-1] or "import.jpg",
        )
        main_filename, main_content = main_image
        thumbnail_filename, thumbnail_content = thumbnail_image
        library.photo.save(main_filename, main_content, save=False)
        library.photo_thumbnail.save(thumbnail_filename, thumbnail_content, save=False)
        library.save(update_fields=["photo", "photo_thumbnail"])
    except (ValueError, OSError) as exc:
        logger.warning(
            "Image processing failed for library %d (%s): %s",
            library_id,
            image_url,
            exc,
        )
