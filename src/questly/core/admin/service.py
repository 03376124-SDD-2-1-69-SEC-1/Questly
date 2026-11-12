"""Admin use cases (ADR-0007 §8). Every public method takes the Actor first."""

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from questly.core.admin.models import (
    AISettings,
    AISettingsChange,
    AISettingsWithUsage,
    InvalidAISettingsError,
    ModelNotAllowedError,
    SelfModificationError,
    SystemStatus,
    UserNotFoundError,
)
from questly.core.admin.ports import (
    AdminRepository,
    GenerationUsage,
    ModelCatalog,
    ProviderQuotaReader,
    SystemHealthChecker,
)
from questly.core.auth.models import (
    Actor,
    InstructorRequest,
    InstructorRequestStatus,
    PermissionDeniedError,
    Role,
    User,
)
from questly.core.auth.ports import AuthRepository, Clock
from questly.core.generation.models import ProviderQuotaSnapshot

USAGE_ZONE = ZoneInfo("Asia/Bangkok")


class AdminService:
    def __init__(
        self,
        admin_repo: AdminRepository,
        auth_repo: AuthRepository,
        health_checker: SystemHealthChecker,
        generation_usage: GenerationUsage,
        provider_quota: ProviderQuotaReader,
        clock: Clock,
        model_catalog: ModelCatalog,
    ) -> None:
        self.admin_repo = admin_repo
        self.auth_repo = auth_repo
        self.health_checker = health_checker
        self.generation_usage = generation_usage
        self.provider_quota_reader = provider_quota
        self.clock = clock
        self.model_catalog = model_catalog

    # --- Instructor Requests ---
    def list_instructor_requests(
        self,
        actor: Actor,
        status_filter: InstructorRequestStatus | None = InstructorRequestStatus.PENDING,
    ) -> list[tuple[InstructorRequest, User]]:
        self._require_admin(actor)
        requests = self.auth_repo.list_instructor_requests(status=status_filter)
        return [
            self._request_and_user(instructor_request)
            for instructor_request in requests
        ]

    def approve_instructor_request(
        self, actor: Actor, request_id: int
    ) -> tuple[InstructorRequest, User]:
        """Raises InstructorRequestNotFoundError or ...AlreadyReviewedError."""
        self._require_admin(actor)
        request = self.auth_repo.review_instructor_request(
            request_id=request_id, reviewer_id=actor.user_id, approve=True
        )
        return self._request_and_user(request)

    def reject_instructor_request(
        self, actor: Actor, request_id: int
    ) -> tuple[InstructorRequest, User]:
        """Raises InstructorRequestNotFoundError or ...AlreadyReviewedError."""
        self._require_admin(actor)
        request = self.auth_repo.review_instructor_request(
            request_id=request_id, reviewer_id=actor.user_id, approve=False
        )
        return self._request_and_user(request)

    # --- User Management ---
    def list_users(
        self,
        actor: Actor,
        q: str | None = None,
        role: Role | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[User], int]:
        self._require_admin(actor)
        role_value = None
        if role is not None:
            role_value = role.value
        return self.auth_repo.search_users(
            q=q, role=role_value, page=page, page_size=page_size
        )

    def update_user_role(self, actor: Actor, user_id: int, new_role: Role) -> User:
        self._require_admin(actor)
        if user_id == actor.user_id and new_role is not Role.ADMIN:
            raise SelfModificationError("Cannot demote your own admin role")
        user = self._get_user(user_id)
        return self.auth_repo.update_user(replace(user, role=new_role))

    def deactivate_user(self, actor: Actor, user_id: int) -> User:
        self._require_admin(actor)
        if user_id == actor.user_id:
            raise SelfModificationError("Cannot deactivate your own admin account")
        user = self._get_user(user_id)
        deactivated = self.auth_repo.update_user(replace(user, is_active=False))
        self.auth_repo.revoke_sessions_of(user_id)
        return deactivated

    # --- AI Settings ---
    def get_ai_settings(self, actor: Actor) -> AISettingsWithUsage:
        self._require_admin(actor)
        return self._with_usage(self.admin_repo.get_ai_settings())

    def allowed_models(self, actor: Actor) -> tuple[str, ...]:
        """The models A-01 offers, read from the catalog on every call."""
        self._require_admin(actor)
        return self._allowed_models()

    def update_ai_settings(
        self, actor: Actor, change: AISettingsChange
    ) -> AISettingsWithUsage:
        self._require_admin(actor)
        if change.model is not None and change.model not in self._allowed_models():
            raise ModelNotAllowedError(change.model)
        if change.daily_quota is not None and change.daily_quota < 1:
            raise InvalidAISettingsError("Daily quota must be at least 1")
        if change.max_pages is not None and change.max_pages < 1:
            raise InvalidAISettingsError("Max pages must be at least 1")
        current = self.admin_repo.get_ai_settings()
        saved = self.admin_repo.update_ai_settings(_merge(current, change))
        return self._with_usage(saved)

    def provider_quota(self, actor: Actor) -> ProviderQuotaSnapshot:
        """The A-01 AI tab's real OpenRouter free quota, as T-03 prints it."""
        self._require_admin(actor)
        return self.provider_quota_reader.snapshot()

    # --- System Status ---
    def get_system_status(self, actor: Actor) -> SystemStatus:
        self._require_admin(actor)
        return self.health_checker.check_services_health()

    def _allowed_models(self) -> tuple[str, ...]:
        return tuple(model.id for model in self.model_catalog.available_models())

    def _with_usage(self, settings: AISettings) -> AISettingsWithUsage:
        return AISettingsWithUsage(
            settings=settings,
            usage_this_month=self.generation_usage.count_all_generations(
                self._month_start()
            ),
        )

    def _month_start(self) -> datetime:
        """The first instant of the current month in Bangkok, as UTC."""
        local = self.clock.now().astimezone(USAGE_ZONE)
        return local.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).astimezone(UTC)

    def _require_admin(self, actor: Actor) -> None:
        if actor.role is not Role.ADMIN:
            raise PermissionDeniedError

    def _get_user(self, user_id: int) -> User:
        user = self.auth_repo.get_user(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        return user

    def _request_and_user(
        self, instructor_request: InstructorRequest
    ) -> tuple[InstructorRequest, User]:
        user = self.auth_repo.get_user(instructor_request.user_id)
        if user is None:
            raise RuntimeError(
                f"Instructor request {instructor_request.id} has no user"
            )
        return instructor_request, user


def _merge(current: AISettings, change: AISettingsChange) -> AISettings:
    """Apply the fields an Admin changed on top of the stored settings."""
    merged = current
    if change.model is not None:
        merged = replace(merged, model=change.model)
    if change.daily_quota is not None:
        merged = replace(merged, daily_quota=change.daily_quota)
    if change.max_pages is not None:
        merged = replace(merged, max_pages=change.max_pages)
    if change.require_citations is not None:
        merged = replace(merged, require_citations=change.require_citations)
    return merged
