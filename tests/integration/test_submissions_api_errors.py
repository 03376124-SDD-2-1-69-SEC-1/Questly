"""Characterise the error bodies of the Submissions JSON routes."""

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
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, FakeClock, seed_user
from tests.fakes.submissions import ScriptedCodeRunner

DEADLINE = datetime(2027, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 10, 21, tzinfo=UTC)
TEACHER = "teacher@kmitl.ac.th"
STUDENT = "66010001@kmitl.ac.th"
OUTSIDER = "66010002@kmitl.ac.th"
CODE = "print(3)"


def error(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"detail": {"code": code, "message": message}}


NOT_FOUND = error("not_found", "Not found")


class Campus:
    """A Classroom with one problem; the Student is a Member, the outsider is not."""

    def __init__(self, *, allow_resubmission: bool = True) -> None:
        users = FakeAuthRepository()
        self.accounts = {}
        for email, name, role in [
            (TEACHER, "Teacher Somchai", Role.INSTRUCTOR),
            (STUDENT, "Student Nattapong", Role.STUDENT),
            (OUTSIDER, "Outsider Student", Role.STUDENT),
        ]:
            user = seed_user(users, email=email, full_name=name, role=role)
            self.accounts[email] = Actor(
                user_id=user.id, role=role, full_name=name, email=email
            )
        self.clock = FakeClock(NOW)
        self.runner = ScriptedCodeRunner({"1 2": "3"})
        self.app = build_app(
            auth_repository=users, code_runner=self.runner, clock=self.clock
        )
        self.classrooms = self.app.state.classroom_service
        self.classroom = self.classrooms.create(
            self.accounts[TEACHER],
            course_code="01076001",
            course_name="Programming I",
            section="1",
            semester="1/2569",
        )
        self.classrooms.join(self.accounts[STUDENT], self.classroom.join_code)
        published = self.app.state.assignment_service.publish(
            self.accounts[TEACHER],
            AssignmentContent(
                title="Add two numbers",
                problem_statement="Print the sum of two integers.",
                difficulty=Difficulty.EASY,
                test_cases=[TestCase(input_data="1 2", expected_output="3")],
            ),
            Schedule(deadline=DEADLINE, allow_resubmission=allow_resubmission),
            [self.classroom.id],
        )
        self.assignment_id = published.assignment.id
        self.base = f"/api/v1/classrooms/{self.classroom.id}"
        self.path = f"{self.base}/assignments/{self.assignment_id}"

    async def client_for(self, email: str) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver"
        )
        await client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        )
        return client

    def receive(self, email: str) -> int:
        """Submit without judging: the Submission stays pending."""
        return (
            self.app.state.submission_service.submit(
                self.accounts[email], self.classroom.id, self.assignment_id, CODE
            )
        ).id


@pytest.mark.anyio
async def test_a_classroom_the_actor_cannot_see_is_404_on_every_route() -> None:
    campus = Campus()
    outsider = await campus.client_for(OUTSIDER)
    body = {"code": CODE}

    responses = [
        await outsider.post(f"{campus.path}/submissions", json=body),
        await outsider.get(f"{campus.path}/submissions"),
        await outsider.get(f"{campus.path}/submissions.csv"),
        await outsider.post(f"{campus.path}/runs", json=body),
    ]

    assert [r.status_code for r in responses] == [404] * 4
    assert [r.json() for r in responses] == [NOT_FOUND] * 4


@pytest.mark.anyio
async def test_an_unknown_assignment_or_classroom_is_404() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)
    body = {"code": CODE}

    responses = [
        await student.post(f"{campus.base}/assignments/999/submissions", json=body),
        await student.get(f"{campus.base}/assignments/999/submissions"),
        await student.post(f"{campus.base}/assignments/999/runs", json=body),
        await student.post(
            f"/api/v1/classrooms/999/assignments/{campus.assignment_id}/runs",
            json=body,
        ),
    ]

    assert [r.status_code for r in responses] == [404] * 4
    assert [r.json() for r in responses] == [NOT_FOUND] * 4


@pytest.mark.anyio
async def test_an_unknown_submission_is_404() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)

    response = await student.get("/api/v1/submissions/999")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.anyio
async def test_someone_elses_submission_looks_like_a_missing_one() -> None:
    campus = Campus()
    submission_id = campus.receive(STUDENT)
    outsider = await campus.client_for(OUTSIDER)

    response = await outsider.get(f"/api/v1/submissions/{submission_id}")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.anyio
async def test_an_archived_classroom_is_409_on_submit_and_run() -> None:
    campus = Campus()
    campus.classrooms.archive(campus.accounts[TEACHER], campus.classroom.id)
    student = await campus.client_for(STUDENT)
    body = {"code": CODE}

    submit = await student.post(f"{campus.path}/submissions", json=body)
    run = await student.post(f"{campus.path}/runs", json=body)

    expected = error("classroom_archived", "Classroom is archived")
    assert (submit.status_code, run.status_code) == (409, 409)
    assert [submit.json(), run.json()] == [expected] * 2


@pytest.mark.anyio
async def test_a_closed_posting_is_409_on_submit_and_run() -> None:
    campus = Campus()
    campus.clock.advance(DEADLINE - NOW + timedelta(days=1))
    student = await campus.client_for(STUDENT)
    body = {"code": CODE}

    submit = await student.post(f"{campus.path}/submissions", json=body)
    run = await student.post(f"{campus.path}/runs", json=body)

    expected = error("posting_closed", "Submissions are closed")
    assert (submit.status_code, run.status_code) == (409, 409)
    assert [submit.json(), run.json()] == [expected] * 2


@pytest.mark.anyio
async def test_a_second_submission_on_a_one_shot_problem_is_409() -> None:
    campus = Campus(allow_resubmission=False)
    student = await campus.client_for(STUDENT)
    first = await student.post(f"{campus.path}/submissions", json={"code": CODE})

    second = await student.post(f"{campus.path}/submissions", json={"code": CODE})

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json() == error(
        "resubmission_not_allowed", "This problem allows one submission only"
    )


@pytest.mark.anyio
async def test_a_submission_still_being_judged_blocks_submit_and_run_with_409() -> None:
    campus = Campus()
    campus.receive(STUDENT)
    student = await campus.client_for(STUDENT)
    body = {"code": CODE}

    submit = await student.post(f"{campus.path}/submissions", json=body)
    run = await student.post(f"{campus.path}/runs", json=body)

    expected = error("submission_pending", "Your last submission is still being judged")
    assert (submit.status_code, run.status_code) == (409, 409)
    assert [submit.json(), run.json()] == [expected] * 2


@pytest.mark.anyio
async def test_empty_code_is_422_on_submit_and_run() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)
    body = {"code": "  \n"}

    submit = await student.post(f"{campus.path}/submissions", json=body)
    run = await student.post(f"{campus.path}/runs", json=body)

    expected = error("empty_code", "Code is empty")
    assert (submit.status_code, run.status_code) == (422, 422)
    assert [submit.json(), run.json()] == [expected] * 2


@pytest.mark.anyio
async def test_an_unreachable_runner_is_503_on_run() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)

    response = await student.post(
        f"{campus.path}/runs", json={"code": "print(1)  # judge down"}
    )

    assert response.status_code == 503
    assert response.json() == error(
        "runner_unavailable",
        "The code runner is unavailable; nothing was saved. Try again shortly.",
    )


@pytest.mark.anyio
async def test_a_member_asking_for_the_export_is_403() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)

    response = await student.get(f"{campus.path}/submissions.csv")

    assert response.status_code == 403
    assert response.json() == error("forbidden", "Not allowed")
