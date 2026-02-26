from ninja import Schema


class ErrorOut(Schema):
    """Shared error response schema for all API endpoints.
    Provides a consistent error shape with optional structured details."""

    message: str
    details: dict[str, object] | None = None
