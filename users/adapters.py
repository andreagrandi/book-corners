from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

User = get_user_model()


class AccountAdapter(DefaultAccountAdapter):
    """Custom account adapter for allauth.
    Redirects to home after login, respecting the next parameter."""

    pass


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom social account adapter for Google OAuth.
    Normalizes emails and allows social signup."""

    def is_open_for_signup(self, request, sociallogin):
        """Allow new users to sign up via Google OAuth."""
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
                return super().save_user(request, sociallogin, form=form)
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
