from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser
from django.http import HttpRequest
from ninja_jwt.authentication import JWTBaseAuthentication
from ninja_jwt.exceptions import AuthenticationFailed, InvalidToken


def get_optional_jwt_user(*, request: HttpRequest) -> AbstractBaseUser | None:
    """Extract and validate a JWT bearer token if present on the request.
    Returns None silently when the token is missing or invalid."""
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return None

    raw_token = auth_header[len("Bearer "):]
    if not raw_token:
        return None

    try:
        validated_token = JWTBaseAuthentication.get_validated_token(raw_token)
        return JWTBaseAuthentication().get_user(validated_token)
    except (InvalidToken, AuthenticationFailed):
        return None
