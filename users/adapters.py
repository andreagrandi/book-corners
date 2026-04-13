import re
import unicodedata

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

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

    def is_open_for_signup(self, request, sociallogin):
        """Allow new users to sign up via any configured social provider."""
        return True

    def populate_user(self, request, sociallogin, data):
        """Normalize email to lowercase before allauth processes it.
        Prevents case-mismatch issues with our unique email constraint."""
        user = super().populate_user(request, sociallogin, data)
        if user.email:
            user.email = user.email.lower()
        return user

    def save_user(self, request, sociallogin, form=None):
        """Save a new social signup inside a transaction.
        Recovers from race conditions on email or username collisions."""
        try:
            with transaction.atomic():
                user = super().save_user(request, sociallogin, form=form)
            provider_id = sociallogin.account.provider
            via = _PROVIDER_LABELS.get(provider_id, provider_id.title())
            notify_new_registration(user, via=via)
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
