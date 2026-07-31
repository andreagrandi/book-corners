import math
import re
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext

from libraries.models import (
    Library,
    OpenStreetMapContribution,
    OpenStreetMapContributionEvent,
)

OVERPASS_MAX_ATTEMPTS = 3
OVERPASS_MAX_RETRY_DELAY_SECONDS = 10.0
OVERPASS_CONNECT_TIMEOUT_SECONDS = 5.0
OVERPASS_RESPONSE_TIMEOUT_SECONDS = 30.0
OSM_PUBLIC_TAGS = (
    "amenity",
    "shop",
    "name",
    "addr:street",
    "addr:housenumber",
    "brand",
    "operator",
)
OSM_ELEMENT_TYPES = {
    choice.value for choice in OpenStreetMapContribution.ElementType
}
OSM_COMPATIBLE_BOOK_SHARING_AMENITIES = {"public_bookcase", "give_box"}
SENSITIVE_REASON_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:password|passwd|secret|api[_ -]?key|access[_ -]?token|bearer)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){7,}(?!\d)"),
)


class OpenStreetMapDuplicateCheckError(Exception):
    """Describe one sanitized read-only duplicate-check failure.
    Carries a stable code and operator-safe message for persistence."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        """Initialize a duplicate-check failure without raw response data.
        Keeps stored and displayed errors safe for operators."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def run_osm_duplicate_check(
    *,
    library: Library,
    actor: Any,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> OpenStreetMapContribution:
    """Run and persist one read-only OSM duplicate check.
    Stores only allowlisted candidate data and an immutable audit event."""
    reason = library.osm_precheck_ineligibility_reason()
    if reason is not None:
        raise ValueError(reason)

    try:
        payload = _fetch_overpass_payload(
            library=library,
            client=client,
            sleep=sleep,
        )
        candidates = _build_duplicate_candidates(
            library=library,
            elements=payload["elements"],
            radius_meters=_duplicate_radius_meters(),
        )
    except OpenStreetMapDuplicateCheckError as exc:
        return _record_duplicate_check_failure(
            library=library,
            actor=actor,
            error=exc,
        )

    return _record_duplicate_check_success(
        library=library,
        actor=actor,
        candidates=candidates,
    )


def withdraw_osm_submission_permission(
    *,
    library: Library,
    actor: Any,
) -> OpenStreetMapContribution:
    """Withdraw a submitter's unconsumed OSM permission and record it.
    Never grants permission or changes a completed OSM link."""
    with transaction.atomic():
        locked_library = Library.objects.select_for_update().get(pk=library.pk)
        contribution, _ = OpenStreetMapContribution.objects.get_or_create(
            library=locked_library
        )
        if not locked_library.osm_submission_allowed:
            raise ValueError(
                gettext("OpenStreetMap permission is not currently granted.")
            )
        if (
            contribution.osm_element_id is not None
            or contribution.status
            in {
                OpenStreetMapContribution.Status.ALREADY_PRESENT,
                OpenStreetMapContribution.Status.SUBMITTING,
                OpenStreetMapContribution.Status.CONTRIBUTED,
            }
        ):
            raise ValueError(
                gettext(
                    "OpenStreetMap permission cannot be withdrawn after "
                    "linking or submission."
                )
            )

        locked_library.osm_submission_allowed = False
        locked_library.osm_submission_allowed_at = None
        locked_library.save(
            update_fields=[
                "osm_submission_allowed",
                "osm_submission_allowed_at",
                "updated_at",
            ]
        )
        OpenStreetMapContributionEvent.objects.create(
            contribution=contribution,
            event_type=OpenStreetMapContributionEvent.EventType.PERMISSION_WITHDRAWN,
            outcome=OpenStreetMapContributionEvent.Outcome.SUCCESS,
            actor=actor,
            details={},
        )
        return contribution


