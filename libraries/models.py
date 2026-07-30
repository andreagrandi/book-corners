import re
import uuid
from copy import copy
from functools import partial
from typing import Any

from django.conf import settings
from django.contrib.gis.db.models import PointField
from django.contrib.gis.geos import Point
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.files.storage import Storage
from django.db import models, transaction
from django.db.models import Q
from django.utils.text import slugify
from django.utils.translation import gettext, gettext_lazy as _

from libraries.image_processing import build_library_photo_files

MAX_LIBRARY_PHOTOS_PER_USER = 3
LIBRARY_PLACEHOLDER_IMAGE = "images/library-placeholder.png"
LIBRARY_PLACEHOLDER_IMAGE_WEBP = "images/library-placeholder.webp"
LIBRARY_EDITABLE_FIELDS = (
    "name",
    "description",
    "address",
    "city",
    "country",
    "postal_code",
    "wheelchair_accessible",
    "capacity",
    "is_indoor",
    "is_lit",
    "website",
    "contact",
    "operator",
    "brand",
)


class Library(models.Model):
    """A community book exchange library location with its details."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    class SubmissionOrigin(models.TextChoices):
        LEGACY = "legacy", _("Legacy")
        USER = "user", _("User")
        STAFF = "staff", _("Staff")
        IMPORT = "import", _("Import")

    class WheelchairAccess(models.TextChoices):
        YES = "yes", _("Yes")
        NO = "no", _("No")
        LIMITED = "limited", _("Limited")

    name = models.CharField(max_length=255, blank=True, default="")
    slug = models.SlugField(max_length=280, unique=True, editable=False)
    description = models.TextField(blank=True, default="")
    photo = models.ImageField(upload_to="libraries/photos/%Y/%m/", blank=True, default="")
    photo_thumbnail = models.ImageField(
        upload_to="libraries/photos/thumbnails/%Y/%m/",
        blank=True,
        default="",
    )
    pending_photo = models.ImageField(
        upload_to="libraries/pending_photos/%Y/%m/",
        blank=True,
        default="",
    )
    pending_photo_thumbnail = models.ImageField(
        upload_to="libraries/pending_photos/thumbnails/%Y/%m/",
        blank=True,
        default="",
    )
    pending_changes = models.JSONField(null=True, blank=True, default=None)
    location = PointField(srid=4326)
    address = models.CharField(max_length=255, blank=True, default="")
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=2)
    postal_code = models.CharField(max_length=20, blank=True, default="")
    wheelchair_accessible = models.CharField(
        max_length=10,
        choices=WheelchairAccess.choices,
        blank=True,
        default="",
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)
    is_indoor = models.BooleanField(null=True, blank=True)
    is_lit = models.BooleanField(null=True, blank=True)
    website = models.URLField(max_length=500, blank=True, default="")
    contact = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=100, blank=True, default="")
    operator = models.CharField(max_length=255, blank=True, default="")
    brand = models.CharField(max_length=255, blank=True, default="")
    external_id = models.CharField(max_length=100, blank=True, default="", db_index=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    rejection_reason = models.TextField(
        verbose_name=_("Rejection reason"),
        blank=True,
        default="",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="libraries",
    )
    osm_submission_allowed = models.BooleanField(default=False)
    osm_submission_allowed_at = models.DateTimeField(null=True, blank=True)
    submission_origin = models.CharField(
        max_length=10,
        choices=SubmissionOrigin.choices,
        default=SubmissionOrigin.LEGACY,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "libraries"
        ordering = ["-created_at"]
        verbose_name_plural = "libraries"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        osm_submission_allowed=False,
                        osm_submission_allowed_at__isnull=True,
                    )
                    | Q(
                        osm_submission_allowed=True,
                        osm_submission_allowed_at__isnull=False,
                        submission_origin="user",
                    )
                ),
                name="lib_osm_permission_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["city", "address"], name="idx_lib_city_address"),
            models.Index(fields=["country"], name="idx_lib_country"),
            models.Index(fields=["-created_at"], name="idx_lib_created_at_desc"),
            models.Index(fields=["-updated_at"], name="idx_lib_updated_at_desc"),
            models.Index(fields=["source"], name="idx_lib_source"),
            models.Index(fields=["status", "-created_at"], name="idx_lib_status_created"),
            models.Index(fields=["created_by", "-created_at"], name="idx_lib_creator_created"),
            models.Index(fields=["operator"], name="idx_lib_operator"),
            models.Index(fields=["brand"], name="idx_lib_brand"),
            models.Index(
                fields=["submission_origin"],
                name="idx_lib_submission_origin",
            ),
        ]

    def __init__(self, *args, **kwargs):
        """Initialize instance and snapshot the current photo name.
        Allows cheap in-memory change detection on save."""
        super().__init__(*args, **kwargs)
        self._original_photo_name = self._loaded_file_name(field_name="photo")
        self._original_photo_thumbnail_name = self._loaded_file_name(
            field_name="photo_thumbnail"
        )

    def __str__(self) -> str:
        """Return a readable string representation.
        Keeps output clear in logs and admin screens."""
        if self.name:
            return f"{self.name} ({self.city})"
        if self.address:
            return f"{self.address}, {self.city}"
        return self.city

    def osm_precheck_ineligibility_reason(self) -> str | None:
        """Return the first reason an OSM duplicate check is unavailable.
        Applies durable submission and provenance safeguards in one place."""
        if self.status != self.Status.APPROVED:
            return gettext("The library is not approved.")
        if self.submission_origin != self.SubmissionOrigin.USER:
            return gettext("The library was not submitted directly by a user.")
        if self.created_by_id is None:
            return gettext("The original submitter account is no longer linked.")
        if not self.osm_submission_allowed:
            return gettext("The submitter did not allow OpenStreetMap submission.")
        if self._has_osm_origin():
            return gettext("The library originated from OpenStreetMap.")
        if self.has_pending_update or self.pending_photo:
            return gettext("The library has an update awaiting moderation.")

        contribution = self._osm_contribution_or_none()
        if contribution is None:
            return None
        if contribution.status == OpenStreetMapContribution.Status.CONTRIBUTED:
            return gettext("The library was already contributed to OpenStreetMap.")
        if (
            contribution.status == OpenStreetMapContribution.Status.ALREADY_PRESENT
            or contribution.osm_element_id is not None
        ):
            return gettext("An existing OpenStreetMap feature is already linked.")
        if contribution.status == OpenStreetMapContribution.Status.SUBMITTING:
            return gettext("An OpenStreetMap submission is already in progress.")
        return None

    def osm_contribution_ineligibility_reason(
        self,
        *,
        max_age_seconds: int,
    ) -> str | None:
        """Return the first reason a future OSM contribution is blocked.
        Extends base eligibility with duplicate-check state and freshness."""
        precheck_reason = self.osm_precheck_ineligibility_reason()
        if precheck_reason is not None:
            return precheck_reason

        contribution = self._osm_contribution_or_none()
        if (
            contribution is None
            or contribution.status == OpenStreetMapContribution.Status.UNCHECKED
        ):
            return gettext("An OpenStreetMap duplicate check has not been completed.")
        if contribution.status == OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE:
            return gettext("A possible OpenStreetMap duplicate requires resolution.")
        if contribution.status == OpenStreetMapContribution.Status.FAILED:
            return gettext("The latest OpenStreetMap duplicate check failed.")
        if contribution.status != OpenStreetMapContribution.Status.NO_MATCH:
            return gettext("The OpenStreetMap state does not allow contribution.")
        if not contribution.duplicate_check_is_current(
            max_age_seconds=max_age_seconds
        ):
            return gettext("The latest OpenStreetMap duplicate check is stale.")
        return None

    def _has_osm_origin(self) -> bool:
        """Return whether source metadata identifies an OSM record.
        Provides a second safety check in addition to durable provenance."""
        compact_source = re.sub(r"[^a-z0-9]", "", self.source.casefold())
        if compact_source == "osm" or "openstreetmap" in compact_source:
            return True
        return bool(
            re.search(
                r"(?:^|[\s:/])(?:node|way|relation)[/:]\d+(?:$|[\s/?#])",
                self.external_id.casefold(),
            )
        )

    def _osm_contribution_or_none(self) -> "OpenStreetMapContribution | None":
        """Return the related OSM state without raising for absent rows.
        Treats missing state as the explicit unchecked condition."""
        try:
            return self.osm_contribution
        except OpenStreetMapContribution.DoesNotExist:
            return None

    def save(self, *args, **kwargs) -> None:
        """Persist the model instance.
        Applies model-specific rules before writing data."""
        was_adding = self._state.adding
        old_photo_name = self._original_photo_name
        old_thumbnail_name = self._original_photo_thumbnail_name
        save_kwargs = kwargs

        if not self.slug:
            self.slug = self._generate_unique_slug()

        if self._photo_needs_processing():
            self._optimize_uploaded_photo()
            save_kwargs = self._merge_photo_fields_into_update_kwargs(kwargs=kwargs)

        super().save(*args, **save_kwargs)
        if not was_adding:
            self._schedule_superseded_photo_cleanup(
                old_photo_name=old_photo_name,
                old_thumbnail_name=old_thumbnail_name,
            )
        self._original_photo_name = self._loaded_file_name(field_name="photo")
        self._original_photo_thumbnail_name = self._loaded_file_name(
            field_name="photo_thumbnail"
        )

    @property
    def has_pending_update(self) -> bool:
        """Return whether owner-proposed changes await moderation.
        Uses null to distinguish no proposal from a photo-only proposal."""
        return self.pending_changes is not None

    def stage_update(
        self,
        *,
        changes: dict[str, Any],
        photo: Any | None = None,
    ) -> bool:
        """Store proposed edits without changing approved public fields.
        Returns whether the pending proposal changed."""
        staged_changes = dict(self.pending_changes or {})
        coordinate_names = {"latitude", "longitude"}

        for field_name, value in changes.items():
            if field_name in coordinate_names:
                continue
            if field_name not in LIBRARY_EDITABLE_FIELDS:
                continue
            if value == getattr(self, field_name):
                staged_changes.pop(field_name, None)
            else:
                staged_changes[field_name] = value

        if coordinate_names.issubset(changes):
            latitude = changes["latitude"]
            longitude = changes["longitude"]
            if latitude == self.location.y and longitude == self.location.x:
                staged_changes.pop("latitude", None)
                staged_changes.pop("longitude", None)
            else:
                staged_changes["latitude"] = latitude
                staged_changes["longitude"] = longitude

        pending_changes = (
            staged_changes
            if staged_changes or self.pending_photo or photo is not None
            else None
        )
        proposal_changed = (
            pending_changes != self.pending_changes or photo is not None
        )
        if not proposal_changed:
            return False

        update_fields = ["pending_changes", "updated_at"]
        if photo is not None:
            self._store_pending_photo(photo=photo)
            update_fields.extend(["pending_photo", "pending_photo_thumbnail"])

        self.pending_changes = pending_changes
        self.save(update_fields=update_fields)
        return True

    def moderation_preview(self) -> "Library":
        """Return an unsaved view of the proposed moderated values.
        Keeps the persisted approved record unchanged for public queries."""
        preview = copy(self)
        for field_name, value in (self.pending_changes or {}).items():
            if field_name in LIBRARY_EDITABLE_FIELDS:
                setattr(preview, field_name, value)

        changes = self.pending_changes or {}
        if "latitude" in changes and "longitude" in changes:
            preview.location = Point(
                x=changes["longitude"],
                y=changes["latitude"],
                srid=4326,
            )

        if self.pending_photo:
            preview.photo = self.pending_photo.name
            preview.photo_thumbnail = self.pending_photo_thumbnail.name

        if self.has_pending_update:
            preview.status = self.Status.PENDING
            preview.rejection_reason = ""

        return preview

    def apply_pending_update(self) -> None:
        """Apply approved proposed values to the public library record.
        Clears the moderation payload after the live fields are updated."""
        if not self.has_pending_update:
            return

        update_fields = [
            "pending_changes",
            "pending_photo",
            "pending_photo_thumbnail",
            "updated_at",
        ]
        for field_name, value in (self.pending_changes or {}).items():
            if field_name not in LIBRARY_EDITABLE_FIELDS:
                continue
            setattr(self, field_name, value)
            update_fields.append(field_name)

        changes = self.pending_changes or {}
        if "latitude" in changes and "longitude" in changes:
            self.location = Point(
                x=changes["longitude"],
                y=changes["latitude"],
                srid=4326,
            )
            update_fields.append("location")

        if self.pending_photo:
            self.photo = self.pending_photo.name
            self.photo_thumbnail = self.pending_photo_thumbnail.name
            update_fields.extend(["photo", "photo_thumbnail"])

        self.pending_changes = None
        self.pending_photo = ""
        self.pending_photo_thumbnail = ""
        self.save(update_fields=set(update_fields))

    def discard_pending_update(self) -> None:
        """Discard proposed values while retaining the approved library.
        Removes staged image files because no live field references them."""
        if not self.has_pending_update:
            return

        if self.pending_photo:
            self.pending_photo.delete(save=False)
        if self.pending_photo_thumbnail:
            self.pending_photo_thumbnail.delete(save=False)

        self.pending_changes = None
        self.pending_photo = ""
        self.pending_photo_thumbnail = ""
        self.save(
            update_fields=[
                "pending_changes",
                "pending_photo",
                "pending_photo_thumbnail",
                "updated_at",
            ]
        )

    def _store_pending_photo(self, *, photo: Any) -> None:
        """Optimize and store a replacement photo for moderation.
        Deletes superseded staged files after their replacements are ready."""
        old_photo_name = self.pending_photo.name
        old_thumbnail_name = self.pending_photo_thumbnail.name
        main_image, thumbnail_image = build_library_photo_files(
            image_file=photo,
            original_name=getattr(photo, "name", "library-photo.jpg"),
        )
        main_filename, main_content = main_image
        thumbnail_filename, thumbnail_content = thumbnail_image

        self.pending_photo.save(main_filename, main_content, save=False)
        self.pending_photo_thumbnail.save(
            thumbnail_filename,
            thumbnail_content,
            save=False,
        )

        if old_photo_name and old_photo_name != self.pending_photo.name:
            self.pending_photo.storage.delete(old_photo_name)
        if (
            old_thumbnail_name
            and old_thumbnail_name != self.pending_photo_thumbnail.name
        ):
            self.pending_photo_thumbnail.storage.delete(old_thumbnail_name)

    @property
    def card_photo_url(self) -> str:
        """Return the best image URL for cards and map popups.
        Prefers thumbnails, falls back to primary photo, then placeholder."""
        if self.photo_thumbnail:
            try:
                return self.photo_thumbnail.url
            except ValueError:
                pass

        if self.photo:
            try:
                return self.photo.url
            except ValueError:
                pass

        return staticfiles_storage.url(LIBRARY_PLACEHOLDER_IMAGE)

    @property
    def card_photo_url_webp(self) -> str:
        """Return the WebP placeholder URL when no photo is available.
        Returns empty string when a real photo exists (no WebP conversion needed)."""
        if self.photo_thumbnail or self.photo:
            return ""
        return staticfiles_storage.url(LIBRARY_PLACEHOLDER_IMAGE_WEBP)

    def _photo_needs_processing(self) -> bool:
        """Determine whether the current photo should be optimized.
        Compares against the snapshot taken at init to avoid a DB query."""
        if not self.photo:
            return False

        if getattr(self.photo, "_file", None) is None:
            return False

        if self._state.adding or self.pk is None:
            return True

        return self._original_photo_name != self.photo.name

    def _optimize_uploaded_photo(self) -> None:
        """Create optimized JPEG derivatives for the uploaded library photo.
        Stores both the resized primary image and the card-size thumbnail."""
        source_photo = getattr(self.photo, "_file", None)
        if source_photo is None:
            return

        main_image, thumbnail_image = build_library_photo_files(
            image_file=source_photo,
            original_name=self.photo.name or "library-photo.jpg",
        )
        main_filename, main_content = main_image
        thumbnail_filename, thumbnail_content = thumbnail_image

        self.photo.save(main_filename, main_content, save=False)
        self.photo_thumbnail.save(thumbnail_filename, thumbnail_content, save=False)

    def _merge_photo_fields_into_update_kwargs(
        self,
        *,
        kwargs: dict[str, object],
    ) -> dict[str, object]:
        """Ensure generated photo fields persist during update-only saves.
        Adds primary and thumbnail fields when update_fields is provided."""
        update_fields = kwargs.get("update_fields")
        if update_fields is None:
            return kwargs

        merged_kwargs = dict(kwargs)
        if isinstance(update_fields, str):
            merged_update_fields = {update_fields}
        else:
            try:
                merged_update_fields = set(update_fields)
            except TypeError:
                return kwargs
        merged_update_fields.update({"photo", "photo_thumbnail"})
        merged_kwargs["update_fields"] = merged_update_fields
        return merged_kwargs

    def _schedule_superseded_photo_cleanup(
        self,
        *,
        old_photo_name: str,
        old_thumbnail_name: str,
    ) -> None:
        """Schedule deletion of replaced live photo files after commit.
        Defers cleanup so a rolled-back database update keeps its files."""
        photo_field = self._meta.get_field("photo")
        thumbnail_field = self._meta.get_field("photo_thumbnail")
        superseded_files = (
            (
                old_photo_name,
                self._loaded_file_name(field_name="photo"),
                photo_field.storage,
            ),
            (
                old_thumbnail_name,
                self._loaded_file_name(field_name="photo_thumbnail"),
                thumbnail_field.storage,
            ),
        )
        for old_name, current_name, storage in superseded_files:
            if not old_name or old_name == current_name:
                continue
            transaction.on_commit(
                partial(
                    self._delete_unreferenced_photo_file,
                    storage=storage,
                    name=old_name,
                )
            )

    @classmethod
    def _delete_unreferenced_photo_file(
        cls,
        *,
        storage: Storage,
        name: str,
    ) -> None:
        """Delete a superseded file only when no photo field references it.
        Protects shared community and staged images from premature cleanup."""
        library_reference = cls.objects.filter(
            Q(photo=name)
            | Q(photo_thumbnail=name)
            | Q(pending_photo=name)
            | Q(pending_photo_thumbnail=name)
        ).exists()
        community_reference = LibraryPhoto.objects.filter(
            Q(photo=name) | Q(photo_thumbnail=name)
        ).exists()
        if not library_reference and not community_reference:
            storage.delete(name)

    def _loaded_file_name(self, *, field_name: str) -> str:
        """Return a loaded file field name without resolving deferred data.
        Avoids recursive refreshes for querysets that omit photo columns."""
        field_value = self.__dict__.get(field_name, "")
        return getattr(field_value, "name", field_value) or ""

    def _generate_unique_slug(self) -> str:
        """Generate a unique slug from city, address, and optionally name.
        Uses at most two queries instead of one per collision."""
        max_length = self._meta.get_field("slug").max_length
        suffix_reserve = 4  # room for "-999"

        if self.name:
            base = slugify(f"{self.city} {self.address} {self.name}")
        else:
            base = slugify(f"{self.city} {self.address}")

        base = base[: max_length - suffix_reserve]

        if not base:
            base = uuid.uuid4().hex[:8]

        if not Library.objects.filter(slug=base).exists():
            return base

        # Find the highest numeric suffix for this base slug
        existing_slugs = Library.objects.filter(
            slug__startswith=f"{base}-"
        ).values_list("slug", flat=True)

        max_suffix = 1
        for slug in existing_slugs:
            suffix = slug[len(base) + 1 :]
            if suffix.isdigit():
                max_suffix = max(max_suffix, int(suffix))

        return f"{base}-{max_suffix + 1}"


class OpenStreetMapContribution(models.Model):
    """Store the current OpenStreetMap review state for one library.
    Keeps operational state separate from source and permission metadata."""

    class Status(models.TextChoices):
        UNCHECKED = "unchecked", _("Unchecked")
        NO_MATCH = "no_match", _("No match found")
        POSSIBLE_DUPLICATE = "possible_duplicate", _("Possible duplicate")
        ALREADY_PRESENT = "already_present", _("Already present")
        SUBMITTING = "submitting", _("Submitting")
        CONTRIBUTED = "contributed", _("Contributed")
        FAILED = "failed", _("Failed")

    class ElementType(models.TextChoices):
        NODE = "node", _("Node")
        WAY = "way", _("Way")
        RELATION = "relation", _("Relation")

    library = models.OneToOneField(
        Library,
        on_delete=models.CASCADE,
        related_name="osm_contribution",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNCHECKED,
        db_index=True,
    )
    checked_at = models.DateTimeField(null=True, blank=True)
    duplicate_candidates = models.JSONField(default=list, blank=True)
    osm_element_type = models.CharField(
        max_length=8,
        choices=ElementType.choices,
        blank=True,
        default="",
    )
    osm_element_id = models.BigIntegerField(null=True, blank=True)
    changeset_id = models.BigIntegerField(null=True, blank=True)
    contributed_at = models.DateTimeField(null=True, blank=True)
    contributed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="osm_contributions",
    )
    last_attempt_key = models.UUIDField(null=True, blank=True, unique=True)
    last_error_code = models.CharField(max_length=50, blank=True, default="")
    last_error_message = models.CharField(max_length=500, blank=True, default="")
    last_error_retryable = models.BooleanField(default=False)

    class Meta:
        db_table = "openstreetmap_contributions"
        ordering = ["-checked_at", "library_id"]
        indexes = [
            models.Index(
                fields=["-checked_at", "library"],
                name="idx_osm_checked_library",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(osm_element_id__isnull=True, osm_element_type="")
                    | (
                        Q(osm_element_id__isnull=False)
                        & ~Q(osm_element_type="")
                    )
                ),
                name="osm_element_fields_paired",
            ),
            models.UniqueConstraint(
                fields=["osm_element_type", "osm_element_id"],
                condition=Q(osm_element_id__isnull=False),
                name="unique_osm_element_link",
            ),
        ]

    def __str__(self) -> str:
        """Return a readable current-state label.
        Identifies the library and OSM review status in admin screens."""
        return f"{self.library}: {self.get_status_display()}"

    def duplicate_check_is_current(self, *, max_age_seconds: int) -> bool:
        """Return whether the latest duplicate check remains fresh.
        Requires a successful no-match state and a completed timestamp."""
        if self.status != self.Status.NO_MATCH or self.checked_at is None:
            return False
        from django.utils import timezone

        age = timezone.now() - self.checked_at
        return age.total_seconds() <= max_age_seconds


class OpenStreetMapContributionEvent(models.Model):
    """Store one immutable event in the OpenStreetMap audit history.
    Retains sanitized staff actions, checks, and future write outcomes."""

    class EventType(models.TextChoices):
        PERMISSION_WITHDRAWN = "permission_withdrawn", _("Permission withdrawn")
        DUPLICATE_CHECK = "duplicate_check", _("Duplicate check")
        DUPLICATE_RESOLUTION = "duplicate_resolution", _("Duplicate resolution")
        WRITE_STARTED = "write_started", _("Write started")
        WRITE_SUCCEEDED = "write_succeeded", _("Write succeeded")
        WRITE_FAILED = "write_failed", _("Write failed")
        RECONCILIATION_REQUIRED = (
            "reconciliation_required",
            _("Reconciliation required"),
        )

    class Outcome(models.TextChoices):
        SUCCESS = "success", _("Success")
        FAILED = "failed", _("Failed")
        BLOCKED = "blocked", _("Blocked")

    contribution = models.ForeignKey(
        OpenStreetMapContribution,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="osm_contribution_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    attempt_key = models.UUIDField(null=True, blank=True, db_index=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "openstreetmap_contribution_events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["-created_at"],
                name="idx_osm_event_created",
            ),
            models.Index(
                fields=["contribution", "-created_at"],
                name="idx_osm_event_contrib_created",
            ),
            models.Index(
                fields=["event_type", "-created_at"],
                name="idx_osm_event_type_created",
            ),
            models.Index(
                fields=["outcome", "-created_at"],
                name="idx_osm_event_outcome_created",
            ),
        ]

    def __str__(self) -> str:
        """Return a readable audit-event label.
        Identifies the library and event type without exposing details."""
        return f"{self.contribution.library}: {self.get_event_type_display()}"

    def save(self, *args, **kwargs) -> None:
        """Create a new audit event without allowing later mutation.
        Enforces append-only behavior through the model write path."""
        if not self._state.adding:
            raise ValueError("OpenStreetMap contribution events are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        """Reject direct deletion of an existing audit event.
        Preserves the append-only audit trail during normal operations."""
        raise ValueError("OpenStreetMap contribution events cannot be deleted.")


class Report(models.Model):
    """A user-submitted report about a library issue."""

    class Reason(models.TextChoices):
        DAMAGED = "damaged", _("Damaged")
        MISSING = "missing", _("Missing")
        INCORRECT_INFO = "incorrect_info", _("Incorrect Info")
        INAPPROPRIATE = "inappropriate", _("Inappropriate")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        RESOLVED = "resolved", _("Resolved")
        DISMISSED = "dismissed", _("Dismissed")

    library = models.ForeignKey(
        "Library",
        on_delete=models.CASCADE,
        related_name="reports",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    details = models.TextField()
    photo = models.ImageField(
        upload_to="reports/photos/%Y/%m/",
        blank=True,
        default="",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reports"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="idx_report_status_created"),
            models.Index(fields=["created_by", "-created_at"], name="idx_report_creator_created"),
        ]

    def __str__(self) -> str:
        """Return a readable string representation.
        Keeps output clear in logs and admin screens."""
        return f"Report: {self.get_reason_display()} - {self.library}"


class LibraryPhoto(models.Model):
    """A community-submitted photo for an existing library."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    library = models.ForeignKey(
        "Library",
        on_delete=models.CASCADE,
        related_name="user_photos",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="library_photos",
    )
    photo = models.ImageField(upload_to="libraries/user_photos/%Y/%m/")
    photo_thumbnail = models.ImageField(
        upload_to="libraries/user_photos/thumbnails/%Y/%m/",
        blank=True,
        default="",
    )
    caption = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "library_photos"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["library", "status"], name="idx_photo_library_status"),
            models.Index(fields=["status", "-created_at"], name="idx_photo_status_created"),
            models.Index(fields=["created_by", "-created_at"], name="idx_photo_creator_created"),
        ]

    def __init__(self, *args, **kwargs):
        """Initialize instance and snapshot current photo name and status.
        Allows cheap in-memory change detection on save."""
        super().__init__(*args, **kwargs)
        self._original_photo_name = self.photo.name if self.photo else ""
        self._original_status = self.status

    def __str__(self) -> str:
        """Return a readable string representation.
        Keeps output clear in logs and admin screens."""
        label = self.caption or "Photo"
        return f"{label} - {self.library}"

    def save(self, *args, **kwargs) -> None:
        """Persist the model instance.
        Applies photo optimization and promotes to primary on approval."""
        save_kwargs = kwargs

        if self._photo_needs_processing():
            self._optimize_uploaded_photo()
            save_kwargs = self._merge_photo_fields_into_update_kwargs(kwargs=kwargs)

        super().save(*args, **save_kwargs)

        if self._status_changed_to_approved():
            self._promote_to_library_primary()

    def _status_changed_to_approved(self) -> bool:
        """Check whether the status just transitioned to approved.
        Compares against the snapshot taken at init."""
        return (
            self.status == self.Status.APPROVED
            and self._original_status != self.Status.APPROVED
        )

    def _promote_to_library_primary(self) -> None:
        """Copy this photo to the parent library's primary photo fields.
        Called automatically when the photo is approved."""
        library = self.library
        library.photo = self.photo
        library.photo_thumbnail = self.photo_thumbnail
        library.save(update_fields=["photo", "photo_thumbnail"])

    @property
    def card_photo_url(self) -> str:
        """Return the best image URL for gallery cards.
        Prefers thumbnails and falls back to the main uploaded photo."""
        if self.photo_thumbnail:
            try:
                return self.photo_thumbnail.url
            except ValueError:
                pass

        if self.photo:
            try:
                return self.photo.url
            except ValueError:
                return ""

        return ""

    def _photo_needs_processing(self) -> bool:
        """Determine whether the current photo should be optimized.
        Compares against the snapshot taken at init to avoid a DB query."""
        if not self.photo:
            return False

        if getattr(self.photo, "_file", None) is None:
            return False

        if self._state.adding or self.pk is None:
            return True

        return self._original_photo_name != self.photo.name

    def _optimize_uploaded_photo(self) -> None:
        """Create optimized JPEG derivatives for the uploaded photo.
        Stores both the resized primary image and the card-size thumbnail."""
        source_photo = getattr(self.photo, "_file", None)
        if source_photo is None:
            return

        main_image, thumbnail_image = build_library_photo_files(
            image_file=source_photo,
            original_name=self.photo.name or "library-photo.jpg",
        )
        main_filename, main_content = main_image
        thumbnail_filename, thumbnail_content = thumbnail_image

        self.photo.save(main_filename, main_content, save=False)
        self.photo_thumbnail.save(thumbnail_filename, thumbnail_content, save=False)

    def _merge_photo_fields_into_update_kwargs(
        self,
        *,
        kwargs: dict[str, object],
    ) -> dict[str, object]:
        """Ensure generated photo fields persist during update-only saves.
        Adds primary and thumbnail fields when update_fields is provided."""
        update_fields = kwargs.get("update_fields")
        if update_fields is None:
            return kwargs

        merged_kwargs = dict(kwargs)
        if isinstance(update_fields, str):
            merged_update_fields = {update_fields}
        else:
            try:
                merged_update_fields = set(update_fields)
            except TypeError:
                return kwargs
        merged_update_fields.update({"photo", "photo_thumbnail"})
        merged_kwargs["update_fields"] = merged_update_fields
        return merged_kwargs


