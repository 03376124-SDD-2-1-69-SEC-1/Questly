"""Integration tests for classroom CSV export endpoints."""

import csv
import io
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    Schedule,
    TestCase,
)
from questly.core.auth.models import Actor, Role
from questly.core.submissions.service import GIVE_UP
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, FakeClock, seed_user
from tests.fakes.submissions import ScriptedCodeRunner

DEADLINE = datetime(2027, 1, 1, tzinfo=UTC)
# 18:30:05 UTC is 01:30:05 the next day in Bangkok (UTC+7).
NOW = datetime(2026, 10, 21, 18, 30, 5, tzinfo=UTC)
NOW_BANGKOK = "2026-10-22 01:30:05"
GOOD_CODE = "print(sum(map(int, input().split())))"
TEACHER_EMAIL = "teacher@kmitl.ac.th"
STUDENT_EMAIL = "66010001@kmitl.ac.th"
LECTURER_EMAIL = "visiting.lecturer@kmitl.ac.th"
HYPERLINK_EMAIL = "66010003@kmitl.ac.th"
HYPERLINK_NAME = '=HYPERLINK("http://x")'
OUTSIDER_EMAIL = "outsider@kmitl.ac.th"

SUMMARY_HEADER = ["Student Number", "Student Name", "Add two numbers", "Total"]
SUBMISSIONS_HEADER = [
    "Problem No.",
    "Assignment",
    "Submission ID",
    "Student Number",
    "Student Name",
    "Attempt",
    "Version",
    "Submitted At (Bangkok)",
    "Status",
    "Score",
    "Max Score",
    "Passed Tests",
    "Total Tests",
    "Late",
]


def parse_csv(content: bytes) -> list[list[str]]:
    assert content.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))


class ExportCampus:
    """Classroom with one assignment and several Students enrolled."""

    def __init__(self) -> None:
        users = FakeAuthRepository()
        self.accounts = {}
        for email, name, role in [
            (TEACHER_EMAIL, "Teacher Somchai", Role.INSTRUCTOR),
            (STUDENT_EMAIL, "Student Nattapong", Role.STUDENT),
            (LECTURER_EMAIL, "Visiting Lecturer", Role.STUDENT),
            (HYPERLINK_EMAIL, HYPERLINK_NAME, Role.STUDENT),
            (OUTSIDER_EMAIL, "Outsider Student", Role.STUDENT),
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
        classrooms = self.app.state.classroom_service
        self.classroom = classrooms.create(
            self.accounts[TEACHER_EMAIL],
            course_code="01076001",
            course_name="Programming I",
            section="1",
            semester="1/2569",
        )
        for email in (STUDENT_EMAIL, LECTURER_EMAIL, HYPERLINK_EMAIL):
            classrooms.join(self.accounts[email], self.classroom.join_code)

        self.assignment_id = self.publish("Add two numbers")
        self.summary_url = f"/api/v1/classrooms/{self.classroom.id}/summary.csv"
        self.submissions_url = (
            f"/api/v1/classrooms/{self.classroom.id}"
            f"/assignments/{self.assignment_id}/submissions.csv"
        )
        self.submit_url = (
            f"/api/v1/classrooms/{self.classroom.id}"
            f"/assignments/{self.assignment_id}/submissions"
        )

    def publish(self, title: str) -> int:
        published = self.app.state.assignment_service.publish(
            self.accounts[TEACHER_EMAIL],
            AssignmentContent(
                title=title,
                problem_statement="Print the sum of two integers.",
                difficulty=Difficulty.EASY,
                test_cases=[TestCase(input_data="1 2", expected_output="3")],
            ),
            Schedule(deadline=DEADLINE),
            [self.classroom.id],
        )
        return published.assignment.id

    def receive(self, email: str) -> int:
        """Submit without judging: the Submission stays `received`."""
        submission = self.app.state.submission_service.submit(
            self.accounts[email], self.classroom.id, self.assignment_id, GOOD_CODE
        )
        return submission.id

    def fail(self, email: str) -> int:
        """A Submission the runner never judged, given up after GIVE_UP."""
        service = self.app.state.submission_service
        self.runner.backend.down()
        submission_id = self.receive(email)
        service.judge(submission_id)
        self.runner.backend.resume()
        self.clock.advance(GIVE_UP)
        service.recover()
        return submission_id

    async def client_for(self, email: str) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver"
        )
        await client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        )
        return client


@pytest.mark.anyio
async def test_owner_can_export_summary_csv() -> None:
    campus = ExportCampus()
    student = await campus.client_for(STUDENT_EMAIL)
    created = await student.post(campus.submit_url, json={"code": GOOD_CODE})
    assert created.status_code == 202
    teacher = await campus.client_for(TEACHER_EMAIL)

    response = await teacher.get(campus.summary_url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="classroom-{campus.classroom.id}-summary.csv"'
    )
    assert parse_csv(response.content) == [
        SUMMARY_HEADER,
        ["66010001", "Student Nattapong", "10", "10"],
        ["", "Visiting Lecturer", "", "0"],
        ["66010003", "'" + HYPERLINK_NAME, "", "0"],
    ]


