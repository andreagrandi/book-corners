"""Tests for changed-only approved-library GeoJSON export generation."""

from __future__ import annotations

import gzip
import io
import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.db import close_old_connections, connections

from libraries import library_export
from libraries.library_export import LibraryExportError, generate_library_export
from libraries.models import Library


@pytest.fixture
def export_directory(settings, tmp_path) -> Path:
    """Configure an isolated persistent media directory for one export test.
    Uses a stable public base URL so absolute photo URLs are deterministic.
    """
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.SITE_URL = "https://bookcorners.example"
    return Path(settings.MEDIA_ROOT) / library_export.EXPORT_DIRECTORY_NAME


def _create_library(
    *,
    index: int,
    status: str = Library.Status.APPROVED,
    source: str = "",
    external_id: str = "",
    photo: str = "",
) -> Library:
    """Create a library with export-relevant fields and deterministic values.
    Supplies varied public data without attaching private moderation state.
    """
    return Library.objects.create(
        slug=f"export-library-{index}",
        name=f"Export Library {index}",
        description=f"Description {index}",
        photo=photo,
        location=Point(x=10 + index / 100, y=40 + index / 100, srid=4326),
        address=f"Via Export {index}",
        city=f"Export City {index}",
        country="IT",
        postal_code=f"50{index:03d}",
        wheelchair_accessible=Library.WheelchairAccess.LIMITED,
        capacity=index * 10,
        is_indoor=index % 2 == 0,
        is_lit=index % 2 == 1,
        website=f"https://example.org/libraries/{index}",
        contact=f"Contact {index}",
        source=source,
        operator=f"Operator {index}",
        brand=f"Brand {index}",
        external_id=external_id,
        status=status,
    )


def _read_manifest(export_directory: Path) -> dict[str, object]:
    """Read the active manifest from an isolated export directory.
    Keeps assertions focused on the public artifact contract.
    """
    return json.loads((export_directory / "latest.json").read_text(encoding="utf-8"))


def _read_features(
    *, export_directory: Path, manifest: dict[str, object]
) -> list[dict[str, object]]:
    """Read the active GeoJSON features selected by a manifest.
    Lets small focused tests inspect the generated public representation.
    """
    export = manifest["export"]
    assert isinstance(export, dict)
    geojson = export["geojson"]
    assert isinstance(geojson, dict)
    payload = json.loads(
        (export_directory / str(geojson["filename"])).read_text(encoding="utf-8")
    )
    assert payload["type"] == "FeatureCollection"
    return payload["features"]


