"""APNs provider client for server-side push notification delivery."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from django.conf import settings

from users.models import DeviceToken

logger = logging.getLogger(__name__)

APNS_PRODUCTION_HOST = "api.push.apple.com"
APNS_SANDBOX_HOST = "api.sandbox.push.apple.com"
APNS_TOKEN_TTL_SECONDS = 50 * 60
APNS_TIMEOUT_SECONDS = 10.0
INVALID_DEVICE_TOKEN_REASONS = frozenset({"BadDeviceToken", "Unregistered"})

_cached_provider_token = ""
_cached_provider_token_issued_at = 0


@dataclass(frozen=True)
class APNSResult:
    """Result returned by APNs for one delivery attempt.
    Carries enough response data for cleanup decisions and logging."""

    status_code: int
    reason: str = ""
    apns_id: str = ""
    timestamp: int | None = None


def reset_provider_token_cache() -> None:
    """Clear the cached APNs provider token.
    Tests and credential rotations use this to force a fresh JWT."""
    global _cached_provider_token, _cached_provider_token_issued_at

    _cached_provider_token = ""
    _cached_provider_token_issued_at = 0


def _normalized_auth_key() -> str:
    """Return APNs private key text in PEM format.
    Supports environment variables stored with escaped newlines."""
    return settings.APNS_AUTH_KEY.replace("\\n", "\n")


def _build_jwt(*, now_seconds: int | None = None) -> str:
    """Build or reuse an APNs provider authentication JWT.
    Apple accepts ES256 provider tokens for up to one hour."""
    global _cached_provider_token, _cached_provider_token_issued_at

    issued_at = int(time.time()) if now_seconds is None else now_seconds
    if (
        _cached_provider_token
        and _cached_provider_token_issued_at
        and issued_at - _cached_provider_token_issued_at < APNS_TOKEN_TTL_SECONDS
    ):
        return _cached_provider_token

    _cached_provider_token = jwt.encode(
        {"iss": settings.APNS_TEAM_ID, "iat": issued_at},
        _normalized_auth_key(),
        algorithm="ES256",
        headers={"kid": settings.APNS_KEY_ID},
    )
    _cached_provider_token_issued_at = issued_at
    return _cached_provider_token


def _resolve_environment(*, environment: str | None) -> str:
    """Resolve the APNs environment for a delivery attempt.
    Falls back to APNS_USE_SANDBOX only when no token environment is supplied."""
    if environment in {
        DeviceToken.Environment.SANDBOX.value,
        DeviceToken.Environment.PRODUCTION.value,
    }:
        return environment
    if environment:
        raise ValueError(f"Unsupported APNs environment: {environment}")
    if settings.APNS_USE_SANDBOX:
        return DeviceToken.Environment.SANDBOX.value
    return DeviceToken.Environment.PRODUCTION.value


def _resolve_host(*, environment: str | None) -> str:
    """Return the APNs host for the requested environment.
    Production tokens and sandbox tokens must be sent to separate hosts."""
    resolved_environment = _resolve_environment(environment=environment)
    if resolved_environment == DeviceToken.Environment.PRODUCTION.value:
        return APNS_PRODUCTION_HOST
    return APNS_SANDBOX_HOST


def _build_headers() -> dict[str, str]:
    """Build APNs request headers for alert notifications.
    Includes token auth, bundle topic, push type, and priority."""
    return {
        "authorization": f"bearer {_build_jwt()}",
        "apns-topic": settings.APNS_BUNDLE_ID,
        "apns-push-type": "alert",
        "apns-priority": "10",
    }


def _build_payload(
    *,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an APNs alert payload.
    Custom data is nested under a top-level data key for client routing."""
    payload: dict[str, Any] = {
        "aps": {
            "alert": {
                "title": title,
                "body": body,
            },
            "sound": "default",
        },
    }
    if data:
        payload["data"] = data
    return payload


def _parse_response(*, response: httpx.Response) -> APNSResult:
    """Convert an APNs HTTP response into an APNSResult.
    Handles empty success bodies and structured JSON error bodies."""
    reason = ""
    timestamp = None
    if response.content:
        try:
            body = response.json()
        except ValueError:
            body = {}
        reason = str(body.get("reason", ""))
        raw_timestamp = body.get("timestamp")
        timestamp = raw_timestamp if isinstance(raw_timestamp, int) else None

    return APNSResult(
        status_code=response.status_code,
        reason=reason,
        apns_id=response.headers.get("apns-id", ""),
        timestamp=timestamp,
    )


def is_invalid_token_result(*, result: APNSResult) -> bool:
    """Return whether APNs indicates the token should be removed.
    Covers inactive tokens and malformed device-token responses."""
    if result.status_code == 410:
        return True
    return result.status_code == 400 and result.reason in INVALID_DEVICE_TOKEN_REASONS


def send(
    *,
    token: str,
    title: str,
    body: str,
    environment: str | None,
    data: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> APNSResult:
    """Send one alert notification to APNs.
    Returns a delivery result instead of raising for transport failures."""
    host = _resolve_host(environment=environment)
    url = f"https://{host}/3/device/{token}"
    payload = _build_payload(title=title, body=body, data=data)
    active_client = client or httpx.Client(http2=True, timeout=APNS_TIMEOUT_SECONDS)
    should_close_client = client is None

    try:
        response = active_client.post(url, headers=_build_headers(), json=payload)
    except httpx.HTTPError as exc:
        logger.warning("APNs delivery transport error: %s", exc)
        return APNSResult(status_code=0, reason=exc.__class__.__name__)
    finally:
        if should_close_client:
            active_client.close()

    return _parse_response(response=response)
