"""HTML pages A-01 Users, AI and System status (ADR-0007 §8).

Tabs are separate URLs: `/admin/settings` (Users), `/admin/settings/ai` and
`/admin/settings/status`. Every action is a form post followed by a redirect;
a refusal the Admin can fix is drawn as an alert on the same page.
"""

from math import ceil
from typing import Annotated
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from questly.core.admin.models import (
    AISettingsChange,
    InvalidAISettingsError,
    ModelNotAllowedError,
    SelfModificationError,
    ServiceHealthStatus,
    UserNotFoundError,
)
from questly.core.admin.service import AdminService
from questly.core.auth.csrf import require_csrf
from questly.core.auth.current import current_actor
from questly.core.auth.models import (
    Actor,
    InstructorRequestAlreadyReviewedError,
    InstructorRequestNotFoundError,
    Role,
)

router = APIRouter(tags=["pages"], include_in_schema=False)

USERS_URL = "/admin/settings"
AI_URL = "/admin/settings/ai"
STATUS_URL = "/admin/settings/status"
PAGE_SIZE = 25
UNPROCESSABLE = 422
LOCAL_ZONE = ZoneInfo("Asia/Bangkok")


def _service(request: Request) -> AdminService:
    return request.app.state.admin_service


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _users_url(q: str, role: str, page: int) -> str:
    params: dict[str, str] = {}
    if q:
        params["q"] = q
    if role:
        params["role"] = role
    if page > 1:
        params["page"] = str(page)
    if not params:
        return USERS_URL
    return f"{USERS_URL}?{urlencode(params)}"


# ---- A-01 Users, A-01b --------------------------------------------------------


