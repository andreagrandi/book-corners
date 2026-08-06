import re
import unicodedata

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.providers.base import AuthProcess

from users.contributor_agreements import (
    WEB_SOCIAL_REGISTRATION_CHANNELS,
    WEB_SOCIAL_REGISTRATION_FLOW,
    record_current_acceptance,
    validate_web_social_registration_state,
)
from users.notifications import notify_new_registration

User = get_user_model()

_PROVIDER_LABELS = {
    "google": "Google OAuth",
    "apple": "Apple Sign In",
}


def _normalize(text):
    """Strip to ASCII lowercase, replace non-alphanumeric with underscores."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]", "_", text.lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _generate_username(txts):
    """Build a unique username from candidate text values.
    Tries first_last, then email prefix, then 'user' as base."""
    first_name = _normalize(txts[0]) if len(txts) > 0 else ""
    last_name = _normalize(txts[1]) if len(txts) > 1 else ""
    email = txts[2] if len(txts) > 2 else ""

    # Candidate 1: first_last or just first or just last
    if first_name and last_name:
        base = f"{first_name}_{last_name}"
    elif first_name:
        base = first_name
    elif last_name:
        base = last_name
    else:
        base = ""

    # Candidate 2: email prefix
    if not base and email:
        base = _normalize(email.split("@")[0])

    # Candidate 3: fallback
    if not base:
        base = "user"

    # Truncate to leave room for suffix (Django username max_length=150)
    base = base[:147]

    # Check uniqueness, add progressive suffix if needed
    candidate = base
    if not User.objects.filter(username=candidate).exists():
        return candidate

    counter = 1
    while True:
        candidate = f"{base}{counter:03d}"
        if not User.objects.filter(username=candidate).exists():
            return candidate
        counter += 1


class AccountAdapter(DefaultAccountAdapter):
    """Custom account adapter for allauth.
    Generates readable usernames and redirects to home after login."""

    def generate_unique_username(self, txts, regex=None):
        """Generate a readable unique username from profile data.
        Uses first_last format with progressive numeric suffixes."""
        return _generate_username(txts)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom social account adapter for Google and Apple sign-in.
    Normalizes emails and allows social signup."""

    def pre_social_login(self, request, sociallogin):
        """Require valid signup intent only when OAuth would create a web account.
        Leaves existing login, account connection, and redirect processes unchanged."""
        super().pre_social_login(request, sociallogin)
        process = sociallogin.state.get("process", AuthProcess.LOGIN)
        if process != AuthProcess.LOGIN or sociallogin.is_existing:
            return
        agreement_error = validate_web_social_registration_state(
            state=sociallogin.state,
            provider=sociallogin.account.provider,
        )
        if agreement_error:
            messages.error(
                request,
                _(
                    "To create a new account with social login, start from registration and accept the contributor agreement."
                ),
            )
            raise ImmediateHttpResponse(redirect("register"))

    def is_open_for_signup(self, request, sociallogin):
        """Allow new web social signups only with valid stashed acceptance.
        Keeps non-login allauth processes and direct adapter compatibility intact."""
        if sociallogin is None:
            return super().is_open_for_signup(request, sociallogin)
        process = sociallogin.state.get("process", AuthProcess.LOGIN)
        if process != AuthProcess.LOGIN:
            return super().is_open_for_signup(request, sociallogin)
        agreement_error = validate_web_social_registration_state(
            state=sociallogin.state,
            provider=sociallogin.account.provider,
        )
        return agreement_error is None

    def populate_user(self, request, sociallogin, data):
        """Normalize email to lowercase before allauth processes it.
        Prevents case-mismatch issues with our unique email constraint."""
        user = super().populate_user(request, sociallogin, data)
        if user.email:
            user.email = user.email.lower()
        return user

    def save_user(self, request, sociallogin, form=None):
        """Save a new social signup and any explicit web acceptance atomically.
        Recovers from races while leaving native API acceptance to its own contract."""
        provider_id = sociallogin.account.provider
        state_data = sociallogin.state.get("data")
        is_web_registration = (
            isinstance(state_data, dict)
            and state_data.get("flow") == WEB_SOCIAL_REGISTRATION_FLOW
        )
        if is_web_registration:
            agreement_error = validate_web_social_registration_state(
                state=sociallogin.state,
                provider=provider_id,
            )
            if agreement_error:
                raise ValueError(agreement_error)

        try:
            with transaction.atomic():
                user = super().save_user(request, sociallogin, form=form)
                if is_web_registration:
                    record_current_acceptance(
                        user=user,
                        channel=WEB_SOCIAL_REGISTRATION_CHANNELS[provider_id],
                    )
            via = _PROVIDER_LABELS.get(provider_id, provider_id.title())
            transaction.on_commit(
                lambda user=user, via=via: notify_new_registration(user, via=via),
            )
            return user
        except IntegrityError:
            # Another request created the user between our check and insert.
            # Look up the winner by email and connect to them instead.
            email = sociallogin.user.email
            if email:
                existing_user = User.objects.filter(email=email).first()
                if existing_user:
                    sociallogin.connect(request, existing_user)
                    return existing_user
            # If no email match, re-raise — unexpected constraint violation.
            raise
