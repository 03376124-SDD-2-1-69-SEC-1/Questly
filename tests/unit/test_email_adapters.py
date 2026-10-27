"""SmtpEmailSender and ResendEmailSender, driven by fakes. Nothing dials out."""

import json
import smtplib
import ssl
from email.message import EmailMessage
from types import TracebackType
from typing import Self

import httpx
import pytest

from questly.config import Settings
from questly.core.notifications.ports import EmailDeliveryError
from questly.integrations.email import (
    RESEND_EMAILS_URL,
    SEND_TIMEOUT_SECONDS,
    ResendEmailSender,
    SmtpEmailSender,
    StubEmailSender,
)

PASSWORD = "hunter2-app-password"
API_KEY = "re_live_secret_key"
SENDER = "Questly <noreply@questly.example.com>"
THAI_NAME_BODY = (
    "สวัสดี สมชาย ประเสริฐ,\n\nเปิดลิงก์นี้เพื่อยืนยันอีเมล:\n\nhttp://x/verify?token=t\n"
)


class Calls:
    """What the fake SMTP classes were asked, in order, across both classes."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.constructed: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.message: EmailMessage | None = None
        self.login_args: tuple[str, str] | None = None
        self.fail_at = ""
        self.error: Exception = smtplib.SMTPException("boom")

    def factory(self, name: str):
        calls = self

        class FakeSmtp:
            def __init__(self, *args: object, **kwargs: object) -> None:
                calls.constructed.append((name, args, kwargs))
                calls.events.append(f"connect:{name}")
                calls._maybe_fail("connect")

            def __enter__(self) -> Self:
                return self

            def __exit__(
                self,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                traceback: TracebackType | None,
            ) -> None:
                calls.events.append("quit")

            def starttls(self, *, context: ssl.SSLContext) -> None:
                calls.events.append("starttls")
                assert isinstance(context, ssl.SSLContext)
                assert context.verify_mode == ssl.CERT_REQUIRED
                calls._maybe_fail("starttls")

            def login(self, user: str, password: str) -> None:
                calls.events.append("login")
                calls.login_args = (user, password)
                calls._maybe_fail("login")

            def send_message(self, msg: EmailMessage) -> None:
                calls.events.append("send_message")
                calls.message = msg
                calls._maybe_fail("send_message")

        return FakeSmtp

    def _maybe_fail(self, step: str) -> None:
        if self.fail_at == step:
            raise self.error


def _smtp(calls: Calls, *, security: str, port: int) -> SmtpEmailSender:
    return SmtpEmailSender(
        host="smtp.example.com",
        port=port,
        username="mailer@example.com",
        password=PASSWORD,
        sender=SENDER,
        security=security,
        smtp_factory=calls.factory("SMTP"),
        smtp_ssl_factory=calls.factory("SMTP_SSL"),
    )


def test_starttls_connects_upgrades_logs_in_then_sends() -> None:
    calls = Calls()

    _smtp(calls, security="starttls", port=587).send(
        to="somchai@kmitl.ac.th", subject="Verify", body="hello"
    )

    assert calls.events == [
        "connect:SMTP",
        "starttls",
        "login",
        "send_message",
        "quit",
    ]
    (name, args, kwargs), *_ = calls.constructed
    assert (name, args) == ("SMTP", ("smtp.example.com", 587))
    assert kwargs == {"timeout": SEND_TIMEOUT_SECONDS}
    assert calls.login_args == ("mailer@example.com", PASSWORD)


def test_ssl_uses_smtp_ssl_with_a_verifying_context_and_no_starttls() -> None:
    calls = Calls()

    _smtp(calls, security="ssl", port=465).send(
        to="somchai@kmitl.ac.th", subject="Verify", body="hello"
    )

    assert calls.events == ["connect:SMTP_SSL", "login", "send_message", "quit"]
    (name, args, kwargs), *_ = calls.constructed
    assert (name, args) == ("SMTP_SSL", ("smtp.example.com", 465))
    assert kwargs["timeout"] == SEND_TIMEOUT_SECONDS
    context = kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_message_is_plain_text_utf8_with_thai_content_intact() -> None:
    calls = Calls()

    _smtp(calls, security="starttls", port=587).send(
        to="somchai@kmitl.ac.th", subject="ยืนยันอีเมล Questly", body=THAI_NAME_BODY
    )

    message = calls.message
    assert message is not None
    assert message["From"] == SENDER
    assert message["To"] == "somchai@kmitl.ac.th"
    assert message["Subject"] == "ยืนยันอีเมล Questly"
    assert message.get_content_type() == "text/plain"
    assert message.get_content_charset() == "utf-8"
    assert message.get_content() == THAI_NAME_BODY


@pytest.mark.parametrize("security", ["starttls", "ssl"])
@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPAuthenticationError(535, b"5.7.8 not accepted"),
        smtplib.SMTPRecipientsRefused({"a@b": (550, b"no")}),
        smtplib.SMTPServerDisconnected("gone"),
        ConnectionRefusedError("refused"),
        TimeoutError("timed out"),
        ssl.SSLError("handshake"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_every_library_failure_becomes_an_email_delivery_error(
    security: str, error: Exception
) -> None:
    calls = Calls()
    calls.fail_at = "login"
    calls.error = error

    with pytest.raises(EmailDeliveryError) as raised:
        _smtp(calls, security=security, port=587).send(
            to="somchai@kmitl.ac.th", subject="Verify", body="hello"
        )

    assert raised.value.__cause__ is error
    assert PASSWORD not in str(raised.value)
    assert "hello" not in str(raised.value)


@pytest.mark.parametrize("step", ["connect", "starttls", "send_message"])
def test_failure_at_any_step_is_mapped(step: str) -> None:
    calls = Calls()
    calls.fail_at = step

    with pytest.raises(EmailDeliveryError):
        _smtp(calls, security="starttls", port=587).send(
            to="somchai@kmitl.ac.th", subject="Verify", body="hello"
        )


def test_an_address_with_a_newline_is_refused_not_leaked_as_a_valueerror() -> None:
    calls = Calls()

    with pytest.raises(EmailDeliveryError):
        _smtp(calls, security="starttls", port=587).send(
            to="a@kmitl.ac.th\nBcc: victim@example.com", subject="Verify", body="x"
        )

    assert calls.events == []


def test_smtp_failure_does_not_log_the_body_or_the_password(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = Calls()
    calls.fail_at = "login"

    with caplog.at_level("DEBUG"), pytest.raises(EmailDeliveryError):
        _smtp(calls, security="starttls", port=587).send(
            to="somchai@kmitl.ac.th", subject="Verify", body="token=SECRETTOKEN"
        )

    assert "SECRETTOKEN" not in caplog.text
    assert PASSWORD not in caplog.text


def _resend(handler: httpx.MockTransport) -> ResendEmailSender:
    return ResendEmailSender(
        api_key=API_KEY, sender=SENDER, client=httpx.Client(transport=handler)
    )


def test_resend_posts_the_documented_request() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "email_1"})

    _resend(httpx.MockTransport(handle)).send(
        to="somchai@kmitl.ac.th", subject="ยืนยันอีเมล", body=THAI_NAME_BODY
    )

    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == RESEND_EMAILS_URL == "https://api.resend.com/emails"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "from": SENDER,
        "to": ["somchai@kmitl.ac.th"],
        "subject": "ยืนยันอีเมล",
        "text": THAI_NAME_BODY,
    }


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, {"name": "restricted_api_key", "message": "API key is invalid"}),
        (422, {"name": "validation_error", "message": "Invalid `from` field"}),
        (429, {"name": "rate_limit_exceeded", "message": "Too many requests"}),
        (500, {"name": "internal_server_error", "message": "Try again"}),
    ],
)
def test_resend_error_status_becomes_an_error_with_the_providers_reason(
    status: int, body: dict[str, str]
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))

    with pytest.raises(EmailDeliveryError) as raised:
        _resend(transport).send(to="a@kmitl.ac.th", subject="s", body="b")

    message = str(raised.value)
    assert str(status) in message
    assert body["name"] in message
    assert body["message"] in message
    assert API_KEY not in message


def test_resend_error_without_a_json_body_still_reports_the_status() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(502, text="<html>bad gateway</html>")
    )

    with pytest.raises(EmailDeliveryError, match="502"):
        _resend(transport).send(to="a@kmitl.ac.th", subject="s", body="b")


def test_resend_transport_failure_becomes_an_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(EmailDeliveryError) as raised:
        _resend(httpx.MockTransport(refuse)).send(
            to="a@kmitl.ac.th", subject="s", body="b"
        )

    assert isinstance(raised.value.__cause__, httpx.ConnectTimeout)
    assert API_KEY not in str(raised.value)


def test_resend_default_client_uses_the_send_timeout() -> None:
    sender = ResendEmailSender(api_key=API_KEY, sender=SENDER)

    assert sender._client.timeout == httpx.Timeout(SEND_TIMEOUT_SECONDS)


def _settings(**override: object) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        r2_endpoint_url="https://unused.r2.cloudflarestorage.com",
        r2_bucket_name="unused",
        r2_access_key_id="unused",
        r2_secret_access_key="unused",
        **override,
    )


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({}, StubEmailSender),
        ({"email_backend": "stub"}, StubEmailSender),
        (
            {
                "email_backend": "smtp",
                "email_from": SENDER,
                "smtp_host": "smtp.example.com",
                "smtp_username": "u",
                "smtp_password": PASSWORD,
            },
            SmtpEmailSender,
        ),
        (
            {
                "email_backend": "resend",
                "email_from": SENDER,
                "resend_api_key": API_KEY,
            },
            ResendEmailSender,
        ),
    ],
)
def test_main_builds_the_adapter_the_backend_names(
    override: dict[str, str], expected: type
) -> None:
    from questly.main import _build_email_sender

    assert type(_build_email_sender(_settings(**override))) is expected