class SocialPost(models.Model):
    """Tracks libraries that have been posted to social media."""

    library = models.ForeignKey(
        Library,
        on_delete=models.CASCADE,
        related_name="social_posts",
    )
    post_text = models.TextField()
    posted_at = models.DateTimeField(auto_now_add=True)
    mastodon_url = models.URLField(blank=True, default="")
    bluesky_url = models.URLField(blank=True, default="")
    instagram_url = models.URLField(blank=True, default="")

    class Meta:
        db_table = "social_posts"
        ordering = ["-posted_at"]

    def __str__(self) -> str:
        """Return a readable string representation.
        Identifies the library and posting timestamp."""
        return f"SocialPost for {self.library} at {self.posted_at}"


class InstagramToken(models.Model):
    """Stores the current long-lived Instagram access token for automatic refresh."""

    access_token = models.TextField()
    refreshed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "instagram_tokens"

    def __str__(self) -> str:
        """Return a readable string representation.
        Shows the refresh timestamp for admin display."""
        return f"InstagramToken (refreshed {self.refreshed_at})"


class Favourite(models.Model):
    """A user's bookmark of an approved library."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favourites",
    )
    library = models.ForeignKey(
        "Library",
        on_delete=models.CASCADE,
        related_name="favourites",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "favourites"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "library"],
                name="unique_user_library_favourite",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="idx_fav_user_created",
            ),
        ]

    def __str__(self) -> str:
        """Return a readable string representation.
        Identifies the user-library favourite relationship."""
        return f"Favourite: {self.user} -> {self.library}"
