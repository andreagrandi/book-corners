from typing import Any

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from users.auth import resolve_login_identifier

User = get_user_model()


def _apply_input_classes(*, form: forms.BaseForm) -> None:
    """Handle apply input classes.
    Keeps this module logic focused and reusable."""
    for field in form.fields.values():
        existing_classes = field.widget.attrs.get("class", "")
        classes = f"{existing_classes} input w-full".strip()
        field.widget.attrs["class"] = classes


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the object state.
        Sets up values required by later calls."""
        super().__init__(*args, **kwargs)
        _apply_input_classes(form=self)

    def clean_username(self) -> str:
        """Normalize the username field before persistence.
        Trims surrounding whitespace from user input values."""
        username = self.cleaned_data.get("username", "")
        return username.strip()

    def clean_email(self) -> str:
        """Normalize email and reject duplicates before persistence.
        Ensures case-insensitive uniqueness at the form level."""
        email = self.cleaned_data.get("email", "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("A user with this email already exists."))
        return email


class ChangeEmailForm(forms.Form):
    """Form for changing the user's email address.
    Validates uniqueness case-insensitively before accepting."""

    email = forms.EmailField(label=_("New email address"))

    def __init__(self, *args: Any, user: Any = None, **kwargs: Any) -> None:
        """Store the current user for duplicate-email validation.
        Applies consistent input styling across all form fields."""
        super().__init__(*args, **kwargs)
        self.user = user
        _apply_input_classes(form=self)

    def clean_email(self) -> str:
        """Normalize and validate uniqueness of the new email.
        Rejects duplicates and no-change submissions."""
        email = self.cleaned_data.get("email", "").strip().lower()
        if self.user and self.user.email == email:
            raise forms.ValidationError(_("This is already your current email address."))
        if User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError(_("A user with this email already exists."))
        return email


class ChangePasswordForm(SetPasswordForm):
    """Form for changing the user's password with current password verification.
    Extends Django's SetPasswordForm with an additional current-password check."""

    current_password = forms.CharField(
        label=_("Current password"),
        widget=forms.PasswordInput,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize with consistent input styling.
        Reorders fields to place current password first."""
        super().__init__(*args, **kwargs)
        self.fields = {
            "current_password": self.fields["current_password"],
            "new_password1": self.fields["new_password1"],
            "new_password2": self.fields["new_password2"],
        }
        _apply_input_classes(form=self)

    def clean_current_password(self) -> str:
        """Verify that the current password is correct.
        Prevents unauthorized password changes via stolen sessions."""
        current_password = self.cleaned_data.get("current_password", "")
        if not self.user.check_password(current_password):
            raise forms.ValidationError(_("Your current password is incorrect."))
        return current_password


class DeleteAccountForm(forms.Form):
    """Form for confirming account deletion with password.
    Requires current password to prevent accidental or unauthorized deletions."""

    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput,
    )

    def __init__(self, *args: Any, user: Any = None, **kwargs: Any) -> None:
        """Store the current user for password verification.
        Applies consistent input styling across all form fields."""
        super().__init__(*args, **kwargs)
        self.user = user
        _apply_input_classes(form=self)

    def clean_password(self) -> str:
        """Verify the password before allowing account deletion.
        Ensures the user intentionally confirms the destructive action."""
        password = self.cleaned_data.get("password", "")
        if not self.user.check_password(password):
            raise forms.ValidationError(_("Incorrect password."))
        return password


class SocialDeleteAccountForm(forms.Form):
    """Form for confirming account deletion for social-only users.
    Requires typing DELETE since social users have no password to verify."""

    confirm_text = forms.CharField(
        label=_("Type DELETE to confirm"),
        max_length=10,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize with consistent input styling.
        Applies the same field classes used across all forms."""
        super().__init__(*args, **kwargs)
        _apply_input_classes(form=self)

    def clean_confirm_text(self) -> str:
        """Verify the user typed DELETE exactly.
        Prevents accidental account deletion for social-only users."""
        value = self.cleaned_data.get("confirm_text", "").strip()
        if value != "DELETE":
            raise forms.ValidationError(_("Please type DELETE to confirm."))
        return value


class UsernameOrEmailAuthenticationForm(AuthenticationForm):
    username = forms.CharField(label=_("Username or email"), max_length=254)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the object state.
        Sets up values required by later calls."""
        super().__init__(*args, **kwargs)
        _apply_input_classes(form=self)

    def clean(self) -> dict[str, Any]:
        """Handle clean.
        Supports the module workflow with a focused operation."""
        username_or_email_value = self.cleaned_data.get("username")
        password_value = self.cleaned_data.get("password")
        username_or_email = (
            username_or_email_value.strip()
            if isinstance(username_or_email_value, str)
            else ""
        )
        password = password_value if isinstance(password_value, str) else ""

        if username_or_email and password:
            self.cleaned_data["username"] = username_or_email
            username = resolve_login_identifier(username_or_email)

            self.user_cache = authenticate(
                self.request,
                username=username,
                password=password,
            )
            if self.user_cache is None:
                raise self.get_invalid_login_error()

            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data