def resolve_osm_duplicate(
    *,
    contribution: OpenStreetMapContribution,
    actor: Any,
    resolution: str,
    element_type: str,
    element_id: int,
    reason: str,
) -> OpenStreetMapContribution:
    """Record a staff decision for one possible OSM duplicate.
    Links a confirmed feature or requires a fresh check after rejection."""
    allowed_resolutions = {"same_feature", "different_feature", "unclear"}
    if resolution not in allowed_resolutions:
        raise ValueError(gettext("Select a valid duplicate resolution."))
    if element_type not in OSM_ELEMENT_TYPES:
        raise ValueError(gettext("Select a valid OpenStreetMap element type."))
    clean_reason = _validated_resolution_reason(reason=reason)

    with transaction.atomic():
        locked_contribution = (
            OpenStreetMapContribution.objects.select_for_update()
            .select_related("library")
            .get(pk=contribution.pk)
        )
        if (
            locked_contribution.status
            != OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
        ):
            raise ValueError(
                gettext("This library has no unresolved possible duplicate.")
            )
        if not _candidate_exists(
            candidates=get_unresolved_osm_duplicate_candidates(
                contribution=locked_contribution
            ),
            element_type=element_type,
            element_id=element_id,
        ):
            raise ValueError(
                gettext("The selected OpenStreetMap candidate is not current.")
            )

        outcome = OpenStreetMapContributionEvent.Outcome.SUCCESS
        if resolution == "same_feature":
            locked_contribution.status = (
                OpenStreetMapContribution.Status.ALREADY_PRESENT
            )
            locked_contribution.osm_element_type = element_type
            locked_contribution.osm_element_id = element_id
            update_fields = ["status", "osm_element_type", "osm_element_id"]
        elif resolution == "different_feature":
            locked_contribution.status = OpenStreetMapContribution.Status.UNCHECKED
            update_fields = ["status"]
        else:
            outcome = OpenStreetMapContributionEvent.Outcome.BLOCKED
            update_fields = []

        if update_fields:
            try:
                locked_contribution.save(update_fields=update_fields)
            except IntegrityError as exc:
                raise ValueError(
                    gettext(
                        "This OpenStreetMap feature is already linked to "
                        "another library."
                    )
                ) from exc

        OpenStreetMapContributionEvent.objects.create(
            contribution=locked_contribution,
            event_type=OpenStreetMapContributionEvent.EventType.DUPLICATE_RESOLUTION,
            outcome=outcome,
            actor=actor,
            details={
                "resolution": resolution,
                "element_type": element_type,
                "element_id": element_id,
                "reason": clean_reason,
            },
        )
        return locked_contribution


def get_unresolved_osm_duplicate_candidates(
    *,
    contribution: OpenStreetMapContribution,
) -> list[dict[str, Any]]:
    """Return blocking candidates without a different-feature resolution.
    Keeps resolved warnings visible in history but out of active staff choices."""
    resolved_pairs = _different_feature_resolution_pairs(
        contribution=contribution
    )
    return [
        candidate
        for candidate in contribution.duplicate_candidates
        if candidate.get("signals")
        and (candidate.get("element_type"), candidate.get("element_id"))
        not in resolved_pairs
    ]


