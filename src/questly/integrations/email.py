"""Email adapters: the stub that logs, and SMTP and Resend for real delivery.

Every adapter satisfies the EmailSender port and raises only
EmailDeliveryError. Messages are plain text in UTF-8, each send has a 10 second
timeout and nothing retries. The body holds the verification token, so only the
stub logs it (scripts/demo.py relies on that); the real adapters never log a
body, a password or an API key.
"""

import logging
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from types import TracebackType
from typing import Protocol, Self

import httpx

from questly.core.notifications.ports import EmailDeliveryError

logger = logging.getLogger("questly.email")

SEND_TIMEOUT_SECONDS = 10
RESEND_EMAILS_URL = "https://api.resend.com/emails"


@dataclass(frozen=True, slots=True)
class SentEmail:
    to: str
    subject: str
    body: str


class StubEmailSender:
    """Capture outgoing emails without requiring a mail server.

    The whole body is logged so a verification link can be followed in demo
    mode (scripts/demo.py promises that).
    """

    def __init__(self) -> None:
        self.messages: list[SentEmail] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        """Record an email sent through the notification email port."""
        self.messages.append(SentEmail(to=to, subject=subject, body=body))
        logger.warning("email to %s: %s\n%s", to, subject, body)


class SmtpConnection(Protocol):
    """The slice of smtplib.SMTP and smtplib.SMTP_SSL that SmtpEmailSender uses."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> object: ...

    def starttls(self, *, context: ssl.SSLContext) -> object: ...

    def login(self, user: str, password: str) -> object: ...

    def send_message(self, msg: EmailMessage) -> object: ...


SmtpFactory = Callable[..., SmtpConnection]


class SmtpEmailSender:
    """Send through an SMTP server with `ssl` or `starttls` and a login."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        security: str,
        smtp_factory: SmtpFactory = smtplib.SMTP,
        smtp_ssl_factory: SmtpFactory = smtplib.SMTP_SSL,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._security = security
        self._smtp_factory = smtp_factory
        self._smtp_ssl_factory = smtp_ssl_factory

    def send(self, *, to: str, subject: str, body: str) -> None:
        try:
            message = EmailMessage()
            message["From"] = self._sender
            message["To"] = to
            message["Subject"] = subject
            message.set_content(body, charset="utf-8")

            context = ssl.create_default_context()
            if self._security == "ssl":
                connection = self._smtp_ssl_factory(
                    self._host,
                    self._port,
                    timeout=SEND_TIMEOUT_SECONDS,
                    context=context,
                )
            else:
                connection = self._smtp_factory(
                    self._host, self._port, timeout=SEND_TIMEOUT_SECONDS
                )
            with connection as server:
                if self._security != "ssl":
                    server.starttls(context=context)
                server.login(self._username, self._password)
                server.send_message(message)
        # smtplib.SMTPException and ssl.SSLError are OSError subclasses, as are
        # timeouts and refused connections. ValueError is a header the message
        # builder refuses, such as an address holding a newline.
        except (OSError, ValueError) as exc:
            raise EmailDeliveryError(
                f"SMTP delivery via {self._host}:{self._port} failed "
                f"({type(exc).__name__})"
            ) from exc


class ResendEmailSender:
    """Send through the Resend REST API."""

    def __init__(
        self,
        *,
        api_key: str,
        sender: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._sender = sender
        if client is None:
            client = httpx.Client(timeout=SEND_TIMEOUT_SECONDS)
        self._client = client

    def send(self, *, to: str, subject: str, body: str) -> None:
        try:
            response = self._client.post(
                RESEND_EMAILS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "from": self._sender,
                    "to": [to],
                    "subject": subject,
                    "text": body,
                },
            )
        except httpx.HTTPError as exc:
            raise EmailDeliveryError(
                f"Resend request failed ({type(exc).__name__})"
            ) from exc
        if not response.is_success:
            raise EmailDeliveryError(
                f"Resend rejected the email: HTTP {response.status_code}"
                f"{_resend_error_detail(response)}"
            )


def _resend_error_detail(response: httpx.Response) -> str:
    """Resend's `name` and `message` from an error body, as ": name: message"."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    parts = [
        value
        for value in (payload.get("name"), payload.get("message"))
        if isinstance(value, str) and value
    ]
    return f": {': '.join(parts)}" if parts else ""
