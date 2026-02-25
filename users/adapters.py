from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


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
