from __future__ import annotations

from typing import Any

from django.db.models import Exists, F, OuterRef, QuerySet

from libraries.models import Library
from users.models import ContributorAgreementAcceptance

CC_BY_SA_4_0 = "CC-BY-SA-4.0"
PHOTO_ORIGIN_USER = Library.PhotoOrigin.USER.value
PHOTO_ORIGIN_UNKNOWN = Library.PhotoOrigin.UNKNOWN.value
AUTHOR_ACCEPTED_ANNOTATION = "_photo_author_has_agreement"
ANNOTATED_AUTHOR_ID = "_photo_licence_author_id"
PHOTO_LICENCE_CACHE_ATTRIBUTE = "_photo_licence_fields"


def photo_author_has_agreement_exists(*, author_reference: str) -> Exists:
    """Build an Exists expression for any contributor agreement acceptance.
    Any version or date counts, because the agreement covers earlier contributions."""
    return Exists(
        ContributorAgreementAcceptance.objects.filter(
            user=OuterRef(author_reference),
        )
    )


def annotate_library_photo_licence(queryset: QuerySet) -> QuerySet:
    """Prefetch the main photo author and agreement flag on a Library queryset.
    Keeps licence resolution free of per-row queries."""
    return queryset.select_related("photo_author").annotate(
        **{
            AUTHOR_ACCEPTED_ANNOTATION: photo_author_has_agreement_exists(
                author_reference="photo_author",
            ),
            ANNOTATED_AUTHOR_ID: F("photo_author"),
        }
    )


def annotate_library_photo_licence_for_user_photos(queryset: QuerySet) -> QuerySet:
    """Prefetch the uploader and agreement flag on a LibraryPhoto queryset.
    Keeps licence resolution free of per-row queries."""
    return queryset.select_related("created_by").annotate(
        **{
            AUTHOR_ACCEPTED_ANNOTATION: photo_author_has_agreement_exists(
                author_reference="created_by",
            ),
            ANNOTATED_AUTHOR_ID: F("created_by"),
        }
    )


def build_photo_licence_fields(
    *,
    has_photo: bool,
    origin: str,
    author_username: str | None,
    author_has_agreement: bool,
    source_url: str,
) -> dict[str, str | None]:
    """Apply the image licence rules to plain values.
    An image is CC BY-SA only with user origin, a known author and an agreement."""
    if not has_photo:
        return {
            "photo_origin": None,
            "photo_license": None,
            "photo_author": None,
            "photo_source_url": None,
        }

    resolved_origin = origin or PHOTO_ORIGIN_UNKNOWN
    is_cc_by_sa = bool(
        resolved_origin == PHOTO_ORIGIN_USER
        and author_username
        and author_has_agreement
    )
    return {
        "photo_origin": resolved_origin,
        "photo_license": CC_BY_SA_4_0 if is_cc_by_sa else None,
        "photo_author": author_username if is_cc_by_sa else None,
        "photo_source_url": source_url or None,
    }


def _author_has_agreement(*, obj: Any, author: Any) -> bool:
    """Return the agreement flag from the queryset annotation or a direct query.
    Ignores the annotation when the author changed after loading, as in previews."""
    if author is None:
        return False
    annotated = getattr(obj, AUTHOR_ACCEPTED_ANNOTATION, None)
    if annotated is not None and getattr(obj, ANNOTATED_AUTHOR_ID, None) == author.pk:
        return bool(annotated)
    return ContributorAgreementAcceptance.objects.filter(user=author).exists()


def get_library_photo_licence_fields(*, library: Any) -> dict[str, str | None]:
    """Return the four licence fields for a library's main photo.
    Caches the result on the instance so the four resolvers share one lookup."""
    cached = getattr(library, PHOTO_LICENCE_CACHE_ATTRIBUTE, None)
    if cached is not None:
        return cached
    author = library.photo_author if library.photo_author_id else None
    fields = build_photo_licence_fields(
        has_photo=bool(library.photo),
        origin=library.photo_origin,
        author_username=author.username if author else None,
        author_has_agreement=_author_has_agreement(obj=library, author=author),
        source_url=library.photo_source_url,
    )
    setattr(library, PHOTO_LICENCE_CACHE_ATTRIBUTE, fields)
    return fields


def get_user_photo_licence_fields(*, photo: Any) -> dict[str, str | None]:
    """Return the four licence fields for a community photo.
    Community photos always have user origin and no source URL."""
    cached = getattr(photo, PHOTO_LICENCE_CACHE_ATTRIBUTE, None)
    if cached is not None:
        return cached
    author = photo.created_by if photo.created_by_id else None
    fields = build_photo_licence_fields(
        has_photo=bool(photo.photo),
        origin=PHOTO_ORIGIN_USER,
        author_username=author.username if author else None,
        author_has_agreement=_author_has_agreement(obj=photo, author=author),
        source_url="",
    )
    setattr(photo, PHOTO_LICENCE_CACHE_ATTRIBUTE, fields)
    return fields
