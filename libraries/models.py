import uuid

from django.conf import settings
from django.contrib.gis.db.models import PointField
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from libraries.image_processing import build_library_photo_files

MAX_LIBRARY_PHOTOS_PER_USER = 3
LIBRARY_PLACEHOLDER_IMAGE = "images/library-placeholder.png"
LIBRARY_PLACEHOLDER_IMAGE_WEBP = "images/library-placeholder.webp"


class Library(models.Model):
    """A little free library location with its details."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

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
    location = PointField(srid=4326)
    address = models.CharField(max_length=255)
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
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="libraries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "libraries"
        ordering = ["-created_at"]
        verbose_name_plural = "libraries"
        indexes = [
            models.Index(fields=["city", "address"], name="idx_lib_city_address"),
            models.Index(fields=["country"], name="idx_lib_country"),
            models.Index(fields=["-created_at"], name="idx_lib_created_at_desc"),
            models.Index(fields=["source"], name="idx_lib_source"),
            models.Index(fields=["status", "-created_at"], name="idx_lib_status_created"),
            models.Index(fields=["created_by", "-created_at"], name="idx_lib_creator_created"),
            models.Index(fields=["operator"], name="idx_lib_operator"),
            models.Index(fields=["brand"], name="idx_lib_brand"),
        ]

    def __init__(self, *args, **kwargs):
        """Initialize instance and snapshot the current photo name.
        Allows cheap in-memory change detection on save."""
        super().__init__(*args, **kwargs)
        self._original_photo_name = self.photo.name if self.photo else ""

    def __str__(self) -> str:
        """Return a readable string representation.
        Keeps output clear in logs and admin screens."""
        if self.name:
            return f"{self.name} ({self.city})"
        return f"{self.address}, {self.city}"

    def save(self, *args, **kwargs) -> None:
        """Persist the model instance.
        Applies model-specific rules before writing data."""
        save_kwargs = kwargs

        if not self.slug:
            self.slug = self._generate_unique_slug()

        if self._photo_needs_processing():
            self._optimize_uploaded_photo()
            save_kwargs = self._merge_photo_fields_into_update_kwargs(kwargs=kwargs)

        super().save(*args, **save_kwargs)

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
        on_delete=models.CASCADE,
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
        on_delete=models.CASCADE,
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
