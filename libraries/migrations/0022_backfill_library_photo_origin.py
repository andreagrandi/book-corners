from django.db import migrations

BATCH_SIZE = 500


def backfill_photo_origin(apps, schema_editor) -> None:
    """Record the origin of every existing main library photo.
    Uses approved community photos first, then import markers, then the creator."""
    Library = apps.get_model("libraries", "Library")
    LibraryPhoto = apps.get_model("libraries", "LibraryPhoto")

    promoted_authors: dict[tuple[int, str], int | None] = {}
    approved_photos = (
        LibraryPhoto.objects.filter(status="approved")
        .order_by("created_at", "pk")
        .values_list("library_id", "photo", "created_by_id")
    )
    for library_id, photo_name, created_by_id in approved_photos.iterator():
        promoted_authors[(library_id, photo_name)] = created_by_id

    pending_updates = []
    libraries = Library.objects.exclude(photo="").only(
        "pk",
        "photo",
        "external_id",
        "source",
        "submission_origin",
        "created_by_id",
        "photo_origin",
        "photo_author_id",
        "photo_source_url",
    )
    for library in libraries.iterator(chunk_size=BATCH_SIZE):
        library.photo_author_id = None
        library.photo_source_url = ""
        key = (library.pk, library.photo.name)
        is_imported = (
            library.external_id != ""
            or library.source != ""
            or library.submission_origin == "import"
        )

        if key in promoted_authors:
            # The author account was deleted when the id is None, so the origin stays unknown.
            promoted_author_id = promoted_authors[key]
            if promoted_author_id is None:
                library.photo_origin = "unknown"
            else:
                library.photo_origin = "user"
                library.photo_author_id = promoted_author_id
        elif is_imported:
            library.photo_origin = "external"
        elif library.created_by_id is not None:
            library.photo_origin = "user"
            library.photo_author_id = library.created_by_id
        else:
            library.photo_origin = "unknown"

        pending_updates.append(library)
        if len(pending_updates) >= BATCH_SIZE:
            Library.objects.bulk_update(
                pending_updates,
                ["photo_origin", "photo_author", "photo_source_url"],
            )
            pending_updates = []

    if pending_updates:
        Library.objects.bulk_update(
            pending_updates,
            ["photo_origin", "photo_author", "photo_source_url"],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("libraries", "0021_library_photo_origin"),
    ]

    operations = [
        migrations.RunPython(
            code=backfill_photo_origin,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
