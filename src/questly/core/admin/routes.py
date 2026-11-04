"""Admin API routes (ADR-0007 §8)."""

from fastapi import APIRouter, Query, Request, status

from questly.core.admin.models import (
    AISettingsChange,
    AISettingsWithUsage,
    ModelNotAllowedError,
    SelfModificationError,
    SystemStatus,
    UserNotFoundError,
)
from questly.core.admin.schemas import (
    AISettingsResponse,
    AISettingsUpdate,
    InstructorRequestResponse,
    ServiceStatusResponse,
    SystemStatusResponse,
    UserAdminResponse,
    UserPaginatedResponse,
    UserRoleUpdate,
)
from questly.core.admin.service import AdminService
from questly.core.auth.current import current_actor
from questly.core.auth.models import (
    InstructorRequest,
    InstructorRequestAlreadyReviewedError,
    InstructorRequestNotFoundError,
    InstructorRequestStatus,
    Role,
    User,
)
from questly.core.http_errors import ErrorRule, ErrorTable

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

REQUEST_ERRORS = ErrorTable(
    {
        InstructorRequestNotFoundError: ErrorRule(
            status.HTTP_404_NOT_FOUND,
            "instructor_request_not_found",
            "Instructor request not found",
        ),
        InstructorRequestAlreadyReviewedError: ErrorRule(
            status.HTTP_409_CONFLICT,
            "instructor_request_already_reviewed",
            "Instructor request has already been reviewed",
        ),
    }
)
USER_ERRORS = ErrorTable(
    {
        UserNotFoundError: ErrorRule(
            status.HTTP_404_NOT_FOUND, "user_not_found", "User not found"
        ),
        SelfModificationError: ErrorRule(
            status.HTTP_400_BAD_REQUEST, "self_modification", str
        ),
    }
)
AI_SETTINGS_ERRORS = ErrorTable(
    {
        ModelNotAllowedError: ErrorRule(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "model_not_allowed",
            "Model is not in the configured allowlist",
        )
    }
)


def _service(request: Request) -> AdminService:
    return request.app.state.admin_service


def _request_response(
    instructor_request: InstructorRequest, user: User
) -> InstructorRequestResponse:
    return InstructorRequestResponse(
        id=instructor_request.id,
        user_id=instructor_request.user_id,
        full_name=user.full_name,
        email=user.email,
        faculty=instructor_request.faculty,
        status=instructor_request.status,
        reviewed_by=instructor_request.reviewed_by,
        reviewed_at=instructor_request.reviewed_at,
        requested_at=instructor_request.requested_at,
    )


def _user_response(user: User) -> UserAdminResponse:
    return UserAdminResponse(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=user.role.value,
        is_active=user.is_active,
    )


def _ai_settings_response(current: AISettingsWithUsage) -> AISettingsResponse:
    return AISettingsResponse(
        model=current.settings.model,
        daily_quota=current.settings.daily_quota,
        max_pages=current.settings.max_pages,
        require_citations=current.settings.require_citations,
        usage_this_month=current.usage_this_month,
    )


def _status_response(system_status: SystemStatus) -> SystemStatusResponse:
    return SystemStatusResponse(
        services=[
            ServiceStatusResponse(
                name=service.name, status=service.status, details=service.details
            )
            for service in system_status.services
        ]
    )


@router.get("/instructor-requests", response_model=list[InstructorRequestResponse])
def list_instructor_requests(
    request: Request,
    status_filter: InstructorRequestStatus | None = Query(  # noqa: B008 — FastAPI
        InstructorRequestStatus.PENDING, alias="status"
    ),
) -> list[InstructorRequestResponse]:
    actor = current_actor(request)
    return [
        _request_response(instructor_request, user)
        for instructor_request, user in _service(request).list_instructor_requests(
            actor, status_filter=status_filter
        )
    ]


@router.post(
    "/instructor-requests/{request_id}/approve",
    response_model=InstructorRequestResponse,
)
def approve_instructor_request(
    request: Request, request_id: int
) -> InstructorRequestResponse:
    actor = current_actor(request)
    with REQUEST_ERRORS.translate():
        instructor_request, user = _service(request).approve_instructor_request(
            actor, request_id=request_id
        )
    return _request_response(instructor_request, user)


@router.post(
    "/instructor-requests/{request_id}/reject",
    response_model=InstructorRequestResponse,
)
def reject_instructor_request(
    request: Request, request_id: int
) -> InstructorRequestResponse:
    actor = current_actor(request)
    with REQUEST_ERRORS.translate():
        instructor_request, user = _service(request).reject_instructor_request(
            actor, request_id=request_id
        )
    return _request_response(instructor_request, user)


@router.get("/users", response_model=UserPaginatedResponse)
def list_users(
    request: Request,
    q: str | None = Query(None, max_length=100),
    role: Role | None = None,
    page: int = Query(1, ge=1),
) -> UserPaginatedResponse:
    actor = current_actor(request)
    items, total = _service(request).list_users(
        actor, q=q, role=role, page=page, page_size=25
    )
    return UserPaginatedResponse(
        items=[_user_response(user) for user in items],
        total=total,
        page=page,
        page_size=25,
    )


@router.patch("/users/{user_id}", response_model=UserAdminResponse)
def update_user_role(
    request: Request, user_id: int, body: UserRoleUpdate
) -> UserAdminResponse:
    actor = current_actor(request)
    with USER_ERRORS.translate():
        user = _service(request).update_user_role(
            actor, user_id=user_id, new_role=body.role
        )
    return _user_response(user)


@router.post("/users/{user_id}/deactivate", response_model=UserAdminResponse)
def deactivate_user(request: Request, user_id: int) -> UserAdminResponse:
    actor = current_actor(request)
    with USER_ERRORS.translate():
        user = _service(request).deactivate_user(actor, user_id=user_id)
    return _user_response(user)


@router.get("/ai-settings", response_model=AISettingsResponse)
def get_ai_settings(request: Request) -> AISettingsResponse:
    actor = current_actor(request)
    return _ai_settings_response(_service(request).get_ai_settings(actor))


@router.put("/ai-settings", response_model=AISettingsResponse)
def update_ai_settings(request: Request, body: AISettingsUpdate) -> AISettingsResponse:
    actor = current_actor(request)
    change = AISettingsChange(
        model=body.model,
        daily_quota=body.daily_quota,
        max_pages=body.max_pages,
        require_citations=body.require_citations,
    )
    with AI_SETTINGS_ERRORS.translate():
        updated = _service(request).update_ai_settings(actor, change)
    return _ai_settings_response(updated)


@router.get("/status", response_model=SystemStatusResponse)
def get_system_status(request: Request) -> SystemStatusResponse:
    actor = current_actor(request)
    return _status_response(_service(request).get_system_status(actor))
