from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.i18n import set_language

from allauth.socialaccount.adapter import get_adapter as get_socialaccount_adapter
from allauth.socialaccount.providers.base import AuthProcess

from users.auth import is_social_only_user
from users.forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    ContributorAgreementForm,
    DeleteAccountForm,
    RegistrationForm,
    SocialDeleteAccountForm,
    UsernameOrEmailAuthenticationForm,
)
from users.contributor_agreements import (
    CURRENT_CONTRIBUTOR_AGREEMENT_VERSION,
    record_current_acceptance,
    web_social_registration_state_data,
)
from users.models import ContributorAgreementAcceptance
from users.notifications import notify_new_registration
from users.security import is_auth_rate_limited

_SOCIAL_REGISTRATION_PROVIDER_SETTINGS = {
    "apple": "APPLE_OAUTH_ENABLED",
    "google": "GOOGLE_OAUTH_ENABLED",
}


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


def _registration_context(
    *,
    form: RegistrationForm,
    agreement_form: ContributorAgreementForm,
) -> dict[str, object]:
    """Build consistent registration context for every validation response.
    Resets stale checked choices before rendering the current agreement version."""
    agreement_form.reset_stale_choice()
    return {
        "form": form,
        "agreement_form": agreement_form,
        "agreement_version": CURRENT_CONTRIBUTOR_AGREEMENT_VERSION,
    }


def _render_rate_limited_auth_response(
    *,
    request: HttpRequest,
    template_name: str,
    form: RegistrationForm | UsernameOrEmailAuthenticationForm,
    retry_after_seconds: int,
    message: str,
    agreement_form: ContributorAgreementForm | None = None,
) -> HttpResponse:
    """Render a throttled auth response with consistent UX feedback.
    Returns an HTTP 429 page with retry hints for the client."""
    form.add_error(None, message)
    context: dict[str, object] = {"form": form}
    if isinstance(form, RegistrationForm) and agreement_form is not None:
        context = _registration_context(form=form, agreement_form=agreement_form)
    response = render(request, template_name, context, status=429)
    response.headers["Retry-After"] = str(retry_after_seconds)
    return response


def register_view(request: HttpRequest) -> HttpResponse:
    """Create a password account only after current agreement acceptance.
    Records web acceptance atomically while keeping login behavior unchanged."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return redirect("home")

    form_data = request.POST if request.method == "POST" else None
    form = RegistrationForm(form_data)
    agreement_form = ContributorAgreementForm(form_data)
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
                agreement_form=agreement_form,
                retry_after_seconds=retry_after_seconds,
                message=_("Too many registration attempts. Please try again in a few minutes."),
            )

        registration_is_valid = form.is_valid()
        agreement_is_valid = agreement_form.is_valid()
        if registration_is_valid and agreement_is_valid:
            with transaction.atomic():
                user = form.save()
                record_current_acceptance(
                    user=user,
                    channel=(
                        ContributorAgreementAcceptance.Channel.WEB_CREDENTIAL_REGISTRATION
                    ),
                )
            notify_new_registration(user, via="email")
            login(
                request=request,
                user=user,
                backend="django.contrib.auth.backends.ModelBackend",
            )
            return redirect("home")

    context = _registration_context(form=form, agreement_form=agreement_form)
    return render(request, "users/register.html", context)


@require_POST
def social_register_view(request: HttpRequest, provider_id: str) -> HttpResponse:
    """Start an allowed social signup only after explicit current acceptance.
    Stores intent in per-flow OAuth state so ordinary social login stays unchanged."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return redirect("home")

    setting_name = _SOCIAL_REGISTRATION_PROVIDER_SETTINGS.get(provider_id)
    if setting_name is None or not getattr(settings, setting_name, False):
        raise Http404

    agreement_form = ContributorAgreementForm(request.POST)
    if not agreement_form.is_valid():
        form = RegistrationForm(
            initial={
                "username": request.POST.get("username", ""),
                "email": request.POST.get("email", ""),
            },
        )
        context = _registration_context(form=form, agreement_form=agreement_form)
        return render(request, "users/register.html", context)

    provider = get_socialaccount_adapter(request).get_provider(
        request,
        provider=provider_id,
    )
    return provider.redirect(
        request,
        process=AuthProcess.LOGIN,
        data=web_social_registration_state_data(provider=provider_id),
    )


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
