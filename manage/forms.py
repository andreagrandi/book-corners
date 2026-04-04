from django import forms
from django.utils.translation import gettext_lazy as _

from libraries.models import Library


class LibraryFilterForm(forms.Form):
    """Filter form for the library list in the manage interface."""

    status = forms.ChoiceField(
        choices=[("", _("All statuses"))] + list(Library.Status.choices),
        required=False,
    )
    country = forms.CharField(max_length=2, required=False)
    source = forms.CharField(max_length=100, required=False)
    q = forms.CharField(max_length=200, required=False, label=_("Search"))

    def __init__(self, *args, **kwargs):
        """Initialize form and apply CSS classes to all fields."""
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input input-sm input-bordered")
        self.fields["status"].widget.attrs["class"] = "select select-sm select-bordered"
