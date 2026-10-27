"""Publishing and "Notify students" never wait on member emails (OPS-44)."""

import logging
import threading

import pytest

from questly.core.notifications.ports import EmailDeliveryError
from questly.integrations.email import QueuedEmailSender
from tests.integration.test_notifications_api import (
    CLASSMATE,
    STUDENT,
    TEACHER,
    Campus,
)


class StuckProvider:
    """A mail provider that hangs until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.delivered: list[str] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        assert self.release.wait(timeout=5)
        self.delivered.append(f"{to} {subject}")


class RefusingProvider:
    def send(self, *, to: str, subject: str, body: str) -> None:
        raise EmailDeliveryError("provider unreachable")


async def _kinds(campus: Campus, email: str) -> list[str]:
    member = await campus.client_for(email)
    response = await member.get("/api/v1/notifications")
    return [item["kind"] for item in response.json()["items"]]


@pytest.mark.anyio
async def test_publish_and_notify_return_while_the_provider_hangs() -> None:
    provider = StuckProvider()
    queued = QueuedEmailSender(provider)
    queued.start()
    # Campus publishes to a Classroom of two Students while the provider hangs.
    campus = Campus(bulk_email_sender=queued)
    teacher = await campus.client_for(TEACHER)

    response = await teacher.post(campus.notify_path)

    assert response.status_code == 204
    assert provider.delivered == []
    for email in (STUDENT, CLASSMATE):
        assert await _kinds(campus, email) == [
            "assignment_notified",
            "assignment_published",
        ]
    provider.release.set()
    queued.wait_until_idle()
    assert provider.delivered == [
        f"{STUDENT} New problem: Binary search",
        f"{CLASSMATE} New problem: Binary search",
        f"{STUDENT} Reminder: Binary search",
        f"{CLASSMATE} Reminder: Binary search",
    ]


@pytest.mark.anyio
async def test_a_failing_provider_keeps_every_notification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    queued = QueuedEmailSender(RefusingProvider())
    queued.start()
    with caplog.at_level(logging.WARNING, logger="questly.email"):
        campus = Campus(bulk_email_sender=queued)
        teacher = await campus.client_for(TEACHER)
        response = await teacher.post(campus.notify_path)
        queued.wait_until_idle()

    assert response.status_code == 204
    for email in (STUDENT, CLASSMATE):
        assert await _kinds(campus, email) == [
            "assignment_notified",
            "assignment_published",
        ]
    assert caplog.text.count("not sent") == 4
    assert "/classes/" not in caplog.text
