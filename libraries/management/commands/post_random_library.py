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

PLATFORM_CHOICES = ("mastodon", "bluesky", "instagram")


class Command(BaseCommand):
    """Post a random approved library with a photo to social media.
    Tracks posted libraries to avoid repeats."""

    help = "Post a random approved library to social media"

    def add_arguments(self, parser):
        """Register command-line flags for the management command.
        Adds the dry-run and only options."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build post text and print it without actually posting",
        )
        parser.add_argument(
            "--only",
            choices=PLATFORM_CHOICES,
            help="Post to a single platform only (skip all others)",
        )

    def handle(self, *args, **options):
        """Execute the social media posting workflow.
        Selects an unposted library, posts to configured platforms, and records results."""
        dry_run = options["dry_run"]
        only_platform = options["only"]

        mastodon_configured = bool(
            getattr(settings, "MASTODON_INSTANCE_URL", "")
            and getattr(settings, "MASTODON_ACCESS_TOKEN", "")
        )
        bluesky_configured = bool(
            getattr(settings, "BLUESKY_HANDLE", "")
            and getattr(settings, "BLUESKY_APP_PASSWORD", "")
        )
        instagram_configured = self._is_instagram_configured()

        if only_platform:
            if only_platform == "mastodon":
                bluesky_configured = False
                instagram_configured = False
            elif only_platform == "bluesky":
                mastodon_configured = False
                instagram_configured = False
            elif only_platform == "instagram":
                mastodon_configured = False
                bluesky_configured = False

        if not dry_run and not mastodon_configured and not bluesky_configured and not instagram_configured:
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

        # Access the photo from storage (needed for both AI analysis and posting)
        image_path = self._get_photo_path(library)

        # AI image analysis (best-effort, never blocks posting)
        ai_result = None
        if getattr(settings, "OPENROUTER_API_KEY", "") and image_path:
            from libraries.social.image_ai import analyze_library_image

            ai_result = analyze_library_image(image_path, library)

        alt_text = ai_result["alt_text"] if ai_result else None
        ai_hashtags = ai_result["hashtags"] if ai_result else []

        post_text = build_post_text(
            library, detail_url, extra_hashtags=ai_hashtags or None,
        )
        instagram_text = build_post_text(
            library, detail_url, max_length=2200,
            extra_hashtags=ai_hashtags or None, max_hashtags=5,
        )

        if dry_run:
            self.stdout.write(f"Library: {library}")
            self.stdout.write(f"URL: {detail_url}")
            self.stdout.write(f"Text ({len(post_text)} chars):\n{post_text}")
            if instagram_configured:
                self.stdout.write(
                    f"\nInstagram text ({len(instagram_text)} chars):\n{instagram_text}"
                )
            if ai_result:
                self.stdout.write(f"\nAI alt text: {alt_text}")
                self.stdout.write(f"AI hashtags: {ai_hashtags}")
            else:
                self.stdout.write("\nAI analysis: skipped (no API key or failed)")
            return

        if not image_path:
            logger.error("Could not access photo for library %s", library.pk)
            return

        mastodon_url = ""
        bluesky_url = ""
        instagram_url = ""
        errors = []

        try:
            if mastodon_configured:
                mastodon_url = self._post_to_mastodon(
                    library, post_text, image_path, alt_text=alt_text,
                )
                logger.info("Posted to Mastodon: %s", mastodon_url)
            else:
                logger.info("Mastodon not configured, skipping")
        except Exception as exc:
            logger.exception("Mastodon posting failed for library %s", library.pk)
            errors.append(f"Mastodon: {exc}")

        try:
            if bluesky_configured:
                bluesky_url = self._post_to_bluesky(
                    library, post_text, image_path,
                    alt_text=alt_text, extra_hashtags=ai_hashtags or None,
                )
                logger.info("Posted to Bluesky: %s", bluesky_url)
            else:
                logger.info("Bluesky not configured, skipping")
        except Exception as exc:
            logger.exception("Bluesky posting failed for library %s", library.pk)
            errors.append(f"Bluesky: {exc}")

        try:
            if instagram_configured:
                instagram_url = self._post_to_instagram(library, instagram_text, image_path)
                logger.info("Posted to Instagram: %s", instagram_url)
            else:
                logger.info("Instagram not configured, skipping")
        except Exception as exc:
            logger.exception("Instagram posting failed for library %s", library.pk)
            errors.append(f"Instagram: {exc}")

        # Clean up temp file if we created one
        if image_path and not library.photo.storage.exists(library.photo.name):
            image_path.unlink(missing_ok=True)

        if mastodon_url or bluesky_url or instagram_url:
            social_post = SocialPost.objects.create(
                library=library,
                post_text=post_text,
                mastodon_url=mastodon_url,
                bluesky_url=bluesky_url,
                instagram_url=instagram_url,
            )
            self.stdout.write(f"Posted library: {library}")
            if mastodon_url:
                self.stdout.write(f"  Mastodon: {mastodon_url}")
            if bluesky_url:
                self.stdout.write(f"  Bluesky: {bluesky_url}")
            if instagram_url:
                self.stdout.write(f"  Instagram: {instagram_url}")
            notify_social_post(social_post)

        if errors:
            error_details = "\n".join(errors)
            self.stderr.write(f"Errors:\n{error_details}")
            notify_social_post_error(library, error_details)

    def _is_instagram_configured(self) -> bool:
        """Check whether Instagram credentials are available.
        Checks both env var and DB-stored token."""
        from libraries.models import InstagramToken

        if not getattr(settings, "INSTAGRAM_USER_ID", ""):
            return False
        if getattr(settings, "INSTAGRAM_ACCESS_TOKEN", ""):
            return True
        return InstagramToken.objects.exists()

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

    def _post_to_mastodon(
        self, library, text: str, image_path: Path, *, alt_text: str | None = None,
    ) -> str:
        """Delegate to the Mastodon client module.
        Returns the URL of the created status."""
        from libraries.social.mastodon import post_library

        return post_library(library, text=text, image_path=image_path, alt_text=alt_text)

    def _post_to_bluesky(
        self,
        library,
        text: str,
        image_path: Path,
        *,
        alt_text: str | None = None,
        extra_hashtags: list[str] | None = None,
    ) -> str:
        """Delegate to the Bluesky client module.
        Returns the URL of the created post."""
        from libraries.social.bluesky import post_library

        return post_library(
            library, text=text, image_path=image_path,
            alt_text=alt_text, extra_hashtags=extra_hashtags,
        )

    def _post_to_instagram(self, library, text: str, image_path: Path) -> str:
        """Delegate to the Instagram client module.
        Returns the permalink of the created post."""
        from libraries.social.instagram import post_library

        return post_library(library, text=text, image_path=image_path)
