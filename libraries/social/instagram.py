"""Instagram posting client for library social media sharing."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal, NamedTuple

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.instagram.com/v26.0"
CONTAINER_POLL_INTERVAL = 5  # seconds between status checks
CONTAINER_POLL_MAX_ATTEMPTS = 12  # up to 60 seconds total
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 30
RETRY_MAX_DELAY_SECONDS = 60


class InstagramResult(NamedTuple):
    """Result of a successful Instagram post.
    Carries the permalink, media ID, and any permalink lookup failure."""

    permalink: str
    media_id: str
    permalink_error: InstagramAPIError | None = None

class InstagramAPIError(RuntimeError):
    """Represent a safe, structured Instagram Graph API failure.
    Carries Meta diagnostics without exposing request details or URLs."""

    def __init__(
        self,
        *,
        operation: str,
        status_code: int,
        message: str,
        code: int | None = None,
        error_subcode: int | None = None,
        is_transient: bool | None = None,
        fbtrace_id: str | None = None,
    ) -> None:
        """Initialize an Instagram API error with safe diagnostic fields.
        The exception text contains no request payload or URL."""
        self.operation = operation
        self.status_code = status_code
        self.message = message
        self.code = code
        self.error_subcode = error_subcode
        self.is_transient = is_transient
        self.fbtrace_id = fbtrace_id

        metadata = [f"operation={operation}"]
        if code is not None:
            metadata.append(f"code={code}")
        if error_subcode is not None:
            metadata.append(f"error_subcode={error_subcode}")
        if is_transient is not None:
            metadata.append(f"is_transient={is_transient}")
        if fbtrace_id:
            metadata.append(f"fbtrace_id={fbtrace_id}")
        super().__init__(
            f"Instagram API {status_code}: {message} ({'; '.join(metadata)})"
        )


def _raise_with_detail(
    response: requests.Response,
    *,
    operation: str = "request",
    sensitive_values: tuple[str, ...] = (),
) -> None:
    """Raise a structured exception for a failed Instagram API response.
    Parses only safe Meta error fields and never includes raw response data."""
    if response.ok:
        return

    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = None

    error = payload.get("error", payload) if isinstance(payload, dict) else None
    if isinstance(error, dict) and error:
        message = error.get("message")
        if not isinstance(message, str) or not message:
            message = "Instagram API request failed"
        for sensitive_value in sensitive_values:
            message = message.replace(sensitive_value, "[redacted]")

        code = error.get("code")
        if isinstance(code, bool):
            code = None
        elif isinstance(code, str) and code.isdecimal():
            code = int(code)
        elif not isinstance(code, int):
            code = None

        error_subcode = error.get("error_subcode")
        if isinstance(error_subcode, bool):
            error_subcode = None
        elif isinstance(error_subcode, str) and error_subcode.isdecimal():
            error_subcode = int(error_subcode)
        elif not isinstance(error_subcode, int):
            error_subcode = None

        is_transient = error.get("is_transient")
        if not isinstance(is_transient, bool):
            is_transient = None

        fbtrace_id = error.get("fbtrace_id")
        if not isinstance(fbtrace_id, str) or not fbtrace_id:
            fbtrace_id = None
    else:
        response_text = getattr(response, "text", "")
        if payload is None and response_text:
            message = "non-JSON response body"
        elif not payload and not response_text:
            message = "empty response body"
        else:
            message = "Instagram API request failed"
        code = None
        error_subcode = None
        is_transient = None
        fbtrace_id = None

    raise InstagramAPIError(
        operation=operation,
        status_code=response.status_code,
        message=message,
        code=code,
        error_subcode=error_subcode,
        is_transient=is_transient,
        fbtrace_id=fbtrace_id,
    )


def _read_required_string(
    response: requests.Response,
    *,
    operation: str,
    field: str,
) -> str:
    """Read a required string from a successful JSON object response.
    Converts malformed payloads into safe structured API errors."""
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = None

    value = payload.get(field) if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value:
        raise InstagramAPIError(
            operation=operation,
            status_code=response.status_code,
            message=f"invalid successful response: missing or invalid {field}",
        )
    return value


def _request_with_retry(
    *,
    operation: str,
    method: Literal["get", "post"],
    url: str,
    retry_transient: bool = True,
    sensitive_values: tuple[str, ...] = (),
    **kwargs,
) -> requests.Response:
    """Perform one Instagram API step with bounded transient retries.
    Retries only this request and raises structured errors when it fails."""
    request = requests.get if method == "get" else requests.post
    payload_sensitive_values = tuple(
        value
        for payload_name in ("data", "params")
        for payload in (kwargs.get(payload_name),)
        if isinstance(payload, dict)
        for value in payload.values()
        if isinstance(value, str) and value
    )
    sensitive_values = tuple(
        sorted(
            {
                value
                for value in (*sensitive_values, *payload_sensitive_values)
                if isinstance(value, str) and value
            },
            key=len,
            reverse=True,
        )
    )

    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            response = request(url, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            if not retry_transient or attempt == RETRY_MAX_ATTEMPTS:
                attempt_label = "attempt" if attempt == 1 else "attempts"
                raise InstagramAPIError(
                    operation=operation,
                    status_code=0,
                    message=f"transport failure after {attempt} {attempt_label}",
                ) from None
            retry_status_code = None
        else:
            if response.ok:
                return response
            if (
                not retry_transient
                or response.status_code not in TRANSIENT_STATUS_CODES
                or attempt == RETRY_MAX_ATTEMPTS
            ):
                _raise_with_detail(
                    response,
                    operation=operation,
                    sensitive_values=sensitive_values,
                )
            retry_status_code = response.status_code

        delay = min(
            RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
            RETRY_MAX_DELAY_SECONDS,
        )
        if retry_status_code is None:
            logger.warning(
                "Instagram %s transport failure "
                "(attempt %d/%d); retrying in %d seconds",
                operation,
                attempt,
                RETRY_MAX_ATTEMPTS,
                delay,
            )
        else:
            logger.warning(
                "Instagram %s temporarily failed with HTTP %s "
                "(attempt %d/%d); retrying in %d seconds",
                operation,
                retry_status_code,
                attempt,
                RETRY_MAX_ATTEMPTS,
                delay,
            )
        time.sleep(delay)

    raise InstagramAPIError(
        operation=operation,
        status_code=0,
        message="Instagram API retry loop exited unexpectedly",
    )


def _get_access_token() -> str:
    """Return the current Instagram access token.
    Prefers the DB-stored token, falls back to the env var for bootstrap."""
    from libraries.models import InstagramToken

    token_row = InstagramToken.objects.order_by("-refreshed_at").first()
    if token_row:
        return token_row.access_token
    return settings.INSTAGRAM_ACCESS_TOKEN


def _wait_for_container(container_id: str, access_token: str) -> None:
    """Poll the container status until it is ready to publish.
    Raises RuntimeError if the container fails or times out."""
    for attempt in range(CONTAINER_POLL_MAX_ATTEMPTS):
        response = _request_with_retry(
            operation="container_status",
            method="get",
            url=f"{GRAPH_API_URL}/{container_id}",
            sensitive_values=(container_id,),
            params={
                "fields": "status_code",
                "access_token": access_token,
            },
            timeout=30,
        )
        status = _read_required_string(
            response,
            operation="container_status",
            field="status_code",
        )
        logger.info("Instagram container status: %s (attempt %d)", status, attempt + 1)

        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container failed with status: {status}")

        time.sleep(CONTAINER_POLL_INTERVAL)

    raise RuntimeError("Instagram container timed out waiting to become FINISHED")


def _publish_container(user_id: str, creation_id: str, access_token: str) -> str:
    """Publish a ready Instagram container and return its media ID.
    Retries transient HTTP responses without recreating the container."""
    response = _request_with_retry(
        operation="media_publish",
        method="post",
        url=f"{GRAPH_API_URL}/{user_id}/media_publish",
        sensitive_values=(user_id, creation_id),
        data={
            "creation_id": creation_id,
            "access_token": access_token,
        },
        timeout=60,
    )
    return _read_required_string(
        response,
        operation="media_publish",
        field="id",
    )


def post_library(library, text: str, image_path: Path) -> InstagramResult:
    """Post a library with photo to Instagram and return the result.
    Uses the two-step container publish flow via the Instagram Graph API."""
    from libraries.image_processing import ensure_instagram_aspect_ratio

    ensure_instagram_aspect_ratio(library=library)

    user_id = settings.INSTAGRAM_USER_ID
    access_token = _get_access_token()
    base_url = getattr(settings, "SITE_URL", "http://localhost:8000").rstrip("/")
    image_url = f"{base_url}{library.photo.url}"

    # Step 1: Create media container
    container_response = _request_with_retry(
        operation="container_creation",
        method="post",
        url=f"{GRAPH_API_URL}/{user_id}/media",
        sensitive_values=(user_id,),
        data={
            "image_url": image_url,
            "caption": text,
            "access_token": access_token,
        },
        timeout=60,
    )
    creation_id = _read_required_string(
        container_response,
        operation="container_creation",
        field="id",
    )
    logger.info("Created Instagram container")

    # Step 2: Wait for container to be ready
    _wait_for_container(creation_id, access_token)

    # Step 3: Publish the container
    media_id = _publish_container(
        user_id=user_id,
        creation_id=creation_id,
        access_token=access_token,
    )
    logger.info("Published Instagram media")
    try:
        permalink_response = _request_with_retry(
            operation="permalink",
            method="get",
            url=f"{GRAPH_API_URL}/{media_id}",
            sensitive_values=(media_id,),
            params={
                "fields": "permalink",
                "access_token": access_token,
            },
            timeout=30,
        )
        permalink = _read_required_string(
            permalink_response,
            operation="permalink",
            field="permalink",
        )
    except InstagramAPIError as exc:
        logger.warning("Instagram media published but permalink is unavailable")
        return InstagramResult(
            permalink="",
            media_id=media_id,
            permalink_error=exc,
        )

    return InstagramResult(permalink=permalink, media_id=media_id)


def comment_on_media(media_id: str, text: str) -> str:
    """Post a comment on an Instagram media object.
    Returns the comment ID from the Graph API."""
    access_token = _get_access_token()
    response = _request_with_retry(
        operation="comment",
        method="post",
        url=f"{GRAPH_API_URL}/{media_id}/comments",
        retry_transient=False,
        sensitive_values=(media_id,),
        data={
            "message": text,
            "access_token": access_token,
        },
        timeout=30,
    )
    comment_id = _read_required_string(
        response,
        operation="comment",
        field="id",
    )
    logger.info("Posted Instagram comment")
    return comment_id

