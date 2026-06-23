from __future__ import annotations

from enum import Enum

from ninja import Schema
from pydantic import Field


class DeviceTokenEnvironmentEnum(str, Enum):
    """APNs device token environment values.
    Keeps API payloads aligned with APNs sandbox and production routing."""

    SANDBOX = "sandbox"
    PRODUCTION = "production"


class DeviceTokenIn(Schema):
    """Payload for registering an APNs device token.
    The environment must match the client build that produced the token."""

    token: str = Field(
        min_length=1,
        max_length=255,
        description="APNs device token as a hexadecimal string.",
        examples=["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"],
    )
    environment: DeviceTokenEnvironmentEnum = Field(
        description="APNs environment for this token: sandbox or production.",
        examples=["sandbox"],
    )


class DeviceTokenOut(Schema):
    """Registered APNs device token response.
    Confirms the normalized token and environment stored for the user."""

    token: str = Field(description="Registered APNs device token.")
    environment: str = Field(description="APNs environment for this token.")
    is_active: bool = Field(description="Whether the token is active for delivery.")

