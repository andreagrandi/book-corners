"""Tests for the one-time public-dataset email command."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from users.models import (
    ContributorAgreementAcceptance,
    PublicDatasetEmailDelivery,
    User,
)
from users.public_dataset_email import (
    PUBLIC_DATASET_EMAIL_BODY,
    PUBLIC_DATASET_EMAIL_REPLY_TO,
    PUBLIC_DATASET_EMAIL_SUBJECT,
)

LOCMEM_EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE_EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_CAMPAIGN_FROM = "Book Corners <info@bookcorners.org>"
COMMAND_MODULE = "users.management.commands.send_public_dataset_email"

pytestmark = pytest.mark.django_db()


def _create_user(*, username: str, email: str, **extra_fields: Any) -> User:
    """Create one account for campaign command tests.
    Keeps individual test setup concise and explicit."""
    return User.objects.create_user(
        username=username,
        email=email,
        **extra_fields,
    )


@override_settings(
    EMAIL_BACKEND=LOCMEM_EMAIL_BACKEND,
    PUBLIC_DATASET_EMAIL_FROM=DEFAULT_CAMPAIGN_FROM,
)
def test_default_invocation_previews_complete_email_without_sending() -> None:
    """Preview the exact campaign without contacting recipients.
    Keeps the default production invocation read-only and non-interactive."""
    eligible = _create_user(username="eligible", email="eligible@example.com")
    delivered = _create_user(username="delivered", email="delivered@example.com")
    _create_user(username="blank", email="")
    _create_user(username="invalid", email="not-an-email")
    PublicDatasetEmailDelivery.objects.create(
        user=delivered,
        recipient_email=delivered.email,
    )
    stdout = io.StringIO()

    with patch("builtins.input") as input_mock:
        call_command("send_public_dataset_email", stdout=stdout)

    output = stdout.getvalue()
    input_mock.assert_not_called()
    assert f"Eligible unsent recipients: {1}" in output
    assert f"Already delivered recipients: {1}" in output
    assert f"Accounts without a usable email: {2}" in output
    assert f"From: {DEFAULT_CAMPAIGN_FROM}" in output
    assert f"Reply-To: {PUBLIC_DATASET_EMAIL_REPLY_TO}" in output
    assert "To: one eligible user per message" in output
    assert "CC: none" in output
    assert "BCC: none" in output
    assert f"Subject: {PUBLIC_DATASET_EMAIL_SUBJECT}" in output
    assert PUBLIC_DATASET_EMAIL_BODY in output
    assert f"user_id={eligible.pk}" not in output
    assert "username='blank'" in output
    assert "username='invalid'" in output
    assert "Preview only: no email was sent" in output
    assert len(mail.outbox) == 0
    assert PublicDatasetEmailDelivery.objects.count() == 1


@override_settings(
    EMAIL_BACKEND=LOCMEM_EMAIL_BACKEND,
    PUBLIC_DATASET_EMAIL_FROM=DEFAULT_CAMPAIGN_FROM,
)
def test_send_requires_exact_confirmation_and_releases_lock() -> None:
    """Cancel delivery unless the operator types the exact token.
    Releases the session lock after every safe cancellation."""
    _create_user(username="cancelled", email="cancelled@example.com")
    stdout = io.StringIO()

    with (
        patch(
            f"{COMMAND_MODULE}.try_acquire_public_dataset_email_lock",
            return_value=True,
        ),
        patch(f"{COMMAND_MODULE}.release_public_dataset_email_lock") as release_mock,
        patch("builtins.input", return_value="send"),
    ):
        call_command("send_public_dataset_email", send=True, stdout=stdout)

    output = stdout.getvalue()
    release_mock.assert_called_once_with()
    assert output.index("Body:") < output.index('Type "SEND"')
    assert "Cancelled: no email was sent" in output
    assert len(mail.outbox) == 0
    assert not PublicDatasetEmailDelivery.objects.exists()


@override_settings(
    EMAIL_BACKEND=LOCMEM_EMAIL_BACKEND,
    PUBLIC_DATASET_EMAIL_FROM=DEFAULT_CAMPAIGN_FROM,
)
def test_send_delivers_individual_english_messages_to_every_usable_account() -> None:
    """Send the same English message to every usable account individually.
    Ignores activity, staff, language, contribution, and agreement state."""
    regular = _create_user(username="regular", email="regular@example.com")
    inactive = _create_user(
        username="inactive",
        email="inactive@example.com",
        is_active=False,
    )
    staff = _create_user(
        username="staff",
        email="staff@example.com",
        is_staff=True,
    )
    italian = _create_user(
        username="italian",
        email="italian@example.com",
        language="it",
    )
    accepted = _create_user(username="accepted", email="accepted@example.com")
    ContributorAgreementAcceptance.objects.create(
        user=accepted,
        agreement_version="1.0",
        channel=ContributorAgreementAcceptance.Channel.API_EXISTING_USER,
    )
    _create_user(username="blank", email="")
    _create_user(username="invalid", email="invalid-address")
    stdout = io.StringIO()

    with (
        patch(
            f"{COMMAND_MODULE}.try_acquire_public_dataset_email_lock",
            return_value=True,
        ),
        patch(f"{COMMAND_MODULE}.release_public_dataset_email_lock"),
        patch("builtins.input", return_value="SEND"),
    ):
        call_command("send_public_dataset_email", send=True, stdout=stdout)

    expected_addresses = {
        regular.email,
        inactive.email,
        staff.email,
        italian.email,
        accepted.email,
    }
    assert len(mail.outbox) == len(expected_addresses)
    assert {message.to[0] for message in mail.outbox} == expected_addresses
    for message in mail.outbox:
        assert len(message.to) == 1
        assert message.cc == []
        assert message.bcc == []
        assert message.from_email == DEFAULT_CAMPAIGN_FROM
        assert message.reply_to == [PUBLIC_DATASET_EMAIL_REPLY_TO]
        assert message.subject == PUBLIC_DATASET_EMAIL_SUBJECT
        assert message.body == PUBLIC_DATASET_EMAIL_BODY

    deliveries = PublicDatasetEmailDelivery.objects.order_by("user_id")
    assert deliveries.count() == len(expected_addresses)
    assert {delivery.recipient_email for delivery in deliveries} == expected_addresses
    output = stdout.getvalue()
    assert "Sent: 5" in output
    assert "Unusable email: 2" in output
    assert "Failed: 0" in output


@override_settings(
    EMAIL_BACKEND=LOCMEM_EMAIL_BACKEND,
    PUBLIC_DATASET_EMAIL_FROM=DEFAULT_CAMPAIGN_FROM,
)
def test_successful_rerun_skips_delivered_accounts_without_prompting() -> None:
    """Skip every previously successful account on a retry.
    Avoids duplicate email even when the production command is rerun."""
    _create_user(username="once", email="once@example.com")

    with (
        patch(
            f"{COMMAND_MODULE}.try_acquire_public_dataset_email_lock",
            return_value=True,
        ),
        patch(f"{COMMAND_MODULE}.release_public_dataset_email_lock"),
        patch("builtins.input", return_value="SEND"),
    ):
        call_command("send_public_dataset_email", send=True, stdout=io.StringIO())

    retry_stdout = io.StringIO()
    with (
        patch(
            f"{COMMAND_MODULE}.try_acquire_public_dataset_email_lock",
            return_value=True,
        ),
        patch(f"{COMMAND_MODULE}.release_public_dataset_email_lock"),
        patch("builtins.input") as input_mock,
    ):
        call_command("send_public_dataset_email", send=True, stdout=retry_stdout)

    input_mock.assert_not_called()
    assert len(mail.outbox) == 1
    assert PublicDatasetEmailDelivery.objects.count() == 1
    assert "Eligible unsent recipients: 0" in retry_stdout.getvalue()
    assert "Already delivered recipients: 1" in retry_stdout.getvalue()


@override_settings(
    EMAIL_BACKEND=LOCMEM_EMAIL_BACKEND,
    PUBLIC_DATASET_EMAIL_FROM=DEFAULT_CAMPAIGN_FROM,
)
def test_partial_failure_records_successes_and_retries_only_failed_recipient() -> None:
    """Preserve successful records when another provider call fails.
    Retries only the unrecorded account on the next explicit run."""
    first = _create_user(username="first", email="first@example.com")
    failed = _create_user(username="failed", email="failed@example.com")
    last = _create_user(username="last", email="last@example.com")
    first_stdout = io.StringIO()

    with (
        patch(
            f"{COMMAND_MODULE}.try_acquire_public_dataset_email_lock",
            return_value=True,
        ),
        patch(f"{COMMAND_MODULE}.release_public_dataset_email_lock"),
        patch("builtins.input", return_value="SEND"),
        patch(
            "users.public_dataset_email.EmailMessage.send",
            side_effect=[1, RuntimeError("provider unavailable"), 1],
        ),
        pytest.raises(CommandError, match="1 public-dataset email delivery"),
    ):
        call_command("send_public_dataset_email", send=True, stdout=first_stdout)

    assert set(
        PublicDatasetEmailDelivery.objects.values_list("user_id", flat=True)
    ) == {first.pk, last.pk}
    assert "Sent: 2" in first_stdout.getvalue()
    assert "Failed: 1" in first_stdout.getvalue()
    assert "email='failed@example.com'" in first_stdout.getvalue()

    retry_stdout = io.StringIO()
    with (
        patch(
            f"{COMMAND_MODULE}.try_acquire_public_dataset_email_lock",
            return_value=True,
        ),
        patch(f"{COMMAND_MODULE}.release_public_dataset_email_lock"),
        patch("builtins.input", return_value="SEND"),
        patch(
            "users.public_dataset_email.EmailMessage.send",
            return_value=1,
        ) as send_mock,
    ):
        call_command("send_public_dataset_email", send=True, stdout=retry_stdout)

    send_mock.assert_called_once_with(fail_silently=False)
    assert PublicDatasetEmailDelivery.objects.filter(user=failed).exists()
    assert PublicDatasetEmailDelivery.objects.count() == 3
    assert "Eligible unsent recipients: 1" in retry_stdout.getvalue()
    assert "Already delivered recipients: 2" in retry_stdout.getvalue()


@override_settings(
    EMAIL_BACKEND=CONSOLE_EMAIL_BACKEND,
    PUBLIC_DATASET_EMAIL_FROM=DEFAULT_CAMPAIGN_FROM,
)
def test_send_refuses_console_backend_after_printing_preview() -> None:
    """Reject a backend that only prints messages to the console.
    Prevents false successful-delivery records on a misconfigured VPS."""
    _create_user(username="console", email="console@example.com")
    stdout = io.StringIO()

    with (
        patch(
            f"{COMMAND_MODULE}.try_acquire_public_dataset_email_lock",
            return_value=True,
        ),
        patch(f"{COMMAND_MODULE}.release_public_dataset_email_lock") as release_mock,
        patch("builtins.input") as input_mock,
        pytest.raises(CommandError, match="console email backend"),
    ):
        call_command("send_public_dataset_email", send=True, stdout=stdout)

    input_mock.assert_not_called()
    release_mock.assert_called_once_with()
    assert PUBLIC_DATASET_EMAIL_BODY in stdout.getvalue()
    assert not PublicDatasetEmailDelivery.objects.exists()


@override_settings(
    EMAIL_BACKEND=LOCMEM_EMAIL_BACKEND,
    PUBLIC_DATASET_EMAIL_FROM="Book Corners <noreply@verified.example>",
)
def test_fallback_sender_keeps_visible_contact_and_reply_to() -> None:
    """Allow a provider-approved fallback From address when required.
    Keeps the usable contact address visible and replyable in every message."""
    _create_user(username="fallback", email="fallback@example.com")

    with (
        patch(
            f"{COMMAND_MODULE}.try_acquire_public_dataset_email_lock",
            return_value=True,
        ),
        patch(f"{COMMAND_MODULE}.release_public_dataset_email_lock"),
        patch("builtins.input", return_value="SEND"),
    ):
        call_command("send_public_dataset_email", send=True, stdout=io.StringIO())

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.from_email == "Book Corners <noreply@verified.example>"
    assert message.reply_to == [PUBLIC_DATASET_EMAIL_REPLY_TO]
    assert PUBLIC_DATASET_EMAIL_REPLY_TO in message.body


@override_settings(
    EMAIL_BACKEND=LOCMEM_EMAIL_BACKEND,
    PUBLIC_DATASET_EMAIL_FROM=DEFAULT_CAMPAIGN_FROM,
)
def test_send_aborts_before_preview_when_campaign_lock_is_unavailable() -> None:
    """Reject a second overlapping production command immediately.
    Prevents two processes from selecting and sending the same recipients."""
    _create_user(username="locked", email="locked@example.com")
    stdout = io.StringIO()

    with (
        patch(
            f"{COMMAND_MODULE}.try_acquire_public_dataset_email_lock",
            return_value=False,
        ),
        patch(f"{COMMAND_MODULE}.release_public_dataset_email_lock") as release_mock,
        patch("builtins.input") as input_mock,
        pytest.raises(CommandError, match="already running"),
    ):
        call_command("send_public_dataset_email", send=True, stdout=stdout)

    input_mock.assert_not_called()
    release_mock.assert_not_called()
    assert stdout.getvalue() == ""
    assert len(mail.outbox) == 0


def test_app_manifest_never_runs_public_dataset_email_automatically() -> None:
    """Keep the one-time command out of deploy and cron hooks.
    Requires a deliberate operator action for every delivery attempt."""
    app_json_path = Path(__file__).resolve().parent.parent / "app.json"
    app_config = json.loads(app_json_path.read_text(encoding="utf-8"))

    predeploy = app_config["scripts"]["dokku"]["predeploy"]
    cron_commands = [entry["command"] for entry in app_config["cron"]]
    assert "send_public_dataset_email" not in predeploy
    assert all("send_public_dataset_email" not in command for command in cron_commands)


def test_email_copy_uses_refreshed_contributor_agreement_wording() -> None:
    """Use the approved present-tense contributor agreement update.
    Avoids sending the outdated forthcoming-agreement paragraph from the issue."""
    assert "I've also published a Contributor Agreement" in PUBLIC_DATASET_EMAIL_BODY
    assert "Existing users do not need to accept it, reply, or take any action." in (
        PUBLIC_DATASET_EMAIL_BODY
    )
    assert "Once it's ready" not in PUBLIC_DATASET_EMAIL_BODY
