from typing import Any

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

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
            username = username_or_email
            if "@" in username_or_email:
                matching_user = User.objects.filter(email__iexact=username_or_email).first()
                if matching_user is not None:
                    username = matching_user.get_username()

            self.user_cache = authenticate(
                self.request,
                username=username,
                password=password,
            )
            if self.user_cache is None:
                raise self.get_invalid_login_error()

            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data
