"""External sign-up and on-screen verification over HTTP (OPS-43, ADR-0013)."""

import html
import re
from dataclasses import replace

import pytest
from fastapi import FastAPI

from questly.config import PRODUCTION
from questly.core.notifications.ports import EmailSender
from questly.integrations.email import StubEmailSender
from tests.fakes.app import build_app, fake_settings
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user
from tests.integration.forms import asgi_client, post_form
from tests.integration.test_email_delivery import ProviderDown

KMITL_SIGN_UP = {
    "full_name": "Somchai Prasert",
    "email": "somchai@kmitl.ac.th",
    "password": "correct horse",
    "confirm_password": "correct horse",
}
EXTERNAL_SIGN_UP = {
    **KMITL_SIGN_UP,
    "email": "ada@example.com",
    "affiliation": "external",
}
LINK = re.compile(r"http://localhost:8000/verify\?token=[\w-]+")
ONSCREEN = 'data-state="G-04a-onscreen"'
FAILED = 'data-state="delivery-failed"'
CHECKED_EXTERNAL = 'value="external" checked'
CHECKED_KMITL = 'value="kmitl" checked'


def _app(
    mailer: EmailSender,
    *domains: str,
    app_env: str = "",
    auth_repository: FakeAuthRepository | None = None,
) -> FastAPI:
    """A fake app whose ONSCREEN_VERIFY_DOMAINS lists `domains`."""
    settings = replace(
        fake_settings(), onscreen_verify_domains=domains, app_env=app_env
    )
    return build_app(
        settings=settings,
        verification_mailer=mailer,
        auth_repository=auth_repository,
    )


def _sent_link(mailer: ProviderDown) -> str:
    match = LINK.search(mailer.attempts[-1].body)
    assert match is not None
    return match.group(0)


def _onscreen_href(page: str) -> str:
    block = page.split(ONSCREEN, 1)[1]
    match = re.search(r'href="([^"]+)"', block)
    assert match is not None
    return html.unescape(match.group(1))


# G-03 ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_signup_page_offers_both_affiliations_with_kmitl_chosen() -> None:
    async with asgi_client(build_app()) as client:
        page = await client.get("/signup")

    assert "สมัครในฐานะ" in page.text
    assert CHECKED_KMITL in page.text
    assert 'value="external"' in page.text and CHECKED_EXTERNAL not in page.text
    assert "บุคคลภายนอกขอสิทธิ์อาจารย์ไม่ได้" in page.text
    assert 'placeholder="name@example.com"' in page.text


@pytest.mark.anyio
async def test_external_signup_emails_once_and_shows_no_link() -> None:
    mailer = ProviderDown(down=False)
    users = FakeAuthRepository()
    app = _app(mailer, "kmitl.ac.th", auth_repository=users)
    async with asgi_client(app) as client:
        response = await post_form(client, "/signup", EXTERNAL_SIGN_UP, page="/signup")

    assert response.status_code == 200
    assert 'data-state="G-04a"' in response.text
    assert [attempt.to for attempt in mailer.attempts] == ["ada@example.com"]
    assert "token=" not in response.text
    assert ONSCREEN not in response.text
    assert users.find_user_by_email("ada@example.com") is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("form", "copy"),
    [
        ({"email": "somchai@kmitl.ac.th"}, "อีเมล @kmitl.ac.th ให้เลือก"),
        ({"email": "ada@localhost"}, "อีเมลไม่ถูกต้อง"),
        (
            {"wants_instructor": "on", "faculty": "Science"},
            "บุคคลภายนอกขอสิทธิ์อาจารย์ไม่ได้ เลือก",
        ),
    ],
)
async def test_external_signup_errors_keep_the_external_choice(
    form: dict[str, str], copy: str
) -> None:
    users = FakeAuthRepository()
    async with asgi_client(build_app(auth_repository=users)) as client:
        response = await post_form(
            client, "/signup", {**EXTERNAL_SIGN_UP, **form}, page="/signup"
        )

    assert response.status_code == 422
    assert copy in html.unescape(response.text)
    assert CHECKED_EXTERNAL in response.text
    assert CHECKED_KMITL not in response.text
    assert users.list_users() == []


@pytest.mark.anyio
async def test_kmitl_signup_error_keeps_the_kmitl_choice() -> None:
    async with asgi_client(build_app()) as client:
        response = await post_form(
            client,
            "/signup",
            {**KMITL_SIGN_UP, "email": "ada@example.com", "affiliation": "kmitl"},
            page="/signup",
        )

    assert response.status_code == 422
    assert 'data-state="G-03a"' in response.text
    assert CHECKED_KMITL in response.text


# G-04a on screen -----------------------------------------------------------------


@pytest.mark.anyio
async def test_listed_domain_shows_the_emailed_link_and_it_verifies() -> None:
    mailer = ProviderDown(down=False)
    async with asgi_client(_app(mailer, "kmitl.ac.th")) as client:
        signup = await post_form(client, "/signup", KMITL_SIGN_UP, page="/signup")
        href = _onscreen_href(signup.text)
        verified = await client.get(href)

    assert [attempt.to for attempt in mailer.attempts] == ["somchai@kmitl.ac.th"]
    assert href == _sent_link(mailer)
    assert "ยืนยันอีเมล" in signup.text
    assert "ระบบอีเมลของมหาวิทยาลัยกรองอีเมลจากเรา" in signup.text
    assert 'data-state="demo-link"' not in signup.text
    assert 'data-state="G-04b"' in verified.text