@pytest.mark.anyio
async def test_summary_csv_neutralises_formulas_in_titles_and_names() -> None:
    campus = ExportCampus()
    campus.publish("@SUM(1+1)")
    teacher = await campus.client_for(TEACHER_EMAIL)

    response = await teacher.get(campus.summary_url)

    header, *rows = parse_csv(response.content)
    assert header == [
        "Student Number",
        "Student Name",
        "Add two numbers",
        "'@SUM(1+1)",
        "Total",
    ]
    assert rows[2][:2] == ["66010003", "'" + HYPERLINK_NAME]


@pytest.mark.anyio
async def test_student_cannot_export_summary_csv() -> None:
    campus = ExportCampus()
    student = await campus.client_for(STUDENT_EMAIL)

    response = await student.get(campus.summary_url)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden"


@pytest.mark.anyio
async def test_outsider_cannot_export_summary_csv() -> None:
    campus = ExportCampus()
    outsider = await campus.client_for(OUTSIDER_EMAIL)

    response = await outsider.get(campus.summary_url)

    assert response.status_code == 404


@pytest.mark.anyio
async def test_owner_can_export_submissions_csv() -> None:
    campus = ExportCampus()
    student = await campus.client_for(STUDENT_EMAIL)
    created = await student.post(campus.submit_url, json={"code": GOOD_CODE})
    assert created.status_code == 202
    submission_id = created.json()["id"]
    polled = await student.get(f"/api/v1/submissions/{submission_id}")
    assert polled.json()["status"] == "done"

    teacher = await campus.client_for(TEACHER_EMAIL)
    response = await teacher.get(campus.submissions_url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="classroom-{campus.classroom.id}'
        f'-assignment-{campus.assignment_id}-submissions.csv"'
    )
    assert parse_csv(response.content) == [
        SUBMISSIONS_HEADER,
        [
            "1",
            "Add two numbers",
            str(submission_id),
            "66010001",
            "Student Nattapong",
            "1",
            "1",
            NOW_BANGKOK,
            "done",
            "10",
            "10",
            "1",
            "1",
            "False",
        ],
    ]


@pytest.mark.anyio
async def test_submissions_csv_leaves_the_verdict_blank_until_judged() -> None:
    campus = ExportCampus()
    failed_id = campus.fail(LECTURER_EMAIL)
    pending_id = campus.receive(STUDENT_EMAIL)
    teacher = await campus.client_for(TEACHER_EMAIL)

    response = await teacher.get(campus.submissions_url)

    rows = {row[2]: row for row in parse_csv(response.content)[1:]}
    assert rows[str(pending_id)][3:5] == ["66010001", "Student Nattapong"]
    assert rows[str(pending_id)][8:] == ["received", "", "10", "", "", "False"]
    assert rows[str(failed_id)][3:5] == ["", "Visiting Lecturer"]
    assert rows[str(failed_id)][8:] == ["failed", "", "10", "", "", "False"]


@pytest.mark.anyio
async def test_submissions_csv_neutralises_a_formula_student_name() -> None:
    campus = ExportCampus()
    hyperlink = await campus.client_for(HYPERLINK_EMAIL)
    created = await hyperlink.post(campus.submit_url, json={"code": GOOD_CODE})
    assert created.status_code == 202
    teacher = await campus.client_for(TEACHER_EMAIL)

    response = await teacher.get(campus.submissions_url)

    (row,) = parse_csv(response.content)[1:]
    assert row[3:5] == ["66010003", "'" + HYPERLINK_NAME]


@pytest.mark.anyio
async def test_submissions_csv_names_the_problem_and_neutralises_its_title() -> None:
    campus = ExportCampus()
    title = '=HYPERLINK("http://x")'
    second_id = campus.publish(title)
    student = await campus.client_for(STUDENT_EMAIL)
    base = f"/api/v1/classrooms/{campus.classroom.id}/assignments/{second_id}"
    created = await student.post(f"{base}/submissions", json={"code": GOOD_CODE})
    assert created.status_code == 202
    teacher = await campus.client_for(TEACHER_EMAIL)

    response = await teacher.get(f"{base}/submissions.csv")

    assert response.headers["content-disposition"] == (
        f'attachment; filename="classroom-{campus.classroom.id}'
        f'-assignment-{second_id}-submissions.csv"'
    )
    header, row = parse_csv(response.content)
    assert header == SUBMISSIONS_HEADER
    assert row[:2] == ["2", "'" + title]


@pytest.mark.anyio
async def test_student_cannot_export_submissions_csv() -> None:
    campus = ExportCampus()
    student = await campus.client_for(STUDENT_EMAIL)

    response = await student.get(campus.submissions_url)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "forbidden"


@pytest.mark.anyio
async def test_outsider_cannot_export_submissions_csv() -> None:
    campus = ExportCampus()
    outsider = await campus.client_for(OUTSIDER_EMAIL)

    response = await outsider.get(campus.submissions_url)

    assert response.status_code == 404
