import pytest
from django.contrib.gis.geos import Point
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_osm_permission_migration_defaults_existing_libraries_to_legacy() -> None:
    """Verify existing libraries migrate without inferred OSM permission.
    Preserves old rows as default-off legacy submissions."""
    migrate_from = ("libraries", "0018_library_pending_changes_and_photos")
    migrate_to = ("libraries", "0019_library_osm_submission_permission")
    executor = MigrationExecutor(connection=connection)
    executor.migrate(targets=[migrate_from])
    old_apps = executor.loader.project_state(nodes=[migrate_from]).apps
    OldLibrary = old_apps.get_model("libraries", "Library")
    old_library = OldLibrary.objects.create(
        slug="legacy-library",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        city="Florence",
        country="IT",
    )

    executor = MigrationExecutor(connection=connection)
    executor.migrate(targets=[migrate_to])
    new_apps = executor.loader.project_state(nodes=[migrate_to]).apps
    NewLibrary = new_apps.get_model("libraries", "Library")
    migrated_library = NewLibrary.objects.get(pk=old_library.pk)

    assert migrated_library.osm_submission_allowed is False
    assert migrated_library.osm_submission_allowed_at is None
    assert migrated_library.submission_origin == "legacy"

    executor = MigrationExecutor(connection=connection)
    executor.migrate(
        targets=[("libraries", "0020_openstreetmap_contribution_state")]
    )


@pytest.mark.django_db(transaction=True)
def test_osm_state_migration_does_not_infer_state_for_existing_libraries() -> None:
    """Verify existing libraries receive no fabricated OSM review state.
    Leaves every old record explicitly unknown until a staff check occurs."""
    migrate_from = ("libraries", "0019_library_osm_submission_permission")
    migrate_to = ("libraries", "0020_openstreetmap_contribution_state")
    executor = MigrationExecutor(connection=connection)
    executor.migrate(targets=[migrate_from])
    old_apps = executor.loader.project_state(nodes=[migrate_from]).apps
    OldLibrary = old_apps.get_model("libraries", "Library")
    OldLibrary.objects.create(
        slug="existing-osm-candidate",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        city="Florence",
        country="IT",
    )

    executor = MigrationExecutor(connection=connection)
    executor.migrate(targets=[migrate_to])
    new_apps = executor.loader.project_state(nodes=[migrate_to]).apps
    Contribution = new_apps.get_model(
        "libraries",
        "OpenStreetMapContribution",
    )
    Event = new_apps.get_model(
        "libraries",
        "OpenStreetMapContributionEvent",
    )

    assert Contribution.objects.count() == 0
    assert Event.objects.count() == 0
