"""Preview and send the one-time public-dataset account email."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from users.public_dataset_email import (
    PUBLIC_DATASET_EMAIL_BODY,
    PUBLIC_DATASET_EMAIL_REPLY_TO,
    PUBLIC_DATASET_EMAIL_SUBJECT,
    PublicDatasetEmailSelection,
    PublicDatasetEmailRecipient,
    release_public_dataset_email_lock,
    select_public_dataset_email_recipients,
    send_public_dataset_email,
    try_acquire_public_dataset_email_lock,
)

CONSOLE_EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


@dataclass(frozen=True, slots=True)
class DeliveryFailure:
    """Describe one recipient whose backend send failed.
    Keeps the final operator report concise and actionable."""

    recipient: PublicDatasetEmailRecipient
    error: str


class Command(BaseCommand):
    """Preview and explicitly send one individual email per user.
    Records successful deliveries so retries do not duplicate messages."""

    help = "Preview or send the one-time public-dataset email to every user."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the explicit delivery activation flag.
        Keeps the default command invocation preview-only."""
        parser.add_argument(
            "--send",
            action="store_true",
            help="Send after showing the preview and receiving confirmation",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Run the preview or guarded individual-delivery workflow.
        Reports unusable accounts and failures without exposing recipients to each other."""
        send_requested = bool(options["send"])
        lock_acquired = False

        if send_requested:
            lock_acquired = try_acquire_public_dataset_email_lock()
            if not lock_acquired:
                raise CommandError(
                    "Another public-dataset email command is already running."
                )

        try:
            selection = select_public_dataset_email_recipients()
            from_email = str(settings.PUBLIC_DATASET_EMAIL_FROM)
            self._write_preview(selection=selection, from_email=from_email)

            if not send_requested:
                self.stdout.write(
                    self.style.WARNING(
                        "Preview only: no email was sent and no delivery was recorded."
                    )
                )
                return

            self._validate_delivery_configuration(from_email=from_email)
            if not selection.recipients:
                self._write_summary(selection=selection, sent_count=0, failures=[])
                return

            if not self._confirmed():
                self.stdout.write(
                    self.style.WARNING(
                        "Cancelled: no email was sent and no delivery was recorded."
                    )
                )
                return

            sent_count, failures = self._send_messages(
                selection=selection,
                from_email=from_email,
            )
            self._write_summary(
                selection=selection,
                sent_count=sent_count,
                failures=failures,
            )
            if failures:
                raise CommandError(
                    f"{len(failures)} public-dataset email delivery attempt(s) failed."
                )
        finally:
            if lock_acquired:
                release_public_dataset_email_lock()

    def _write_preview(
        self,
        *,
        selection: PublicDatasetEmailSelection,
        from_email: str,
    ) -> None:
        """Print the final message and current recipient counts.
        Gives the operator all delivery details before confirmation."""
        self.stdout.write("Public dataset email preview")
        self.stdout.write(f"Eligible unsent recipients: {len(selection.recipients)}")
        self.stdout.write(
            f"Already delivered recipients: {selection.already_delivered_count}"
        )
        self.stdout.write(
            f"Accounts without a usable email: {len(selection.unusable_accounts)}"
        )
        self.stdout.write("")
        self.stdout.write(f"From: {from_email}")
        self.stdout.write(f"Reply-To: {PUBLIC_DATASET_EMAIL_REPLY_TO}")
        self.stdout.write("To: one eligible user per message")
        self.stdout.write("CC: none")
        self.stdout.write("BCC: none")
        self.stdout.write(f"Subject: {PUBLIC_DATASET_EMAIL_SUBJECT}")
        self.stdout.write("")
        self.stdout.write("Body:")
        self.stdout.write(PUBLIC_DATASET_EMAIL_BODY)
        self._write_unusable_accounts(selection=selection)

    def _validate_delivery_configuration(self, *, from_email: str) -> None:
        """Reject configurations that cannot perform a real delivery.
        Prevents console output from being recorded as a successful send."""
        if not from_email.strip():
            raise CommandError("PUBLIC_DATASET_EMAIL_FROM must not be empty.")
        if settings.EMAIL_BACKEND == CONSOLE_EMAIL_BACKEND:
            raise CommandError(
                "The console email backend cannot be used with --send. "
                "Configure a real email backend first."
            )

    def _confirmed(self) -> bool:
        """Require the exact operator confirmation token.
        Treats EOF and every other response as a safe cancellation."""
        self.stdout.write("")
        self.stdout.write("Type \"SEND\" to deliver these messages:")
        try:
            response = input()
        except EOFError:
            return False
        return response == "SEND"

    def _send_messages(
        self,
        *,
        selection: PublicDatasetEmailSelection,
        from_email: str,
    ) -> tuple[int, list[DeliveryFailure]]:
        """Deliver one message at a time and retain every failure.
        Continues through the snapshot so one bad address does not block others."""
        sent_count = 0
        failures: list[DeliveryFailure] = []
        for recipient in selection.recipients:
            try:
                send_public_dataset_email(
                    recipient=recipient,
                    from_email=from_email,
                )
            except Exception as exc:
                failures.append(
                    DeliveryFailure(recipient=recipient, error=str(exc))
                )
            else:
                sent_count += 1
        return sent_count, failures

    def _write_summary(
        self,
        *,
        selection: PublicDatasetEmailSelection,
        sent_count: int,
        failures: list[DeliveryFailure],
    ) -> None:
        """Print final delivery totals and failed account details.
        Makes a partial production run safe to assess before retrying."""
        self.stdout.write("")
        self.stdout.write("Public dataset email summary")
        self.stdout.write(f"Sent: {sent_count}")
        self.stdout.write(
            f"Already delivered: {selection.already_delivered_count}"
        )
        self.stdout.write(f"Unusable email: {len(selection.unusable_accounts)}")
        self.stdout.write(f"Failed: {len(failures)}")
        self._write_unusable_accounts(selection=selection)
        if failures:
            self.stdout.write("Failed deliveries:")
            for failure in failures:
                recipient = failure.recipient
                self.stdout.write(
                    f"- user_id={recipient.user_id} username={recipient.username!r} "
                    f"email={recipient.email!r} error={failure.error}"
                )

    def _write_unusable_accounts(
        self,
        *,
        selection: PublicDatasetEmailSelection,
    ) -> None:
        """Print accounts that lack a valid delivery address.
        Supports manual review without changing or excluding other accounts."""
        if not selection.unusable_accounts:
            return
        self.stdout.write("Accounts without a usable email:")
        for account in selection.unusable_accounts:
            self.stdout.write(
                f"- user_id={account.user_id} username={account.username!r} "
                f"email={account.email!r}"
            )
