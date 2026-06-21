from typing import Any

from django import forms
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from libraries.forms import COUNTRY_CHOICES
from libraries.models import Library


class LibraryFilterForm(forms.Form):
    """Filter libraries in the manage interface.
    Keeps list filtering compact and reusable."""

    status = forms.ChoiceField(
        choices=[("", _("All statuses"))] + list(Library.Status.choices),
        required=False,
    )
    country = forms.CharField(max_length=2, required=False)
    source = forms.CharField(max_length=100, required=False)
    q = forms.CharField(max_length=200, required=False, label=_("Search"))

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the filter form.
        Applies compact manage-interface field styles."""
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input input-sm input-bordered")
        self.fields["status"].widget.attrs["class"] = "select select-sm select-bordered"


class LibraryEditForm(forms.ModelForm):
    """Edit core library fields from the manage interface.
    Exposes coordinates separately while persisting the PostGIS point."""

    latitude = forms.FloatField(label=_("Latitude"))
    longitude = forms.FloatField(label=_("Longitude"))
    country = forms.ChoiceField(choices=COUNTRY_CHOICES)
    description = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    rejection_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    class Meta:
        model = Library
        fields = (
            "name",
            "description",
            "address",
            "city",
            "country",
            "postal_code",
            "latitude",
            "longitude",
            "wheelchair_accessible",
            "capacity",
            "is_indoor",
            "is_lit",
            "website",
            "contact",
            "source",
            "operator",
            "brand",
            "external_id",
            "status",
            "rejection_reason",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the edit form.
        Seeds coordinate fields and applies manage-interface field styles."""
        super().__init__(*args, **kwargs)

        if self.instance.pk and self.instance.location:
            self.initial.setdefault("latitude", self.instance.location.y)
            self.initial.setdefault("longitude", self.instance.location.x)

        text_inputs = (
            "name",
            "address",
            "city",
            "postal_code",
            "website",
            "contact",
            "source",
            "operator",
            "brand",
            "external_id",
        )
        for field_name in text_inputs:
            self.fields[field_name].widget.attrs["class"] = "input w-full"

        for field_name in ("latitude", "longitude", "capacity"):
            self.fields[field_name].widget.attrs.update({
                "class": "input w-full",
                "step": "any",
            })

        for field_name in ("description", "rejection_reason"):
            self.fields[field_name].widget.attrs["class"] = "textarea w-full"

        for field_name in (
            "country",
            "wheelchair_accessible",
            "is_indoor",
            "is_lit",
            "status",
        ):
            self.fields[field_name].widget.attrs["class"] = "select w-full"

    def clean_latitude(self) -> float:
        """Validate the latitude value.
        Rejects coordinates outside the supported geographic range."""
        latitude = self.cleaned_data["latitude"]
        if latitude < -90 or latitude > 90:
            raise ValidationError(_("Latitude must be between -90 and 90."))
        return latitude

    def clean_longitude(self) -> float:
        """Validate the longitude value.
        Rejects coordinates outside the supported geographic range."""
        longitude = self.cleaned_data["longitude"]
        if longitude < -180 or longitude > 180:
            raise ValidationError(_("Longitude must be between -180 and 180."))
        return longitude

    def save(self, commit: bool = True) -> Library:
        """Persist the edited library.
        Converts latitude and longitude back into the PostGIS point field."""
        library = super().save(commit=False)
        library.location = Point(
            x=self.cleaned_data["longitude"],
            y=self.cleaned_data["latitude"],
            srid=4326,
        )

        if commit:
            library.save()

        return library
