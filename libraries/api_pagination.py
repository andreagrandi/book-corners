from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import QuerySet

from libraries.api_schemas import PaginationMeta


def paginate_queryset(
    *,
    queryset: QuerySet,
    page: int,
    page_size: int,
    max_page_size: int = 50,
) -> tuple[list, PaginationMeta]:
    """Paginate a queryset and return items with navigation metadata.
    Clamps page and page_size to safe bounds before slicing."""
    clamped_page_size = max(1, min(page_size, max_page_size))
    clamped_page = max(1, page)

    paginator = Paginator(queryset, clamped_page_size)

    if paginator.count == 0:
        return [], PaginationMeta(
            page=1,
            page_size=clamped_page_size,
            total=0,
            total_pages=0,
            has_next=False,
            has_previous=False,
        )

    if clamped_page > paginator.num_pages:
        clamped_page = paginator.num_pages

    page_obj = paginator.page(clamped_page)

    return list(page_obj.object_list), PaginationMeta(
        page=clamped_page,
        page_size=clamped_page_size,
        total=paginator.count,
        total_pages=paginator.num_pages,
        has_next=page_obj.has_next(),
        has_previous=page_obj.has_previous(),
    )
