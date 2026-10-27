"""A mail provider that fails must not 500 a sign-up or hide a publish (OPS-37)."""

import re
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from questly.config import PRODUCTION
from questly.core.notifications.ports import EmailDeliveryError
from questly.integrations.email import SentEmail, StubEmailSender
from tests.fakes.app import build_app, fake_settings
from tests.fakes.auth import FakeAuthRepository, seed_user
from tests.integration.forms import post_form
from tests.integration.test_notifications_api import (
    CLASSMATE,
    STUDENT,
    TEACHER,
    Campus,
)

SIGN_UP = {
    "full_name": "Somchai Prasert",
    "email": "somchai@kmitl.ac.th",
    "password": "correct horse",
    "confirm_password": "correct horse",
}
SENT_COPY = "เราได้ส่งลิงก์ยืนยัน"
LINK = re.compile(r"http://localhost:8000/verify\?token=[\w-]+")


class ProviderDown:
    """An EmailSender whose provider is unreachable until `down` is cleared."""

    def __init__(self, *, down: bool = True) -> None:
        self.down = down
        self.attempts: list[SentEmail] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.attempts.append(SentEmail(to=to, subject=subject, body=body))
        if self.down:
            raise EmailDeliveryError("provider unreachable")


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.anyio
async def test_page_signup_survives_a_failed_send_and_offers_the_resend() -> None:
    mailer = ProviderDown()
    users = FakeAuthRepository()
    app = build_app(auth_repository=users, verification_mailer=mailer)
    async with _client(app) as client:
        response = await post_form(client, "/signup", SIGN_UP, page="/signup")

    assert response.status_code == 200
    assert 'data-state="G-04a"' in response.text
    assert 'data-state="delivery-failed"' in response.text
    assert "ส่งอีเมลอีกครั้ง" in response.text
    assert SENT_COPY not in response.text
    assert SIGN_UP["email"] in response.text
    assert users.find_user_by_email(SIGN_UP["email"]) is not None


@pytest.mark.anyio
async def test_the_link_of_a_failed_send_still_verifies_the_account() -> None:
    mailer = ProviderDown()
    app = build_app(verification_mailer=mailer)
    async with _client(app) as client:
        await post_form(client, "/signup", SIGN_UP, page="/signup")
        (attempt,) = mailer.attempts
        match = LINK.search(attempt.body)
        assert match is not None
        verified = await client.get(match.group(0))

    assert 'data-state="G-04b"' in verified.text


@pytest.mark.anyio
async def test_page_resend_failure_shows_the_warning_then_success_clears_it() -> None:
    mailer = ProviderDown(down=False)
    users = FakeAuthRepository()
    seed_user(users, email="new@kmitl.ac.th", full_name="N", verified=False)
    app = build_app(auth_repository=users, verification_mailer=mailer)
    async with _client(app) as client:
        mailer.down = True
        failed = await post_form(
            client, "/verify/resend", {"email": "new@kmitl.ac.th"}, page="/verify"
        )
        mailer.down = False
        sent = await post_form(
            client, "/verify/resend", {"email": "new@kmitl.ac.th"}, page="/verify"
        )

    assert failed.status_code == 200
    assert 'data-state="delivery-failed"' in failed.text
    assert SENT_COPY not in failed.text
    assert "ส่งอีเมลอีกครั้ง" in failed.text
    assert 'data-state="delivery-failed"' not in sent.text
    assert SENT_COPY in sent.text


@pytest.mark.anyio
async def test_a_working_signup_shows_no_warning() -> None:
    mailer = ProviderDown(down=False)
    async with _client(build_app(verification_mailer=mailer)) as client:
        response = await post_form(client, "/signup", SIGN_UP, page="/signup")

    assert 'data-state="G-04a"' in response.text
    assert 'data-state="delivery-failed"' not in response.text
    assert SENT_COPY in response.text


@pytest.mark.anyio
async def test_api_signup_stays_201_when_the_send_fails() -> None:
    users = FakeAuthRepository()
    app = build_app(auth_repository=users, verification_mailer=ProviderDown())
    working = build_app(verification_mailer=ProviderDown(down=False))
    async with _client(app) as client, _client(working) as reference:
        failed = await client.post("/api/v1/auth/signup", json=SIGN_UP)
        expected = await reference.post("/api/v1/auth/signup", json=SIGN_UP)

    assert failed.status_code == 201
    assert failed.json() == expected.json()
    assert users.find_user_by_email(SIGN_UP["email"]) is not None


@pytest.mark.anyio
async def test_api_resend_answers_503_when_the_send_fails() -> None:
    users = FakeAuthRepository()
    seed_user(users, email="new@kmitl.ac.th", full_name="N", verified=False)
    app = build_app(auth_repository=users, verification_mailer=ProviderDown())
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/auth/verify/resend", json={"email": "new@kmitl.ac.th"}
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "email_delivery_failed"


