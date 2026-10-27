"""HTTP tests for the notifications API and T-02c "Notify students", per role."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    Schedule,
    TestCase,
)
from questly.core.auth.models import Actor, Role
from questly.core.notifications.ports import EmailSender
from questly.integrations.email import StubEmailSender
from tests.fakes.app import build_app, fake_settings
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, FakeClock, seed_user

DEADLINE = datetime(2026, 10, 15, 16, 59, tzinfo=UTC)
TEACHER = "somchai.p@kmitl.ac.th"
OTHER_TEACHER = "warunee.k@kmitl.ac.th"
STUDENT = "66010001@kmitl.ac.th"
CLASSMATE = "66010002@kmitl.ac.th"
OUTSIDER = "66010003@kmitl.ac.th"
ADMIN = "admin@kmitl.ac.th"


class Campus:
    """Somchai publishes one problem to Sec 1, where two Students are Members."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        bulk_email_sender: EmailSender | None = None,
    ) -> None:
        users = FakeAuthRepository()
        accounts = {}
        for email, name, role in [
            (TEACHER, "Somchai Prasert", Role.INSTRUCTOR),
            (OTHER_TEACHER, "Warunee Kaew", Role.INSTRUCTOR),
            (STUDENT, "Nattapong Suwan", Role.STUDENT),
            (CLASSMATE, "Pimchanok Wong", Role.STUDENT),
            (OUTSIDER, "Kittipong Dee", Role.STUDENT),
            (ADMIN, "Admin", Role.ADMIN),
        ]:
            user = seed_user(users, email=email, full_name=name, role=role)
            accounts[email] = Actor(
                user_id=user.id, role=role, full_name=name, email=email
            )
        self.mailer = StubEmailSender()
        self.clock = FakeClock()
        self.app = build_app(
            settings=replace(fake_settings(), base_url=base_url),
            auth_repository=users,
            verification_mailer=self.mailer,
            bulk_email_sender=bulk_email_sender,
            clock=self.clock,
        )
        classrooms = self.app.state.classroom_service
        self.classroom = classrooms.create(
            accounts[TEACHER],
            course_code="01076001",
            course_name="Programming I",
            section="1",
            semester="1/2569",
        )
        classrooms.join(accounts[STUDENT], self.classroom.join_code)
        classrooms.join(accounts[CLASSMATE], self.classroom.join_code)
        published = self.app.state.assignment_service.publish(
            accounts[TEACHER],
            AssignmentContent(
                title="Binary search",
                problem_statement="Print the index of x, or -1.",
                difficulty=Difficulty.MEDIUM,
                test_cases=[TestCase(input_data="1\n4\n4", expected_output="0")],
            ),
            Schedule(deadline=DEADLINE),
            [self.classroom.id],
        )
        self.assignment_id = published.assignment.id

    async def client_for(self, email: str) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver"
        )
        await client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        )
        return client

    @property
    def notify_path(self) -> str:
        return (
            f"/api/v1/classrooms/{self.classroom.id}"
            f"/assignments/{self.assignment_id}/notify"
        )


@pytest.mark.anyio
async def test_publishing_shows_up_in_each_members_list_only() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)
    outsider = await campus.client_for(OUTSIDER)
    teacher = await campus.client_for(TEACHER)

    listed = await student.get("/api/v1/notifications")

    (item,) = listed.json()["items"]
    assert item["kind"] == "assignment_published"
    assert item["payload"] == {
        "classroom_id": campus.classroom.id,
        "assignment_id": campus.assignment_id,
        "title": "Binary search",
    }
    assert item["read_at"] is None
    assert (await outsider.get("/api/v1/notifications")).json()["items"] == []
    assert (await teacher.get("/api/v1/notifications")).json()["items"] == []


@pytest.mark.anyio
async def test_listing_needs_a_login() -> None:
    campus = Campus()
    async with AsyncClient(
        transport=ASGITransport(app=campus.app), base_url="http://testserver"
    ) as anonymous:
        response = await anonymous.get("/api/v1/notifications")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_mark_read_is_404_for_someone_elses_or_a_missing_notification() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)
    classmate = await campus.client_for(CLASSMATE)
    (theirs,) = (await classmate.get("/api/v1/notifications")).json()["items"]

    stolen = await student.post(f"/api/v1/notifications/{theirs['id']}/read")
    missing = await student.post("/api/v1/notifications/999999/read")

    assert stolen.status_code == 404
    assert missing.status_code == 404
    (still,) = (await classmate.get("/api/v1/notifications")).json()["items"]
    assert still["read_at"] is None


