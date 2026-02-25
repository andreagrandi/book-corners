from django.conf import settings
from django.contrib.gis.db.models import PointField
from django.db import models
from django.utils.text import slugify

from libraries.image_processing import build_library_photo_files


class Library(models.Model):
    """A little free library location with its details."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    name = models.CharField(max_length=255, blank=True, default="")
    slug = models.SlugField(max_length=280, unique=True, editable=False)
    description = models.TextField(blank=True, default="")
    photo = models.ImageField(upload_to="libraries/photos/%Y/%m/")
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
        Skips unchanged stored files and path-only fixture assignments."""
        if not self.photo:
            return False

        if getattr(self.photo, "_file", None) is None:
            return False

        if self._state.adding or self.pk is None:
            return True

        existing_photo_name = (
            Library.objects.filter(pk=self.pk)
            .values_list("photo", flat=True)
            .first()
        )
        return existing_photo_name != self.photo.name

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
        """Generate a unique slug from city, address, and optionally name."""
        max_length = self._meta.get_field("slug").max_length
        suffix_reserve = 4  # room for "-999"

        if self.name:
            base = slugify(f"{self.city} {self.address} {self.name}")
        else:
            base = slugify(f"{self.city} {self.address}")

        base = base[: max_length - suffix_reserve]

        slug = base
        counter = 2
        while Library.objects.filter(slug=slug).exists():
            slug = f"{base}-{counter}"
            counter += 1

        return slug


class Report(models.Model):
    """A user-submitted report about a library issue."""

    class Reason(models.TextChoices):
        DAMAGED = "damaged", "Damaged"
        MISSING = "missing", "Missing"
        INCORRECT_INFO = "incorrect_info", "Incorrect Info"
        INAPPROPRIATE = "inappropriate", "Inappropriate"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

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

    def __str__(self) -> str:
        """Return a readable string representation.
        Keeps output clear in logs and admin screens."""
        return f"Report: {self.get_reason_display()} - {self.library}"
