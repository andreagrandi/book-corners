import structlog
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ImproperlyConfigured, ValidationError as DjangoValidationError
from django.core.validators import validate_email
from ninja import Router, Schema
from ninja_jwt.authentication import JWTAuth
from ninja_jwt.exceptions import TokenError
from ninja_jwt.tokens import RefreshToken
from pydantic import Field

from allauth.socialaccount.adapter import get_adapter as get_socialaccount_adapter

from config.api_schemas import ErrorOut
from users.auth import is_social_only_user, resolve_login_identifier
from users.security import is_auth_rate_limited

MessageOut = ErrorOut

logger = structlog.get_logger(__name__)

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
    Accepts username or email address plus password."""

    username: str = Field(min_length=1, max_length=254, description="Username or email address.", examples=["janedoe", "jane@example.com"])
    password: str = Field(min_length=1, max_length=128, description="Account password.", examples=["s3cure!Pass"])


class SocialLoginIn(Schema):
    """Social login payload for exchanging a native identity token for JWT.
    Used by iOS/Android apps that authenticate via Apple or Google SDKs."""

    provider: str = Field(description="Social provider: 'apple' or 'google'.")
    id_token: str = Field(min_length=20, description="Identity token JWT from the native SDK.")
    first_name: str = Field(default="", max_length=150, description="Optional first name (Apple only provides on first sign-in).")
    last_name: str = Field(default="", max_length=150, description="Optional last name (Apple only provides on first sign-in).")


class RefreshIn(Schema):
    """Token refresh payload containing the refresh token.
    Used to obtain a new access token without re-authenticating."""

    refresh: str = Field(min_length=20, description="Refresh token obtained from login or registration.", examples=["eyJhbGciOiJIUzI1NiIs..."])


class MeOut(Schema):
    """Current authenticated user profile.
    Returns basic account information and authentication type."""

    id: int = Field(description="Unique user identifier.", examples=[1])
    username: str = Field(description="Username.", examples=["janedoe"])
    email: str = Field(description="Email address.", examples=["jane@example.com"])
    is_social_only: bool = Field(description="True when the account uses social login only (no local password). Email and password changes are unavailable for these accounts.", examples=[False])


def build_token_pair(*, user: AbstractBaseUser) -> TokenPairOut:
    """Create a JWT access/refresh token pair for a user.
    Wraps ninja-jwt's RefreshToken into the API response schema."""
    refresh = RefreshToken.for_user(user)
    return TokenPairOut(
        access=str(refresh.access_token),
        refresh=str(refresh),
    )


_SUPPORTED_SOCIAL_PROVIDERS = frozenset({"apple", "google"})


@auth_router.post("/social", response={200: TokenPairOut, 400: ErrorOut, 429: ErrorOut}, auth=None, summary="Social login with native identity token")
def social_login(request, payload: SocialLoginIn):
    """Exchange a native Apple/Google identity token for a JWT token pair.
    Creates or links accounts automatically based on email matching."""
    limited, _ = is_auth_rate_limited(
        request=request,
        scope="api-social",
        max_attempts=settings.AUTH_RATE_LIMIT_SOCIAL_ATTEMPTS,
    )
    if limited:
        return 429, {"message": "Too many social login attempts. Please try again later."}

    if payload.provider not in _SUPPORTED_SOCIAL_PROVIDERS:
        return 400, {"message": "Unsupported provider. Use 'apple' or 'google'."}

    try:
        adapter = get_socialaccount_adapter()
        provider = adapter.get_provider(request, payload.provider)
        sociallogin = provider.verify_token(request, {"id_token": payload.id_token})
    except (DjangoValidationError, ImproperlyConfigured):
        return 400, {"message": "Invalid identity token."}

    sociallogin.lookup()

    if sociallogin.is_existing:
        user = sociallogin.user
        # Email match without linked social account — connect it now
        if not sociallogin.account.pk:
            sociallogin.connect(request, user)
        return 200, build_token_pair(user=user)

    # New user — set name from Apple first sign-in before saving
    if payload.first_name:
        sociallogin.user.first_name = payload.first_name
    if payload.last_name:
        sociallogin.user.last_name = payload.last_name

    user = adapter.save_user(request, sociallogin)
    logger.info("social_login_new_user", provider=payload.provider, user_id=user.pk)
    return 200, build_token_pair(user=user)


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
    """Authenticate with username or email and return a JWT token pair.
    Returns 401 for invalid credentials or 429 when rate-limited."""
    limited, _ = is_auth_rate_limited(
        request=request,
        scope="api-login",
        max_attempts=settings.AUTH_RATE_LIMIT_LOGIN_ATTEMPTS,
    )
    if limited:
        return 429, {"message": "Too many login attempts. Please try again later."}

    identifier = payload.username.strip()
    username = resolve_login_identifier(identifier)
    user = authenticate(
        request,
        username=username,
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
        is_social_only=is_social_only_user(request.user),
    )


