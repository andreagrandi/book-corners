"""Background tasks for APNs push notification delivery."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.tasks import task
from django.utils import timezone

from users import apns
from users.models import DeviceToken

logger = logging.getLogger(__name__)


def _send_device_token(
    *,
    device_token: DeviceToken,
    title: str,
    body: str,
    data: dict[str, Any] | None,
) -> None:
    """Send one push notification and persist token lifecycle effects.
    Deletes invalid tokens and updates last-used timestamps on success."""
    try:
        result = apns.send(
            token=device_token.token,
            title=title,
            body=body,
            environment=device_token.environment,
            data=data,
        )
    except Exception:
        logger.exception("APNs delivery failed before request for token %s", device_token.pk)
        return

    if result.status_code == 200:
        device_token.last_used_at = timezone.now()
        device_token.save(update_fields=["last_used_at", "updated_at"])
        return

    if apns.is_invalid_token_result(result=result):
        token_pk = device_token.pk
        device_token.delete()
        logger.info("Deleted invalid APNs device token %s", token_pk)
        return

    logger.warning(
        "APNs delivery failed for token %s: status=%s reason=%s apns_id=%s",
        device_token.pk,
        result.status_code,
        result.reason,
        result.apns_id,
    )


@task()
def send_push_to_user(
    user_id: int,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Send a push notification to all active tokens for one user.
    Does nothing when APNs credentials are not configured."""
    if not settings.APNS_ENABLED:
        return

    device_tokens = DeviceToken.objects.filter(
        user_id=user_id,
        is_active=True,
    )
    for device_token in device_tokens:
        _send_device_token(
            device_token=device_token,
            title=title,
            body=body,
            data=data,
        )


@task()
def send_push_to_staff(
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Send a push notification to every active staff device token.
    Does nothing when APNs credentials are not configured."""
    if not settings.APNS_ENABLED:
        return

    device_tokens = DeviceToken.objects.filter(
        user__is_staff=True,
        is_active=True,
    ).select_related("user")
    for device_token in device_tokens:
        _send_device_token(
            device_token=device_token,
            title=title,
            body=body,
            data=data,
        )

