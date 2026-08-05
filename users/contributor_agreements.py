"""Helpers for current contributor-agreement metadata and audit records."""

from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.urls import reverse

from users.models import ContributorAgreementAcceptance

CURRENT_CONTRIBUTOR_AGREEMENT_VERSION = "1.0"
CURRENT_CONTRIBUTOR_AGREEMENT_URL_NAME = "contributor_agreement_1_0_en"


def validate_acceptance(
    *,
    agreement_version: str | None,
    agreement_accepted: bool | None,
) -> str | None:
    """Validate an explicit acceptance of the current agreement.
    Returns a client-safe error message or None when the acceptance is valid."""
    if agreement_accepted is None or agreement_version is None:
        return "Contributor agreement acceptance is required."
    if agreement_accepted is not True:
        return "Contributor agreement acceptance must be true."
    if agreement_version != CURRENT_CONTRIBUTOR_AGREEMENT_VERSION:
        return "Contributor agreement version is not current."
    return None


def current_agreement_url() -> str:
    """Return the absolute URL for the immutable current agreement copy.
    Uses the configured site URL rather than trusting an incoming host header."""
    site_url = getattr(settings, "SITE_URL", "http://localhost:8000").rstrip("/")
    return f"{site_url}{reverse(CURRENT_CONTRIBUTOR_AGREEMENT_URL_NAME)}"


def has_current_acceptance(*, user: AbstractBaseUser) -> bool:
    """Return whether the user accepted the deployed agreement version.
    Checks the exact current version instead of assuming the newest record is valid."""
    return ContributorAgreementAcceptance.objects.filter(
        user=user,
        agreement_version=CURRENT_CONTRIBUTOR_AGREEMENT_VERSION,
    ).exists()


def record_current_acceptance(
    *,
    user: AbstractBaseUser,
    channel: ContributorAgreementAcceptance.Channel | str,
) -> ContributorAgreementAcceptance:
    """Record current-version acceptance without changing an existing audit row.
    The user, timestamp, and channel are supplied by server-side flow logic."""
    acceptance, _ = ContributorAgreementAcceptance.objects.get_or_create(
        user=user,
        agreement_version=CURRENT_CONTRIBUTOR_AGREEMENT_VERSION,
        defaults={"channel": channel},
    )
    return acceptance


def current_agreement_status(*, user: AbstractBaseUser) -> dict[str, Any]:
    """Build the authenticated current-agreement status payload.
    Keeps version, immutable URL, and currentness consistent across API responses."""
    return {
        "current_version": CURRENT_CONTRIBUTOR_AGREEMENT_VERSION,
        "agreement_url": current_agreement_url(),
        "is_current": has_current_acceptance(user=user),
    }
