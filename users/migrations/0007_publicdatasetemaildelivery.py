import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0006_add_web_contributor_agreement_channels"),
    ]
    operations = [
        migrations.CreateModel(
            name="PublicDatasetEmailDelivery",
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
                ("recipient_email", models.EmailField(max_length=254)),
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="public_dataset_email_delivery",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "public_dataset_email_deliveries",
                "ordering": ["sent_at"],
            },
        ),
    ]
