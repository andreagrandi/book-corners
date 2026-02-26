from ninja import Schema
from pydantic import Field


class ErrorOut(Schema):
    """Shared error response schema for all API endpoints.
    Provides a consistent error shape with optional structured details."""

    message: str = Field(
        description="Human-readable error message.",
        examples=["Not found."],
    )
    details: dict[str, object] | None = Field(
        default=None,
        description="Optional structured details about the error, such as field-level validation failures.",
        examples=[{"errors": [{"loc": ["body", "name"], "msg": "field required", "type": "missing"}]}],
    )
