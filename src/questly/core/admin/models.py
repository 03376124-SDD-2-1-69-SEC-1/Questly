"""Admin domain model (ADR-0007 §8).

No FastAPI, Pydantic or database imports. Roles and Instructor request
statuses come from `core/auth/models.py`; this slice only reads and changes
them through `AuthRepository`.
"""

from dataclasses import dataclass
from enum import StrEnum


class UserNotFoundError(Exception):
    """The account an Admin is acting on does not exist: 404."""


class SelfModificationError(Exception):
    """An Admin tried to demote or deactivate their own account: 400."""


class ModelNotAllowedError(Exception):
    """The chosen generation model is not in the current model list: 422."""


class InvalidAISettingsError(Exception):
    """A daily quota or page limit below one: 422. The message names the field."""


@dataclass(frozen=True, slots=True)
class AISettings:
    """The single row of `core.ai_settings`."""

    model: str
    daily_quota: int
    max_pages: int
    require_citations: bool


@dataclass(frozen=True, slots=True)
class AvailableModel:
    """A generation model the provider offers right now (OPS-55)."""

    id: str
    supports_response_format: bool


@dataclass(frozen=True, slots=True)
class AISettingsChange:
    """What an Admin asked to change. A field left as None keeps its value."""

    model: str | None = None
    daily_quota: int | None = None
    max_pages: int | None = None
    require_citations: bool | None = None


@dataclass(frozen=True, slots=True)
class AISettingsWithUsage:
    """A-01 AI tab: the stored settings and this month's generations."""

    settings: AISettings
    usage_this_month: int


class ServiceHealthStatus(StrEnum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    """One row of A-01 System status. `details` is None when nothing to add."""

    name: str
    status: ServiceHealthStatus
    details: str | None = None


@dataclass(frozen=True, slots=True)
class SystemStatus:
    services: list[ServiceStatus]
