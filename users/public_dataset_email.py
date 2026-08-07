"""Build and send the one-time public-dataset account email."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import connection

from users.models import PublicDatasetEmailDelivery, User

PUBLIC_DATASET_EMAIL_SUBJECT = "A small update about Book Corners"
PUBLIC_DATASET_EMAIL_REPLY_TO = "info@bookcorners.org"
PUBLIC_DATASET_EMAIL_LOCK_ID = 7_009_000_000_000_161
PUBLIC_DATASET_EMAIL_BODY = """Hi everyone,

Over the last few days I've been reviewing some aspects of Book Corners following valuable feedback from the community, and I've made a couple of improvements that I'd like to share.

Book Corners has always made library information publicly available through its website and API. To better align with the OpenStreetMap ODbL license and give something back to the open data community, the same public library data is now also available as a bulk download.

The downloadable dataset contains only public library information. It does not include names, email addresses, account details, or any other personal information.

I've also published a Contributor Agreement to make it clearer how contributed library data may be used and redistributed. New users are now asked to accept it during registration. Existing users do not need to accept it, reply, or take any action.

If you're happy for your library to remain public, you don't need to do anything.

If you have any concerns about your library being included in the downloadable dataset, please email me at info@bookcorners.org. I'll be happy to remove it from the public dataset. Since Book Corners is built around sharing publicly available libraries, this also means the library will no longer be listed on the platform.

As always, thank you for helping make Book Corners what it is today.

Best regards,

Andrea"""


@dataclass(frozen=True, slots=True)
class PublicDatasetEmailRecipient:
    """Snapshot one eligible command recipient.
    Keeps preview and delivery tied to the same account address."""

    user_id: int
    username: str
    email: str


@dataclass(frozen=True, slots=True)
class UnusablePublicDatasetEmailAccount:
    """Describe one account without a deliverable address.
    Gives the operator enough context for manual follow-up."""

    user_id: int
    username: str
    email: str


@dataclass(frozen=True, slots=True)
class PublicDatasetEmailSelection:
    """Capture the one-time campaign recipient snapshot.
    Reports eligible, delivered, and unusable account groups separately."""

    recipients: tuple[PublicDatasetEmailRecipient, ...]
    already_delivered_count: int
    unusable_accounts: tuple[UnusablePublicDatasetEmailAccount, ...]


def select_public_dataset_email_recipients() -> PublicDatasetEmailSelection:
    """Select every unsent account with a valid email address.
    Applies no activity, language, contribution, ownership, or agreement filters."""
    delivered_user_ids = set(
        PublicDatasetEmailDelivery.objects.values_list("user_id", flat=True)
    )
    recipients: list[PublicDatasetEmailRecipient] = []
    unusable_accounts: list[UnusablePublicDatasetEmailAccount] = []
    already_delivered_count = 0

    users = User.objects.only("id", "username", "email").order_by("id")
    for user in users.iterator(chunk_size=500):
        if user.pk in delivered_user_ids:
            already_delivered_count += 1
            continue

        email = user.email.strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            unusable_accounts.append(
                UnusablePublicDatasetEmailAccount(
                    user_id=user.pk,
                    username=user.username,
                    email=user.email,
                )
            )
            continue

        recipients.append(
            PublicDatasetEmailRecipient(
                user_id=user.pk,
                username=user.username,
                email=email,
            )
        )

    return PublicDatasetEmailSelection(
        recipients=tuple(recipients),
        already_delivered_count=already_delivered_count,
        unusable_accounts=tuple(unusable_accounts),
    )


def build_public_dataset_email(
    *,
    recipient_email: str,
    from_email: str,
) -> EmailMessage:
    """Build one recipient-specific plain-text message.
    Sets an individual To address with no CC or BCC recipients."""
    return EmailMessage(
        subject=PUBLIC_DATASET_EMAIL_SUBJECT,
        body=PUBLIC_DATASET_EMAIL_BODY,
        from_email=from_email,
        to=[recipient_email],
        cc=[],
        bcc=[],
        reply_to=[PUBLIC_DATASET_EMAIL_REPLY_TO],
    )


def send_public_dataset_email(
    *,
    recipient: PublicDatasetEmailRecipient,
    from_email: str,
) -> PublicDatasetEmailDelivery:
    """Send one message and record backend acceptance.
    Leaves failed recipients unrecorded so a later command can retry them."""
    message = build_public_dataset_email(
        recipient_email=recipient.email,
        from_email=from_email,
    )
    sent_count = message.send(fail_silently=False)
    if sent_count != 1:
        raise RuntimeError("The email backend did not accept the message.")

    return PublicDatasetEmailDelivery.objects.create(
        user_id=recipient.user_id,
        recipient_email=recipient.email,
    )


def try_acquire_public_dataset_email_lock() -> bool:
    """Try to acquire the campaign-wide PostgreSQL session lock.
    Prevents overlapping command processes from sending duplicate messages."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s)",
            [PUBLIC_DATASET_EMAIL_LOCK_ID],
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def release_public_dataset_email_lock() -> None:
    """Release the campaign-wide PostgreSQL session lock.
    Makes later explicit retries available after this process finishes."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_unlock(%s)",
            [PUBLIC_DATASET_EMAIL_LOCK_ID],
        )
