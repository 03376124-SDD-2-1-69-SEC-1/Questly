"""HTTP tests for G-01 login, G-03 sign-up, G-04 verify pages."""

from dataclasses import replace
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.auth.models import Role
from questly.core.auth.pages import DemoAccount
from questly.integrations.email import StubEmailSender
from tests.fakes.app import build_app, fake_settings
from tests.fakes.auth import (
    DEFAULT_PASSWORD,
    FakeAuthRepository,
    FakeClock,
    seed_user,
    verification_path,
)
from tests.integration.forms import post_form


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _page_id(response) -> str:
    """Every page template marks its root with the Figma page ID it renders."""
    marker = 'data-page="'
    start = response.text.index(marker) + len(marker)
    return response.text[start : response.text.index('"', start)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "page_id"),
    [
        ("/login", "G-01"),
        ("/signup", "G-03"),
        ("/verify", "G-04"),
    ],
)
async def test_public_pages_render_their_page(path: str, page_id: str) -> None:
    async with _client(build_app()) as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert _page_id(response) == page_id
    assert "<script" not in response.text.lower()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("role", "location"),
    [
        (Role.STUDENT, "/classes"),
        (Role.INSTRUCTOR, "/classes"),
        (Role.ADMIN, "/admin/settings"),
    ],
)
async def test_login_form_redirects_by_role_and_sets_the_cookie(
    role: Role, location: str
) -> None:
    repository = FakeAuthRepository()
    seed_user(repository, email="u@kmitl.ac.th", full_name="U", role=role)
    async with _client(build_app(auth_repository=repository)) as client:
        response = await post_form(
            client,
            "/login",
            {"email": "u@kmitl.ac.th", "password": DEFAULT_PASSWORD},
            page="/login",
        )

    assert response.status_code == 303
    assert response.headers["location"] == location
    assert "questly_session" in response.headers["set-cookie"]
    assert "httponly" in response.headers["set-cookie"].lower()


@pytest.mark.anyio
async def test_login_form_shows_01a_and_01b() -> None:
    repository = FakeAuthRepository()
    seed_user(repository, email="new@kmitl.ac.th", full_name="N", verified=False)
    async with _client(build_app(auth_repository=repository)) as client:
        wrong = await post_form(
            client,
            "/login",
            {"email": "new@kmitl.ac.th", "password": "nope"},
            page="/login",
        )
        unverified = await post_form(
            client,
            "/login",
            {"email": "new@kmitl.ac.th", "password": DEFAULT_PASSWORD},
            page="/login",
        )

    assert 'data-state="G-01a"' in wrong.text
    assert 'data-state="G-01b"' in unverified.text


@pytest.mark.anyio
async def test_demo_accounts_appear_on_login_only_when_passed() -> None:
    demo = (DemoAccount(label="Admin", email="admin@kmitl.ac.th", password="x"),)
    async with (
        _client(build_app()) as plain,
        _client(build_app(demo_accounts=demo)) as demo_client,
    ):
        without = await plain.get("/login")
        with_demo = await demo_client.get("/login")

    assert 'data-demo="log-in-as"' not in without.text
    assert 'data-demo="log-in-as"' in with_demo.text


@pytest.mark.anyio
async def test_signup_form_success_shows_04a_then_the_link_verifies() -> None:
    mailer = StubEmailSender()
    form = {
        "full_name": "Somchai",
        "email": "somchai@kmitl.ac.th",
        "password": "correct horse",
        "confirm_password": "correct horse",
    }
    async with _client(build_app(verification_mailer=mailer)) as client:
        waiting = await post_form(client, "/signup", form, page="/signup")
        verified = await client.get(verification_path(mailer))

    assert 'data-state="G-04a"' in waiting.text
    assert 'data-state="G-04b"' in verified.text


@pytest.mark.anyio
async def test_verification_email_uses_configured_base_url_not_request_host() -> None:
    mailer = StubEmailSender()
    settings = replace(fake_settings(), base_url="https://questly.example.com/")
    client = _client(build_app(settings=settings, verification_mailer=mailer))
    client.headers["host"] = "attacker.example"
    form = {
        "full_name": "Somchai",
        "email": "somchai@kmitl.ac.th",
        "password": "correct horse",
        "confirm_password": "correct horse",
    }

    response = await post_form(client, "/signup", form, page="/signup")

    assert response.status_code == 200
    verify_url = verification_path(mailer)
    assert verify_url.startswith("https://questly.example.com/verify?token=")
    assert "attacker.example" not in mailer.messages[-1].body


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("email", "state"),
    [("somchai@gmail.com", "G-03a"), ("taken@kmitl.ac.th", "G-03b")],
)
async def test_signup_form_shows_03a_and_03b(email: str, state: str) -> None:
    repository = FakeAuthRepository()
    seed_user(repository, email="taken@kmitl.ac.th", full_name="T")
    form = {
        "full_name": "X",
        "email": email,
        "password": "correct horse",
        "confirm_password": "correct horse",
    }
    async with _client(build_app(auth_repository=repository)) as client:
        response = await post_form(client, "/signup", form, page="/signup")

    assert response.status_code == 422
    assert f'data-state="{state}"' in response.text


@pytest.mark.anyio
async def test_verify_page_shows_04c_for_an_expired_link() -> None:
    mailer = StubEmailSender()
    clock = FakeClock()
    form = {
        "full_name": "S",
        "email": "s@kmitl.ac.th",
        "password": "correct horse",
        "confirm_password": "correct horse",
    }
    async with _client(build_app(verification_mailer=mailer, clock=clock)) as client:
        await post_form(client, "/signup", form, page="/signup")
        clock.advance(timedelta(hours=25))
        response = await client.get(verification_path(mailer))

    assert response.status_code == 410
    assert 'data-state="G-04c"' in response.text


@pytest.mark.anyio
async def test_logout_clears_the_session() -> None:
    repository = FakeAuthRepository()
    seed_user(repository, email="u@kmitl.ac.th", full_name="U")
    async with _client(build_app(auth_repository=repository)) as client:
        await post_form(
            client,
            "/login",
            {"email": "u@kmitl.ac.th", "password": DEFAULT_PASSWORD},
            page="/login",
        )
        logout = await post_form(client, "/logout", {}, page="/classes")
        me = await client.get("/api/v1/auth/me")

    assert logout.headers["location"] == "/login"
    assert me.status_code == 401
