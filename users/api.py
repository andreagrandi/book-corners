from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from ninja import Router, Schema
from ninja_jwt.authentication import JWTAuth
from ninja_jwt.exceptions import TokenError
from ninja_jwt.tokens import RefreshToken
from pydantic import Field

from config.api_schemas import ErrorOut
from users.security import is_auth_rate_limited

User = get_user_model()

auth_router = Router(tags=["auth"])


class TokenPairOut(Schema):
    access: str
    refresh: str


class AccessTokenOut(Schema):
    access: str


class RegisterIn(Schema):
    username: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=8, max_length=128)
    email: str = Field(min_length=3, max_length=254)


class LoginIn(Schema):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class RefreshIn(Schema):
    refresh: str = Field(min_length=20)


class MeOut(Schema):
    id: int
    username: str
    email: str


def build_token_pair(*, user: AbstractBaseUser) -> TokenPairOut:
    """Handle build token pair.
    Supports the module workflow with a focused operation."""
    refresh = RefreshToken.for_user(user)
    return TokenPairOut(
        access=str(refresh.access_token),
        refresh=str(refresh),
    )


@auth_router.post("/register", response={201: TokenPairOut, 400: ErrorOut, 429: ErrorOut}, auth=None)
def register(request, payload: RegisterIn):
    """Handle register.
    Supports the module workflow with a focused operation."""
    limited, _ = is_auth_rate_limited(
        request=request,
        scope="api-register",
        max_attempts=settings.AUTH_RATE_LIMIT_REGISTER_ATTEMPTS,
    )
    if limited:
        return 429, {"message": "Too many registration attempts. Please try again later."}

    normalized_username = payload.username.strip()
    normalized_email = str(payload.email).strip().lower()

    try:
        validate_email(normalized_email)
    except DjangoValidationError:
        return 400, {"message": "Provide a valid email address."}

    if User.objects.filter(username__iexact=normalized_username).exists():
        return 400, {"message": "Username already exists."}

    if User.objects.filter(email__iexact=normalized_email).exists():
        return 400, {"message": "Email already exists."}

    try:
        validate_password(payload.password)
    except DjangoValidationError as error:
        message = str(error.messages[0]) if error.messages else "Password does not meet security requirements."
        return 400, {"message": message}

    user = User.objects.create_user(
        username=normalized_username,
        email=normalized_email,
        password=payload.password,
    )
    return 201, build_token_pair(user=user)


@auth_router.post("/login", response={200: TokenPairOut, 401: ErrorOut, 429: ErrorOut}, auth=None)
def login(request, payload: LoginIn):
    """Handle login.
    Supports the module workflow with a focused operation."""
    limited, _ = is_auth_rate_limited(
        request=request,
        scope="api-login",
        max_attempts=settings.AUTH_RATE_LIMIT_LOGIN_ATTEMPTS,
    )
    if limited:
        return 429, {"message": "Too many login attempts. Please try again later."}

    normalized_username = payload.username.strip()
    user = authenticate(
        request,
        username=normalized_username,
        password=payload.password,
    )
    if user is None:
        return 401, {"message": "Invalid credentials."}

    return 200, build_token_pair(user=user)


@auth_router.post("/refresh", response={200: AccessTokenOut, 401: ErrorOut, 429: ErrorOut}, auth=None)
def refresh(request, payload: RefreshIn):
    """Handle refresh.
    Supports the module workflow with a focused operation."""
    limited, _ = is_auth_rate_limited(
        request=request,
        scope="api-refresh",
        max_attempts=settings.AUTH_RATE_LIMIT_REFRESH_ATTEMPTS,
    )
    if limited:
        return 429, {"message": "Too many refresh attempts. Please try again later."}

    try:
        refresh_token = RefreshToken(payload.refresh)
    except TokenError:
        return 401, {"message": "Invalid or expired refresh token."}

    return 200, AccessTokenOut(access=str(refresh_token.access_token))


@auth_router.get("/me", response=MeOut, auth=JWTAuth())
def me(request):
    """Handle me.
    Supports the module workflow with a focused operation."""
    return MeOut(
        id=request.user.id,
        username=request.user.username,
        email=request.user.email,
    )
