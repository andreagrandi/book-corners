import pytest
from django.contrib.auth import get_user_model
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


@pytest.mark.django_db(transaction=True)
def test_photo_origin_backfill_classifies_existing_photos() -> None:
    """Verify the backfill records an origin for every library with a photo.
    Applies approved community photo, import, creator and unknown rules in order."""
    migrate_from = ("libraries", "0021_library_photo_origin")
    migrate_to = ("libraries", "0022_backfill_library_photo_origin")
    # Users come from the current model because the historical user state lacks later columns.
    user_model = get_user_model()
    creator = user_model.objects.create_user(username="backfill-creator")
    contributor = user_model.objects.create_user(username="backfill-contributor")
    later_contributor = user_model.objects.create_user(username="backfill-later")
    executor = MigrationExecutor(connection=connection)
    executor.migrate(targets=[migrate_from])
    old_apps = executor.loader.project_state(nodes=[migrate_from]).apps
    OldLibrary = old_apps.get_model("libraries", "Library")
    OldLibraryPhoto = old_apps.get_model("libraries", "LibraryPhoto")

    def create_library(*, slug: str, **fields: object) -> int:
        """Create a historical library row with shared location defaults.
        Returns the primary key so assertions can reload the migrated row."""
        return OldLibrary.objects.create(
            slug=slug,
            location=Point(x=11.2558, y=43.7696, srid=4326),
            city="Florence",
            country="IT",
            **fields,
        ).pk

    promoted_pk = create_library(
        slug="promoted",
        photo="libraries/user_photos/promoted.jpg",
        created_by_id=creator.pk,
    )
    OldLibraryPhoto.objects.create(
        library_id=promoted_pk,
        created_by_id=contributor.pk,
        photo="libraries/user_photos/promoted.jpg",
        status="approved",
    )
    OldLibraryPhoto.objects.create(
        library_id=promoted_pk,
        created_by_id=later_contributor.pk,
        photo="libraries/user_photos/promoted.jpg",
        status="approved",
    )
    OldLibraryPhoto.objects.create(
        library_id=promoted_pk,
        created_by_id=later_contributor.pk,
        photo="libraries/user_photos/other.jpg",
        status="approved",
    )
    unapproved_pk = create_library(
        slug="unapproved-match",
        photo="libraries/photos/unapproved.jpg",
        created_by_id=creator.pk,
    )
    OldLibraryPhoto.objects.create(
        library_id=unapproved_pk,
        created_by_id=contributor.pk,
        photo="libraries/photos/unapproved.jpg",
        status="pending",
    )
    orphaned_promotion_pk = create_library(
        slug="orphaned-promotion",
        photo="libraries/user_photos/orphaned.jpg",
        created_by_id=creator.pk,
    )
    OldLibraryPhoto.objects.create(
        library_id=orphaned_promotion_pk,
        created_by=None,
        photo="libraries/user_photos/orphaned.jpg",
        status="approved",
    )
    imported_pk = create_library(
        slug="imported",
        photo="libraries/photos/imported.jpg",
        submission_origin="legacy",
        source="OpenStreetMap",
        external_id="node/1",
        created_by_id=creator.pk,
    )
    import_origin_pk = create_library(
        slug="import-origin",
        photo="libraries/photos/import-origin.jpg",
        submission_origin="import",
        created_by_id=creator.pk,
    )
    user_created_pk = create_library(
        slug="user-created",
        photo="libraries/photos/user-created.jpg",
        submission_origin="user",
        created_by_id=creator.pk,
    )
    no_author_pk = create_library(
        slug="no-author",
        photo="libraries/photos/no-author.jpg",
    )
    no_photo_pk = create_library(slug="no-photo", created_by_id=creator.pk)

    executor = MigrationExecutor(connection=connection)
    executor.migrate(targets=[migrate_to])
    new_apps = executor.loader.project_state(nodes=[migrate_to]).apps
    NewLibrary = new_apps.get_model("libraries", "Library")

    def origin_of(pk: int) -> tuple[str, int | None, str]:
        """Load the migrated origin fields for one library.
        Returns origin, author id and source URL for direct comparison."""
        row = NewLibrary.objects.get(pk=pk)
        return row.photo_origin, row.photo_author_id, row.photo_source_url

    assert origin_of(promoted_pk) == ("user", later_contributor.pk, "")
    assert origin_of(unapproved_pk) == ("user", creator.pk, "")
    assert origin_of(orphaned_promotion_pk) == ("unknown", None, "")
    assert origin_of(imported_pk) == ("external", None, "")
    assert origin_of(import_origin_pk) == ("external", None, "")
    assert origin_of(user_created_pk) == ("user", creator.pk, "")
    assert origin_of(no_author_pk) == ("unknown", None, "")
    assert origin_of(no_photo_pk) == ("", None, "")

    executor = MigrationExecutor(connection=connection)
    executor.migrate(targets=executor.loader.graph.leaf_nodes())
