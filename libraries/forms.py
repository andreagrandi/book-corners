from __future__ import annotations

from typing import Any

import pycountry
from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django import forms
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError

from libraries.models import Library, LibraryPhoto, MAX_LIBRARY_PHOTOS_PER_USER, Report


COUNTRY_CHOICES = [
    ("", "Select a country"),
    *sorted(
        ((country.alpha_2, country.name) for country in pycountry.countries),
        key=lambda item: item[1],
    ),
]

SEARCH_COUNTRY_CHOICES = [
    ("", "Any country"),
    *COUNTRY_CHOICES[1:],
]

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def _validate_uploaded_photo(*, uploaded_photo: Any, max_size_bytes: int) -> Any:
    """Validate uploaded image files for size and format.
    Rejects non-image payloads and files larger than configured limits."""
    if uploaded_photo is None:
        return uploaded_photo

    if uploaded_photo.size > max_size_bytes:
        max_size_mb = max_size_bytes / (1024 * 1024)
        if max_size_mb >= 1:
            raise ValidationError(f"Photo must be {max_size_mb:.0f}MB or smaller.")
        raise ValidationError(f"Photo must be at most {max_size_bytes} bytes.")

    start_position = None
    if hasattr(uploaded_photo, "tell"):
        try:
            start_position = uploaded_photo.tell()
        except (OSError, ValueError):
            start_position = None

    try:
        if hasattr(uploaded_photo, "seek"):
            uploaded_photo.seek(0)

        with Image.open(uploaded_photo) as image:
            image_format = (image.format or "").upper()
            if image_format not in ALLOWED_IMAGE_FORMATS:
                raise ValidationError("Upload a valid image in JPEG, PNG, or WEBP format.")
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError("Upload a valid image in JPEG, PNG, or WEBP format.")
    finally:
        if start_position is not None and hasattr(uploaded_photo, "seek"):
            try:
                uploaded_photo.seek(start_position)
            except (OSError, ValueError):
                pass

    return uploaded_photo


