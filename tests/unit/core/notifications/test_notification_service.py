"""Unit tests for NotificationService."""

from datetime import UTC, datetime

import pytest

from questly.core.classrooms.models import Membership
from questly.core.notifications.models import Notification, NotificationKind
from questly.core.notifications.service import (
    NotificationNotFoundError,
    NotificationService,
)
from questly.integrations.email import StubEmailSender
from tests.fakes.auth import FakeAuthRepository, seed_user
from tests.fakes.classrooms import FakeClassroomRepository
from tests.fakes.notifications import FakeNotificationRepository


class FixedClock:
    """Return a predictable time."""

    def now(self) -> datetime:
        return datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


@pytest.fixture
def repository() -> FakeNotificationRepository:
    return FakeNotificationRepository()


@pytest.fixture
def mailer() -> StubEmailSender:
    return StubEmailSender()


@pytest.fixture
def classrooms() -> FakeClassroomRepository:
    return FakeClassroomRepository()


@pytest.fixture
def users() -> FakeAuthRepository:
    return FakeAuthRepository()


@pytest.fixture
def service(
    repository: FakeNotificationRepository,
    mailer: StubEmailSender,
    classrooms: FakeClassroomRepository,
    users: FakeAuthRepository,
) -> NotificationService:
    return NotificationService(
        repository=repository,
        email_sender=mailer,
        clock=FixedClock(),
        members=classrooms,
        accounts=users,
        base_url="https://questly.example.com",
    )


def add_notification(
    repository: FakeNotificationRepository,
    *,
    user_id: int,
) -> Notification:
    return repository.create(
        Notification(
            user_id=user_id,
            kind=NotificationKind.ASSIGNMENT_PUBLISHED,
            payload={"title": "Homework 1"},
            created_at=datetime(2026, 10, 10, 5, 0, tzinfo=UTC),
        )
    )


def test_list_for_user_returns_only_own_notifications(
    service: NotificationService,
    repository: FakeNotificationRepository,
) -> None:
    own = add_notification(repository, user_id=1)
    add_notification(repository, user_id=2)

    assert service.list_for_user(1) == [own]


def test_mark_read_marks_own_notification(
    service: NotificationService,
    repository: FakeNotificationRepository,
) -> None:
    notification = add_notification(repository, user_id=1)

    result = service.mark_read(1, notification.id or 0)

    assert result.read_at == FixedClock().now()


def test_mark_read_rejects_another_users_notification(
    service: NotificationService,
    repository: FakeNotificationRepository,
) -> None:
    notification = add_notification(repository, user_id=2)

    with pytest.raises(NotificationNotFoundError):
        service.mark_read(1, notification.id or 0)


def test_mark_all_read_only_affects_own_notifications(
    service: NotificationService,
    repository: FakeNotificationRepository,
) -> None:
    own = add_notification(repository, user_id=1)
    other = add_notification(repository, user_id=2)

    assert service.mark_all_read(1) == 1
    assert repository.get(own.id or 0).read_at == FixedClock().now()
    assert repository.get(other.id or 0).read_at is None


def test_create_uses_clock_time(
    service: NotificationService,
) -> None:
    result = service.create(
        user_id=1,
        kind=NotificationKind.ASSIGNMENT_UPDATED,
        payload={"title": "Homework 1", "reason": "Deadline changed"},
    )

    assert result.id is not None
    assert result.created_at == FixedClock().now()
    assert result.user_id == 1
    assert result.kind is NotificationKind.ASSIGNMENT_UPDATED


def _member(
    users: FakeAuthRepository,
    classrooms: FakeClassroomRepository,
    classroom_id: int,
    email: str,
    name: str,
) -> int:
    user = seed_user(users, email=email, full_name=name)
    classrooms.add_membership(
        Membership(
            classroom_id=classroom_id, student_id=user.id, joined_at=FixedClock().now()
        )
    )
    return user.id


PAYLOAD = {"classroom_id": 7, "assignment_id": 3, "title": "Binary search"}


def test_notify_members_stores_a_row_per_member_of_that_classroom_only(
    service: NotificationService,
    repository: FakeNotificationRepository,
    users: FakeAuthRepository,
    classrooms: FakeClassroomRepository,
) -> None:
    member = _member(users, classrooms, 7, "66010001@kmitl.ac.th", "Nattapong")
    elsewhere = _member(users, classrooms, 8, "66010002@kmitl.ac.th", "Pimchanok")

    service.notify_members(7, NotificationKind.ASSIGNMENT_PUBLISHED, PAYLOAD)

    (stored,) = repository.list_for_user(member)
    assert stored.kind is NotificationKind.ASSIGNMENT_PUBLISHED
    assert stored.payload == PAYLOAD
    assert stored.created_at == FixedClock().now()
    assert repository.list_for_user(elsewhere) == []


def test_notify_members_emails_each_member_with_a_link_to_the_problem(
    service: NotificationService,
    mailer: StubEmailSender,
    users: FakeAuthRepository,
    classrooms: FakeClassroomRepository,
) -> None:
    _member(users, classrooms, 7, "66010001@kmitl.ac.th", "Nattapong")
    _member(users, classrooms, 7, "66010002@kmitl.ac.th", "Pimchanok")

    service.notify_members(
        7,
        NotificationKind.ASSIGNMENT_UPDATED,
        {**PAYLOAD, "reason": "Test 4 expected the wrong output"},
    )

    assert [m.to for m in mailer.messages] == [
        "66010001@kmitl.ac.th",
        "66010002@kmitl.ac.th",
    ]
    first = mailer.messages[0]
    assert first.subject == "Problem updated: Binary search"
    assert "Hi Nattapong," in first.body
    assert "Test 4 expected the wrong output" in first.body
    assert "https://questly.example.com/classes/7/assignments/3" in first.body


def test_notify_members_of_an_empty_classroom_stores_and_sends_nothing(
    service: NotificationService,
    repository: FakeNotificationRepository,
    mailer: StubEmailSender,
) -> None:
    service.notify_members(7, NotificationKind.ASSIGNMENT_NOTIFIED, PAYLOAD)

    assert repository.list_for_user(1) == []
    assert mailer.messages == []


def test_member_emails_use_the_bulk_sender_and_direct_mail_does_not(
    repository: FakeNotificationRepository,
    mailer: StubEmailSender,
    users: FakeAuthRepository,
    classrooms: FakeClassroomRepository,
) -> None:
    bulk = StubEmailSender()
    service = NotificationService(
        repository=repository,
        email_sender=mailer,
        clock=FixedClock(),
        members=classrooms,
        accounts=users,
        base_url="https://questly.example.com",
        bulk_email_sender=bulk,
    )
    _member(users, classrooms, 7, "66010001@kmitl.ac.th", "Nattapong")

    service.notify_members(7, NotificationKind.ASSIGNMENT_PUBLISHED, PAYLOAD)
    service.send_email(to="new@kmitl.ac.th", subject="Verify", body="link")

    assert [m.to for m in bulk.messages] == ["66010001@kmitl.ac.th"]
    assert [m.to for m in mailer.messages] == ["new@kmitl.ac.th"]


def test_without_a_bulk_sender_member_emails_use_the_email_sender(
    service: NotificationService,
    mailer: StubEmailSender,
    users: FakeAuthRepository,
    classrooms: FakeClassroomRepository,
) -> None:
    _member(users, classrooms, 7, "66010001@kmitl.ac.th", "Nattapong")

    service.notify_members(7, NotificationKind.ASSIGNMENT_PUBLISHED, PAYLOAD)

    assert [m.to for m in mailer.messages] == ["66010001@kmitl.ac.th"]
