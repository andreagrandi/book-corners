from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AbstractBaseUser
from ninja import Router, Schema
from ninja_jwt.authentication import JWTAuth
from ninja_jwt.exceptions import TokenError
from ninja_jwt.tokens import RefreshToken

User = get_user_model()

auth_router = Router(tags=["auth"])


class ErrorOut(Schema):
    message: str


class TokenPairOut(Schema):
    access: str
    refresh: str


class AccessTokenOut(Schema):
    access: str


class RegisterIn(Schema):
    username: str
    password: str
    email: str = ""


class LoginIn(Schema):
    username: str
    password: str


class RefreshIn(Schema):
    refresh: str


class MeOut(Schema):
    id: int
    username: str
    email: str


def build_token_pair(*, user: AbstractBaseUser) -> TokenPairOut:
    refresh = RefreshToken.for_user(user)
    return TokenPairOut(
        access=str(refresh.access_token),
        refresh=str(refresh),
    )


@auth_router.post("/register", response={201: TokenPairOut, 400: ErrorOut}, auth=None)
def register(request, payload: RegisterIn):
    if User.objects.filter(username=payload.username).exists():
        return 400, {"message": "Username already exists."}

    user = User.objects.create_user(
        username=payload.username,
        email=payload.email,
        password=payload.password,
    )
    return 201, build_token_pair(user=user)


@auth_router.post("/login", response={200: TokenPairOut, 401: ErrorOut}, auth=None)
def login(request, payload: LoginIn):
    user = authenticate(
        request,
        username=payload.username,
        password=payload.password,
    )
    if user is None:
        return 401, {"message": "Invalid credentials."}

    return 200, build_token_pair(user=user)


@auth_router.post("/refresh", response={200: AccessTokenOut, 401: ErrorOut}, auth=None)
def refresh(request, payload: RefreshIn):
    try:
        refresh_token = RefreshToken(payload.refresh)
    except TokenError:
        return 401, {"message": "Invalid or expired refresh token."}

    return 200, AccessTokenOut(access=str(refresh_token.access_token))


@auth_router.get("/me", response=MeOut, auth=JWTAuth())
def me(request):
    return MeOut(
        id=request.user.id,
        username=request.user.username,
        email=request.user.email,
    )
