"""Tests for the contributor agreement checkbox on web contribution forms.

Users without a current acceptance must accept it when submitting, editing or
adding a photo, so their photos can be credited under CC BY-SA 4.0.
"""

from io import BytesIO
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from libraries.models import Library, LibraryPhoto
from users.contributor_agreements import (
    CURRENT_CONTRIBUTOR_AGREEMENT_VERSION,
    has_current_acceptance,
)
from users.models import ContributorAgreementAcceptance

User = get_user_model()
CHECKBOX_NAME = "contributor_agreement_accepted"


def _photo(*, file_name: str = "library.jpg") -> SimpleUploadedFile:
    """Build an in-memory JPEG upload.
    Keeps the web form tests free of fixture files."""
    image_bytes = BytesIO()
    Image.new("RGB", (640, 480), color=(140, 165, 210)).save(image_bytes, format="JPEG")
    return SimpleUploadedFile(
        name=file_name,
        content=image_bytes.getvalue(),
        content_type="image/jpeg",
    )


def _agreement_data(**overrides: Any) -> dict[str, Any]:
    """Build the agreement fields posted by a checked checkbox.
    Accepts overrides to simulate unchecked or stale submissions."""
    data: dict[str, Any] = {
        "contributor_agreement_version": CURRENT_CONTRIBUTOR_AGREEMENT_VERSION,
        CHECKBOX_NAME: "on",
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def _submit_payload(**overrides: Any) -> dict[str, Any]:
    """Build a valid submit-library payload with a photo.
    Accepts overrides for the agreement fields under test."""
    payload: dict[str, Any] = {
        "name": "Agreement Shelf",
        "description": "",
        "address": "Via Rosina 15",
        "city": "Florence",
        "country": "IT",
        "postal_code": "",
        "latitude": "43.7696",
        "longitude": "11.2558",
        "photo": _photo(),
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def new_user(db: Any) -> Any:
    """Create a user without any contributor agreement acceptance.
    Mirrors accounts registered on the web before the agreement existed."""
    return User.objects.create_user(username="early-user", password="pw")


@pytest.fixture
def accepted_user(db: Any) -> Any:
    """Create a user with a current contributor agreement acceptance.
    Such users must not see the checkbox on contribution forms."""
    accepted = User.objects.create_user(username="accepted-user", password="pw")
    ContributorAgreementAcceptance.objects.create(
        user=accepted,
        agreement_version=CURRENT_CONTRIBUTOR_AGREEMENT_VERSION,
        channel=ContributorAgreementAcceptance.Channel.WEB_CREDENTIAL_REGISTRATION,
    )
    return accepted


@pytest.fixture
def approved_library(db: Any) -> Library:
    """Create an approved library that accepts community photos.
    Gives the photo form tests a target library."""
    return Library.objects.create(
        name="Approved Agreement Library",
        photo="libraries/photos/2026/02/test.jpg",
        location=Point(x=11.2558, y=43.7696, srid=4326),
        address="Via Rosina 15",
        city="Florence",
        country="IT",
        status=Library.Status.APPROVED,
    )


class TestSubmitLibraryAgreement:
    """Tests for the agreement checkbox on the submit-library form."""

    def test_form_shows_checkbox_for_user_without_acceptance(
        self, client: Client, new_user: Any
    ) -> None:
        """Verify the submit page renders the checkbox for a non-accepted user.
        Links the agreement next to the checkbox."""
        client.force_login(new_user)

        response = client.get(reverse("submit_library"))

        assert f'name="{CHECKBOX_NAME}"' in response.content.decode()
        assert reverse("contributor_agreement_page") in response.content.decode()

    def test_form_hides_checkbox_for_accepted_user(
        self, client: Client, accepted_user: Any
    ) -> None:
        """Verify the submit page has no checkbox once the user accepted.
        Keeps the form unchanged for users who already accepted."""
        client.force_login(accepted_user)

        response = client.get(reverse("submit_library"))

        assert CHECKBOX_NAME not in response.content.decode()

    def test_submit_without_checkbox_fails_and_creates_nothing(
        self, client: Client, new_user: Any
    ) -> None:
        """Verify a missing checkbox rejects the submission.
        No library and no acceptance row may be created."""
        client.force_login(new_user)

        response = client.post(
            reverse("submit_library"),
            data=_submit_payload(**_agreement_data(**{CHECKBOX_NAME: None})),
        )

        assert response.status_code == 200
        assert "You must accept the contributor agreement to continue." in response.content.decode()
        assert not Library.objects.filter(name="Agreement Shelf").exists()
        assert not has_current_acceptance(user=new_user)

    def test_submit_with_stale_version_fails(self, client: Client, new_user: Any) -> None:
        """Verify a stale agreement version rejects the submission.
        Users must accept the version that is currently deployed."""
        client.force_login(new_user)

        response = client.post(
            reverse("submit_library"),
            data=_submit_payload(**_agreement_data(contributor_agreement_version="0.1")),
        )

        assert response.status_code == 200
        assert not Library.objects.filter(name="Agreement Shelf").exists()
        assert not has_current_acceptance(user=new_user)

    def test_submit_with_checkbox_records_acceptance(
        self, client: Client, new_user: Any
    ) -> None:
        """Verify a checked box saves the library and records the acceptance.
        The acceptance uses the web existing-user channel."""
        client.force_login(new_user)

        response = client.post(
            reverse("submit_library"),
            data=_submit_payload(**_agreement_data()),
        )

        assert response.status_code == 302
        assert Library.objects.filter(name="Agreement Shelf").exists()
        acceptance = ContributorAgreementAcceptance.objects.get(user=new_user)
        assert acceptance.agreement_version == CURRENT_CONTRIBUTOR_AGREEMENT_VERSION
        assert acceptance.channel == ContributorAgreementAcceptance.Channel.WEB_EXISTING_USER

    def test_accepted_user_submits_without_checkbox(
        self, client: Client, accepted_user: Any
    ) -> None:
        """Verify an accepted user submits without any agreement fields.
        The existing acceptance row stays the only one."""
        client.force_login(accepted_user)

        response = client.post(reverse("submit_library"), data=_submit_payload())

        assert response.status_code == 302
        assert ContributorAgreementAcceptance.objects.filter(user=accepted_user).count() == 1
        assert (
            ContributorAgreementAcceptance.objects.get(user=accepted_user).channel
            == ContributorAgreementAcceptance.Channel.WEB_CREDENTIAL_REGISTRATION
        )


class TestEditLibraryAgreement:
    """Tests for the agreement checkbox on the edit-library form."""

    def _library(self, *, owner: Any) -> Library:
        """Create a pending library owned by the given user.
        Gives the edit tests a library the user may edit."""
        return Library.objects.create(
            name="Edit Agreement Shelf",
            photo="libraries/photos/2026/02/edit.jpg",
            location=Point(x=11.2558, y=43.7696, srid=4326),
            address="Via Rosina 15",
            city="Florence",
            country="IT",
            status=Library.Status.PENDING,
            created_by=owner,
        )

    def _payload(self, **overrides: Any) -> dict[str, Any]:
        """Build an edit payload without a replacement photo.
        Accepts overrides for the agreement fields under test."""
        payload = _submit_payload(name="Renamed Agreement Shelf", **overrides)
        payload.pop("photo")
        return payload

    def test_edit_without_checkbox_fails(self, client: Client, new_user: Any) -> None:
        """Verify editing without the checkbox is rejected.
        The library keeps its name and no acceptance is recorded."""
        library = self._library(owner=new_user)
        client.force_login(new_user)

        response = client.post(
            reverse("edit_library", kwargs={"slug": library.slug}),
            data=self._payload(),
        )

        assert response.status_code == 200
        library.refresh_from_db()
        assert library.name == "Edit Agreement Shelf"
        assert not has_current_acceptance(user=new_user)

    def test_edit_with_checkbox_records_acceptance(
        self, client: Client, new_user: Any
    ) -> None:
        """Verify editing with the checkbox saves and records the acceptance.
        The acceptance uses the web existing-user channel."""
        library = self._library(owner=new_user)
        client.force_login(new_user)

        response = client.post(
            reverse("edit_library", kwargs={"slug": library.slug}),
            data=self._payload(**_agreement_data()),
        )

        assert response.status_code == 302
        library.refresh_from_db()
        assert library.name == "Renamed Agreement Shelf"
        assert (
            ContributorAgreementAcceptance.objects.get(user=new_user).channel
            == ContributorAgreementAcceptance.Channel.WEB_EXISTING_USER
        )

    def test_accepted_user_sees_no_checkbox_and_edits(
        self, client: Client, accepted_user: Any
    ) -> None:
        """Verify an accepted user edits without a checkbox.
        Nothing changes for users who already accepted."""
        library = self._library(owner=accepted_user)
        client.force_login(accepted_user)
        url = reverse("edit_library", kwargs={"slug": library.slug})

        assert CHECKBOX_NAME not in client.get(url).content.decode()
        response = client.post(url, data=self._payload())

        assert response.status_code == 302


class TestPhotoSubmissionAgreement:
    """Tests for the agreement checkbox on the community photo form."""

    def _post(self, *, client: Client, library: Library, **extra: Any) -> Any:
        """Post a community photo to the library.
        Accepts extra agreement fields for the case under test."""
        return client.post(
            reverse("submit_library_photo", kwargs={"slug": library.slug}),
            {"photo": _photo(file_name="community.jpg"), "caption": "Nice", **extra},
        )

    def test_detail_page_shows_checkbox_for_non_accepted_user(
        self, client: Client, new_user: Any, approved_library: Library
    ) -> None:
        """Verify the photo form on the detail page includes the checkbox.
        Users without acceptance see it, accepted users do not."""
        client.force_login(new_user)

        response = client.get(reverse("library_detail", kwargs={"slug": approved_library.slug}))

        assert f'name="{CHECKBOX_NAME}"' in response.content.decode()

    def test_detail_page_hides_checkbox_for_accepted_user(
        self, client: Client, accepted_user: Any, approved_library: Library
    ) -> None:
        """Verify accepted users see no checkbox on the detail page.
        The photo form stays unchanged for them."""
        client.force_login(accepted_user)

        response = client.get(reverse("library_detail", kwargs={"slug": approved_library.slug}))

        assert CHECKBOX_NAME not in response.content.decode()

    def test_photo_without_checkbox_returns_422_with_error(
        self, client: Client, new_user: Any, approved_library: Library
    ) -> None:
        """Verify a photo without the checkbox is rejected with the form re-rendered.
        Nothing is saved and no acceptance is recorded."""
        client.force_login(new_user)

        response = self._post(
            client=client,
            library=approved_library,
            **_agreement_data(**{CHECKBOX_NAME: None}),
        )

        assert response.status_code == 422
        assert "You must accept the contributor agreement to continue." in response.content.decode()
        assert not LibraryPhoto.objects.filter(created_by=new_user).exists()
        assert not has_current_acceptance(user=new_user)

    def test_photo_with_checkbox_records_acceptance(
        self, client: Client, new_user: Any, approved_library: Library
    ) -> None:
        """Verify a checked box saves the photo and records the acceptance.
        The HTMX partial then renders the form without the checkbox."""
        client.force_login(new_user)

        response = self._post(client=client, library=approved_library, **_agreement_data())

        assert response.status_code == 200
        assert LibraryPhoto.objects.filter(created_by=new_user).count() == 1
        assert (
            ContributorAgreementAcceptance.objects.get(user=new_user).channel
            == ContributorAgreementAcceptance.Channel.WEB_EXISTING_USER
        )

    def test_accepted_user_submits_photo_without_checkbox(
        self, client: Client, accepted_user: Any, approved_library: Library
    ) -> None:
        """Verify an accepted user submits a photo without agreement fields.
        No extra acceptance row is created."""
        client.force_login(accepted_user)

        response = self._post(client=client, library=approved_library)

        assert response.status_code == 200
        assert LibraryPhoto.objects.filter(created_by=accepted_user).count() == 1
        assert ContributorAgreementAcceptance.objects.filter(user=accepted_user).count() == 1


class TestAgreementEndToEndCredit:
    """Tests that accepting through a web form credits the user's photo."""

    def test_submitted_photo_shows_credit_after_acceptance(
        self, client: Client, new_user: Any
    ) -> None:
        """Verify a library photo gets the CC BY-SA credit after web acceptance.
        This is the end-to-end requirement for early web users."""
        client.force_login(new_user)
        client.post(reverse("submit_library"), data=_submit_payload(**_agreement_data()))
        library = Library.objects.get(name="Agreement Shelf")
        library.status = Library.Status.APPROVED
        library.save()

        response = client.get(reverse("library_detail", kwargs={"slug": library.slug}))

        content = response.content.decode()
        assert 'data-photo-credit="cc-by-sa"' in content
        assert "Photo by early-user" in content
        assert "CC BY-SA 4.0" in content
