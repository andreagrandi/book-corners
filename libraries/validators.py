from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

WEBSITE_URL_SCHEMES = ("http", "https")
WEBSITE_URL_ERROR = "Enter a valid URL starting with http:// or https://."

_website_url_validator = URLValidator(
    schemes=list(WEBSITE_URL_SCHEMES),
    message=WEBSITE_URL_ERROR,
)


def validate_website_url(value: str) -> None:
    """Reject website values that are not well-formed http or https URLs.
    Keeps script URLs such as javascript: out of rendered link targets."""
    if value:
        _website_url_validator(value)


def normalize_website_url(value: str) -> str:
    """Strip a website value and add https:// when it has no scheme.
    Matches the web form so bare domains like example.org keep working."""
    value = value.strip()
    if value and not urlsplit(value).scheme:
        return f"https://{value}"
    return value


def is_website_url(value: str | None) -> bool:
    """Return whether a value is a well-formed http or https URL.
    Lets templates, imports and schemas share one scheme allowlist."""
    if not value:
        return False
    try:
        _website_url_validator(value)
    except ValidationError:
        return False
    return True