class ChangeEmailIn(Schema):
    """Payload for changing the user's email address.
    The new email must be unique across all accounts."""

    email: str = Field(min_length=3, max_length=254, description="New email address.", examples=["new@example.com"])


class ChangePasswordIn(Schema):
    """Payload for changing the user's password.
    Requires current password for verification and new password with confirmation."""

    current_password: str = Field(min_length=1, max_length=128, description="Current account password.", examples=["oldPass123!"])
    new_password: str = Field(min_length=8, max_length=128, description="New password (validated against Django password policies).", examples=["newS3cure!Pass"])
    new_password_confirm: str = Field(min_length=8, max_length=128, description="New password confirmation (must match new_password).", examples=["newS3cure!Pass"])


class DeleteAccountIn(Schema):
    """Payload for confirming account deletion.
    Regular users must provide their password; social-only users must send confirm_text=DELETE."""

    password: str | None = Field(default=None, max_length=128, description="Current account password (required for non-social accounts).", examples=["s3cure!Pass"])
    confirm_text: str | None = Field(default=None, max_length=10, description="Type 'DELETE' to confirm (required for social-only accounts that have no password).", examples=["DELETE"])


@auth_router.patch("/me/email", response={200: MeOut, 400: ErrorOut, 403: ErrorOut}, auth=JWTAuth(), summary="Change email address")
def change_email(request, payload: ChangeEmailIn):
    """Update the authenticated user's email address.
    Blocked for social-only accounts whose email is managed by their provider."""
    if is_social_only_user(request.user):
        return 403, {"message": "Social login accounts cannot change their email address."}

    normalized_email = payload.email.strip().lower()

    try:
        validate_email(normalized_email)
    except DjangoValidationError:
        return 400, {"message": "Provide a valid email address."}

    if request.user.email == normalized_email:
        return 400, {"message": "This is already your current email address."}

    if User.objects.filter(email__iexact=normalized_email).exclude(pk=request.user.pk).exists():
        return 400, {"message": "Email already exists."}

    request.user.email = normalized_email
    request.user.save(update_fields=["email"])
    return 200, MeOut(
        id=request.user.id,
        username=request.user.username,
        email=request.user.email,
        is_social_only=is_social_only_user(request.user),
    )


@auth_router.put("/me/password", response={200: MessageOut, 400: ErrorOut, 403: ErrorOut}, auth=JWTAuth(), summary="Change password")
def change_password(request, payload: ChangePasswordIn):
    """Change the authenticated user's password.
    Blocked for social-only accounts who authenticate via their provider instead."""
    if is_social_only_user(request.user):
        return 403, {"message": "Social login accounts cannot change their password."}

    if not request.user.check_password(payload.current_password):
        return 400, {"message": "Current password is incorrect."}

    if payload.new_password != payload.new_password_confirm:
        return 400, {"message": "New passwords do not match."}

    try:
        validate_password(payload.new_password, user=request.user)
    except DjangoValidationError as error:
        message = str(error.messages[0]) if error.messages else "Password does not meet security requirements."
        return 400, {"message": message}

    request.user.set_password(payload.new_password)
    request.user.save(update_fields=["password"])
    return 200, {"message": "Password changed successfully."}


@auth_router.delete("/me", response={200: MessageOut, 400: ErrorOut}, auth=JWTAuth(), summary="Delete account")
def delete_account(request, payload: DeleteAccountIn):
    """Permanently delete the authenticated user's account.
    Regular users verify with password; social-only users must send confirm_text=DELETE."""
    if is_social_only_user(request.user):
        if not payload.confirm_text or payload.confirm_text.strip() != "DELETE":
            return 400, {"message": "Send confirm_text set to 'DELETE' to delete your account."}
    else:
        if not payload.password:
            return 400, {"message": "Password is required."}
        if not request.user.check_password(payload.password):
            return 400, {"message": "Incorrect password."}

    request.user.delete()
    return 200, {"message": "Account deleted successfully."}
