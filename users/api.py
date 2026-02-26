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
    """JWT access and refresh token pair.
    Returned on successful registration or login."""

    access: str = Field(description="Short-lived JWT access token.", examples=["eyJhbGciOiJIUzI1NiIs..."])
    refresh: str = Field(description="Long-lived JWT refresh token.", examples=["eyJhbGciOiJIUzI1NiIs..."])


class AccessTokenOut(Schema):
    """Single JWT access token.
    Returned when refreshing an expired access token."""

    access: str = Field(description="Short-lived JWT access token.", examples=["eyJhbGciOiJIUzI1NiIs..."])


class RegisterIn(Schema):
    """Registration payload for creating a new user account.
    All fields are required and validated server-side."""

    username: str = Field(min_length=3, max_length=150, description="Unique username (3-150 characters).", examples=["janedoe"])
    password: str = Field(min_length=8, max_length=128, description="Password (8-128 characters, validated against Django password policies).", examples=["s3cure!Pass"])
    email: str = Field(min_length=3, max_length=254, description="Email address.", examples=["jane@example.com"])


class LoginIn(Schema):
    """Login payload for authenticating with credentials.
    Accepts username and password."""

    username: str = Field(min_length=1, max_length=254, description="Username or email used during registration.", examples=["janedoe"])
    password: str = Field(min_length=1, max_length=128, description="Account password.", examples=["s3cure!Pass"])


class RefreshIn(Schema):
    """Token refresh payload containing the refresh token.
    Used to obtain a new access token without re-authenticating."""

    refresh: str = Field(min_length=20, description="Refresh token obtained from login or registration.", examples=["eyJhbGciOiJIUzI1NiIs..."])


class MeOut(Schema):
    """Current authenticated user profile.
    Returns basic account information."""

    id: int = Field(description="Unique user identifier.", examples=[1])
    username: str = Field(description="Username.", examples=["janedoe"])
    email: str = Field(description="Email address.", examples=["jane@example.com"])


def build_token_pair(*, user: AbstractBaseUser) -> TokenPairOut:
    """Create a JWT access/refresh token pair for a user.
    Wraps ninja-jwt's RefreshToken into the API response schema."""
    refresh = RefreshToken.for_user(user)
    return TokenPairOut(
        access=str(refresh.access_token),
        refresh=str(refresh),
    )


@auth_router.post("/register", response={201: TokenPairOut, 400: ErrorOut, 429: ErrorOut}, auth=None, summary="Register a new user")
def register(request, payload: RegisterIn):
    """Create a new user account and return a JWT token pair.
    Validates username uniqueness, email format, and password strength."""
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


@auth_router.post("/login", response={200: TokenPairOut, 401: ErrorOut, 429: ErrorOut}, auth=None, summary="Log in with credentials")
def login(request, payload: LoginIn):
    """Authenticate with username and password and return a JWT token pair.
    Returns 401 for invalid credentials or 429 when rate-limited."""
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


@auth_router.post("/refresh", response={200: AccessTokenOut, 401: ErrorOut, 429: ErrorOut}, auth=None, summary="Refresh an access token")
def refresh(request, payload: RefreshIn):
    """Exchange a valid refresh token for a new access token.
    Returns 401 if the refresh token is invalid or expired."""
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


@auth_router.get("/me", response=MeOut, auth=JWTAuth(), summary="Get current user profile")
def me(request):
    """Return the profile of the currently authenticated user.
    Requires a valid JWT access token in the Authorization header."""
    return MeOut(
        id=request.user.id,
        username=request.user.username,
        email=request.user.email,
    )