@pytest.mark.anyio
async def test_mark_read_sets_read_at_on_your_own() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)
    (own,) = (await student.get("/api/v1/notifications")).json()["items"]

    marked = await student.post(f"/api/v1/notifications/{own['id']}/read")

    assert marked.status_code == 200
    assert marked.json()["read_at"] is not None


@pytest.mark.anyio
async def test_read_all_touches_only_your_own() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)
    classmate = await campus.client_for(CLASSMATE)

    first = await student.post("/api/v1/notifications/read-all")
    again = await student.post("/api/v1/notifications/read-all")

    assert first.json() == {"updated_count": 1}
    assert again.json() == {"updated_count": 0}
    (theirs,) = (await classmate.get("/api/v1/notifications")).json()["items"]
    assert theirs["read_at"] is None


@pytest.mark.anyio
async def test_notify_students_reaches_every_member_and_emails_them() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    campus.mailer.messages.clear()

    response = await teacher.post(campus.notify_path)

    assert response.status_code == 204
    for email in (STUDENT, CLASSMATE):
        member = await campus.client_for(email)
        kinds = [
            item["kind"]
            for item in (await member.get("/api/v1/notifications")).json()["items"]
        ]
        assert kinds == ["assignment_notified", "assignment_published"]
    assert sorted(m.to for m in campus.mailer.messages) == [STUDENT, CLASSMATE]
    assert campus.mailer.messages[0].subject == "Reminder: Binary search"


@pytest.mark.anyio
async def test_notification_emails_link_to_the_configured_origin() -> None:
    campus = Campus(base_url="https://questly.example.com")
    teacher = await campus.client_for(TEACHER)
    published = list(campus.mailer.messages)
    campus.mailer.messages.clear()

    await teacher.post(campus.notify_path)

    link = (
        f"https://questly.example.com/classes/{campus.classroom.id}"
        f"/assignments/{campus.assignment_id}"
    )
    reminders = campus.mailer.messages
    assert len(published) == len(reminders) == 2
    for message in [*published, *reminders]:
        assert message.body.splitlines()[-1] == link


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("email", "expected"),
    [(STUDENT, 403), (OUTSIDER, 404), (OTHER_TEACHER, 404), (ADMIN, 404)],
)
async def test_notify_students_is_only_for_the_classrooms_instructor(
    email: str, expected: int
) -> None:
    campus = Campus()
    caller = await campus.client_for(email)

    response = await caller.post(campus.notify_path)

    assert response.status_code == expected
    member = await campus.client_for(CLASSMATE)
    kinds = [
        item["kind"]
        for item in (await member.get("/api/v1/notifications")).json()["items"]
    ]
    assert kinds == ["assignment_published"]


@pytest.mark.anyio
async def test_notify_students_for_an_unposted_assignment_is_404() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)

    response = await teacher.post(
        f"/api/v1/classrooms/{campus.classroom.id}/assignments/999999/notify"
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_list_carries_deadline_tomorrow_for_a_student_only() -> None:
    campus = Campus()
    campus.clock.current = DEADLINE - timedelta(hours=2)
    student = await campus.client_for(STUDENT)
    teacher = await campus.client_for(TEACHER)

    listed = await student.get("/api/v1/notifications")

    assert listed.json()["due_soon"] == [
        {
            "classroom_id": campus.classroom.id,
            "assignment_id": campus.assignment_id,
            "title": "Binary search",
            "deadline": "2026-10-15T16:59:00Z",
        }
    ]
    assert (await teacher.get("/api/v1/notifications")).json()["due_soon"] == []


@pytest.mark.anyio
async def test_deadline_a_week_away_is_not_due_soon() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)

    listed = await student.get("/api/v1/notifications")

    assert listed.json()["due_soon"] == []