def _fetch_overpass_payload(
    *,
    library: Library,
    client: httpx.Client | None,
    sleep: Callable[[float], None],
) -> dict[str, list[dict[str, Any]]]:
    """Fetch and validate one Overpass JSON response.
    Retries only the read-only request with bounded delays."""
    overpass_url = settings.OSM_OVERPASS_URL.strip()
    user_agent = settings.OSM_USER_AGENT.strip()
    if not overpass_url or not user_agent:
        raise OpenStreetMapDuplicateCheckError(
            code="not_configured",
            message=gettext(
                "OpenStreetMap duplicate checks require an Overpass endpoint "
                "and contactable user agent."
            ),
        )

    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=httpx.Timeout(
            OVERPASS_RESPONSE_TIMEOUT_SECONDS,
            connect=OVERPASS_CONNECT_TIMEOUT_SECONDS,
        )
    )
    try:
        response = _post_overpass_with_retries(
            client=active_client,
            overpass_url=overpass_url,
            user_agent=user_agent,
            latitude=library.location.y,
            longitude=library.location.x,
            sleep=sleep,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenStreetMapDuplicateCheckError(
                code="malformed_response",
                message=gettext(
                    "The OpenStreetMap duplicate service returned invalid JSON."
                ),
            ) from exc
    finally:
        if owns_client:
            active_client.close()

    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise OpenStreetMapDuplicateCheckError(
            code="malformed_response",
            message=gettext(
                "The OpenStreetMap duplicate service returned an invalid result."
            ),
        )
    if payload.get("remark"):
        raise OpenStreetMapDuplicateCheckError(
            code="service_error",
            message=gettext(
                "The OpenStreetMap duplicate service could not complete the query."
            ),
            retryable=True,
        )
    if not all(isinstance(element, dict) for element in payload["elements"]):
        raise OpenStreetMapDuplicateCheckError(
            code="malformed_response",
            message=gettext(
                "The OpenStreetMap duplicate service returned invalid elements."
            ),
        )
    return payload


def _post_overpass_with_retries(
    *,
    client: httpx.Client,
    overpass_url: str,
    user_agent: str,
    latitude: float,
    longitude: float,
    sleep: Callable[[float], None],
) -> httpx.Response:
    """POST the Overpass query with bounded read-only retries.
    Converts transport and status failures into stable safe categories."""
    query = _build_overpass_query(
        radius_meters=_duplicate_radius_meters(),
        latitude=latitude,
        longitude=longitude,
    )
    last_error: OpenStreetMapDuplicateCheckError | None = None

    for attempt in range(OVERPASS_MAX_ATTEMPTS):
        response: httpx.Response | None = None
        try:
            response = client.post(
                overpass_url,
                data={"data": query},
                headers={"User-Agent": user_agent},
            )
        except httpx.TimeoutException:
            last_error = OpenStreetMapDuplicateCheckError(
                code="timeout",
                message=gettext("The OpenStreetMap duplicate check timed out."),
                retryable=True,
            )
        except httpx.RequestError:
            last_error = OpenStreetMapDuplicateCheckError(
                code="transport_error",
                message=gettext(
                    "The OpenStreetMap duplicate service could not be reached."
                ),
                retryable=True,
            )
        else:
            if response.status_code < 400:
                return response
            if response.status_code == 429:
                last_error = OpenStreetMapDuplicateCheckError(
                    code="rate_limited",
                    message=gettext(
                        "The OpenStreetMap duplicate service is rate limited."
                    ),
                    retryable=True,
                )
            elif response.status_code >= 500:
                last_error = OpenStreetMapDuplicateCheckError(
                    code="service_error",
                    message=gettext(
                        "The OpenStreetMap duplicate service is unavailable."
                    ),
                    retryable=True,
                )
            else:
                raise OpenStreetMapDuplicateCheckError(
                    code="request_rejected",
                    message=gettext(
                        "The OpenStreetMap duplicate service rejected the request."
                    ),
                )

        if attempt == OVERPASS_MAX_ATTEMPTS - 1:
            break
        if response is not None and response.status_code == 429:
            delay = _retry_after_delay(response=response, attempt=attempt)
        else:
            delay = min(2**attempt, OVERPASS_MAX_RETRY_DELAY_SECONDS)
        sleep(delay)

    if last_error is None:
        raise OpenStreetMapDuplicateCheckError(
            code="unknown_error",
            message=gettext("The OpenStreetMap duplicate check failed."),
        )
    raise last_error


def _retry_after_delay(*, response: httpx.Response, attempt: int) -> float:
    """Return a bounded delay derived from Retry-After when available.
    Falls back to exponential backoff for invalid or missing headers."""
    retry_after = response.headers.get("Retry-After", "").strip()
    fallback = min(2**attempt, OVERPASS_MAX_RETRY_DELAY_SECONDS)
    if not retry_after:
        return fallback
    try:
        delay = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError):
            return fallback
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        delay = (retry_at - datetime.now(tz=UTC)).total_seconds()
    return min(max(delay, 0.0), OVERPASS_MAX_RETRY_DELAY_SECONDS)


def _build_overpass_query(
    *,
    radius_meters: int,
    latitude: float,
    longitude: float,
) -> str:
    """Build the allowlisted nearby-feature Overpass query.
    Requests nodes, ways, and relations needed for duplicate warnings."""
    return (
        "[out:json][timeout:25];"
        "("
        f"nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})"
        '["amenity"="public_bookcase"];'
        f"nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})"
        '["amenity"~"^(library|give_box)$"];'
        f"nwr(around:{radius_meters},{latitude:.7f},{longitude:.7f})"
        '["shop"="books"];'
        ");"
        "out center tags;"
    )


