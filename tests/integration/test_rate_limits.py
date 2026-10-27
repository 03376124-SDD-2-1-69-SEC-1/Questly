"""Auth endpoints refuse a client that sends too many requests (OPS-44)."""

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from questly.core.auth.service import RateLimitBucket, rate_limit_key_for_email
from questly.integrations.rate_limit import RateLimit, SlidingWindowRateLimiter
from questly.main import AUTH_RATE_LIMITS
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user
from tests.integration.forms import csrf_token

EMAIL = "somchai@kmitl.ac.th"
OTHER_EMAIL = "malee@kmitl.ac.th"


def _wrong_login(student: int) -> dict[str, str]:
    """A failed login to a different account each time, like one lab behind NAT."""
    return {"email": f"6501{student:04d}@kmitl.ac.th", "password": "wrong password"}


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


def _fresh_address(attempt: int) -> str:
    return f"10.1.{attempt // 250}.{attempt % 250 + 1}"


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
    assert refused.headers["retry-after"] == "800"


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
    assert refused.headers["retry-after"] == "900"


@pytest.mark.anyio
async def test_login_is_allowed_again_after_the_window() -> None:
    clock = ManualClock()
    body = {"email": EMAIL, "password": DEFAULT_PASSWORD}
    async with _client(_app(clock)) as client:
        for _ in range(10):
            await client.post("/api/v1/auth/login", json=body)
        clock.now += 900
        response = await client.post("/api/v1/auth/login", json=body)

    assert response.status_code == 200


@pytest.mark.anyio
async def test_signup_page_and_api_allow_sixty_per_hour_per_address() -> None:
    clock = ManualClock()
    app = _app(clock)
    form = {
        "full_name": "Somchai Prasert",
        "email": "new@kmitl.ac.th",
        "password": "correct horse",
        "confirm_password": "mismatched horse",
    }
    async with _client(app, "10.0.0.2") as page_client:
        pages = await _post_page(page_client, "/signup", form, 61, "/signup")
    async with _client(app, "10.0.0.3") as api_client:
        apis = [
            (await api_client.post("/api/v1/auth/signup", json=form)).status_code
            for _ in range(61)
        ]

    assert pages == [422] * 60 + [429]
    assert apis == [422] * 60 + [429]


@pytest.mark.anyio
async def test_resend_page_and_api_allow_three_per_account_per_quarter_hour() -> None:
    clock = ManualClock()
    app = _app(clock)
    async with _client(app, "10.0.0.2") as page_client:
        pages = await _post_page(
            page_client, "/verify/resend", {"email": EMAIL}, 4, "/verify"
        )
    async with _client(app, "10.0.0.3") as api_client:
        apis = [
            (
                await api_client.post(
                    "/api/v1/auth/verify/resend", json={"email": OTHER_EMAIL}
                )
            ).status_code
            for _ in range(4)
        ]
        refused = await api_client.post(
            "/api/v1/auth/verify/resend", json={"email": OTHER_EMAIL}
        )

    assert pages == [200] * 3 + [429]
    assert apis == [202] * 3 + [429]
    assert refused.json()["detail"]["code"] == "rate_limited"
    assert refused.headers["retry-after"] == "900"


@pytest.mark.anyio
async def test_each_client_address_has_its_own_count() -> None:
    clock = ManualClock()
    app = _app(clock)
    async with _client(app, "10.0.0.2") as first:
        for student in range(100):
            await first.post("/api/v1/auth/login", json=_wrong_login(student))
        first_refused = await first.post("/api/v1/auth/login", json=_wrong_login(100))
    async with _client(app, "10.0.0.3") as second:
        second_response = await second.post(
            "/api/v1/auth/login", json=_wrong_login(101)
        )

    assert first_refused.status_code == 429
    assert second_response.status_code == 401


@pytest.mark.anyio
async def test_forwarding_headers_do_not_change_the_key() -> None:
    clock = ManualClock()
    async with _client(_app(clock)) as client:
        for student in range(100):
            await client.post(
                "/api/v1/auth/login",
                json=_wrong_login(student),
                headers={"X-Forwarded-For": f"203.0.113.{student}"},
            )
        refused = await client.post(
            "/api/v1/auth/login",
            json=_wrong_login(100),
            headers={"X-Forwarded-For": "203.0.113.200"},
        )

    assert refused.status_code == 429


@pytest.mark.anyio
async def test_eleven_page_logins_to_one_account_from_eleven_addresses() -> None:
    clock = ManualClock()
    app = _app(clock)
    statuses = []
    for attempt in range(11):
        async with _client(app, _fresh_address(attempt)) as client:
            token = await csrf_token(client, "/login")
            response = await client.post(
                "/login",
                data={"email": EMAIL, "password": "wrong", "csrf_token": token},
            )
        statuses.append(response.status_code)

    assert statuses == [401] * 10 + [429]
    assert 'data-page="G-05"' in response.text
    assert response.headers["retry-after"] == "900"


