from __future__ import annotations

import io
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile, File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image, ImageDraw

from libraries.models import Library, Report


@dataclass(frozen=True)
class CitySpec:
    country: str
    city: str
    latitude: float
    longitude: float
    postal_prefix: str
    streets: tuple[str, ...]


CITY_SPECS: tuple[CitySpec, ...] = (
    CitySpec("IT", "Florence", 43.7696, 11.2558, "50", ("Via Roma", "Via dei Neri", "Via San Gallo")),
    CitySpec("IT", "Rome", 41.9028, 12.4964, "00", ("Via del Corso", "Via Cavour", "Via Appia Nuova")),
    CitySpec("IT", "Milan", 45.4642, 9.1900, "20", ("Via Torino", "Corso Buenos Aires", "Via Manzoni")),
    CitySpec("FR", "Paris", 48.8566, 2.3522, "75", ("Rue de Rivoli", "Rue Oberkampf", "Boulevard Voltaire")),
    CitySpec("FR", "Lyon", 45.7640, 4.8357, "69", ("Rue de la Republique", "Rue Victor Hugo", "Cours Lafayette")),
    CitySpec("DE", "Berlin", 52.5200, 13.4050, "10", ("Friedrichstrasse", "Torstrasse", "Karl-Marx-Allee")),
    CitySpec("DE", "Munich", 48.1351, 11.5820, "80", ("Leopoldstrasse", "Sendlinger Strasse", "Landsberger Strasse")),
    CitySpec("ES", "Madrid", 40.4168, -3.7038, "28", ("Calle de Alcala", "Gran Via", "Calle de Atocha")),
    CitySpec("ES", "Barcelona", 41.3874, 2.1686, "08", ("Carrer de Sants", "Carrer d'Arago", "Carrer de Balmes")),
    CitySpec("NL", "Amsterdam", 52.3676, 4.9041, "10", ("Prinsengracht", "Nieuwezijds Voorburgwal", "Rozengracht")),
    CitySpec("GB", "London", 51.5072, -0.1276, "SW", ("Baker Street", "King's Road", "High Street Kensington")),
    CitySpec("PT", "Lisbon", 38.7223, -9.1393, "11", ("Rua Augusta", "Avenida da Liberdade", "Rua do Ouro")),
)

LIBRARY_NAME_PREFIXES: tuple[str, ...] = (
    "Little",
    "Neighborhood",
    "Community",
    "Garden",
    "Corner",
    "Parkside",
    "Riverside",
    "Sunrise",
)

LIBRARY_NAME_SUFFIXES: tuple[str, ...] = (
    "Book Nook",
    "Library Box",
    "Mini Library",
    "Book Share",
    "Reading Spot",
    "Book Exchange",
)