@pytest.mark.anyio
async def test_listed_domain_shows_the_link_in_production() -> None:
    mailer = ProviderDown(down=False)
    app = _app(mailer, "kmitl.ac.th", app_env=PRODUCTION)
    async with asgi_client(app) as client:
        signup = await post_form(client, "/signup", KMITL_SIGN_UP, page="/signup")

    assert _onscreen_href(signup.text) == _sent_link(mailer)


@pytest.mark.anyio
async def test_listed_domain_with_a_failed_send_shows_the_link_not_the_warning() -> (
    None
):
    mailer = ProviderDown()
    async with asgi_client(_app(mailer, "kmitl.ac.th")) as client:
        signup = await post_form(client, "/signup", KMITL_SIGN_UP, page="/signup")
        verified = await client.get(_onscreen_href(signup.text))

    assert len(mailer.attempts) == 1
    assert FAILED not in signup.text
    assert "เราได้ส่งลิงก์ยืนยัน" not in signup.text
    assert "ส่งอีเมลอีกครั้ง" in signup.text
    assert 'data-state="G-04b"' in verified.text


@pytest.mark.anyio
async def test_resend_for_a_listed_domain_shows_a_fresh_single_use_link() -> None:
    mailer = ProviderDown(down=False)
    users = FakeAuthRepository()
    seed_user(users, email="new@kmitl.ac.th", full_name="N", verified=False)
    app = _app(mailer, "kmitl.ac.th", auth_repository=users)
    async with asgi_client(app) as client:
        first = await post_form(
            client, "/verify/resend", {"email": "New@kmitl.ac.th"}, page="/verify"
        )
        second = await post_form(
            client, "/verify/resend", {"email": "new@kmitl.ac.th"}, page="/verify"
        )
        fresh = _onscreen_href(second.text)
        verified = await client.get(fresh)
        reused = await client.get(fresh)

    assert _onscreen_href(first.text) != fresh
    assert fresh == _sent_link(mailer)
    assert len(mailer.attempts) == 2
    assert 'data-state="G-04b"' in verified.text
    assert reused.status_code == 400


@pytest.mark.anyio
async def test_unlisted_domain_gets_no_link_when_the_demo_link_is_off() -> None:
    mailer = StubEmailSender()
    app = _app(mailer, "example.org", app_env=PRODUCTION)
    async with asgi_client(app) as client:
        signup = await post_form(client, "/signup", KMITL_SIGN_UP, page="/signup")

    assert mailer.messages
    assert 'data-state="G-04a"' in signup.text
    assert ONSCREEN not in signup.text
    assert 'data-state="demo-link"' not in signup.text
    assert "token=" not in signup.text


@pytest.mark.anyio
async def test_unlisted_domain_with_a_failed_send_still_warns() -> None:
    mailer = ProviderDown()
    async with asgi_client(_app(mailer, "kmitl.ac.th")) as client:
        signup = await post_form(client, "/signup", EXTERNAL_SIGN_UP, page="/signup")

    assert FAILED in signup.text
    assert "token=" not in signup.text


# API ------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_external_signup_creates_a_student() -> None:
    async with asgi_client(build_app()) as client:
        response = await client.post(
            "/api/v1/auth/signup",
            json={**KMITL_SIGN_UP, "email": "Ada@Example.com", "external": True},
        )

    assert response.status_code == 201
    assert response.json()["email"] == "ada@example.com"
    assert response.json()["role"] == "student"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"email": "ada@example.com"}, "not_kmitl_email"),
        ({"external": True}, "use_kmitl_option"),
        ({"external": True, "email": "ada@localhost"}, "invalid_email"),
        (
            {
                "external": True,
                "email": "ada@example.com",
                "wants_instructor": True,
                "faculty": "Science",
            },
            "external_cannot_request_instructor",
        ),
    ],
)
async def test_api_signup_affiliation_errors(override: dict, code: str) -> None:
    async with asgi_client(build_app()) as client:
        response = await client.post(
            "/api/v1/auth/signup", json={**KMITL_SIGN_UP, **override}
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code


@pytest.mark.anyio
async def test_api_never_returns_the_onscreen_link() -> None:
    mailer = ProviderDown(down=False)
    users = FakeAuthRepository()
    seed_user(users, email="new@kmitl.ac.th", full_name="N", verified=False)
    app = _app(mailer, "kmitl.ac.th", auth_repository=users)
    async with asgi_client(app) as client:
        signup = await client.post("/api/v1/auth/signup", json=KMITL_SIGN_UP)
        resend = await client.post(
            "/api/v1/auth/verify/resend", json={"email": "new@kmitl.ac.th"}
        )

    assert signup.status_code == 201
    assert resend.status_code == 202
    assert len(mailer.attempts) == 2
    for response in (signup, resend):
        assert "token=" not in response.text
        assert "/verify" not in response.text


@pytest.mark.anyio
async def test_api_external_student_cannot_request_instructor_rights() -> None:
    users = FakeAuthRepository()
    seed_user(users, email="ada@example.com", full_name="Ada")
    async with asgi_client(build_app(auth_repository=users)) as client:
        await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": DEFAULT_PASSWORD},
        )
        response = await client.post(
            "/api/v1/auth/instructor-requests", json={"faculty": "Science"}
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "external_cannot_request_instructor"
