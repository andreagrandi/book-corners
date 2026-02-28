"""Management command to detect and optionally remove duplicate libraries.

Identifies duplicates via normalized address matching and geographic
proximity, then reports or auto-deletes them.
"""

from collections import defaultdict

from django.contrib.gis.measure import D
from django.core.management.base import BaseCommand

from libraries.models import Library


DEFAULT_RADIUS_METERS = 100


def _normalize(value: str) -> str:
    """Normalize a string for comparison.
    Strips whitespace and lowercases for consistent matching."""
    return value.strip().lower()


class UnionFind:
    """Disjoint-set structure for merging overlapping duplicate groups.
    Efficiently collapses transitive relationships into single groups."""

    def __init__(self):
        """Initialize an empty union-find structure.
        Parents map is populated lazily as elements are added."""
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        """Find the root representative of an element.
        Applies path compression for amortized constant time."""
        if x not in self.parent:
            self.parent[x] = x
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        """Merge two elements into the same group.
        Uses find to resolve roots before linking."""
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def find_duplicate_groups(
    *,
    radius_meters: int = DEFAULT_RADIUS_METERS,
    city: str = "",
    country: str = "",
) -> list[list[Library]]:
    """Detect duplicate library groups via address and proximity.
    Returns lists of libraries that appear to be duplicates of each other."""
    queryset = Library.objects.all()
    if city:
        queryset = queryset.filter(city__iexact=city)
    if country:
        queryset = queryset.filter(country__iexact=country)

    libraries = list(queryset.order_by("created_at"))
    if not libraries:
        return []

    uf = UnionFind()

    # Pass 1: exact normalized (city, address) matches
    address_groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for lib in libraries:
        key = (_normalize(lib.city), _normalize(lib.address))
        address_groups[key].append(lib.pk)

    for pks in address_groups.values():
        if len(pks) > 1:
            for pk in pks[1:]:
                uf.union(pks[0], pk)

    # Pass 2: geographic proximity
    for i, lib in enumerate(libraries):
        nearby = queryset.filter(
            location__distance_lte=(lib.location, D(m=radius_meters)),
        ).exclude(pk=lib.pk).values_list("pk", flat=True)
        for other_pk in nearby:
            uf.union(lib.pk, other_pk)

    # Collect groups
    groups: dict[int, list[int]] = defaultdict(list)
    for lib in libraries:
        root = uf.find(lib.pk)
        groups[root].append(lib.pk)

    # Filter to groups with >1 member, build Library lists
    lib_by_pk = {lib.pk: lib for lib in libraries}
    result = []
    for pks in groups.values():
        if len(pks) > 1:
            group = [lib_by_pk[pk] for pk in sorted(pks)]
            result.append(group)

    return sorted(result, key=lambda g: g[0].pk)


class Command(BaseCommand):
    """Detect duplicate libraries by address and geographic proximity."""

    help = "Find duplicate libraries based on address and location proximity"

    def add_arguments(self, parser):
        """Define command-line options for filtering and auto-delete.
        Provides radius, city, country, and auto-delete controls."""
        parser.add_argument(
            "--radius",
            type=int,
            default=DEFAULT_RADIUS_METERS,
            help="Proximity radius in meters (default: 100)",
        )
        parser.add_argument(
            "--city",
            type=str,
            default="",
            help="Filter by city name",
        )
        parser.add_argument(
            "--country",
            type=str,
            default="",
            help="Filter by country code (e.g. IT)",
        )
        parser.add_argument(
            "--auto-delete",
            action="store_true",
            help="Delete duplicates automatically (keeps oldest per group)",
        )

    def handle(self, *args, **options):
        """Run duplicate detection and optionally delete duplicates.
        Prints grouped results and prompts before auto-delete."""
        groups = find_duplicate_groups(
            radius_meters=options["radius"],
            city=options["city"],
            country=options["country"],
        )

        if not groups:
            self.stdout.write(self.style.SUCCESS("No duplicates found."))
            return

        total_duplicates = sum(len(g) - 1 for g in groups)
        self.stdout.write(
            self.style.WARNING(
                f"Found {len(groups)} duplicate groups ({total_duplicates} extra entries):"
            )
        )
        self.stdout.write("")

        for i, group in enumerate(groups, start=1):
            self.stdout.write(self.style.MIGRATE_HEADING(f"Group {i}:"))
            for lib in group:
                admin_url = f"/admin/libraries/library/{lib.pk}/change/"
                self.stdout.write(
                    f"  ID={lib.pk} | {lib.name or '(unnamed)'} | "
                    f"{lib.address}, {lib.city} | "
                    f"ext_id={lib.external_id or '(none)'} | "
                    f"status={lib.status} | "
                    f"created={lib.created_at:%Y-%m-%d} | "
                    f"{admin_url}"
                )
            self.stdout.write("")

        if options["auto_delete"]:
            confirm = input(
                f"Delete {total_duplicates} duplicate(s), keeping oldest per group? [y/N] "
            )
            if confirm.lower() == "y":
                deleted_count = 0
                for group in groups:
                    to_delete = group[1:]  # keep oldest (first, sorted by created_at)
                    for lib in to_delete:
                        lib.delete()
                        deleted_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"Deleted {deleted_count} duplicate(s).")
                )
            else:
                self.stdout.write("Aborted.")
