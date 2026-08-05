import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0004_add_device_token"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContributorAgreementAcceptance",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("agreement_version", models.CharField(max_length=32)),
                ("accepted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "channel",
                    models.CharField(
                        choices=[
                            (
                                "api_credential_registration",
                                "API credential registration",
                            ),
                            ("api_social_apple", "API Apple registration"),
                            ("api_social_google", "API Google registration"),
                            (
                                "api_existing_user",
                                "API existing-user acceptance",
                            ),
                        ],
                        max_length=40,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="contributor_agreement_acceptances",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "contributor_agreement_acceptances",
                "ordering": ["-accepted_at"],
                "indexes": [
                    models.Index(
                        fields=["agreement_version", "-accepted_at"],
                        name="idx_agreement_version_time",
                    ),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="contributoragreementacceptance",
            constraint=models.UniqueConstraint(
                fields=("user", "agreement_version"),
                name="unique_user_agreement_version",
            ),
        ),
    ]
