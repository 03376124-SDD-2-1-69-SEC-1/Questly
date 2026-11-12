"""AdminService rules, on the in-memory adapters."""

from datetime import UTC, datetime, timedelta

import pytest

from questly.core.admin.models import (
    AISettingsChange,
    AvailableModel,
    InvalidAISettingsError,
    ModelNotAllowedError,
    SelfModificationError,
    UserNotFoundError,
)
from questly.core.admin.service import AdminService
from questly.core.auth.models import (
    Actor,
    InstructorRequest,
    InstructorRequestAlreadyReviewedError,
    InstructorRequestNotFoundError,
    PermissionDeniedError,
    Role,
    User,
)
from tests.fakes.admin import (
    DEFAULT_AI_SETTINGS,
    FakeAdminRepository,
    FakeSystemHealthChecker,
)
from tests.fakes.auth import DEFAULT_NOW, FakeAuthRepository, FakeClock, seed_user
from tests.fakes.generation import FakeDraftRepository, FakeProviderQuota
from tests.fakes.model_catalog import FakeModelCatalog


def _actor(user: User) -> Actor:
    return Actor(
        user_id=user.id, role=user.role, full_name=user.full_name, email=user.email
    )


class Admin:
    def __init__(self, now: datetime = DEFAULT_NOW) -> None:
        self.auth = FakeAuthRepository()
        self.settings = FakeAdminRepository()
        self.drafts = FakeDraftRepository()
        self.provider = FakeProviderQuota()
        self.catalog = FakeModelCatalog(
            (
                AvailableModel(DEFAULT_AI_SETTINGS.model, False),
                AvailableModel("other/model", True),
            )
        )
        self.service = AdminService(
            self.settings,
            self.auth,
            FakeSystemHealthChecker(),
            generation_usage=self.drafts,
            provider_quota=self.provider,
            clock=FakeClock(now),
            model_catalog=self.catalog,
        )
        self.admin = _actor(
            seed_user(
                self.auth, email="admin@kmitl.ac.th", full_name="Admin", role=Role.ADMIN
            )
        )
        self.student = seed_user(
            self.auth, email="66010001@kmitl.ac.th", full_name="Nattapong Suwan"
        )


def test_partial_ai_settings_change_keeps_the_other_fields() -> None:
    admin = Admin()

    updated = admin.service.update_ai_settings(
        admin.admin, AISettingsChange(daily_quota=3)
    )

    assert updated.settings.daily_quota == 3
    assert updated.settings.model == DEFAULT_AI_SETTINGS.model
    assert updated.settings.max_pages == DEFAULT_AI_SETTINGS.max_pages
    assert updated.settings.require_citations is DEFAULT_AI_SETTINGS.require_citations
    assert admin.settings.settings == updated.settings


def test_model_outside_the_allowlist_is_refused_and_nothing_is_saved() -> None:
    admin = Admin()

    with pytest.raises(ModelNotAllowedError):
        admin.service.update_ai_settings(
            admin.admin, AISettingsChange(model="unlisted/model", daily_quota=3)
        )

    assert admin.settings.settings == DEFAULT_AI_SETTINGS


def test_allowed_models_are_read_from_the_catalog_on_every_call() -> None:
    admin = Admin()
    assert admin.service.allowed_models(admin.admin) == (
        DEFAULT_AI_SETTINGS.model,
        "other/model",
    )

    admin.catalog.models = (AvailableModel("new/model:free", False),)

    assert admin.service.allowed_models(admin.admin) == ("new/model:free",)
    saved = admin.service.update_ai_settings(
        admin.admin, AISettingsChange(model="new/model:free")
    )
    assert saved.settings.model == "new/model:free"
    with pytest.raises(ModelNotAllowedError):
        admin.service.update_ai_settings(
            admin.admin, AISettingsChange(model="other/model")
        )


def test_only_an_admin_may_list_the_allowed_models() -> None:
    admin = Admin()
    student = _actor(admin.student)

    with pytest.raises(PermissionDeniedError):
        admin.service.allowed_models(student)


def test_admin_cannot_demote_or_deactivate_themselves() -> None:
    admin = Admin()

    with pytest.raises(SelfModificationError):
        admin.service.update_user_role(admin.admin, admin.admin.user_id, Role.STUDENT)
    with pytest.raises(SelfModificationError):
        admin.service.deactivate_user(admin.admin, admin.admin.user_id)

    stored = admin.auth.get_user(admin.admin.user_id)
    assert stored.role is Role.ADMIN
    assert stored.is_active is True


def test_unknown_user_raises_user_not_found() -> None:
    admin = Admin()

    with pytest.raises(UserNotFoundError):
        admin.service.update_user_role(admin.admin, 999_999, Role.INSTRUCTOR)
    with pytest.raises(UserNotFoundError):
        admin.service.deactivate_user(admin.admin, 999_999)


def test_review_errors_come_from_the_auth_repository() -> None:
    admin = Admin()
    request = admin.auth.create_instructor_request(
        InstructorRequest(admin.student.id, "Engineering", DEFAULT_NOW)
    )
    admin.service.reject_instructor_request(admin.admin, request.id)

    with pytest.raises(InstructorRequestAlreadyReviewedError):
        admin.service.approve_instructor_request(admin.admin, request.id)
    with pytest.raises(InstructorRequestNotFoundError):
        admin.service.approve_instructor_request(admin.admin, 999_999)
    assert admin.auth.get_user(admin.student.id).role is Role.STUDENT


def test_only_an_admin_may_change_ai_settings() -> None:
    admin = Admin()

    with pytest.raises(PermissionDeniedError):
        admin.service.update_ai_settings(
            _actor(admin.student), AISettingsChange(daily_quota=1)
        )

    assert admin.settings.settings == DEFAULT_AI_SETTINGS


def test_usage_this_month_starts_at_midnight_on_the_first_in_bangkok() -> None:
    bangkok_month_start = datetime(2026, 9, 30, 17, 0, tzinfo=UTC)  # 1 Oct 00:00
    admin = Admin(now=bangkok_month_start + timedelta(hours=1))
    admin.drafts.record_generation(
        admin.student.id, bangkok_month_start - timedelta(seconds=1)
    )
    admin.drafts.record_generation(admin.student.id, bangkok_month_start)
    admin.drafts.record_generation(admin.admin.user_id, bangkok_month_start)

    current = admin.service.get_ai_settings(admin.admin)

    assert current.usage_this_month == 2


def test_provider_quota_is_the_snapshot_the_provider_reports() -> None:
    admin = Admin()

    assert admin.service.provider_quota(admin.admin) == admin.provider.current


def test_only_an_admin_may_read_the_provider_quota() -> None:
    admin = Admin()

    with pytest.raises(PermissionDeniedError):
        admin.service.provider_quota(_actor(admin.student))


@pytest.mark.parametrize(
    "change", [AISettingsChange(daily_quota=0), AISettingsChange(max_pages=-1)]
)
def test_a_quota_or_page_limit_below_one_is_refused_and_nothing_is_saved(
    change: AISettingsChange,
) -> None:
    admin = Admin()

    with pytest.raises(InvalidAISettingsError):
        admin.service.update_ai_settings(admin.admin, change)

    assert admin.settings.settings == DEFAULT_AI_SETTINGS
