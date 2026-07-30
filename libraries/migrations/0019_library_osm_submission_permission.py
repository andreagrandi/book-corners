from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("libraries", "0018_library_pending_changes_and_photos"),
    ]

    operations = [
        migrations.AddField(
            model_name="library",
            name="osm_submission_allowed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="library",
            name="osm_submission_allowed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="library",
            name="submission_origin",
            field=models.CharField(
                choices=[
                    ("legacy", "Legacy"),
                    ("user", "User"),
                    ("staff", "Staff"),
                    ("import", "Import"),
                ],
                default="legacy",
                max_length=10,
            ),
        ),
        migrations.AddConstraint(
            model_name="library",
            constraint=models.CheckConstraint(
                condition=(
                    Q(
                        osm_submission_allowed=False,
                        osm_submission_allowed_at__isnull=True,
                    )
                    | Q(
                        osm_submission_allowed=True,
                        osm_submission_allowed_at__isnull=False,
                        submission_origin="user",
                    )
                ),
                name="lib_osm_permission_valid",
            ),
        ),
        migrations.AddIndex(
            model_name="library",
            index=models.Index(
                fields=["submission_origin"],
                name="idx_lib_submission_origin",
            ),
        ),
    ]
