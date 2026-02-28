"""Management command to post a random approved library to social media."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse

from libraries.models import Library, SocialPost
from libraries.notifications import notify_social_post, notify_social_post_error
from libraries.social.text import build_post_text

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Post a random approved library with a photo to Mastodon and Bluesky.
    Tracks posted libraries to avoid repeats."""

    help = "Post a random approved library to social media"

    def add_arguments(self, parser):
        """Register command-line flags for the management command.
        Adds the dry-run option to preview without posting."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build post text and print it without actually posting",
        )

    def handle(self, *args, **options):
        """Execute the social media posting workflow.
        Selects an unposted library, posts to configured platforms, and records results."""
        dry_run = options["dry_run"]

        mastodon_configured = bool(
            getattr(settings, "MASTODON_INSTANCE_URL", "")
            and getattr(settings, "MASTODON_ACCESS_TOKEN", "")
        )
        bluesky_configured = bool(
            getattr(settings, "BLUESKY_HANDLE", "")
            and getattr(settings, "BLUESKY_APP_PASSWORD", "")
        )

        if not dry_run and not mastodon_configured and not bluesky_configured:
            logger.info("No social media credentials configured, skipping")
            self.stdout.write("No social media credentials configured, skipping")
            return

        library = (
            Library.objects.filter(status=Library.Status.APPROVED)
            .exclude(photo="")
            .exclude(social_posts__isnull=False)
            .order_by("?")
            .first()
        )

        if not library:
            logger.info("All libraries have been posted")
            self.stdout.write("All libraries have been posted")
            return

        detail_path = reverse("library_detail", kwargs={"slug": library.slug})
        base_url = getattr(settings, "SITE_URL", "http://localhost:8000").rstrip("/")
        detail_url = f"{base_url}{detail_path}"
        post_text = build_post_text(library, detail_url)

        if dry_run:
            self.stdout.write(f"Library: {library}")
            self.stdout.write(f"URL: {detail_url}")
            self.stdout.write(f"Text ({len(post_text)} chars):\n{post_text}")
            return

        # Access the photo from storage
        image_path = self._get_photo_path(library)
        if not image_path:
            logger.error("Could not access photo for library %s", library.pk)
            return

        mastodon_url = ""
        bluesky_url = ""
        errors = []

        try:
            if mastodon_configured:
                mastodon_url = self._post_to_mastodon(library, post_text, image_path)
                logger.info("Posted to Mastodon: %s", mastodon_url)
            else:
                logger.info("Mastodon not configured, skipping")
        except Exception as exc:
            logger.exception("Mastodon posting failed for library %s", library.pk)
            errors.append(f"Mastodon: {exc}")

        try:
            if bluesky_configured:
                bluesky_url = self._post_to_bluesky(library, post_text, image_path)
                logger.info("Posted to Bluesky: %s", bluesky_url)
            else:
                logger.info("Bluesky not configured, skipping")
        except Exception as exc:
            logger.exception("Bluesky posting failed for library %s", library.pk)
            errors.append(f"Bluesky: {exc}")

        # Clean up temp file if we created one
        if image_path and not library.photo.storage.exists(library.photo.name):
            image_path.unlink(missing_ok=True)

        if mastodon_url or bluesky_url:
            social_post = SocialPost.objects.create(
                library=library,
                post_text=post_text,
                mastodon_url=mastodon_url,
                bluesky_url=bluesky_url,
            )
            self.stdout.write(f"Posted library: {library}")
            if mastodon_url:
                self.stdout.write(f"  Mastodon: {mastodon_url}")
            if bluesky_url:
                self.stdout.write(f"  Bluesky: {bluesky_url}")
            notify_social_post(social_post)

        if errors:
            error_details = "\n".join(errors)
            self.stderr.write(f"Errors:\n{error_details}")
            notify_social_post_error(library, error_details)

    def _get_photo_path(self, library) -> Path | None:
        """Retrieve the library photo to a local path for uploading.
        Writes storage-backed files to a temp file when needed."""
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

    def _post_to_mastodon(self, library, text: str, image_path: Path) -> str:
        """Delegate to the Mastodon client module.
        Returns the URL of the created status."""
        from libraries.social.mastodon import post_library

        return post_library(library, text=text, image_path=image_path)

    def _post_to_bluesky(self, library, text: str, image_path: Path) -> str:
        """Delegate to the Bluesky client module.
        Returns the URL of the created post."""
        from libraries.social.bluesky import post_library

        return post_library(library, text=text, image_path=image_path)
