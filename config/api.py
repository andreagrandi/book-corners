import structlog
from django.conf import settings
from django.http import Http404, HttpRequest, JsonResponse
from ninja import NinjaAPI
from ninja.errors import HttpError, ValidationError as NinjaValidationError

from config.api_schemas import ErrorOut
from libraries.api import library_router, statistics_router
from users.api import auth_router

logger = structlog.get_logger(__name__)

api = NinjaAPI(
    title="Book Corners API",
    version="1.0.0",
    description=(
        "REST API for discovering, submitting, and reporting little free libraries. "
        "Provides geospatial search, user authentication, and community moderation workflows."
    ),
    servers=[
        {"url": "https://bookcorners.org/api/v1/", "description": "Production"},
        {"url": "http://localhost:8000/api/v1/", "description": "Local development"},
    ],
)
api.add_router("/auth/", auth_router)
api.add_router("/libraries/", library_router)
api.add_router("/statistics/", statistics_router)


@api.exception_handler(Http404)
def handle_not_found(request: HttpRequest, exc: Http404) -> JsonResponse:
    """Return a structured 404 response for missing resources.
    Keeps error format consistent across all API endpoints."""
    return JsonResponse(ErrorOut(message="Not found.").dict(), status=404)


@api.exception_handler(NinjaValidationError)
def handle_validation_error(request: HttpRequest, exc: NinjaValidationError) -> JsonResponse:
    """Return a structured 422 response for request validation failures.
    Includes field-level error details for client-side handling."""
    return JsonResponse(
        ErrorOut(
            message="Validation error.",
            details={"errors": exc.errors},
        ).dict(),
        status=422,
    )


@api.exception_handler(HttpError)
def handle_http_error(request: HttpRequest, exc: HttpError) -> JsonResponse:
    """Return a structured response for explicit HTTP error raises.
    Preserves the intended status code from the raising code."""
    return JsonResponse(
        ErrorOut(message=str(exc)).dict(),
        status=exc.status_code,
    )


@api.exception_handler(Exception)
def handle_internal_error(request: HttpRequest, exc: Exception) -> JsonResponse:
    """Return a structured 500 response for unhandled exceptions.
    Logs the real error and reports to Sentry when configured."""
    logger.exception("Unhandled API exception: %s", exc)

    if settings.SENTRY_DSN:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)

    message = str(exc) if settings.DEBUG else "Internal server error."
    return JsonResponse(ErrorOut(message=message).dict(), status=500)
