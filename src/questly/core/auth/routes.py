"""FastAPI adapter for /api/v1/auth."""

from fastapi import APIRouter, Request, Response, status

from questly.core.auth.current import SESSION_COOKIE, auth_service, current_actor
from questly.core.auth.models import InstructorRequest, User
from questly.core.auth.schemas import (
    InstructorRequestCreate,
    InstructorRequestResponse,
    LoginRequest,
    LoginResponse,
    ResendRequest,
    SignUpRequest,
    UserResponse,
    VerifyRequest,
)
from questly.core.auth.service import (
    SESSION_TTL,
    AccountDeactivatedError,
    EmailAlreadyRegisteredError,
    EmailNotVerifiedError,
    ExternalCannotRequestInstructorError,
    FacultyRequiredError,
    InstructorRequestExistsError,
    InvalidCredentialsError,
    InvalidEmailError,
    NotKmitlEmailError,
    PasswordMismatchError,
    PasswordTooShortError,
    RateLimitBucket,
    RateLimitedError,
    UseKmitlOptionError,
    VerificationEmailNotSentError,
    VerificationLinkExpiredError,
    VerificationLinkInvalidError,
    rate_limit_key_for_email,
)
from questly.core.http_errors import ErrorRule, ErrorTable, fail

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_EXTERNAL_CANNOT_REQUEST = ErrorRule(
    422,
    "external_cannot_request_instructor",
    "Only @kmitl.ac.th accounts can request instructor rights",
)

SIGN_UP_ERRORS = ErrorTable(
    {
        NotKmitlEmailError: ErrorRule(
            422, "not_kmitl_email", "Use your @kmitl.ac.th address to sign up"
        ),
        UseKmitlOptionError: ErrorRule(
            422, "use_kmitl_option", "Sign up an @kmitl.ac.th address as KMITL"
        ),
        InvalidEmailError: ErrorRule(422, "invalid_email", "Enter a valid email address"),
        ExternalCannotRequestInstructorError: _EXTERNAL_CANNOT_REQUEST,
        PasswordTooShortError: ErrorRule(
            422, "password_too_short", "Password must be at least 8 characters"
        ),
        PasswordMismatchError: ErrorRule(
            422, "password_mismatch", "Passwords do not match"
        ),
        FacultyRequiredError: ErrorRule(
            422, "faculty_required", "Faculty is required for an instructor request"
        ),
        EmailAlreadyRegisteredError: ErrorRule(
            409, "email_already_registered", "Email already registered"
        ),
    }
)
VERIFY_ERRORS = ErrorTable(
    {
        VerificationLinkInvalidError: ErrorRule(
            400, "verification_link_invalid", "This link is not valid"
        ),
        VerificationLinkExpiredError: ErrorRule(
            410, "verification_link_expired", "This link has expired"
        ),
    }
)
RESEND_ERRORS = ErrorTable(
    {
        VerificationEmailNotSentError: ErrorRule(
            503, "email_delivery_failed", "The email could not be sent"
        )
    }
)
LOGIN_ERRORS = ErrorTable(
    {
        InvalidCredentialsError: ErrorRule(
            401, "invalid_credentials", "Wrong email or password"
        ),
        EmailNotVerifiedError: ErrorRule(
            403, "email_not_verified", "This email is not verified yet"
        ),
        AccountDeactivatedError: ErrorRule(
            403, "account_deactivated", "This account is deactivated"
        ),
    }
)
INSTRUCTOR_REQUEST_ERRORS = ErrorTable(
    {
        ExternalCannotRequestInstructorError: _EXTERNAL_CANNOT_REQUEST,
        FacultyRequiredError: ErrorRule(422, "faculty_required", "Faculty is required"),
        InstructorRequestExistsError: ErrorRule(
            409, "instructor_request_exists", "A request is already pending"
        ),
    }
)

# A client the server cannot name shares one bucket rather than going unlimited.
UNKNOWN_CLIENT = "unknown"


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        email_verified=user.email_verified,
        student_number=user.student_number,
    )


def _request_response(request: InstructorRequest) -> InstructorRequestResponse:
    return InstructorRequestResponse(
        id=request.id,
        user_id=request.user_id,
        faculty=request.faculty,
        status=request.status.value,
    )