class LibrarySubmissionForm(forms.ModelForm):
    latitude = forms.FloatField(required=True, widget=forms.HiddenInput())
    longitude = forms.FloatField(required=True, widget=forms.HiddenInput())
    country = forms.ChoiceField(choices=COUNTRY_CHOICES)
    description = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 4}))

    class Meta:
        model = Library
        fields = (
            "photo",
            "name",
            "description",
            "address",
            "city",
            "country",
            "postal_code",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the object state.
        Sets up values required by later calls."""
        self.created_by = kwargs.pop("created_by", None)
        super().__init__(*args, **kwargs)

        self.fields["photo"].widget.attrs["class"] = "file-input w-full"
        self.fields["name"].widget.attrs["class"] = "input w-full"
        self.fields["description"].widget.attrs["class"] = "textarea w-full"
        self.fields["address"].widget.attrs["class"] = "input w-full"
        self.fields["city"].widget.attrs["class"] = "input w-full"
        self.fields["country"].widget.attrs["class"] = "w-full"
        self.fields["postal_code"].widget.attrs["class"] = "input w-full"

    def clean_latitude(self) -> float:
        """Validate latitude input.
        Rejects invalid values before save-time logic."""
        latitude = self.cleaned_data["latitude"]
        if latitude < -90 or latitude > 90:
            raise ValidationError("Latitude must be between -90 and 90.")
        return latitude

    def clean_longitude(self) -> float:
        """Validate longitude input.
        Rejects invalid values before save-time logic."""
        longitude = self.cleaned_data["longitude"]
        if longitude < -180 or longitude > 180:
            raise ValidationError("Longitude must be between -180 and 180.")
        return longitude

    def clean_photo(self) -> Any:
        """Validate the library photo before saving the submission.
        Enforces file-type and max-size rules for uploaded images."""
        photo = self.cleaned_data.get("photo")
        return _validate_uploaded_photo(
            uploaded_photo=photo,
            max_size_bytes=settings.MAX_LIBRARY_PHOTO_UPLOAD_BYTES,
        )

    def save(self, commit: bool = True) -> Library:
        """Persist the model instance.
        Applies model-specific rules before writing data."""
        if self.created_by is None:
            raise ValueError("created_by is required to save a library submission")

        library = super().save(commit=False)
        library.status = Library.Status.PENDING
        library.created_by = self.created_by
        library.location = Point(
            x=self.cleaned_data["longitude"],
            y=self.cleaned_data["latitude"],
            srid=4326,
        )

        if commit:
            library.save()

        return library


class ReportSubmissionForm(forms.ModelForm):
    details = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 4}))

    class Meta:
        model = Report
        fields = (
            "reason",
            "details",
            "photo",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize report submission dependencies and widget styles.
        Captures related objects needed to persist report ownership."""
        self.created_by = kwargs.pop("created_by", None)
        self.library = kwargs.pop("library", None)
        super().__init__(*args, **kwargs)

        self.fields["reason"].widget.attrs["class"] = "select w-full"
        self.fields["details"].widget.attrs["class"] = "textarea w-full"
        self.fields["photo"].widget.attrs["class"] = "file-input w-full"

    def save(self, commit: bool = True) -> Report:
        """Persist the report bound to the authenticated user and library.
        Ensures new reports always start in the open moderation state."""
        if self.created_by is None:
            raise ValueError("created_by is required to save a report")
        if self.library is None:
            raise ValueError("library is required to save a report")

        report = super().save(commit=False)
        report.created_by = self.created_by
        report.library = self.library
        report.status = Report.Status.OPEN

        if commit:
            report.save()

        return report

    def clean_photo(self) -> Any:
        """Validate the optional report photo before persisting data.
        Enforces file-type and max-size checks for moderation uploads."""
        photo = self.cleaned_data.get("photo")
        return _validate_uploaded_photo(
            uploaded_photo=photo,
            max_size_bytes=settings.MAX_REPORT_PHOTO_UPLOAD_BYTES,
        )


class LibrarySearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=120)
    near = forms.CharField(required=False, max_length=120)
    radius_km = forms.IntegerField(required=False, min_value=1, max_value=100, initial=10)
    city = forms.CharField(required=False, max_length=100)
    country = forms.ChoiceField(required=False, choices=SEARCH_COUNTRY_CHOICES)
    postal_code = forms.CharField(required=False, max_length=20)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize form widgets for search interactions.
        Keeps quick and advanced search controls visually consistent."""
        super().__init__(*args, **kwargs)

        self.fields["q"].widget.attrs.update(
            {
                "class": "input w-full",
                "placeholder": "Keywords in name or description",
            }
        )
        self.fields["near"].widget.attrs.update(
            {
                "class": "input w-full",
                "placeholder": "City, area, postcode, or address",
            }
        )
        self.fields["radius_km"].widget.attrs.update(
            {
                "class": "input w-full",
                "min": "1",
                "max": "100",
                "step": "1",
            }
        )
        self.fields["city"].widget.attrs.update(
            {
                "class": "input w-full",
                "placeholder": "Filter by city",
            }
        )
        self.fields["country"].widget.attrs.update(
            {
                "class": "select w-full",
            }
        )
        self.fields["postal_code"].widget.attrs.update(
            {
                "class": "input w-full",
                "placeholder": "Filter by postal code",
            }
        )

    def clean(self) -> dict[str, Any]:
        """Normalize optional inputs before search execution.
        Ensures blank radius values fall back to the default distance."""
        cleaned_data = super().clean()

        for field_name in ("q", "near", "city", "postal_code"):
            value = cleaned_data.get(field_name)
            cleaned_data[field_name] = value.strip() if isinstance(value, str) else ""

        country = cleaned_data.get("country")
        cleaned_data["country"] = country.strip().upper() if isinstance(country, str) else ""

        radius_km = cleaned_data.get("radius_km")
        cleaned_data["radius_km"] = radius_km if isinstance(radius_km, int) else 10

        return cleaned_data


class LibraryPhotoSubmissionForm(forms.ModelForm):
    """Form for submitting a community photo to an existing library."""

    class Meta:
        model = LibraryPhoto
        fields = ("photo", "caption")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize photo submission dependencies and widget styles.
        Captures related objects needed to persist photo ownership."""
        self.created_by = kwargs.pop("created_by", None)
        self.library = kwargs.pop("library", None)
        super().__init__(*args, **kwargs)

        self.fields["photo"].widget.attrs["class"] = "file-input w-full"
        self.fields["caption"].widget.attrs.update({
            "class": "input w-full",
            "placeholder": "Optional caption for your photo",
        })

    def clean_photo(self) -> Any:
        """Validate the community photo before saving the submission.
        Enforces file-type and max-size rules for uploaded images."""
        photo = self.cleaned_data.get("photo")
        return _validate_uploaded_photo(
            uploaded_photo=photo,
            max_size_bytes=settings.MAX_LIBRARY_PHOTO_SUBMISSION_BYTES,
        )

    def clean(self) -> dict[str, Any]:
        """Enforce per-user photo limit for a given library.
        Prevents excessive submissions from a single contributor."""
        cleaned_data = super().clean()

        if self.created_by and self.library:
            existing_count = LibraryPhoto.objects.filter(
                library=self.library,
                created_by=self.created_by,
            ).exclude(
                status=LibraryPhoto.Status.REJECTED,
            ).count()
            if existing_count >= MAX_LIBRARY_PHOTOS_PER_USER:
                raise ValidationError(
                    f"You can submit at most {MAX_LIBRARY_PHOTOS_PER_USER} photos per library."
                )

        return cleaned_data

    def save(self, commit: bool = True) -> LibraryPhoto:
        """Persist the photo bound to the authenticated user and library.
        Ensures new photos always start in the pending moderation state."""
        if self.created_by is None:
            raise ValueError("created_by is required to save a photo submission")
        if self.library is None:
            raise ValueError("library is required to save a photo submission")

        library_photo = super().save(commit=False)
        library_photo.created_by = self.created_by
        library_photo.library = self.library
        library_photo.status = LibraryPhoto.Status.PENDING

        if commit:
            library_photo.save()

        return library_photo
