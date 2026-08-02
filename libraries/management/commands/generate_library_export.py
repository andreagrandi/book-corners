"""Generate the changed-only approved-library GeoJSON export."""

from __future__ import annotations

import structlog
from django.core.management.base import BaseCommand, CommandError

from libraries.library_export import generate_library_export

logger = structlog.get_logger(__name__)


class Command(BaseCommand):
    """Generate and conditionally publish a library GeoJSON export.
    Reports whether data changed, stayed unchanged, or was already locked.
    """

    help = "Generate a changed-only GeoJSON export of approved libraries."

    def handle(self, *args: object, **options: object) -> None:
        """Execute the export generator and report its concise outcome.
        Raises a command error after logging failures for scheduled-job visibility.
        """
        try:
            result = generate_library_export()
        except Exception as exc:
            logger.exception("library_export_failed")
            raise CommandError("Library export generation failed.") from exc

        if result.outcome == "locked":
            self.stdout.write("Library export generation is already running; skipped.")
            return
        if result.outcome == "unchanged":
            self.stdout.write(
                f"Library export unchanged: {result.geojson_filename} "
                f"({result.record_count} libraries)."
            )
            return
        self.stdout.write(
            f"Library export published: {result.geojson_filename} "
            f"({result.record_count} libraries)."
        )