@pytest.mark.anyio
async def test_api_resend_for_an_unknown_email_is_202_with_the_provider_down() -> None:
    app = build_app(verification_mailer=ProviderDown())
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/auth/verify/resend", json={"email": "nobody@kmitl.ac.th"}
        )

    assert response.status_code == 202


@pytest.mark.anyio
async def test_one_failing_member_email_spares_the_others_and_the_request() -> None:
    campus = Campus()
    real_send = campus.mailer.send

    def send(*, to: str, subject: str, body: str) -> None:
        if to == STUDENT:
            raise EmailDeliveryError("mailbox refused")
        real_send(to=to, subject=subject, body=body)

    campus.mailer.send = send
    teacher = await campus.client_for(TEACHER)
    campus.mailer.messages.clear()

    response = await teacher.post(campus.notify_path)

    assert response.status_code == 204
    assert [m.to for m in campus.mailer.messages] == [CLASSMATE]
    for email in (STUDENT, CLASSMATE):
        member = await campus.client_for(email)
        kinds = [
            item["kind"]
            for item in (await member.get("/api/v1/notifications")).json()["items"]
        ]
        assert kinds == ["assignment_notified", "assignment_published"]


@pytest.mark.anyio
async def test_a_failing_member_email_is_logged_without_the_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    campus = Campus()

    def send(*, to: str, subject: str, body: str) -> None:
        raise EmailDeliveryError("mailbox refused")

    campus.mailer.send = send
    teacher = await campus.client_for(TEACHER)

    with caplog.at_level("WARNING"):
        response = await teacher.post(campus.notify_path)

    assert response.status_code == 204
    # the stub logs bodies on purpose; only the service's own records matter
    ours = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "questly.core.notifications.service"
    )
    assert "Reminder: Binary search" in ours
    assert "/classes/" not in ours


@pytest.mark.anyio
async def test_stub_backend_outside_production_shows_the_link_on_g04a() -> None:
    mailer = StubEmailSender()
    async with _client(build_app(verification_mailer=mailer)) as client:
        signup = await post_form(client, "/signup", SIGN_UP, page="/signup")

    sent_link = LINK.search(mailer.messages[-1].body)
    assert sent_link is not None
    assert 'data-state="demo-link"' in signup.text
    assert f'href="{sent_link.group(0)}"' in signup.text
    assert "not sent" in signup.text


@pytest.mark.anyio
async def test_demo_link_follows_a_resend() -> None:
    mailer = StubEmailSender()
    users = FakeAuthRepository()
    seed_user(users, email="new@kmitl.ac.th", full_name="N", verified=False)
    app = build_app(auth_repository=users, verification_mailer=mailer)
    async with _client(app) as client:
        response = await post_form(
            client, "/verify/resend", {"email": "New@kmitl.ac.th"}, page="/verify"
        )

    sent_link = LINK.search(mailer.messages[-1].body)
    assert sent_link is not None
    assert f'href="{sent_link.group(0)}"' in response.text


@pytest.mark.anyio
async def test_demo_link_is_hidden_in_production_even_with_the_stub() -> None:
    mailer = StubEmailSender()
    settings = replace(fake_settings(), app_env=PRODUCTION)
    async with _client(build_app(settings=settings, verification_mailer=mailer)) as c:
        signup = await post_form(c, "/signup", SIGN_UP, page="/signup")

    assert 'data-state="G-04a"' in signup.text
    assert 'data-state="demo-link"' not in signup.text
    assert "token=" not in signup.text


@pytest.mark.anyio
async def test_demo_link_is_hidden_for_a_real_backend() -> None:
    mailer = ProviderDown(down=False)
    async with _client(build_app(verification_mailer=mailer)) as client:
        signup = await post_form(client, "/signup", SIGN_UP, page="/signup")

    assert mailer.attempts
    assert 'data-state="demo-link"' not in signup.text
    assert "token=" not in signup.text


@pytest.mark.anyio
async def test_demo_link_never_reaches_api_json() -> None:
    mailer = StubEmailSender()
    users = FakeAuthRepository()
    seed_user(users, email="new@kmitl.ac.th", full_name="N", verified=False)
    app = build_app(auth_repository=users, verification_mailer=mailer)
    async with _client(app) as client:
        signup = await client.post("/api/v1/auth/signup", json=SIGN_UP)
        resend = await client.post(
            "/api/v1/auth/verify/resend", json={"email": "new@kmitl.ac.th"}
        )

    for response in (signup, resend):
        assert "token=" not in response.text
        assert "/verify" not in response.text