@pytest.mark.django_db(transaction=True)
class TestLibraryExport:
    """Exercise file generation against a real PostgreSQL transaction."""

    def test_exports_only_approved_libraries_with_the_public_allowlist(
        self,
        export_directory: Path,
    ) -> None:
        """Export every approved source while omitting private model state.
        Confirms geometry, provenance, and absolute primary-photo URLs are stable.
        """
        approved = _create_library(
            index=1,
            source="OpenStreetMap",
            external_id="node/100",
            photo="libraries/photos/primary.jpg",
        )
        _create_library(index=2, status=Library.Status.PENDING)
        _create_library(index=3, status=Library.Status.REJECTED)

        result = generate_library_export()
        manifest = _read_manifest(export_directory)
        features = _read_features(export_directory=export_directory, manifest=manifest)

        assert result.outcome == "published"
        assert result.record_count == 1
        assert len(features) == 1
        feature = features[0]
        assert feature["geometry"] == {
            "type": "Point",
            "coordinates": [approved.location.x, approved.location.y],
        }
        properties = feature["properties"]
        assert set(properties) == set(library_export.EXPORTED_PROPERTY_NAMES)
        assert properties["id"] == approved.pk
        assert properties["source"] == "OpenStreetMap"
        assert properties["external_id"] == "node/100"
        assert properties["photo_url"] == (
            "https://bookcorners.example/media/libraries/photos/primary.jpg"
        )
        assert properties["created_at"].endswith("Z")
        assert properties["updated_at"].endswith("Z")
        assert "status" not in properties
        assert "created_by" not in properties
        assert "pending_changes" not in properties
        assert "osm_submission_allowed" not in properties
        export = manifest["export"]
        assert isinstance(export, dict)
        geojson = export["geojson"]
        geojson_gzip = export["geojson_gzip"]
        metadata = export["metadata"]
        assert isinstance(geojson, dict)
        assert isinstance(geojson_gzip, dict)
        assert isinstance(metadata, dict)
        raw_bytes = (export_directory / str(geojson["filename"])).read_bytes()
        gzip_bytes = (export_directory / str(geojson_gzip["filename"])).read_bytes()
        metadata_payload = json.loads(
            (export_directory / str(metadata["filename"])).read_text(encoding="utf-8")
        )
        assert metadata_payload["license"]["name"].endswith("ODbL) v1.0")
        assert metadata_payload["data"]["filename"] == geojson["filename"]
        assert metadata_payload["gzip"]["filename"] == geojson_gzip["filename"]
        assert gzip.decompress(gzip_bytes) == raw_bytes

    def test_serializes_missing_provenance_as_explicit_nulls(
        self,
        export_directory: Path,
    ) -> None:
        """Keep approved records with unknown provenance in the export.
        Represents missing source metadata consistently without filtering records.
        """
        _create_library(index=1)

        generate_library_export()
        features = _read_features(
            export_directory=export_directory,
            manifest=_read_manifest(export_directory),
        )

        assert len(features) == 1
        assert features[0]["properties"]["source"] is None
        assert features[0]["properties"]["external_id"] is None

    def test_unchanged_generation_preserves_artifacts_and_updates_checked_at(
        self,
        export_directory: Path,
    ) -> None:
        """Avoid creating a daily immutable duplicate for identical data.
        Refreshes only the manifest check timestamp for future health reporting.
        """
        _create_library(index=1)

        first_result = generate_library_export()
        first_manifest = _read_manifest(export_directory)
        first_export = first_manifest["export"]
        assert isinstance(first_export, dict)
        first_geojson = first_export["geojson"]
        assert isinstance(first_geojson, dict)
        first_geojson_path = export_directory / str(first_geojson["filename"])
        first_contents = first_geojson_path.read_bytes()

        second_result = generate_library_export()
        second_manifest = _read_manifest(export_directory)
        second_export = second_manifest["export"]
        assert isinstance(second_export, dict)

        assert first_result.outcome == "published"
        assert second_result.outcome == "unchanged"
        assert second_result.geojson_filename == first_geojson["filename"]
        assert second_export == first_export
        assert second_manifest["checked_at"] >= first_manifest["checked_at"]
        assert first_geojson_path.read_bytes() == first_contents
        assert len(list(export_directory.glob("libraries-*.geojson"))) == 1
        assert len(list(export_directory.glob("libraries-*.geojson.gz"))) == 1
        assert len(list(export_directory.glob("libraries-*.metadata.json"))) == 1

    def test_schema_change_publishes_when_data_bytes_are_identical(
        self,
        export_directory: Path,
    ) -> None:
        """Publish a new version when the documented schema changes alone.
        Leaves the data checksum stable while changing the schema checksum.
        """
        _create_library(index=1)

        generate_library_export()
        first_manifest = _read_manifest(export_directory)
        first_export = first_manifest["export"]
        assert isinstance(first_export, dict)
        changed_schema = json.loads(json.dumps(library_export.EXPORT_SCHEMA))
        properties = changed_schema["properties"]
        assert isinstance(properties, dict)
        properties["name"]["description"] = "Exported public name."

        with patch.object(library_export, "EXPORT_SCHEMA", changed_schema):
            result = generate_library_export()

        second_export = _read_manifest(export_directory)["export"]
        assert isinstance(second_export, dict)
        assert result.outcome == "published"
        assert second_export["data_checksum"] == first_export["data_checksum"]
        assert second_export["schema_checksum"] != first_export["schema_checksum"]
        assert second_export["geojson"] != first_export["geojson"]

    def test_failed_refresh_preserves_the_previous_manifest_and_artifacts(
        self,
        export_directory: Path,
    ) -> None:
        """Keep a valid active export available when a changed run fails.
        Removes the candidate rather than changing latest.json before publication.
        """
        library = _create_library(index=1)
        generate_library_export()
        manifest_before = (export_directory / "latest.json").read_bytes()
        files_before = sorted(path.name for path in export_directory.iterdir())
        library.description = "Changed description"
        library.save(update_fields=["description"])

        with patch(
            "libraries.library_export._replace_manifest",
            side_effect=LibraryExportError("manifest failure"),
        ):
            with pytest.raises(LibraryExportError, match="manifest failure"):
                generate_library_export()

        assert (export_directory / "latest.json").read_bytes() == manifest_before
        assert sorted(path.name for path in export_directory.iterdir()) == files_before

    def test_retains_active_export_and_seven_previous_versions(
        self,
        export_directory: Path,
    ) -> None:
        """Remove only superseded complete sets after repeated changed publishes.
        Preserves eight rollback-ready versions including the current artifact.
        """
        library = _create_library(index=1)

        for version in range(9):
            Library.objects.filter(pk=library.pk).update(
                description=f"Changed version {version}"
            )
            result = generate_library_export()
            assert result.outcome == "published"

        assert len(list(export_directory.glob("libraries-*.geojson"))) == 8
        assert len(list(export_directory.glob("libraries-*.geojson.gz"))) == 8
        assert len(list(export_directory.glob("libraries-*.metadata.json"))) == 8

    def test_removes_stale_candidate_files_after_acquiring_the_lock(
        self,
        export_directory: Path,
    ) -> None:
        """Clean interrupted temporary writes without touching final artifacts.
        Limits cleanup to the generator's dedicated temporary filename pattern.
        """
        export_directory.mkdir(parents=True)
        stale_path = export_directory / ".library-export-interrupted.geojson.tmp"
        stale_path.write_text("partial", encoding="utf-8")
        _create_library(index=1)

        generate_library_export()

        assert not stale_path.exists()
        assert (export_directory / "latest.json").exists()

    def test_exports_an_empty_feature_collection(self, export_directory: Path) -> None:
        """Publish a valid first export when no approved libraries exist.
        Gives consumers an explicit empty catalogue instead of no artifact.
        """
        _create_library(index=1, status=Library.Status.PENDING)

        result = generate_library_export()
        features = _read_features(
            export_directory=export_directory,
            manifest=_read_manifest(export_directory),
        )

        assert result.outcome == "published"
        assert result.record_count == 0
        assert features == []

    def test_processes_more_than_one_iterator_chunk(self, export_directory: Path) -> None:
        """Generate a complete export containing more than one iterator chunk.
        Exercises bounded catalogue streaming across the configured chunk boundary.
        """
        libraries = [
            Library(
                slug=f"bulk-library-{index}",
                name=f"Bulk Library {index}",
                location=Point(x=10 + index / 10_000, y=40, srid=4326),
                address=f"Via Bulk {index}",
                city="Bulk City",
                country="IT",
                status=Library.Status.APPROVED,
            )
            for index in range(library_export.EXPORT_ITERATOR_CHUNK_SIZE + 1)
        ]
        Library.objects.bulk_create(libraries, batch_size=250)

        result = generate_library_export()
        features = _read_features(
            export_directory=export_directory,
            manifest=_read_manifest(export_directory),
        )

        assert result.record_count == library_export.EXPORT_ITERATOR_CHUNK_SIZE + 1
        assert [feature["properties"]["id"] for feature in features] == sorted(
            feature["properties"]["id"] for feature in features
        )

    def test_management_command_reports_a_published_export(
        self,
        export_directory: Path,
    ) -> None:
        """Expose the service through the documented management-command name.
        Gives the Dokku cron job concise output for successful publication.
        """
        _create_library(index=1)
        output = io.StringIO()

        call_command("generate_library_export", stdout=output)

        assert "Library export published:" in output.getvalue()
        assert (export_directory / "latest.json").exists()

    def test_returns_a_locked_outcome_without_writing_files(
        self,
        export_directory: Path,
    ) -> None:
        """Treat an overlapping scheduled run as a harmless skipped execution.
        Avoids file cleanup or publication while another transaction holds the lock.
        """
        _create_library(index=1)

        with patch(
            "libraries.library_export._try_acquire_export_lock",
            return_value=False,
        ):
            result = generate_library_export()

        assert result.outcome == "locked"
        assert not export_directory.exists() or not list(export_directory.iterdir())

    def test_postgresql_lock_blocks_an_overlapping_generator(
        self,
        export_directory: Path,
    ) -> None:
        """Hold the real transaction lock while a second generator starts.
        Confirms concurrent scheduled jobs cannot publish competing manifests.
        """
        _create_library(index=1)
        generation_started = threading.Event()
        release_generation = threading.Event()
        first_outcome: dict[str, object] = {}
        original_writer = library_export._write_geojson_candidate

        def blocking_writer(*, path: Path) -> library_export.CandidateGeoJSON:
            """Pause the first generator after it has acquired the DB lock.
            Gives the second invocation a deterministic overlap window.
            """
            generation_started.set()
            assert release_generation.wait(timeout=5)
            return original_writer(path=path)

        def run_first_generator() -> None:
            """Run the first generator with a thread-local database connection.
            Releases Django connection state after the concurrent test completes.
            """
            close_old_connections()
            try:
                first_outcome["result"] = generate_library_export()
            finally:
                connections.close_all()

        with patch.object(
            library_export,
            "_write_geojson_candidate",
            side_effect=blocking_writer,
        ):
            worker = threading.Thread(target=run_first_generator)
            worker.start()
            assert generation_started.wait(timeout=5)

            second_result = generate_library_export()

            release_generation.set()
            worker.join(timeout=10)

        assert not worker.is_alive()
        assert second_result.outcome == "locked"
        first_result = first_outcome["result"]
        assert isinstance(first_result, library_export.LibraryExportResult)
        assert first_result.outcome == "published"
        assert (export_directory / "latest.json").exists()