def _build_duplicate_candidates(
    *,
    library: Library,
    elements: list[dict[str, Any]],
    radius_meters: int,
) -> list[dict[str, Any]]:
    """Convert Overpass elements into sanitized duplicate candidates.
    Applies proximity, name, address, brand, and linked-element signals."""
    parsed_candidates: list[dict[str, Any]] = []
    pairs: list[tuple[str, int]] = []
    for element in elements:
        candidate = _parse_candidate(
            library=library,
            element=element,
            radius_meters=radius_meters,
        )
        if candidate is None:
            continue
        parsed_candidates.append(candidate)
        pairs.append((candidate["element_type"], candidate["element_id"]))

    linked_pairs = set(
        OpenStreetMapContribution.objects.filter(
            osm_element_id__in=[element_id for _, element_id in pairs]
        ).values_list("osm_element_type", "osm_element_id")
    )
    for candidate in parsed_candidates:
        pair = (candidate["element_type"], candidate["element_id"])
        if pair in linked_pairs:
            candidate["signals"].append("already_linked")

    return sorted(
        parsed_candidates,
        key=lambda candidate: (
            candidate["distance_meters"],
            candidate["element_type"],
            candidate["element_id"],
        ),
    )


def _parse_candidate(
    *,
    library: Library,
    element: dict[str, Any],
    radius_meters: int,
) -> dict[str, Any] | None:
    """Parse one Overpass element into an allowlisted candidate.
    Rejects malformed data and ignores only out-of-radius elements."""
    element_type = element.get("type")
    element_id = element.get("id")
    if (
        element_type not in OSM_ELEMENT_TYPES
        or type(element_id) is not int
        or element_id <= 0
    ):
        raise _malformed_element_error()

    coordinates = _element_coordinates(element=element)
    if coordinates is None:
        raise _malformed_element_error()
    latitude, longitude = coordinates
    distance_meters = _distance_meters(
        latitude_a=library.location.y,
        longitude_a=library.location.x,
        latitude_b=latitude,
        longitude_b=longitude,
    )
    if distance_meters > radius_meters:
        return None

    raw_tags = element.get("tags")
    if not isinstance(raw_tags, dict):
        raise _malformed_element_error()
    tags = {
        key: str(raw_tags[key])[:255]
        for key in OSM_PUBLIC_TAGS
        if key in raw_tags
        and isinstance(raw_tags[key], (str, int, float, bool))
    }
    if (
        tags.get("amenity")
        not in {"public_bookcase", "library", "give_box"}
        and tags.get("shop") != "books"
    ):
        raise _malformed_element_error()
    signals = _match_signals(
        library=library,
        tags=tags,
        distance_meters=distance_meters,
    )
    return {
        "element_type": element_type,
        "element_id": element_id,
        "distance_meters": round(distance_meters, 1),
        "tags": tags,
        "signals": signals,
    }


