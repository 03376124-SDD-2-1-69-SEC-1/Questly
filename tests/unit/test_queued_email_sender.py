"""QueuedEmailSender: off-request delivery that survives failures (OPS-44)."""

import logging
import threading

import pytest

from questly.core.notifications.ports import EmailDeliveryError
from questly.integrations.email import QueuedEmailSender, SentEmail

BODY = "Hi Nattapong, https://questly.example.com/classes/7/assignments/3"


class Recorder:
    """An inner sender that records, and fails for the addresses it is told to."""

    def __init__(
        self,
        *,
        refuse: frozenset[str] = frozenset(),
        crash: frozenset[str] = frozenset(),
    ) -> None:
        self.refuse = refuse
        self.crash = crash
        self.delivered: list[SentEmail] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        if to in self.refuse:
            raise EmailDeliveryError(f"mailbox refused: {body}")
        if to in self.crash:
            raise RuntimeError(f"bug while sending {body}")
        self.delivered.append(SentEmail(to=to, subject=subject, body=body))


class Blocking:
    """An inner sender that holds every send until `release` is set."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()
        self.delivered: list[str] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.entered.set()
        assert self.release.wait(timeout=5)
        self.delivered.append(to)


def _started(inner: Recorder | Blocking) -> QueuedEmailSender:
    sender = QueuedEmailSender(inner)
    sender.start()
    return sender


def test_delivers_every_email_in_order() -> None:
    inner = Recorder()
    sender = _started(inner)

    for number in range(5):
        sender.send(to=f"s{number}@kmitl.ac.th", subject=f"#{number}", body=BODY)
    sender.wait_until_idle()

    assert [email.subject for email in inner.delivered] == [
        f"#{number}" for number in range(5)
    ]


def test_nothing_is_delivered_before_start() -> None:
    inner = Recorder()
    sender = QueuedEmailSender(inner)
    sender.send(to="a@kmitl.ac.th", subject="Early", body=BODY)

    sender.start()
    sender.start()
    sender.wait_until_idle()

    assert [email.subject for email in inner.delivered] == ["Early"]


def test_a_refused_email_is_logged_and_the_next_one_still_goes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inner = Recorder(refuse=frozenset({"refused@gmail.com"}))
    sender = _started(inner)

    with caplog.at_level(logging.WARNING, logger="questly.email"):
        sender.send(to="refused@gmail.com", subject="New problem: Sort", body=BODY)
        sender.send(to="ok@kmitl.ac.th", subject="New problem: Sort", body=BODY)
        sender.wait_until_idle()

    assert [email.to for email in inner.delivered] == ["ok@kmitl.ac.th"]
    assert "@gmail.com" in caplog.text
    assert "New problem: Sort" in caplog.text


def test_an_unexpected_error_does_not_stop_the_worker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inner = Recorder(crash=frozenset({"crash@kmitl.ac.th"}))
    sender = _started(inner)

    with caplog.at_level(logging.WARNING, logger="questly.email"):
        sender.send(to="crash@kmitl.ac.th", subject="Reminder: Sort", body=BODY)
        sender.send(to="ok@kmitl.ac.th", subject="Reminder: Sort", body=BODY)
        sender.wait_until_idle()

    assert [email.to for email in inner.delivered] == ["ok@kmitl.ac.th"]
    assert "RuntimeError" in caplog.text


def test_failures_never_log_the_body_or_the_full_address(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inner = Recorder(
        refuse=frozenset({"refused@kmitl.ac.th"}),
        crash=frozenset({"crash@kmitl.ac.th"}),
    )
    sender = _started(inner)

    with caplog.at_level(logging.DEBUG):
        sender.send(to="refused@kmitl.ac.th", subject="S", body=BODY)
        sender.send(to="crash@kmitl.ac.th", subject="S", body=BODY)
        sender.wait_until_idle()

    assert len(caplog.records) == 2
    for record in caplog.records:
        assert BODY not in record.getMessage()
        assert record.exc_info is None
        assert "refused@" not in record.getMessage()
        assert "crash@" not in record.getMessage()


def test_send_returns_while_the_inner_sender_is_stuck() -> None:
    inner = Blocking()
    sender = _started(inner)
    sender.send(to="first@kmitl.ac.th", subject="S", body=BODY)
    assert inner.entered.wait(timeout=5)

    sender.send(to="second@kmitl.ac.th", subject="S", body=BODY)

    assert inner.delivered == []
    inner.release.set()
    sender.wait_until_idle()
    assert inner.delivered == ["first@kmitl.ac.th", "second@kmitl.ac.th"]