@pytest.mark.anyio
async def test_eleven_api_logins_to_one_account_from_eleven_addresses() -> None:
    clock = ManualClock()
    app = _app(clock)
    statuses = []
    for attempt in range(11):
        async with _client(app, _fresh_address(attempt)) as client:
            response = await client.post(
                "/api/v1/auth/login", json={"email": EMAIL, "password": "wrong"}
            )
        statuses.append(response.status_code)

    assert statuses == [401] * 10 + [429]
    assert response.json()["detail"]["code"] == "rate_limited"
    assert response.headers["retry-after"] == "900"


@pytest.mark.anyio
async def test_a_hundred_accounts_behind_one_address_are_not_limited() -> None:
    clock = ManualClock()
    async with _client(_app(clock), "10.0.0.2") as client:
        statuses = [
            (
                await client.post("/api/v1/auth/login", json=_wrong_login(student))
            ).status_code
            for student in range(100)
        ]

    assert statuses == [401] * 100


@pytest.mark.anyio
async def test_one_account_gets_three_resends_across_page_api_and_spellings() -> None:
    clock = ManualClock()
    app = _app(clock)
    async with _client(app, "10.0.0.2") as page_client:
        token = await csrf_token(page_client, "/verify")
        first = await page_client.post(
            "/verify/resend",
            data={"email": "Somchai@KMITL.ac.th", "csrf_token": token},
        )
    async with _client(app, "10.0.0.3") as api_client:
        second = await api_client.post(
            "/api/v1/auth/verify/resend", json={"email": f" {EMAIL} "}
        )
        third = await api_client.post(
            "/api/v1/auth/verify/resend", json={"email": EMAIL.upper()}
        )
        api_refused = await api_client.post(
            "/api/v1/auth/verify/resend", json={"email": EMAIL}
        )
    async with _client(app, "10.0.0.4") as page_client:
        token = await csrf_token(page_client, "/verify")
        page_refused = await page_client.post(
            "/verify/resend", data={"email": EMAIL, "csrf_token": token}
        )

    assert [first.status_code, second.status_code, third.status_code] == [
        200,
        202,
        202,
    ]
    assert api_refused.status_code == 429
    assert api_refused.json()["detail"]["code"] == "rate_limited"
    assert api_refused.headers["retry-after"] == "900"
    assert page_refused.status_code == 429
    assert 'data-page="G-05"' in page_refused.text


@pytest.mark.anyio
async def test_an_address_refusal_does_not_count_for_the_account() -> None:
    repository = FakeAuthRepository()
    seed_user(repository, email=EMAIL, full_name="Somchai Prasert")
    app = build_app(
        auth_repository=repository,
        rate_limiter=SlidingWindowRateLimiter(
            {
                RateLimitBucket.LOGIN: RateLimit(limit=1, window_seconds=60),
                RateLimitBucket.LOGIN_EMAIL: RateLimit(limit=2, window_seconds=60),
            },
            clock=ManualClock(),
        ),
    )
    body = {"email": EMAIL, "password": "wrong"}
    async with _client(app, "10.0.0.2") as busy:
        allowed = await busy.post("/api/v1/auth/login", json=body)
        address_refused = await busy.post("/api/v1/auth/login", json=body)
    async with _client(app, "10.0.0.3") as second:
        second_account_hit = await second.post("/api/v1/auth/login", json=body)
    async with _client(app, "10.0.0.4") as third:
        account_refused = await third.post("/api/v1/auth/login", json=body)

    assert allowed.status_code == 401
    assert address_refused.status_code == 429
    assert second_account_hit.status_code == 401
    assert account_refused.status_code == 429


@pytest.mark.anyio
async def test_a_limited_login_never_shows_or_logs_the_email(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    clock = ManualClock()
    app = _app(clock)
    async with _client(app, "10.0.0.2") as client:
        for _ in range(10):
            await client.post(
                "/api/v1/auth/login", json={"email": EMAIL, "password": "wrong"}
            )
        caplog.clear()
        api_refused = await client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": "wrong"}
        )
        token = await csrf_token(client, "/login")
        page_refused = await client.post(
            "/login", data={"email": EMAIL, "password": "wrong", "csrf_token": token}
        )

    assert api_refused.status_code == 429
    assert page_refused.status_code == 429
    for leaked in (EMAIL, rate_limit_key_for_email(EMAIL)):
        assert leaked not in api_refused.text
        assert leaked not in page_refused.text
        assert leaked not in caplog.text
