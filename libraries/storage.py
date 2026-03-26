"""Shared utilities for accessing library photo files from storage."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def get_library_photo_path(library) -> Path | None:
    """Retrieve the library photo as a local filesystem path.
    Writes storage-backed files to a temp file when needed."""
    if not library.photo:
        return None

    try:
        storage = library.photo.storage
        if hasattr(storage, "path"):
            try:
                return Path(storage.path(library.photo.name))
            except NotImplementedError:
                pass

        # Fall back to reading from storage into a temp file
        with storage.open(library.photo.name, "rb") as f:
            content = f.read()

        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)
    except Exception:
        logger.exception("Failed to access photo for library %s", library.pk)
        return None
