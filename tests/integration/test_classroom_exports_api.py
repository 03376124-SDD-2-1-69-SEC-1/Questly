"""Integration tests for classroom CSV export endpoints."""

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
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user
from tests.fakes.submissions import ScriptedCodeRunner

DEADLINE = datetime(2027, 1, 1, tzinfo=UTC)
TEACHER_EMAIL = "teacher@kmitl.ac.th"
STUDENT_EMAIL = "66010001@kmitl.ac.th"
OUTSIDER_EMAIL = "outsider@kmitl.ac.th"


class ExportCampus:
    """Classroom with one assignment and student enrolled."""

    def __init__(self) -> None:
        users = FakeAuthRepository()
        self.accounts = {}
        for email, name, role in [
            (TEACHER_EMAIL, "Teacher Somchai", Role.INSTRUCTOR),
            (STUDENT_EMAIL, "Student Nattapong", Role.STUDENT),
            (OUTSIDER_EMAIL, "Outsider Student", Role.STUDENT),
        ]:
            user = seed_user(users, email=email, full_name=name, role=role)
            self.accounts[email] = Actor(
                user_id=user.id, role=role, full_name=name, email=email
            )

        self.app = build_app(
            auth_repository=users,
            code_runner=ScriptedCodeRunner({"1 2": "3"}),
        )
        classrooms = self.app.state.classroom_service
        self.classroom = classrooms.create(
            self.accounts[TEACHER_EMAIL],
            course_code="01076001",
            course_name="Programming I",
            section="1",
            semester="1/2569",
        )
        classrooms.join(self.accounts[STUDENT_EMAIL], self.classroom.join_code)

        published = self.app.state.assignment_service.publish(
            self.accounts[TEACHER_EMAIL],
            AssignmentContent(
                title="Add two numbers",
                problem_statement="Print the sum of two integers.",
                difficulty=Difficulty.EASY,
                test_cases=[TestCase(input_data="1 2", expected_output="3")],
            ),
            Schedule(deadline=DEADLINE),
            [self.classroom.id],
        )
        self.assignment_id = published.assignment.id
        self.summary_url = f"/api/v1/classrooms/{self.classroom.id}/summary.csv"
        self.submissions_url = (
            f"/api/v1/classrooms/{self.classroom.id}"
            f"/assignments/{self.assignment_id}/submissions.csv"
        )
        self.submit_url = (
            f"/api/v1/classrooms/{self.classroom.id}"
            f"/assignments/{self.assignment_id}/submissions"
        )

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
    teacher = await campus.client_for(TEACHER_EMAIL)

    response = await teacher.get(campus.summary_url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="classroom-{campus.classroom.id}-summary.csv"'
    )
    assert response.content.startswith(b"\xef\xbb\xbf")

    csv_text = response.content.decode("utf-8-sig")
    assert "Student" in csv_text
    assert "Add two numbers" in csv_text
    assert "Total" in csv_text
    assert "Student Nattapong" in csv_text


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
    created = await student.post(
        campus.submit_url,
        json={"code": "print(sum(map(int, input().split())))"},
    )
    assert created.status_code == 202
    sub_id = created.json()["id"]

    teacher = await campus.client_for(TEACHER_EMAIL)
    response = await teacher.get(campus.submissions_url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="classroom-{campus.classroom.id}'
        f'-assignment-{campus.assignment_id}-submissions.csv"'
    )
    assert response.content.startswith(b"\xef\xbb\xbf")

    csv_text = response.content.decode("utf-8-sig")
    assert "Submission ID" in csv_text
    assert "Student Number" in csv_text
    assert "66010001" in csv_text
    assert str(sub_id) in csv_text
    # Student ID column must be derived from email digits
    assert "66010001" in csv_text


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
