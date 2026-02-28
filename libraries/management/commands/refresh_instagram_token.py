"""Management command to refresh the Instagram long-lived access token."""

from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from libraries.models import InstagramToken

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.instagram.com"


class Command(BaseCommand):
    """Refresh the Instagram long-lived token before it expires.
    Stores the new token in the database and notifies on failure."""

    help = "Refresh the Instagram long-lived access token"

    def handle(self, *args, **options):
        """Execute the token refresh workflow.
        Reads the current token, exchanges it for a new one, and stores the result."""
        current_token = self._get_current_token()
        if not current_token:
            self.stdout.write("No Instagram token configured, skipping")
            return

        try:
            new_token = self._refresh_token(current_token)
        except Exception as exc:
            logger.exception("Instagram token refresh failed")
            self._notify_failure(str(exc))
            self.stderr.write(f"Token refresh failed: {exc}")
            return

        InstagramToken.objects.all().delete()
        InstagramToken.objects.create(access_token=new_token)
        self.stdout.write("Instagram token refreshed successfully")

    def _get_current_token(self) -> str:
        """Return the current access token from DB or env var.
        Prefers the DB-stored token for continuity after previous refreshes."""
        token_row = InstagramToken.objects.order_by("-refreshed_at").first()
        if token_row:
            return token_row.access_token
        return getattr(settings, "INSTAGRAM_ACCESS_TOKEN", "")

    def _refresh_token(self, token: str) -> str:
        """Exchange a long-lived token for a new long-lived token.
        Calls the Instagram Graph API refresh endpoint."""
        response = requests.get(
            f"{GRAPH_API_URL}/refresh_access_token",
            params={
                "grant_type": "ig_refresh_token",
                "access_token": token,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _notify_failure(self, error_details: str) -> None:
        """Send an admin email about the token refresh failure.
        Fails silently so email outages never block the command."""
        from libraries.notifications import _get_admin_email

        recipient = _get_admin_email()
        if not recipient:
            return

        try:
            from django.core.mail import send_mail

            send_mail(
                subject="Instagram token refresh failed",
                message=(
                    f"The Instagram access token could not be refreshed.\n\n"
                    f"Error: {error_details}\n\n"
                    f"The token may expire soon. Please refresh it manually "
                    f"via the Meta developer dashboard."
                ),
                from_email=None,
                recipient_list=[recipient],
            )
        except Exception:
            logger.exception("Failed to send token refresh failure notification")
