from pathlib import Path

import pytest
from django.core.management import call_command
from PIL import Image

from libraries.models import Library, Report


def _write_seed_image(file_path: Path) -> None:
    image = Image.new("RGB", (640, 480), color=(120, 145, 190))
    image.save(file_path, format="JPEG")


@pytest.mark.django_db
def test_seed_libraries_uses_local_images(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"

    images_dir = tmp_path / "seed_images"
    images_dir.mkdir(parents=True)
    _write_seed_image(images_dir / "seed-1.jpg")
    _write_seed_image(images_dir / "seed-2.jpg")

    call_command(
        "seed_libraries",
        reset=True,
        count=5,
        images_dir=str(images_dir),
        seed=42,
    )

    assert Library.objects.count() == 5
    assert Library.objects.filter(status=Library.Status.APPROVED).exists()
    assert all(library.photo.name for library in Library.objects.all())


@pytest.mark.django_db
def test_seed_libraries_reset_cleans_reports_and_libraries(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"

    images_dir = tmp_path / "seed_images"
    images_dir.mkdir(parents=True)
    _write_seed_image(images_dir / "seed-1.jpg")

    call_command(
        "seed_libraries",
        count=2,
        images_dir=str(images_dir),
        seed=7,
    )

    library = Library.objects.first()
    assert library is not None

    Report.objects.create(
        library=library,
        created_by=library.created_by,
        reason=Report.Reason.OTHER,
        details="Seed cleanup test report.",
    )

    call_command("seed_libraries", reset=True, count=0)

    assert Library.objects.count() == 0
    assert Report.objects.count() == 0
