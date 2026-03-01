"""Management command to set or replace the Instagram access token."""

from __future__ import annotations

import logging

import requests
from django.core.management.base import BaseCommand, CommandError

from libraries.models import InstagramToken

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.instagram.com"


class Command(BaseCommand):
    """Store a new Instagram access token in the database.
    Validates against the Graph API unless --skip-validation is passed."""

    help = "Set or replace the Instagram access token in the database"

    def add_arguments(self, parser):
        """Register command-line arguments.
        Accepts the token string and an optional validation bypass flag."""
        parser.add_argument(
            "token",
            type=str,
            help="The long-lived Instagram access token",
        )
        parser.add_argument(
            "--skip-validation",
            action="store_true",
            help="Store the token without validating it against the Graph API",
        )

    def handle(self, *args, **options):
        """Validate and store the Instagram token.
        Replaces all existing tokens with the new one."""
        token = options["token"].strip()
        skip_validation = options["skip_validation"]

        if not token:
            raise CommandError("Token must not be empty")

        if not skip_validation:
            self._validate_token(token)

        deleted_count, _ = InstagramToken.objects.all().delete()
        InstagramToken.objects.create(access_token=token)

        if deleted_count:
            self.stdout.write(f"Replaced {deleted_count} existing token(s)")
        self.stdout.write("Instagram token stored successfully")

    def _validate_token(self, token: str) -> None:
        """Check the token against the Graph API /me endpoint.
        Raises CommandError if the token is invalid or the request fails."""
        try:
            response = requests.get(
                f"{GRAPH_API_URL}/me",
                params={"access_token": token},
                timeout=15,
            )
            if not response.ok:
                detail = response.text
                try:
                    detail = response.json().get("error", {}).get("message", detail)
                except Exception:
                    pass
                raise CommandError(
                    f"Token validation failed ({response.status_code}): {detail}"
                )
            user_id = response.json().get("id", "unknown")
            self.stdout.write(f"Token validated for user ID: {user_id}")
        except requests.RequestException as exc:
            raise CommandError(f"Token validation request failed: {exc}") from exc
