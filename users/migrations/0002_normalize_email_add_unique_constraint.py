from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower


def normalize_emails(apps, schema_editor):
    """Lowercase and strip all existing email addresses.
    Fails loudly if normalization would create duplicates."""
    User = apps.get_model("users", "User")
    users_with_email = User.objects.exclude(email="")
    seen = {}
    duplicates = []

    for user in users_with_email.order_by("date_joined"):
        normalized = user.email.strip().lower()
        if not normalized:
            continue
        if normalized in seen:
            duplicates.append(
                f"  {user.username!r} ({user.email!r}) conflicts with "
                f"{seen[normalized]!r}"
            )
        else:
            seen[normalized] = user.username

    if duplicates:
        detail = "\n".join(duplicates)
        raise ValueError(
            "Cannot normalize emails — duplicates found after lowercasing:\n"
            f"{detail}\n"
            "Resolve these manually before re-running the migration."
        )

    for user in users_with_email:
        normalized = user.email.strip().lower()
        if user.email != normalized:
            user.email = normalized
            user.save(update_fields=["email"])


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            normalize_emails,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                Lower("email"),
                condition=Q(email__gt=""),
                name="unique_email_ci",
            ),
        ),
    ]