def _users_page(
    request: Request,
    actor: Actor,
    *,
    q: str = "",
    role: str = "",
    page: int = 1,
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    service = _service(request)
    pending = service.list_instructor_requests(actor)
    role_filter = None  # an unknown `role` shows every role
    if role in {r.value for r in Role}:
        role_filter = Role(role)
    users, total = service.list_users(
        actor, q=q or None, role=role_filter, page=page, page_size=PAGE_SIZE
    )
    role_value = role_filter.value if role_filter is not None else ""
    last_page = max(ceil(total / PAGE_SIZE), 1)
    return _templates(request).TemplateResponse(
        request,
        "admin/a01_users.html",
        {
            "actor": actor,
            "breadcrumb": "Admin settings",
            "tab": "users",
            "pending": pending,
            "users": users,
            "total": total,
            "q": q,
            "role": role_value,
            "role_options": [(r.value, r.value.capitalize()) for r in Role],
            "page": page,
            "last_page": last_page,
            "previous_url": _users_url(q, role_value, page - 1) if page > 1 else "",
            "next_url": (
                _users_url(q, role_value, page + 1) if page < last_page else ""
            ),
            "back_q": q,
            "back_role": role_value,
            "page_size": PAGE_SIZE,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/admin/settings", response_class=HTMLResponse)
def users_tab(
    request: Request, q: str = "", role: str = "", page: int = 1
) -> HTMLResponse:
    """A-01 Users; A-01b when no Instructor request is pending."""
    actor = current_actor(request)
    return _users_page(request, actor, q=q.strip(), role=role, page=max(page, 1))


@router.post("/admin/settings/requests/{request_id}/approve", response_model=None)
def request_approve(
    request: Request, request_id: int, csrf_token: Annotated[str, Form()] = ""
) -> RedirectResponse | HTMLResponse:
    require_csrf(request, csrf_token)
    return _review(request, request_id, approve=True)


@router.post("/admin/settings/requests/{request_id}/reject", response_model=None)
def request_reject(
    request: Request, request_id: int, csrf_token: Annotated[str, Form()] = ""
) -> RedirectResponse | HTMLResponse:
    require_csrf(request, csrf_token)
    return _review(request, request_id, approve=False)


def _review(
    request: Request, request_id: int, *, approve: bool
) -> RedirectResponse | HTMLResponse:
    actor = current_actor(request)
    service = _service(request)
    try:
        if approve:
            service.approve_instructor_request(actor, request_id)
        else:
            service.reject_instructor_request(actor, request_id)
    except InstructorRequestNotFoundError:
        raise HTTPException(status_code=404) from None
    except InstructorRequestAlreadyReviewedError:
        return _users_page(
            request,
            actor,
            error="That request was already approved or rejected.",
            status_code=status.HTTP_409_CONFLICT,
        )
    return _see_other(USERS_URL)


@router.post("/admin/settings/users/{user_id}/deactivate", response_model=None)
def user_deactivate(
    request: Request,
    user_id: int,
    q: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "",
    page: Annotated[int, Form()] = 1,
    csrf_token: Annotated[str, Form()] = "",
) -> RedirectResponse | HTMLResponse:
    require_csrf(request, csrf_token)
    actor = current_actor(request)
    page = max(page, 1)
    try:
        _service(request).deactivate_user(actor, user_id)
    except UserNotFoundError:
        raise HTTPException(status_code=404) from None
    except SelfModificationError:
        return _users_page(
            request,
            actor,
            q=q,
            role=role,
            page=page,
            error="You cannot deactivate your own account.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _see_other(_users_url(q, role, page))


# ---- A-01 AI ----------------------------------------------------------------


def _ai_page(
    request: Request,
    actor: Actor,
    *,
    form: dict[str, str] | None = None,
    error: str = "",
    saved: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    service = _service(request)
    current = service.get_ai_settings(actor)
    if form is None:
        form = {
            "model": current.settings.model,
            "daily_quota": str(current.settings.daily_quota),
            "max_pages": str(current.settings.max_pages),
            "require_citations": "on" if current.settings.require_citations else "",
        }
    models = service.allowed_models(actor)
    provider = service.provider_quota(actor)
    return _templates(request).TemplateResponse(
        request,
        "admin/a01_ai.html",
        {
            "actor": actor,
            "breadcrumb": "Admin settings",
            "tab": "ai",
            "form": form,
            "models": models,
            "stored_model": current.settings.model,
            # Empty list: the provider list could not be read; show only the
            # stored model. Otherwise a stored model missing from it is flagged.
            "catalog_empty": not models,
            "stored_model_missing": bool(models)
            and current.settings.model not in models,
            "usage_this_month": current.usage_this_month,
            "provider": provider,
            "provider_resets": provider.resets_at.astimezone(LOCAL_ZONE).strftime(
                "%H:%M"
            ),
            "error": error,
            "saved": saved,
        },
        status_code=status_code,
    )


@router.get("/admin/settings/ai", response_class=HTMLResponse)
def ai_tab(request: Request, saved: str = "") -> HTMLResponse:
    """A-01 AI: the settings form, this month's usage and the provider quota."""
    actor = current_actor(request)
    return _ai_page(request, actor, saved=bool(saved))


def _whole_number(value: str, label: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        raise InvalidAISettingsError(f"{label} must be a whole number") from None


@router.post("/admin/settings/ai", response_model=None)
def ai_save(
    request: Request,
    model: Annotated[str, Form()] = "",
    daily_quota: Annotated[str, Form()] = "",
    max_pages: Annotated[str, Form()] = "",
    require_citations: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> RedirectResponse | HTMLResponse:
    require_csrf(request, csrf_token)
    actor = current_actor(request)
    submitted = {
        "model": model,
        "daily_quota": daily_quota,
        "max_pages": max_pages,
        "require_citations": require_citations,
    }
    try:
        change = AISettingsChange(
            # Blank only when the stored model left the list: keep it.
            model=model or None,
            daily_quota=_whole_number(daily_quota, "Daily quota"),
            max_pages=_whole_number(max_pages, "Max pages"),
            require_citations=bool(require_citations),
        )
        _service(request).update_ai_settings(actor, change)
    except ModelNotAllowedError:
        return _ai_page(
            request,
            actor,
            form=submitted,
            error=f"{model} is not an allowed model.",
            status_code=UNPROCESSABLE,
        )
    except InvalidAISettingsError as error:
        return _ai_page(
            request,
            actor,
            form=submitted,
            error=str(error),
            status_code=UNPROCESSABLE,
        )
    return _see_other(f"{AI_URL}?saved=1")


# ---- A-01 System status, A-01a -------------------------------------------------


@router.get("/admin/settings/status", response_class=HTMLResponse)
def status_tab(request: Request) -> HTMLResponse:
    """A-01 System status, refreshed every 60 s; A-01a when a service is down."""
    actor = current_actor(request)
    system = _service(request).get_system_status(actor)
    return _templates(request).TemplateResponse(
        request,
        "admin/a01_status.html",
        {
            "actor": actor,
            "breadcrumb": "Admin settings",
            "tab": "status",
            "services": system.services,
            "down": [
                service
                for service in system.services
                if service.status is ServiceHealthStatus.UNHEALTHY
            ],
        },
    )
