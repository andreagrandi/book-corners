from django.contrib.auth import get_user_model

from allauth.socialaccount.models import SocialAccount

User = get_user_model()


def resolve_login_identifier(identifier: str) -> str:
    """Resolve a login identifier to a username.
    If the identifier contains '@', look up the user by email (case-insensitive)."""
    if "@" in identifier:
        matching_user = User.objects.filter(email__iexact=identifier).first()
        if matching_user is not None:
            return matching_user.get_username()
    return identifier


def is_social_only_user(user) -> bool:
    """Check whether a user signed up exclusively via a social provider.
    Returns True when they have a linked social account but no usable password."""
    if user.has_usable_password():
        return False
    return SocialAccount.objects.filter(user=user).exists()
