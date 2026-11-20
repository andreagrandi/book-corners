from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import migrations

_validate_http_url = URLValidator(schemes=["http", "https"])


def _is_http_url(value: str) -> bool:
    """Return whether a value is a well-formed http or https URL.
    Kept inline so this migration does not change if app code changes."""
    try:
        _validate_http_url(value)
    except ValidationError:
        return False
    return True


def add_https_to_bare_websites(apps, schema_editor) -> None:
    """Prefix https:// to stored websites that have no scheme.
    Leaves values that are still not valid URLs unchanged for manual review."""
    Library = apps.get_model("libraries", "Library")
    libraries = (
        Library.objects.exclude(website="")
        .exclude(website__iregex=r"^https?://")
        .only("pk", "website")
    )
    for library in libraries.iterator():
        website = library.website.strip()
        if urlsplit(website).scheme:
            continue
        candidate = f"https://{website}"
        if _is_http_url(candidate):
            Library.objects.filter(pk=library.pk).update(website=candidate)


class Migration(migrations.Migration):

    dependencies = [
        ("libraries", "0023_library_website_http_only"),
    ]

    operations = [
        migrations.RunPython(add_https_to_bare_websites, migrations.RunPython.noop),
    ]