def _element_coordinates(
    *,
    element: dict[str, Any],
) -> tuple[float, float] | None:
    """Return node or representative center coordinates.
    Rejects non-numeric and out-of-range geographic values."""
    coordinate_source = (
        element
        if element.get("type") == OpenStreetMapContribution.ElementType.NODE
        else element.get("center")
    )
    if not isinstance(coordinate_source, dict):
        return None
    try:
        latitude = float(coordinate_source["lat"])
        longitude = float(coordinate_source["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def _malformed_element_error() -> OpenStreetMapDuplicateCheckError:
    """Build the stable failure used for malformed Overpass elements.
    Avoids retaining raw element data in errors, audit records, or logs."""
    return OpenStreetMapDuplicateCheckError(
        code="malformed_response",
        message=gettext(
            "The OpenStreetMap duplicate service returned invalid elements."
        ),
    )


def _match_signals(
    *,
    library: Library,
    tags: dict[str, str],
    distance_meters: float,
) -> list[str]:
    """Return the blocking signals triggered by one nearby feature.
    Compares values only when both Book Corners and OSM data exist."""
    signals: list[str] = []
    amenity = tags.get("amenity", "")
    if amenity == "public_bookcase" and distance_meters <= 25:
        signals.append("nearby_public_bookcase")
    if (
        amenity == "public_bookcase"
        and _normalized_values_match(library.name, tags.get("name", ""))
    ):
        signals.append("same_name")

    library_street, library_house_number = _split_address(library.address)
    osm_street = _normalize(tags.get("addr:street", ""))
    osm_house_number = _normalize(tags.get("addr:housenumber", ""))
    if (
        amenity == "public_bookcase"
        and library_street
        and library_house_number
        and library_street == osm_street
        and library_house_number == osm_house_number
    ):
        signals.append("same_address")

    compatible_book_sharing_tag = (
        amenity in OSM_COMPATIBLE_BOOK_SHARING_AMENITIES
    )
    if compatible_book_sharing_tag and (
        _normalized_values_match(library.brand, tags.get("brand", ""))
        or _normalized_values_match(library.operator, tags.get("operator", ""))
    ):
        signals.append("same_brand_or_operator")
    return signals


def _split_address(address: str) -> tuple[str, str]:
    """Split a free-form address into comparable street and house values.
    Returns empty values when no house number can be identified."""
    house_match = re.search(r"\b\d+[a-z]?(?:[-/]\d+[a-z]?)?\b", address.casefold())
    if house_match is None:
        return _normalize(address), ""
    house_number = _normalize(house_match.group(0))
    street = _normalize(
        f"{address[:house_match.start()]} {address[house_match.end():]}"
    )
    return street, house_number


def _normalize(value: str) -> str:
    """Normalize text for cautious Unicode-insensitive comparison.
    Removes punctuation while preserving letters and numbers."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized)
        .split()
    )


def _normalized_values_match(left: str, right: str) -> bool:
    """Return whether two present values normalize identically.
    Prevents missing tags from being treated as matching evidence."""
    normalized_left = _normalize(left)
    normalized_right = _normalize(right)
    return bool(
        normalized_left
        and normalized_right
        and normalized_left == normalized_right
    )


def _validated_resolution_reason(*, reason: str) -> str:
    """Return a bounded operator reason after sensitive-data checks.
    Rejects private contacts and obvious credentials before audit persistence."""
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError(gettext("Explain the duplicate resolution."))
    if any(pattern.search(clean_reason) for pattern in SENSITIVE_REASON_PATTERNS):
        raise ValueError(
            gettext(
                "Remove private contact details or credentials from the resolution."
            )
        )
    return clean_reason[:500]


def _distance_meters(
    *,
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Calculate great-circle distance between two geographic points.
    Uses the haversine formula for deterministic local matching."""
    earth_radius_meters = 6_371_008.8
    latitude_a_radians = math.radians(latitude_a)
    latitude_b_radians = math.radians(latitude_b)
    latitude_delta = math.radians(latitude_b - latitude_a)
    longitude_delta = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_a_radians)
        * math.cos(latitude_b_radians)
        * math.sin(longitude_delta / 2) ** 2
    )
    return earth_radius_meters * 2 * math.atan2(
        math.sqrt(haversine),
        math.sqrt(1 - haversine),
    )


def _record_duplicate_check_success(
    *,
    library: Library,
    actor: Any,
    candidates: list[dict[str, Any]],
) -> OpenStreetMapContribution:
    """Persist a successful duplicate check and its sanitized candidates.
    Keeps unresolved prior warnings until staff records a resolution."""
    checked_at = timezone.now()
    with transaction.atomic():
        contribution, _ = (
            OpenStreetMapContribution.objects.select_for_update().get_or_create(
                library=library
            )
        )
        resolved_pairs = _different_feature_resolution_pairs(
            contribution=contribution
        )
        blocking_candidates = [
            candidate
            for candidate in candidates
            if candidate["signals"]
            and (candidate["element_type"], candidate["element_id"])
            not in resolved_pairs
        ]
        prior_unresolved_candidates = [
            candidate
            for candidate in contribution.duplicate_candidates
            if candidate.get("signals")
            and (candidate.get("element_type"), candidate.get("element_id"))
            not in resolved_pairs
        ]
        if prior_unresolved_candidates and not blocking_candidates:
            candidates = _merge_candidates(
                existing=contribution.duplicate_candidates,
                current=candidates,
            )
            status = OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
        else:
            status = (
                OpenStreetMapContribution.Status.POSSIBLE_DUPLICATE
                if blocking_candidates
                else OpenStreetMapContribution.Status.NO_MATCH
            )

        contribution.status = status
        contribution.checked_at = checked_at
        contribution.duplicate_candidates = candidates
        contribution.last_error_code = ""
        contribution.last_error_message = ""
        contribution.last_error_retryable = False
        contribution.save(
            update_fields=[
                "status",
                "checked_at",
                "duplicate_candidates",
                "last_error_code",
                "last_error_message",
                "last_error_retryable",
            ]
        )
        OpenStreetMapContributionEvent.objects.create(
            contribution=contribution,
            event_type=OpenStreetMapContributionEvent.EventType.DUPLICATE_CHECK,
            outcome=OpenStreetMapContributionEvent.Outcome.SUCCESS,
            actor=actor,
            details={
                "status": status,
                "checked_at": checked_at.isoformat(),
                "candidates": candidates,
            },
        )
        return contribution