DEFAULT_APPROVED_RATIO = 0.8
DEFAULT_PENDING_RATIO = 0.15
DEFAULT_REJECTED_RATIO = 0.05

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class Command(BaseCommand):
    help = "Seed local library records and optionally reset existing library/report data."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing reports and libraries before generating new data.",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=24,
            help="Number of libraries to generate (default: 24).",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=None,
            help="Optional random seed for deterministic output.",
        )
        parser.add_argument(
            "--images-dir",
            type=str,
            default="libraries_examples",
            help="Directory containing local seed images (reused/cycled as needed).",
        )
        parser.add_argument(
            "--approved-ratio",
            type=float,
            default=DEFAULT_APPROVED_RATIO,
            help="Share of generated records marked as approved (default: 0.8).",
        )
        parser.add_argument(
            "--pending-ratio",
            type=float,
            default=DEFAULT_PENDING_RATIO,
            help="Share of generated records marked as pending (default: 0.15).",
        )
        parser.add_argument(
            "--rejected-ratio",
            type=float,
            default=DEFAULT_REJECTED_RATIO,
            help="Share of generated records marked as rejected (default: 0.05).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        count = options["count"]
        if count < 0:
            raise CommandError("--count must be zero or greater.")

        approved_ratio = options["approved_ratio"]
        pending_ratio = options["pending_ratio"]
        rejected_ratio = options["rejected_ratio"]
        total_ratio = approved_ratio + pending_ratio + rejected_ratio

        if min(approved_ratio, pending_ratio, rejected_ratio) < 0:
            raise CommandError("Status ratios must be non-negative numbers.")
        if total_ratio <= 0:
            raise CommandError("At least one status ratio must be greater than zero.")

        rng = random.Random(options["seed"])

        if options["reset"]:
            self._reset_data()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No new libraries requested. Done."))
            return

        image_paths = self._collect_seed_images(images_dir=Path(options["images_dir"]))
        if image_paths:
            self.stdout.write(f"Using {len(image_paths)} image(s) from seed directory.")
        else:
            self.stdout.write(
                self.style.WARNING(
                    "No seed images found. Falling back to generated placeholder images.",
                )
            )

        created_by = self._get_or_create_seed_user()

        created_count = self._create_libraries(
            count=count,
            created_by=created_by,
            image_paths=image_paths,
            approved_ratio=approved_ratio,
            pending_ratio=pending_ratio,
            rejected_ratio=rejected_ratio,
            rng=rng,
        )

        self.stdout.write(self.style.SUCCESS(f"Created {created_count} libraries."))

    def _reset_data(self) -> None:
        with transaction.atomic():
            report_count = Report.objects.count()
            library_count = Library.objects.count()
            Report.objects.all().delete()
            Library.objects.all().delete()

        self.stdout.write(
            self.style.WARNING(
                f"Deleted {report_count} reports and {library_count} libraries.",
            )
        )

    def _get_or_create_seed_user(self):
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username="seedbot",
            defaults={
                "email": "seedbot@example.com",
            },
        )

        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])

        return user

    def _collect_seed_images(self, *, images_dir: Path) -> list[Path]:
        if images_dir.is_absolute():
            root = images_dir
        else:
            root = Path(settings.BASE_DIR) / images_dir

        if not root.exists() or not root.is_dir():
            return []

        image_paths = [
            path
            for path in sorted(root.iterdir())
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        ]
        return image_paths

    def _create_libraries(
        self,
        *,
        count: int,
        created_by,
        image_paths: list[Path],
        approved_ratio: float,
        pending_ratio: float,
        rejected_ratio: float,
        rng: random.Random,
    ) -> int:
        status_weights = [approved_ratio, pending_ratio, rejected_ratio]
        status_options = [
            Library.Status.APPROVED,
            Library.Status.PENDING,
            Library.Status.REJECTED,
        ]

        created_count = 0
        for index in range(1, count + 1):
            city_spec = rng.choice(CITY_SPECS)
            status = rng.choices(status_options, weights=status_weights, k=1)[0]

            location = Point(
                x=city_spec.longitude + rng.uniform(-0.04, 0.04),
                y=city_spec.latitude + rng.uniform(-0.03, 0.03),
                srid=4326,
            )

            street = rng.choice(city_spec.streets)
            street_number = rng.randint(1, 220)
            address = f"{street} {street_number}"
            postal_code = f"{city_spec.postal_prefix}{rng.randint(0, 99):02d}"

            name = f"{rng.choice(LIBRARY_NAME_PREFIXES)} {rng.choice(LIBRARY_NAME_SUFFIXES)}"
            description = (
                f"A community little free library in {city_spec.city}, maintained by local "
                f"volunteers near {street}."
            )

            library = Library(
                name=name,
                description=description,
                location=location,
                address=address,
                city=city_spec.city,
                country=city_spec.country,
                postal_code=postal_code,
                status=status,
                created_by=created_by,
            )

            self._attach_photo(
                library=library,
                image_paths=image_paths,
                city_spec=city_spec,
                index=index,
                rng=rng,
            )

            library.save()
            created_count += 1

        return created_count

    def _attach_photo(
        self,
        *,
        library: Library,
        image_paths: list[Path],
        city_spec: CitySpec,
        index: int,
        rng: random.Random,
    ) -> None:
        if image_paths:
            selected_image = rng.choice(image_paths)
            with selected_image.open("rb") as image_file:
                library.photo.save(selected_image.name, File(image_file), save=False)
            return

        placeholder_bytes = self._build_placeholder_image(
            city=city_spec.city,
            country=city_spec.country,
            index=index,
            rng=rng,
        )
        filename = f"library-seed-{index:04d}.jpg"
        library.photo.save(filename, ContentFile(placeholder_bytes), save=False)

    def _build_placeholder_image(
        self,
        *,
        city: str,
        country: str,
        index: int,
        rng: random.Random,
    ) -> bytes:
        base_color = (
            rng.randint(35, 95),
            rng.randint(95, 155),
            rng.randint(140, 210),
        )
        image = Image.new("RGB", (1200, 900), color=base_color)
        draw = ImageDraw.Draw(image)

        draw.rectangle((60, 60, 1140, 840), outline=(245, 245, 245), width=5)
        draw.text((100, 120), "Little Free Library", fill=(250, 250, 250))
        draw.text((100, 170), f"{city}, {country}", fill=(250, 250, 250))
        draw.text((100, 220), f"Seed image #{index}", fill=(250, 250, 250))

        payload = io.BytesIO()
        image.save(payload, format="JPEG", quality=88)
        return payload.getvalue()
