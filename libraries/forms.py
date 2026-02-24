from __future__ import annotations

from typing import Any

import pycountry
from django import forms
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError

from libraries.models import Library


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


class LibrarySubmissionForm(forms.ModelForm):
    latitude = forms.FloatField(required=True, widget=forms.HiddenInput())
    longitude = forms.FloatField(required=True, widget=forms.HiddenInput())
    country = forms.ChoiceField(choices=COUNTRY_CHOICES)

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
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the object state.
        Sets up values required by later calls."""
        self.created_by = kwargs.pop("created_by", None)
        super().__init__(*args, **kwargs)

        self.fields["photo"].widget.attrs["class"] = "file-input file-input-bordered w-full"
        self.fields["name"].widget.attrs["class"] = "input input-bordered w-full"
        self.fields["description"].widget.attrs["class"] = "textarea textarea-bordered w-full"
        self.fields["address"].widget.attrs["class"] = "input input-bordered w-full"
        self.fields["city"].widget.attrs["class"] = "input input-bordered w-full"
        self.fields["country"].widget.attrs["class"] = "w-full"
        self.fields["postal_code"].widget.attrs["class"] = "input input-bordered w-full"

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
                "class": "input input-bordered w-full",
                "placeholder": "Keywords in name or description",
            }
        )
        self.fields["near"].widget.attrs.update(
            {
                "class": "input input-bordered w-full",
                "placeholder": "City, area, postcode, or address",
            }
        )
        self.fields["radius_km"].widget.attrs.update(
            {
                "class": "input input-bordered w-full",
                "min": "1",
                "max": "100",
                "step": "1",
            }
        )
        self.fields["city"].widget.attrs.update(
            {
                "class": "input input-bordered w-full",
                "placeholder": "Filter by city",
            }
        )
        self.fields["country"].widget.attrs.update(
            {
                "class": "select select-bordered w-full",
            }
        )
        self.fields["postal_code"].widget.attrs.update(
            {
                "class": "input input-bordered w-full",
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
