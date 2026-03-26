"""Background tasks for asynchronous library operations.

Decouples slow I/O (image fetching, AI enrichment) from request handling via django.tasks.
"""

from __future__ import annotations

import logging
from io import BytesIO

from django.conf import settings
from django.tasks import task

from libraries.geojson_import import GeoJSONImporter, fetch_image_from_url, parse_geojson
from libraries.image_processing import build_library_photo_files
from libraries.models import Library
from libraries.notifications import notify_new_library
from libraries.storage import get_library_photo_path

logger = logging.getLogger(__name__)


@task()
def enrich_library_with_ai(library_id: int) -> None:
    """Run AI image analysis on a submitted library and send admin notification.
    Fills blank name/description from AI, then always notifies the admin."""
    try:
        library = Library.objects.get(pk=library_id)
    except Library.DoesNotExist:
        logger.warning("Library %d not found, skipping AI enrichment", library_id)
        return

    if not library.photo or not getattr(settings, "OPENROUTER_API_KEY", ""):
        notify_new_library(library)
        return

    image_path = get_library_photo_path(library)
    if not image_path:
        notify_new_library(library)
        return

    update_fields: list[str] = []
    try:
        from libraries.social.image_ai import enrich_library_from_image

        result = enrich_library_from_image(image_path=image_path, library=library)
        if result:
            if not library.name and result["name"]:
                library.name = result["name"]
                update_fields.append("name")
            if not library.description and result["description"]:
                library.description = result["description"]
                update_fields.append("description")
    except Exception:
        logger.exception("AI enrichment failed for library %d", library_id)

    if update_fields:
        library.save(update_fields=update_fields)

    notify_new_library(library)

    # Clean up temp file if storage created one
    try:
        from pathlib import Path

        storage = library.photo.storage
        if hasattr(storage, "path"):
            try:
                local_path = Path(storage.path(library.photo.name))
                if local_path == image_path:
                    return  # Not a temp file
            except NotImplementedError:
                pass
        image_path.unlink(missing_ok=True)
    except Exception:
        pass


@task()
def run_geojson_import(geojson_path: str, source: str, status: str, user_id: int) -> None:
    """Run a full GeoJSON import in the background.
    Reads the GeoJSON file from disk, creates libraries, and cleans up."""
    import json
    from pathlib import Path

    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("User %d not found, aborting GeoJSON import", user_id)
        return

    file_path = Path(geojson_path)
    try:
        geojson_data = json.loads(file_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error("Failed to read GeoJSON file %s: %s", geojson_path, exc)
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

    file_path.unlink(missing_ok=True)


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
