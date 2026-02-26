import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point

from libraries.api_pagination import paginate_queryset
from libraries.models import Library

User = get_user_model()


@pytest.fixture
def pagination_user(db):
    """Create a user for library ownership in pagination tests.
    Provides a reusable author for generated library rows."""
    return User.objects.create_user(username="paguser", password="testpass123")


def _create_libraries(*, user, count):
    """Create a batch of approved libraries for pagination tests.
    Generates sequential entries with unique addresses."""
    for i in range(count):
        Library.objects.create(
            name=f"Library {i}",
            address=f"{i} Main St",
            city="TestCity",
            country="DE",
            location=Point(x=8.0 + i * 0.01, y=50.0),
            status=Library.Status.APPROVED,
            created_by=user,
            photo="libraries/photos/test.jpg",
        )


@pytest.mark.django_db
class TestPaginateQueryset:
    """Tests for the paginate_queryset utility function."""

    def test_first_page_returns_correct_items_and_metadata(self, pagination_user):
        """Verify first page returns expected items and navigation flags.
        Confirms has_previous is false and has_next is true for multi-page sets."""
        _create_libraries(user=pagination_user, count=15)
        queryset = Library.objects.order_by("id")

        items, meta = paginate_queryset(queryset=queryset, page=1, page_size=10)

        assert len(items) == 10
        assert meta.page == 1
        assert meta.page_size == 10
        assert meta.total == 15
        assert meta.total_pages == 2
        assert meta.has_next is True
        assert meta.has_previous is False

    def test_last_page_returns_remaining_items(self, pagination_user):
        """Verify last page returns leftover items with correct flags.
        Confirms has_next is false and has_previous is true."""
        _create_libraries(user=pagination_user, count=15)
        queryset = Library.objects.order_by("id")

        items, meta = paginate_queryset(queryset=queryset, page=2, page_size=10)

        assert len(items) == 5
        assert meta.page == 2
        assert meta.has_next is False
        assert meta.has_previous is True

    def test_page_beyond_range_clamps_to_last_page(self, pagination_user):
        """Verify out-of-range page numbers clamp to the last page.
        Prevents empty responses for overshoot page requests."""
        _create_libraries(user=pagination_user, count=5)
        queryset = Library.objects.order_by("id")

        items, meta = paginate_queryset(queryset=queryset, page=999, page_size=5)

        assert len(items) == 5
        assert meta.page == 1
        assert meta.total_pages == 1

    def test_page_size_clamped_to_max(self, pagination_user):
        """Verify page_size exceeding max gets clamped down.
        Prevents clients from requesting unbounded result sets."""
        _create_libraries(user=pagination_user, count=5)
        queryset = Library.objects.order_by("id")

        items, meta = paginate_queryset(
            queryset=queryset, page=1, page_size=200, max_page_size=50,
        )

        assert meta.page_size == 50

    def test_negative_page_clamps_to_first(self, pagination_user):
        """Verify negative page numbers clamp to page 1.
        Handles malformed client input without raising errors."""
        _create_libraries(user=pagination_user, count=5)
        queryset = Library.objects.order_by("id")

        items, meta = paginate_queryset(queryset=queryset, page=-3, page_size=10)

        assert meta.page == 1
        assert len(items) == 5

    def test_empty_queryset_returns_zeroed_metadata(self, pagination_user):
        """Verify empty queryset returns empty items and zeroed pagination.
        Ensures clean response shape when no results match filters."""
        queryset = Library.objects.none()

        items, meta = paginate_queryset(queryset=queryset, page=1, page_size=10)

        assert items == []
        assert meta.page == 1
        assert meta.total == 0
        assert meta.total_pages == 0
        assert meta.has_next is False
        assert meta.has_previous is False
