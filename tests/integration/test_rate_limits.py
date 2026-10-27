"""Auth endpoints refuse a client that sends too many requests (OPS-44)."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from questly.integrations.rate_limit import SlidingWindowRateLimiter
from questly.main import AUTH_RATE_LIMITS
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user
from tests.integration.forms import csrf_token

EMAIL = "somchai@kmitl.ac.th"


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _app(clock: ManualClock) -> FastAPI:
    """An app with the production limits on a clock the test controls."""
    repository = FakeAuthRepository()
    seed_user(repository, email=EMAIL, full_name="Somchai Prasert")
    return build_app(
        auth_repository=repository,
        rate_limiter=SlidingWindowRateLimiter(AUTH_RATE_LIMITS, clock=clock),
    )


def _client(app: FastAPI, address: str = "127.0.0.1") -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=(address, 123)),
        base_url="http://testserver",
    )


async def _post_page(
    client: AsyncClient, action: str, data: dict[str, str], times: int, page: str
) -> list[int]:
    token = await csrf_token(client, page)
    statuses = []
    for _ in range(times):
        response = await client.post(action, data={**data, "csrf_token": token})
        statuses.append(response.status_code)
    return statuses


@pytest.mark.anyio
async def test_the_eleventh_login_page_post_gets_g05_429_with_retry_after() -> None:
    clock = ManualClock()
    async with _client(_app(clock)) as client:
        token = await csrf_token(client, "/login")
        form = {"email": EMAIL, "password": "wrong password", "csrf_token": token}
        earlier = [
            (await client.post("/login", data=form)).status_code for _ in range(10)
        ]
        clock.now += 100
        refused = await client.post("/login", data=form)

    assert earlier == [401] * 10
    assert refused.status_code == 429
    assert 'data-page="G-05"' in refused.text
    assert 'data-status="429"' in refused.text
    assert refused.headers["retry-after"] == "200"


@pytest.mark.anyio
async def test_a_correct_password_is_refused_too_once_the_limit_is_used() -> None:
    clock = ManualClock()
    async with _client(_app(clock)) as client:
        await _post_page(
            client, "/login", {"email": EMAIL, "password": "nope"}, 10, "/login"
        )
        token = await csrf_token(client, "/login")
        response = await client.post(
            "/login",
            data={"email": EMAIL, "password": DEFAULT_PASSWORD, "csrf_token": token},
        )

    assert response.status_code == 429
    assert "questly_session" not in response.cookies


@pytest.mark.anyio
async def test_the_eleventh_api_login_gets_429_rate_limited_json() -> None:
    clock = ManualClock()
    body = {"email": EMAIL, "password": "wrong password"}
    async with _client(_app(clock)) as client:
        earlier = [
            (await client.post("/api/v1/auth/login", json=body)).status_code
            for _ in range(10)
        ]
        refused = await client.post("/api/v1/auth/login", json=body)

    assert earlier == [401] * 10
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "rate_limited"
    assert refused.headers["retry-after"] == "300"


@pytest.mark.anyio
async def test_login_is_allowed_again_after_the_window() -> None:
    clock = ManualClock()
    body = {"email": EMAIL, "password": DEFAULT_PASSWORD}
    async with _client(_app(clock)) as client:
        for _ in range(10):
            await client.post("/api/v1/auth/login", json=body)
        clock.now += 300
        response = await client.post("/api/v1/auth/login", json=body)

    assert response.status_code == 200


@pytest.mark.anyio
async def test_signup_page_and_api_allow_five_per_hour() -> None:
    clock = ManualClock()
    app = _app(clock)
    form = {
        "full_name": "Somchai Prasert",
        "email": "new@kmitl.ac.th",
        "password": "correct horse",
        "confirm_password": "mismatched horse",
    }
    async with _client(app, "10.0.0.2") as page_client:
        pages = await _post_page(page_client, "/signup", form, 6, "/signup")
    async with _client(app, "10.0.0.3") as api_client:
        apis = [
            (await api_client.post("/api/v1/auth/signup", json=form)).status_code
            for _ in range(6)
        ]

    assert pages == [422] * 5 + [429]
    assert apis == [422] * 5 + [429]


@pytest.mark.anyio
async def test_resend_page_and_api_allow_five_per_quarter_hour() -> None:
    clock = ManualClock()
    app = _app(clock)
    async with _client(app, "10.0.0.2") as page_client:
        pages = await _post_page(
            page_client, "/verify/resend", {"email": EMAIL}, 6, "/verify"
        )
    async with _client(app, "10.0.0.3") as api_client:
        apis = [
            (
                await api_client.post(
                    "/api/v1/auth/verify/resend", json={"email": EMAIL}
                )
            ).status_code
            for _ in range(6)
        ]
        refused = await api_client.post(
            "/api/v1/auth/verify/resend", json={"email": EMAIL}
        )

    assert pages == [200] * 5 + [429]
    assert apis == [202] * 5 + [429]
    assert refused.json()["detail"]["code"] == "rate_limited"
    assert refused.headers["retry-after"] == "900"


@pytest.mark.anyio
async def test_each_client_address_has_its_own_count() -> None:
    clock = ManualClock()
    app = _app(clock)
    body = {"email": EMAIL, "password": "wrong password"}
    async with _client(app, "10.0.0.2") as first:
        for _ in range(10):
            await first.post("/api/v1/auth/login", json=body)
        first_refused = await first.post("/api/v1/auth/login", json=body)
    async with _client(app, "10.0.0.3") as second:
        second_response = await second.post("/api/v1/auth/login", json=body)

    assert first_refused.status_code == 429
    assert second_response.status_code == 401


@pytest.mark.anyio
async def test_forwarding_headers_do_not_change_the_key() -> None:
    clock = ManualClock()
    body = {"email": EMAIL, "password": "wrong password"}
    async with _client(_app(clock)) as client:
        for attempt in range(10):
            await client.post(
                "/api/v1/auth/login",
                json=body,
                headers={"X-Forwarded-For": f"203.0.113.{attempt}"},
            )
        refused = await client.post(
            "/api/v1/auth/login", json=body, headers={"X-Forwarded-For": "203.0.113.99"}
        )

    assert refused.status_code == 429
