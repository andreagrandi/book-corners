from django.conf import settings
from django.contrib.gis.db.models import PointField
from django.db import models
from django.utils.text import slugify


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
        if self.name:
            return f"{self.name} ({self.city})"
        return f"{self.address}, {self.city}"

    def save(self, *args, **kwargs) -> None:
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

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
        return f"Report: {self.get_reason_display()} - {self.library}"
