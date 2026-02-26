import logging

from django.conf import settings
from django.http import Http404, HttpRequest, JsonResponse
from ninja import NinjaAPI
from ninja.errors import HttpError, ValidationError as NinjaValidationError

from config.api_schemas import ErrorOut
from users.api import auth_router

logger = logging.getLogger(__name__)

api = NinjaAPI(title="Book Corners API")
api.add_router("/auth/", auth_router)


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
    Logs the real error but hides internals from clients in production."""
    logger.exception("Unhandled API exception: %s", exc)
    message = str(exc) if settings.DEBUG else "Internal server error."
    return JsonResponse(ErrorOut(message=message).dict(), status=500)
