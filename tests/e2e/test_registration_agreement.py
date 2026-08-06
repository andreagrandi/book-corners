import pytest
from django.contrib.auth import get_user_model
from playwright.sync_api import expect

from users.models import ContributorAgreementAcceptance

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

User = get_user_model()


def test_registration_agreement_is_accessible_and_unchecked(live_server, page):
    """Expose an unchecked agreement control with an accessible linked label.
    Opens the complete active-language agreement without losing form state."""
    page.goto(f"{live_server.url}/register/")

    checkbox = page.get_by_role(
        "checkbox",
        name="I have read and accept the Contributor Agreement v1.0",
    )
    expect(checkbox).not_to_be_checked()
    agreement_link = page.get_by_role("link", name="Contributor Agreement v1.0")
    expect(agreement_link).to_have_attribute("target", "_blank")

    with page.expect_popup() as popup_info:
        agreement_link.click()
    agreement_page = popup_info.value
    agreement_page.wait_for_load_state()
    assert agreement_page.url.endswith("/contributor-agreement/")
    expect(agreement_page.get_by_role("heading", level=1)).to_contain_text(
        "Contributor Agreement",
    )


def test_password_registration_requires_acceptance(live_server, page):
    """Reject browser password registration while acceptance remains unchecked.
    Preserves entered account fields and exposes the checkbox validation error."""
    page.goto(f"{live_server.url}/register/")
    page.get_by_label("Username", exact=True).fill("browser-rejected")
    page.get_by_label("Email", exact=True).fill("browser-rejected@example.com")
    page.get_by_label("Password", exact=True).fill("SecretPass123!")
    page.get_by_label("Confirm password", exact=True).fill("SecretPass123!")

    page.get_by_role("button", name="Create account").click()

    expect(page).to_have_url(f"{live_server.url}/register/")
    expect(page.get_by_label("Username", exact=True)).to_have_value("browser-rejected")
    checkbox = page.get_by_role("checkbox")
    expect(checkbox).to_have_attribute("aria-invalid", "true")
    expect(checkbox).to_have_attribute(
        "aria-errormessage",
        "contributor-agreement-error",
    )
    expect(page.get_by_role("alert")).to_contain_text(
        "must accept the contributor agreement",
    )
    assert not User.objects.filter(username="browser-rejected").exists()


def test_password_registration_records_acceptance_in_browser(live_server, page):
    """Complete password registration after an explicit browser checkbox choice.
    Verifies the user and web-channel audit record through the rendered flow."""
    page.goto(f"{live_server.url}/register/")
    page.get_by_label("Username", exact=True).fill("browser-accepted")
    page.get_by_label("Email", exact=True).fill("browser-accepted@example.com")
    page.get_by_label("Password", exact=True).fill("SecretPass123!")
    page.get_by_label("Confirm password", exact=True).fill("SecretPass123!")
    page.get_by_role("checkbox").check()

    page.get_by_role("button", name="Create account").click()

    expect(page).to_have_url(f"{live_server.url}/")
    user = User.objects.get(username="browser-accepted")
    acceptance = ContributorAgreementAcceptance.objects.get(user=user)
    assert (
        acceptance.channel
        == ContributorAgreementAcceptance.Channel.WEB_CREDENTIAL_REGISTRATION
    )


def test_current_checked_choice_survives_browser_validation_error(live_server, page):
    """Keep current-version acceptance checked after unrelated browser errors.
    Avoids asking for the same explicit choice twice in one registration attempt."""
    page.goto(f"{live_server.url}/register/")
    page.get_by_label("Username", exact=True).fill("browser-preserved")
    page.get_by_label("Email", exact=True).fill("browser-preserved@example.com")
    page.get_by_label("Password", exact=True).fill("SecretPass123!")
    page.get_by_label("Confirm password", exact=True).fill("DifferentPass123!")
    checkbox = page.get_by_role("checkbox")
    checkbox.check()

    page.get_by_role("button", name="Create account").click()

    expect(page).to_have_url(f"{live_server.url}/register/")
    expect(checkbox).to_be_checked()
    expect(page.get_by_text("The two password fields didn’t match.")).to_be_visible()