def test_app_json_schedules_the_library_export_daily() -> None:
    """Schedule one daily command invocation through Dokku's app manifest.
    Keeps regular export refreshes independent from application deployments.
    """
    app_json_path = Path(__file__).resolve().parent.parent / "app.json"
    app_config = json.loads(app_json_path.read_text(encoding="utf-8"))

    assert {
        "command": "python manage.py generate_library_export",
        "schedule": "0 2 * * *",
    } in app_config["cron"]


def test_app_json_does_not_generate_the_export_during_predeploy() -> None:
    """Keep export generation out of the application deployment path.
    Prevents catalogue serialization from delaying every release.
    """
    app_json_path = Path(__file__).resolve().parent.parent / "app.json"
    app_config = json.loads(app_json_path.read_text(encoding="utf-8"))

    predeploy = app_config["scripts"]["dokku"]["predeploy"]
    assert predeploy == (
        "python manage.py migrate --noinput "
        "&& python manage.py createcachetable --database default"
    )
    assert "generate_library_export" not in predeploy


def test_gzip_output_is_deterministic(tmp_path: Path) -> None:
    """Produce identical compressed bytes for identical GeoJSON bytes.
    Keeps immutable gzip checksums independent of wall-clock generation time.
    """
    source = tmp_path / "libraries.geojson"
    first = tmp_path / "first.geojson.gz"
    second = tmp_path / "second.geojson.gz"
    source.write_bytes(b'{"type":"FeatureCollection","features":[]}\n')
    checksum, byte_size = library_export._hash_file(path=source)

    library_export._write_gzip_candidate(
        source=source,
        destination=first,
        expected_checksum=checksum,
        expected_byte_size=byte_size,
    )
    library_export._write_gzip_candidate(
        source=source,
        destination=second,
        expected_checksum=checksum,
        expected_byte_size=byte_size,
    )

    assert first.read_bytes() == second.read_bytes()
    assert gzip.decompress(first.read_bytes()) == source.read_bytes()
