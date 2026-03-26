from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.i18n import set_language

from users.auth import is_social_only_user
from users.forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    DeleteAccountForm,
    RegistrationForm,
    SocialDeleteAccountForm,
    UsernameOrEmailAuthenticationForm,
)
from users.notifications import notify_new_registration
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
                message=_("Too many registration attempts. Please try again in a few minutes."),
            )

    if request.method == "POST" and form.is_valid():
        user = form.save()
        notify_new_registration(user, via="email")
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
                message=_("Too many login attempts. Please try again in a few minutes."),
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


@require_POST
def set_language_view(request: HttpRequest) -> HttpResponse:
    """Switch the active language and persist the choice.
    Saves to the user model for authenticated users, delegates cookie to Django."""
    response = set_language(request)
    if hasattr(request, "user") and request.user.is_authenticated:
        language = request.POST.get("language", "")
        valid_codes = [code for code, _name in settings.LANGUAGES]
        if language in valid_codes:
            request.user.language = language
            request.user.save(update_fields=["language"])
    return response


@login_required(login_url="login")
def change_email_view(request: HttpRequest) -> HttpResponse:
    """Allow the authenticated user to change their email address.
    Blocks social-only users whose email is managed by their provider."""
    if is_social_only_user(request.user):
        messages.error(request, _("Social login accounts cannot change their email address."))
        return redirect("dashboard")
    form = ChangeEmailForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        request.user.email = form.cleaned_data["email"]
        request.user.save(update_fields=["email"])
        messages.success(request, _("Your email address has been updated."))
        return redirect("dashboard")
    return render(request, "users/change_email.html", {"form": form})


@login_required(login_url="login")
def change_password_view(request: HttpRequest) -> HttpResponse:
    """Allow the authenticated user to change their password.
    Blocks social-only users who authenticate via their provider instead."""
    if is_social_only_user(request.user):
        messages.error(request, _("Social login accounts cannot change their password."))
        return redirect("dashboard")
    form = ChangePasswordForm(user=request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        update_session_auth_hash(request, request.user)
        messages.success(request, _("Your password has been changed."))
        return redirect("dashboard")
    return render(request, "users/change_password.html", {"form": form})


@login_required(login_url="login")
def delete_account_view(request: HttpRequest) -> HttpResponse:
    """Allow the authenticated user to permanently delete their account.
    Uses password confirmation for regular users, checkbox for social-only users."""
    social_only = is_social_only_user(request.user)
    if social_only:
        form = SocialDeleteAccountForm(request.POST or None)
    else:
        form = DeleteAccountForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        user = request.user
        logout(request)
        user.delete()
        messages.success(request, _("Your account has been deleted."))
        return redirect("home")
    return render(request, "users/delete_account.html", {
        "form": form,
        "is_social_only": social_only,
    })