def _record_duplicate_check_failure(
    *,
    library: Library,
    actor: Any,
    error: OpenStreetMapDuplicateCheckError,
) -> OpenStreetMapContribution:
    """Persist a failed check without discarding earlier candidate evidence.
    Records a stable error category and sanitized operator-facing message."""
    checked_at = timezone.now()
    with transaction.atomic():
        contribution, _ = (
            OpenStreetMapContribution.objects.select_for_update().get_or_create(
                library=library
            )
        )
        contribution.status = OpenStreetMapContribution.Status.FAILED
        contribution.checked_at = checked_at
        contribution.last_error_code = error.code
        contribution.last_error_message = error.message
        contribution.last_error_retryable = error.retryable
        contribution.save(
            update_fields=[
                "status",
                "checked_at",
                "last_error_code",
                "last_error_message",
                "last_error_retryable",
            ]
        )
        OpenStreetMapContributionEvent.objects.create(
            contribution=contribution,
            event_type=OpenStreetMapContributionEvent.EventType.DUPLICATE_CHECK,
            outcome=OpenStreetMapContributionEvent.Outcome.FAILED,
            actor=actor,
            details={
                "error_code": error.code,
                "error_message": error.message,
                "retryable": error.retryable,
            },
        )
        return contribution


def _different_feature_resolution_pairs(
    *,
    contribution: OpenStreetMapContribution,
) -> set[tuple[str, int]]:
    """Return candidates explicitly resolved as different features.
    Reads only sanitized append-only resolution events."""
    pairs: set[tuple[str, int]] = set()
    events = contribution.events.filter(
        event_type=OpenStreetMapContributionEvent.EventType.DUPLICATE_RESOLUTION,
        outcome=OpenStreetMapContributionEvent.Outcome.SUCCESS,
    )
    for event in events:
        if event.details.get("resolution") != "different_feature":
            continue
        element_type = event.details.get("element_type")
        element_id = event.details.get("element_id")
        if element_type in OSM_ELEMENT_TYPES and isinstance(element_id, int):
            pairs.add((element_type, element_id))
    return pairs


def _candidate_exists(
    *,
    candidates: list[dict[str, Any]],
    element_type: str,
    element_id: int,
) -> bool:
    """Return whether the selected candidate is in the current snapshot.
    Rejects forged or stale resolution targets."""
    return any(
        candidate.get("element_type") == element_type
        and candidate.get("element_id") == element_id
        and bool(candidate.get("signals"))
        for candidate in candidates
    )


def _merge_candidates(
    *,
    existing: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge candidate snapshots without losing unresolved warning evidence.
    Prefers current sanitized values for elements still returned."""
    merged = {
        (candidate.get("element_type"), candidate.get("element_id")): candidate
        for candidate in existing
        if isinstance(candidate, dict)
    }
    merged.update(
        {
            (candidate["element_type"], candidate["element_id"]): candidate
            for candidate in current
        }
    )
    return sorted(
        merged.values(),
        key=lambda candidate: (
            candidate.get("distance_meters", math.inf),
            candidate.get("element_type", ""),
            candidate.get("element_id", 0),
        ),
    )


def _duplicate_radius_meters() -> int:
    """Return a safe positive duplicate-check radius from settings.
    Caps accidental configuration drift at the documented upper bound."""
    return min(max(settings.OSM_DUPLICATE_RADIUS_METERS, 1), 10_000)
