"""Characterise the error bodies of the Auth JSON routes."""

from dataclasses import replace
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient, Response

from questly.core.auth.models import Role
from questly.core.notifications.ports import EmailDeliveryError
from questly.integrations.email import StubEmailSender
from questly.integrations.rate_limit import SlidingWindowRateLimiter
from questly.main import AUTH_RATE_LIMITS
from tests.fakes.app import build_app
from tests.fakes.auth import (
    DEFAULT_PASSWORD,
    FakeAuthRepository,
    FakeClock,
    seed_user,
    verification_path,
)

SIGN_UP = {
    "full_name": "Somchai Prasert",
    "email": "somchai@kmitl.ac.th",
    "password": "correct horse",
    "confirm_password": "correct horse",
}
EXTERNAL = {**SIGN_UP, "email": "somchai@gmail.com", "external": True}
SIGN_UP_PATH = "/api/v1/auth/signup"
INSTRUCTOR_REQUESTS = "/api/v1/auth/instructor-requests"


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def assert_error(response: Response, status: int, code: str, message: str) -> None:
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code, "message": message}}


class ProviderDown:
    def is_available(self) -> bool:
        return True

    def send(self, *, to: str, subject: str, body: str) -> None:
        raise EmailDeliveryError("provider unreachable")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "status", "code", "message"),
    [
        (
            {**SIGN_UP, "email": "somchai@gmail.com"},
            422,
            "not_kmitl_email",
            "Use your @kmitl.ac.th address to sign up",
        ),
        (
            {**SIGN_UP, "external": True},
            422,
            "use_kmitl_option",
            "Sign up an @kmitl.ac.th address as KMITL",
        ),
        (
            {**EXTERNAL, "email": "not-an-email"},
            422,
            "invalid_email",
            "Enter a valid email address",
        ),
        (
            {**EXTERNAL, "wants_instructor": True, "faculty": "Science"},
            422,
            "external_cannot_request_instructor",
            "Only @kmitl.ac.th accounts can request instructor rights",
        ),
        (
            {**SIGN_UP, "password": "short", "confirm_password": "short"},
            422,
            "password_too_short",
            "Password must be at least 8 characters",
        ),
        (
            {**SIGN_UP, "confirm_password": "different one"},
            422,
            "password_mismatch",
            "Passwords do not match",
        ),
        (
            {**SIGN_UP, "wants_instructor": True},
            422,
            "faculty_required",
            "Faculty is required for an instructor request",
        ),
    ],
)
async def test_sign_up_errors(body: dict, status: int, code: str, message: str) -> None:
    async with _client(build_app()) as client:
        response = await client.post(SIGN_UP_PATH, json=body)

    assert_error(response, status, code, message)


@pytest.mark.anyio
async def test_sign_up_twice_is_a_409() -> None:
    async with _client(build_app()) as client:
        await client.post(SIGN_UP_PATH, json=SIGN_UP)
        response = await client.post(SIGN_UP_PATH, json=SIGN_UP)

    assert_error(response, 409, "email_already_registered", "Email already registered")


@pytest.mark.anyio
async def test_sign_up_still_creates_the_account_when_the_email_is_not_sent() -> None:
    async with _client(build_app(verification_mailer=ProviderDown())) as client:
        response = await client.post(SIGN_UP_PATH, json=SIGN_UP)

    assert response.status_code == 201
    assert response.json()["email"] == SIGN_UP["email"]


@pytest.mark.anyio
async def test_verify_errors() -> None:
    clock = FakeClock()
    mailer = StubEmailSender()
    async with _client(build_app(verification_mailer=mailer, clock=clock)) as client:
        unknown = await client.post("/api/v1/auth/verify", json={"token": "nope"})
        await client.post(SIGN_UP_PATH, json=SIGN_UP)
        token = verification_path(mailer).split("token=", 1)[1]
        clock.advance(timedelta(days=2))
        expired = await client.post("/api/v1/auth/verify", json={"token": token})

    assert_error(unknown, 400, "verification_link_invalid", "This link is not valid")
    assert_error(expired, 410, "verification_link_expired", "This link has expired")


