from django.conf import settings
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from users.forms import RegistrationForm, UsernameOrEmailAuthenticationForm
from users.security import is_auth_rate_limited


def _get_safe_next_url(*, request: HttpRequest) -> str:
    """Handle get safe next url.
    Keeps this module logic focused and reusable."""
    next_value = request.POST.get("next") or request.GET.get("next") or ""
    next_url = next_value if isinstance(next_value, str) else ""
    if not next_url:
        return ""

    if url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url

    return ""


def _render_rate_limited_auth_response(
    *,
    request: HttpRequest,
    template_name: str,
    form: RegistrationForm | UsernameOrEmailAuthenticationForm,
    retry_after_seconds: int,
    message: str,
) -> HttpResponse:
    """Render a throttled auth response with consistent UX feedback.
    Returns an HTTP 429 page with retry hints for the client."""
    form.add_error(None, message)
    response = render(request, template_name, {"form": form}, status=429)
    response.headers["Retry-After"] = str(retry_after_seconds)
    return response


def register_view(request: HttpRequest) -> HttpResponse:
    """Handle register view.
    Supports the module workflow with a focused operation."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return redirect("home")

    form = RegistrationForm(request.POST or None)
    if request.method == "POST":
        limited, retry_after_seconds = is_auth_rate_limited(
            request=request,
            scope="web-register",
            max_attempts=settings.AUTH_RATE_LIMIT_REGISTER_ATTEMPTS,
        )
        if limited:
            return _render_rate_limited_auth_response(
                request=request,
                template_name="users/register.html",
                form=form,
                retry_after_seconds=retry_after_seconds,
                message="Too many registration attempts. Please try again in a few minutes.",
            )

    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request=request, user=user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("home")

    return render(request, "users/register.html", {"form": form})


def login_view(request: HttpRequest) -> HttpResponse:
    """Handle login view.
    Supports the module workflow with a focused operation."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return redirect("home")

    form = UsernameOrEmailAuthenticationForm(request=request, data=request.POST or None)
    if request.method == "POST":
        limited, retry_after_seconds = is_auth_rate_limited(
            request=request,
            scope="web-login",
            max_attempts=settings.AUTH_RATE_LIMIT_LOGIN_ATTEMPTS,
        )
        if limited:
            return _render_rate_limited_auth_response(
                request=request,
                template_name="users/login.html",
                form=form,
                retry_after_seconds=retry_after_seconds,
                message="Too many login attempts. Please try again in a few minutes.",
            )

    if request.method == "POST" and form.is_valid():
        login(request=request, user=form.get_user())
        next_url = _get_safe_next_url(request=request)
        if next_url:
            return redirect(next_url)
        return redirect("home")

    return render(request, "users/login.html", {"form": form})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """Handle logout view.
    Supports the module workflow with a focused operation."""
    logout(request=request)
    return redirect("home")
