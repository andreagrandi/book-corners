import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("libraries", "0019_library_osm_submission_permission"),
    ]

    operations = [
        migrations.CreateModel(
            name="OpenStreetMapContribution",
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
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("unchecked", "Unchecked"),
                            ("no_match", "No match found"),
                            ("possible_duplicate", "Possible duplicate"),
                            ("already_present", "Already present"),
                            ("submitting", "Submitting"),
                            ("contributed", "Contributed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="unchecked",
                        max_length=20,
                    ),
                ),
                ("checked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "duplicate_candidates",
                    models.JSONField(blank=True, default=list),
                ),
                (
                    "osm_element_type",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("node", "Node"),
                            ("way", "Way"),
                            ("relation", "Relation"),
                        ],
                        default="",
                        max_length=8,
                    ),
                ),
                ("osm_element_id", models.BigIntegerField(blank=True, null=True)),
                ("changeset_id", models.BigIntegerField(blank=True, null=True)),
                ("contributed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_attempt_key",
                    models.UUIDField(blank=True, null=True, unique=True),
                ),
                (
                    "last_error_code",
                    models.CharField(blank=True, default="", max_length=50),
                ),
                (
                    "last_error_message",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                (
                    "last_error_retryable",
                    models.BooleanField(default=False),
                ),
                (
                    "contributed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="osm_contributions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "library",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="osm_contribution",
                        to="libraries.library",
                    ),
                ),
            ],
            options={
                "db_table": "openstreetmap_contributions",
                "ordering": ["-checked_at", "library_id"],
                "indexes": [
                    models.Index(
                        fields=["-checked_at", "library"],
                        name="idx_osm_checked_library",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            Q(osm_element_id__isnull=True, osm_element_type="")
                            | (
                                Q(osm_element_id__isnull=False)
                                & ~Q(osm_element_type="")
                            )
                        ),
                        name="osm_element_fields_paired",
                    ),
                    models.UniqueConstraint(
                        condition=Q(osm_element_id__isnull=False),
                        fields=("osm_element_type", "osm_element_id"),
                        name="unique_osm_element_link",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="OpenStreetMapContributionEvent",
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
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("permission_withdrawn", "Permission withdrawn"),
                            ("duplicate_check", "Duplicate check"),
                            ("duplicate_resolution", "Duplicate resolution"),
                            ("write_started", "Write started"),
                            ("write_succeeded", "Write succeeded"),
                            ("write_failed", "Write failed"),
                            (
                                "reconciliation_required",
                                "Reconciliation required",
                            ),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("success", "Success"),
                            ("failed", "Failed"),
                            ("blocked", "Blocked"),
                        ],
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "attempt_key",
                    models.UUIDField(blank=True, db_index=True, null=True),
                ),
                ("details", models.JSONField(blank=True, default=dict)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="osm_contribution_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "contribution",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="libraries.openstreetmapcontribution",
                    ),
                ),
            ],
            options={
                "db_table": "openstreetmap_contribution_events",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["-created_at"],
                        name="idx_osm_event_created",
                    ),
                    models.Index(
                        fields=["contribution", "-created_at"],
                        name="idx_osm_event_contrib_created",
                    ),
                    models.Index(
                        fields=["event_type", "-created_at"],
                        name="idx_osm_event_type_created",
                    ),
                    models.Index(
                        fields=["outcome", "-created_at"],
                        name="idx_osm_event_outcome_created",
                    ),
                ],
            },
        ),
    ]