def enforce_rate_limit(
    request: Request, bucket: RateLimitBucket, *, email: str | None = None
) -> None:
    """Raise a 429 with Retry-After once this client has used up `bucket`.

    The key is the connecting address. Forwarding headers are never read here:
    uvicorn rewrites the address from them only for a trusted proxy, so a
    client cannot choose its own key. main.py renders the 429 as G-05 for a
    page and keeps the JSON envelope for /api/.

    With `email`, the account's own bucket is counted next, so many students
    behind one campus NAT share only the loose per-address limit. A request
    the address bucket refuses never reaches the account bucket.
    """
    address = UNKNOWN_CLIENT if request.client is None else request.client.host
    _hit(request, bucket, address)
    if email is not None:
        _hit(request, _ACCOUNT_BUCKETS[bucket], rate_limit_key_for_email(email))


_ACCOUNT_BUCKETS = {
    RateLimitBucket.LOGIN: RateLimitBucket.LOGIN_EMAIL,
    RateLimitBucket.RESEND: RateLimitBucket.RESEND_EMAIL,
}


def _hit(request: Request, bucket: RateLimitBucket, key: str) -> None:
    try:
        request.app.state.rate_limiter.hit(bucket, key)
    except RateLimitedError as limited:
        fail(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            "Too many requests, try later",
            headers={"Retry-After": str(limited.retry_after_seconds)},
        )


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    """HttpOnly, SameSite=Lax, and Secure unless create_app(secure_cookies=False)."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.app.state.secure_cookies,
    )


@router.post(
    "/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
def sign_up(request: Request, payload: SignUpRequest) -> UserResponse:
    """Create a Student account and email a verification link."""
    enforce_rate_limit(request, RateLimitBucket.SIGNUP)
    with SIGN_UP_ERRORS.translate():
        try:
            user = auth_service(request).sign_up(
                full_name=payload.full_name,
                email=payload.email,
                password=payload.password,
                confirm_password=payload.confirm_password,
                wants_instructor=payload.wants_instructor,
                faculty=payload.faculty,
                external=payload.external,
            )
        except VerificationEmailNotSentError as not_sent:
            # the account exists; the client can ask for a resend
            user = not_sent.user
    return user_response(user)


@router.post("/verify", response_model=UserResponse)
def verify(request: Request, payload: VerifyRequest) -> UserResponse:
    """Confirm an email address with the token from the link."""
    with VERIFY_ERRORS.translate():
        return user_response(auth_service(request).verify_email(payload.token))


@router.post("/verify/resend", status_code=status.HTTP_202_ACCEPTED)
def resend(request: Request, payload: ResendRequest) -> Response:
    """Email a fresh verification link if the account exists and is unverified."""
    enforce_rate_limit(request, RateLimitBucket.RESEND, email=payload.email)
    with RESEND_ERRORS.translate():
        auth_service(request).resend_verification(payload.email)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/login", response_model=LoginResponse)
def log_in(
    request: Request, payload: LoginRequest, response: Response
) -> LoginResponse:
    """Start a session; the token travels in an HttpOnly cookie."""
    enforce_rate_limit(request, RateLimitBucket.LOGIN, email=payload.email)
    with LOGIN_ERRORS.translate():
        result = auth_service(request).log_in(
            email=payload.email, password=payload.password
        )
    set_session_cookie(request, response, result.token)
    return LoginResponse(user=user_response(result.user), landing=result.landing.value)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def log_out(request: Request) -> Response:
    """End the current session."""
    token = request.cookies.get(SESSION_COOKIE)
    if token is not None:
        auth_service(request).log_out(token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/me", response_model=UserResponse)
def me(request: Request) -> UserResponse:
    """The logged-in account."""
    actor = current_actor(request)
    return user_response(auth_service(request).me(actor))


@router.post(
    "/instructor-requests",
    response_model=InstructorRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_instructor(
    request: Request, payload: InstructorRequestCreate
) -> InstructorRequestResponse:
    """Ask an Admin for Instructor rights."""
    actor = current_actor(request)
    with INSTRUCTOR_REQUEST_ERRORS.translate():
        created = auth_service(request).request_instructor(
            actor, faculty=payload.faculty
        )
    return _request_response(created)
