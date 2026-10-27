"""HTML pages G-01 login, G-03 sign-up, G-04 verify, and `/` (ADR-0007).

Every POST handler calls `require_csrf` first; anonymous pages set the
`questly_csrf` cookie their forms are checked against.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from questly.core.auth.csrf import require_csrf, set_anonymous_csrf_cookie
from questly.core.auth.current import SESSION_COOKIE, auth_service, current_actor
from questly.core.auth.models import Landing, NotAuthenticatedError
from questly.core.auth.routes import enforce_rate_limit, set_session_cookie
from questly.core.auth.service import (
    NO_LINK,
    AccountDeactivatedError,
    EmailAlreadyRegisteredError,
    EmailNotVerifiedError,
    ExternalCannotRequestInstructorError,
    FacultyRequiredError,
    InvalidCredentialsError,
    InvalidEmailError,
    NotKmitlEmailError,
    PasswordMismatchError,
    PasswordTooShortError,
    RateLimitBucket,
    UseKmitlOptionError,
    VerificationEmailNotSentError,
    VerificationLinkExpiredError,
    VerificationLinkInvalidError,
)

router = APIRouter(tags=["pages"], include_in_schema=False)

LANDING_URLS = {
    Landing.CLASSROOMS: "/classes",
    Landing.ADMIN_SETTINGS: "/admin/settings",
}

# G-03 "สมัครในฐานะ" radio values (ADR-0013)
KMITL = "kmitl"
EXTERNAL = "external"

FACULTIES = (
    "Engineering",
    "Information Technology",
    "Science",
    "Architecture, Art and Design",
    "Industrial Education and Technology",
    "Agricultural Technology",
    "Food Industry",
    "Liberal Arts",
    "Business",
    "Medicine",
    "Dentistry",
    "Other",
)


@dataclass(frozen=True, slots=True)
class ErrorCopy:
    """What G-05 says for one HTTP status: icon, Thai title and message."""

    icon: str
    title: str
    message: str


ERROR_COPY = {
    400: ErrorCopy("x", "คำขอไม่ถูกต้อง", "ระบบอ่านคำขอนี้ไม่ได้ ลองกลับไปแล้วทำรายการใหม่"),
    401: ErrorCopy("shield", "กรุณาเข้าสู่ระบบ", "หน้านี้ต้องเข้าสู่ระบบก่อนจึงจะใช้งานได้"),
    403: ErrorCopy("shield", "คุณไม่มีสิทธิ์เข้าถึงหน้านี้", "บัญชีของคุณไม่ได้รับอนุญาตให้ทำรายการนี้"),
    404: ErrorCopy("search", "ไม่พบหน้าที่คุณต้องการ", "ลิงก์อาจผิดหรือหน้านี้ถูกย้ายหรือลบไปแล้ว"),
    405: ErrorCopy("x", "ไม่รองรับการดำเนินการนี้", "หน้านี้ไม่รับคำขอแบบที่ส่งมา"),
    408: ErrorCopy("clock", "หมดเวลาการเชื่อมต่อ", "เซิร์ฟเวอร์รอคำขอนานเกินไป ลองใหม่อีกครั้ง"),
    410: ErrorCopy("file", "หน้านี้ถูกนำออกแล้ว", "เนื้อหานี้ไม่มีให้เข้าถึงอีกต่อไป"),
    413: ErrorCopy("upload", "ไฟล์ใหญ่เกินไป", "ลดขนาดไฟล์แล้วอัปโหลดอีกครั้ง"),
    429: ErrorCopy("clock", "ส่งคำขอถี่เกินไป", "รอสักครู่แล้วลองใหม่อีกครั้ง"),
    500: ErrorCopy(
        "settings",
        "เซิร์ฟเวอร์ขัดข้อง",
        "มีบางอย่างผิดพลาดที่ฝั่งเรา ทีมงานจะรีบแก้ไข ลองใหม่อีกครั้งภายหลัง",
    ),
    502: ErrorCopy(
        "settings", "เซิร์ฟเวอร์ตอบกลับไม่ถูกต้อง", "บริการที่เกี่ยวข้องตอบกลับผิดปกติ ลองใหม่อีกครั้ง"
    ),
    503: ErrorCopy(
        "settings",
        "ระบบยังไม่พร้อมให้บริการ",
        "ระบบกำลังปิดปรับปรุงหรือมีผู้ใช้งานหนาแน่น ลองใหม่อีกครั้งภายหลัง",
    ),
    504: ErrorCopy(
        "clock", "เซิร์ฟเวอร์ตอบกลับช้าเกินไป", "บริการที่เกี่ยวข้องใช้เวลานานเกินไป ลองใหม่อีกครั้ง"
    ),
    505: ErrorCopy(
        "x", "ไม่รองรับ HTTP เวอร์ชันนี้", "เบราว์เซอร์ใช้ HTTP เวอร์ชันที่เซิร์ฟเวอร์ไม่รองรับ"
    ),
}
DEFAULT_ERROR_COPY = ErrorCopy(
    "x", "เกิดข้อผิดพลาดบางอย่าง", "มีบางอย่างไม่เป็นไปตามที่คาดไว้ ลองใหม่อีกครั้งหรือกลับหน้าแรก"
)


def error_page(
    request: Request, status_code: int, *, title: str = "", message: str = ""
) -> HTMLResponse:
    """G-05: the branded page for any HTTP error a browser can land on.

    `title` and `message` override the copy for a status whose cause the
    caller knows better, such as an expired form on a 403.
    """
    copy = ERROR_COPY.get(status_code, DEFAULT_ERROR_COPY)
    retry_url = (
        request.url.path if status_code >= 500 and request.method == "GET" else ""
    )
    return _templates(request).TemplateResponse(
        request,
        "shared/g05_error.html",
        {
            "status_code": status_code,
            "icon_name": copy.icon,
            "title": title or copy.title,
            "message": message or copy.message,
            "retry_url": retry_url,
        },
        status_code=status_code,
    )


@dataclass(frozen=True, slots=True)
class DemoAccount:
    """A seeded account listed under "log in as" on G-01 in demo mode only."""

    label: str
    email: str
    password: str


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def _demo_accounts(request: Request) -> tuple[DemoAccount, ...]:
    return request.app.state.demo_accounts


def _anonymous(request: Request, response: HTMLResponse) -> HTMLResponse:
    set_anonymous_csrf_cookie(request, response)
    return response


def _login_page(
    request: Request, *, email: str = "", error: str = "", status_code: int = 200
) -> HTMLResponse:
    return _anonymous(
        request,
        _templates(request).TemplateResponse(
            request,
            "shared/g01_login.html",
            {"email": email, "error": error, "demo_accounts": _demo_accounts(request)},
            status_code=status_code,
        ),
    )


def _signup_page(
    request: Request, *, form: dict[str, str], error: str = "", status_code: int = 200
) -> HTMLResponse:
    return _anonymous(
        request,
        _templates(request).TemplateResponse(
            request,
            "shared/g03_signup.html",
            {"form": form, "error": error, "faculties": FACULTIES},
            status_code=status_code,
        ),
    )


def _verify_page(
    request: Request,
    *,
    state: str,
    email: str = "",
    delivery_failed: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    link = NO_LINK
    if state == "waiting" and email:
        link = auth_service(request).shown_link_for(email)
    return _anonymous(
        request,
        _templates(request).TemplateResponse(
            request,
            "shared/g04_verify.html",
            {
                "state": state,
                "email": email,
                "delivery_failed": delivery_failed,
                "link": link,
            },
            status_code=status_code,
        ),
    )


@router.get("/", response_model=None)
def root(request: Request) -> HTMLResponse | RedirectResponse:
    """G-00 for a visitor; a logged-in account goes to its landing."""
    try:
        actor = current_actor(request)
    except NotAuthenticatedError:
        return _templates(request).TemplateResponse(
            request, "shared/g00_landing.html", {}
        )
    landing = auth_service(request).landing_for(actor)
    return RedirectResponse(
        LANDING_URLS[landing], status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    """G-01."""
    return _login_page(request)


@router.post("/login", response_model=None)
def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    """G-01 submit: 01a wrong password, 01b email not verified."""
    require_csrf(request, csrf_token)
    enforce_rate_limit(request, RateLimitBucket.LOGIN)
    try:
        result = auth_service(request).log_in(email=email, password=password)
    except InvalidCredentialsError:
        return _login_page(
            request, email=email, error="wrong_password", status_code=401
        )
    except EmailNotVerifiedError:
        return _login_page(request, email=email, error="not_verified", status_code=403)
    except AccountDeactivatedError:
        return _login_page(request, email=email, error="deactivated", status_code=403)
    response = RedirectResponse(
        LANDING_URLS[result.landing], status_code=status.HTTP_303_SEE_OTHER
    )
    set_session_cookie(request, response, result.token)
    return response


@router.post("/logout")
def logout_submit(
    request: Request, csrf_token: Annotated[str, Form()] = ""
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    token = request.cookies.get(SESSION_COOKIE)
    if token is not None:
        auth_service(request).log_out(token)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request) -> HTMLResponse:
    """G-03."""
    return _signup_page(request, form={"affiliation": KMITL})


@router.post("/signup", response_class=HTMLResponse)
def signup_submit(
    request: Request,
    full_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
    wants_instructor: Annotated[str, Form()] = "",
    faculty: Annotated[str, Form()] = "",
    affiliation: Annotated[str, Form()] = KMITL,
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """G-03 submit: 03a not a KMITL email, 03b already registered; success → G-04a."""
    require_csrf(request, csrf_token)
    enforce_rate_limit(request, RateLimitBucket.SIGNUP)
    external = affiliation == EXTERNAL
    form = {
        "full_name": full_name,
        "email": email,
        "wants_instructor": wants_instructor,
        "faculty": faculty,
        "affiliation": EXTERNAL if external else KMITL,
    }
    error = ""
    delivery_failed = False
    try:
        user = auth_service(request).sign_up(
            full_name=full_name,
            email=email,
            password=password,
            confirm_password=confirm_password,
            wants_instructor=wants_instructor == "on",
            faculty=faculty,
            external=external,
        )
    except VerificationEmailNotSentError as not_sent:
        # the account and its token exist; G-04a offers the resend button
        user = not_sent.user
        delivery_failed = True
    except NotKmitlEmailError:
        error = "not_kmitl_email"
    except UseKmitlOptionError:
        error = "use_kmitl_option"
    except InvalidEmailError:
        error = "invalid_email"
    except ExternalCannotRequestInstructorError:
        error = "external_cannot_request_instructor"
    except EmailAlreadyRegisteredError:
        error = "already_registered"
    except PasswordTooShortError:
        error = "password_too_short"
    except PasswordMismatchError:
        error = "password_mismatch"
    except FacultyRequiredError:
        error = "faculty_required"
    except ValueError:
        error = "full_name_required"
    if error:
        return _signup_page(request, form=form, error=error, status_code=422)
    return _verify_page(
        request, state="waiting", email=user.email, delivery_failed=delivery_failed
    )


@router.get("/verify", response_class=HTMLResponse)
def verify_page(request: Request, token: str = "") -> HTMLResponse:
    """G-04: 04a waiting (no token), 04b verified, 04c link expired."""
    if not token:
        return _verify_page(request, state="waiting")
    try:
        user = auth_service(request).verify_email(token)
    except VerificationLinkExpiredError:
        return _verify_page(request, state="expired", status_code=410)
    except VerificationLinkInvalidError:
        return _verify_page(request, state="invalid", status_code=400)
    return _verify_page(request, state="verified", email=user.email)


@router.post("/verify/resend", response_class=HTMLResponse)
def verify_resend(
    request: Request,
    email: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """G-01b / G-04c "Send a new link"."""
    require_csrf(request, csrf_token)
    enforce_rate_limit(request, RateLimitBucket.RESEND)
    delivery_failed = False
    try:
        auth_service(request).resend_verification(email)
    except VerificationEmailNotSentError:
        delivery_failed = True
    return _verify_page(
        request, state="waiting", email=email, delivery_failed=delivery_failed
    )