@pytest.mark.anyio
async def test_resend_is_503_when_the_email_cannot_be_sent() -> None:
    repository = FakeAuthRepository()
    seed_user(repository, email="u@kmitl.ac.th", full_name="U", verified=False)
    app = build_app(auth_repository=repository, verification_mailer=ProviderDown())
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/auth/verify/resend", json={"email": "u@kmitl.ac.th"}
        )

    assert_error(response, 503, "email_delivery_failed", "The email could not be sent")


@pytest.mark.anyio
async def test_login_errors() -> None:
    repository = FakeAuthRepository()
    seed_user(repository, email="unverified@kmitl.ac.th", full_name="U", verified=False)
    seed_user(repository, email="ok@kmitl.ac.th", full_name="O")
    async with _client(build_app(auth_repository=repository)) as client:
        wrong = await client.post(
            "/api/v1/auth/login", json={"email": "ok@kmitl.ac.th", "password": "nope"}
        )
        unverified = await client.post(
            "/api/v1/auth/login",
            json={"email": "unverified@kmitl.ac.th", "password": DEFAULT_PASSWORD},
        )

    assert_error(wrong, 401, "invalid_credentials", "Wrong email or password")
    assert_error(
        unverified, 403, "email_not_verified", "This email is not verified yet"
    )


@pytest.mark.anyio
async def test_login_of_a_deactivated_account_is_403() -> None:
    repository = FakeAuthRepository()
    user = seed_user(repository, email="gone@kmitl.ac.th", full_name="G")
    repository.update_user(replace(user, is_active=False))
    async with _client(build_app(auth_repository=repository)) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "gone@kmitl.ac.th", "password": DEFAULT_PASSWORD},
        )

    assert_error(response, 403, "account_deactivated", "This account is deactivated")


@pytest.mark.anyio
async def test_the_eleventh_login_is_429_with_retry_after() -> None:
    body = {"email": "nobody@kmitl.ac.th", "password": "nope"}
    app = build_app(rate_limiter=SlidingWindowRateLimiter(AUTH_RATE_LIMITS))
    async with _client(app) as client:
        earlier = [
            (await client.post("/api/v1/auth/login", json=body)).status_code
            for _ in range(10)
        ]
        refused = await client.post("/api/v1/auth/login", json=body)

    assert earlier == [401] * 10
    assert_error(refused, 429, "rate_limited", "Too many requests, try later")
    assert refused.headers["retry-after"] == "900"


@pytest.mark.anyio
async def test_instructor_request_errors() -> None:
    repository = FakeAuthRepository()
    seed_user(repository, email="s@kmitl.ac.th", full_name="S")
    seed_user(repository, email="ext@gmail.com", full_name="E")
    async with _client(build_app(auth_repository=repository)) as client:
        await client.post(
            "/api/v1/auth/login",
            json={"email": "s@kmitl.ac.th", "password": DEFAULT_PASSWORD},
        )
        blank = await client.post(INSTRUCTOR_REQUESTS, json={"faculty": " "})
        await client.post(INSTRUCTOR_REQUESTS, json={"faculty": "Science"})
        again = await client.post(INSTRUCTOR_REQUESTS, json={"faculty": "Science"})
        await client.post("/api/v1/auth/logout")
        await client.post(
            "/api/v1/auth/login",
            json={"email": "ext@gmail.com", "password": DEFAULT_PASSWORD},
        )
        external = await client.post(INSTRUCTOR_REQUESTS, json={"faculty": "Science"})

    assert_error(blank, 422, "faculty_required", "Faculty is required")
    assert_error(
        again, 409, "instructor_request_exists", "A request is already pending"
    )
    assert_error(
        external,
        422,
        "external_cannot_request_instructor",
        "Only @kmitl.ac.th accounts can request instructor rights",
    )


@pytest.mark.anyio
async def test_instructor_request_by_an_instructor_is_403() -> None:
    repository = FakeAuthRepository()
    seed_user(repository, email="t@kmitl.ac.th", full_name="T", role=Role.INSTRUCTOR)
    async with _client(build_app(auth_repository=repository)) as client:
        await client.post(
            "/api/v1/auth/login",
            json={"email": "t@kmitl.ac.th", "password": DEFAULT_PASSWORD},
        )
        response = await client.post(INSTRUCTOR_REQUESTS, json={"faculty": "Science"})

    assert response.status_code == 403
