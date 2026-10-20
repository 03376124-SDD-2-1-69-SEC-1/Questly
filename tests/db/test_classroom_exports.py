"""Classroom CSV exports over the real PostgreSQL database."""

import csv
import io
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    Schedule,
    TestCase,
)
from questly.core.auth.models import Actor, Role, User
from questly.core.auth.service import hash_password
from questly.database.core.auth_repository import SQLAuthRepository
from questly.database.session import SessionFactory
from questly.main import create_app
from tests.fakes.app import FakeObjectStorage, fake_settings
from tests.fakes.submissions import ScriptedCodeRunner
from tests.fakes.vector import FakeVectorRepository
from tests.integration.forms import asgi_client, log_in

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery"
INSTRUCTOR_EMAIL = "exports.teacher@example.com"
STUDENT_EMAIL = "66010001@kmitl.ac.th"
STUDENT_NAME = "Nattapong Suwan"
FORMULA_EMAIL = "66010003@kmitl.ac.th"
FORMULA_NAME = '=HYPERLINK("http://x")'
OUTSIDER_EMAIL = "exports.outsider@example.com"


class ExportWorld:
    """A real PostgreSQL classroom with one assignment and two Students."""

    def __init__(
        self,
        postgres_url: str,
        session_factory: SessionFactory,
    ) -> None:
        self.postgres_url = postgres_url
        self.session_factory = session_factory

        self.app = create_app(
            settings=replace(fake_settings(), database_url=postgres_url),
            object_storage=FakeObjectStorage(),
            vector_repository=FakeVectorRepository(),
            code_runner=ScriptedCodeRunner(
                {"1 2": "3"},
            ),
            secure_cookies=False,
        )

        users = SQLAuthRepository(session_factory)

        def account(email: str, name: str, role: Role) -> Actor:
            user = users.create_user(
                User(
                    email=email,
                    full_name=name,
                    password_hash=hash_password(PASSWORD),
                    role=role,
                    email_verified=True,
                )
            )
            return Actor(user.id, role, name, email)

        self.teacher = account(INSTRUCTOR_EMAIL, "Exports Teacher", Role.INSTRUCTOR)
        self.student = account(STUDENT_EMAIL, STUDENT_NAME, Role.STUDENT)
        self.formula_student = account(FORMULA_EMAIL, FORMULA_NAME, Role.STUDENT)
        self.outsider = account(OUTSIDER_EMAIL, "Exports Outsider", Role.STUDENT)

        classrooms = self.app.state.classroom_service
        self.classroom = classrooms.create(
            self.teacher,
            course_code="EXPORT01",
            course_name="CSV Export Test",
            section="1",
            semester="1/2569",
        )
        classrooms.join(self.student, self.classroom.join_code)
        classrooms.join(self.formula_student, self.classroom.join_code)

        published = self.app.state.assignment_service.publish(
            self.teacher,
            AssignmentContent(
                title="Add two numbers",
                problem_statement="Print the sum of two integers.",
                difficulty=Difficulty.EASY,
                test_cases=[
                    TestCase(
                        input_data="1 2",
                        expected_output="3",
                    )
                ],
            ),
            Schedule(
                deadline=datetime(2027, 1, 1, tzinfo=UTC),
            ),
            [self.classroom.id],
        )
        self.assignment = published.assignment

        base = f"/api/v1/classrooms/{self.classroom.id}"
        self.summary_url = f"{base}/summary.csv"
        self.submissions_url = (
            f"{base}/assignments/{self.assignment.id}/submissions.csv"
        )
        self.submit_url = f"{base}/assignments/{self.assignment.id}/submissions"

    async def client(self, actor: Actor):
        client = asgi_client(self.app)
        await log_in(client, actor.email, PASSWORD)
        return client


@pytest.fixture()
def export_world(
    postgres_url: str,
    session_factory: SessionFactory,
    empty_core_tables: None,
) -> Generator[ExportWorld, None, None]:
    world = ExportWorld(postgres_url, session_factory)
    yield world
    # empty_core_tables cleans up the shared core tables after the test.


def parse_csv(content: bytes) -> list[list[str]]:
    assert content.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))


def student_number(email: str) -> str:
    local_part = email.split("@", 1)[0]
    return local_part if local_part.isdigit() else ""


def formula_safe(text: str) -> str:
    """The expected cell: a leading formula character is neutralised."""
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def stored_submissions(world: ExportWorld) -> list[dict[str, object]]:
    """Every Submission as the database holds it, oldest first."""
    with world.session_factory() as session:
        rows = session.execute(
            text(
                "SELECT s.id, u.email, u.full_name, a.title, s.attempt, "
                "s.version_number, s.submitted_at, s.status, s.score, s.max_score, "
                "s.is_late, "
                "(SELECT count(*) FROM core.submission_test_results r "
                " WHERE r.submission_id = s.id AND r.verdict = 'passed') AS passed, "
                "(SELECT count(*) FROM core.submission_test_results r "
                " WHERE r.submission_id = s.id) AS total "
                "FROM core.submissions s JOIN core.users u ON u.id = s.student_id "
                "JOIN core.classroom_assignments p ON p.id = s.posting_id "
                "JOIN core.assignments a ON a.id = p.assignment_id "
                "ORDER BY s.id"
            )
        ).mappings()
        return [dict(row) for row in rows]


def stored_members(world: ExportWorld) -> list[dict[str, object]]:
    """The Classroom's Members as the database holds them, in join order."""
    with world.session_factory() as session:
        rows = session.execute(
            text(
                "SELECT u.id, u.email, u.full_name FROM core.classroom_members m "
                "JOIN core.users u ON u.id = m.student_id "
                "WHERE m.classroom_id = :classroom ORDER BY m.joined_at, m.id"
            ),
            {"classroom": world.classroom.id},
        ).mappings()
        return [dict(row) for row in rows]


@pytest.mark.anyio
async def test_summary_csv_matches_the_database(export_world: ExportWorld) -> None:
    student = await export_world.client(export_world.student)
    created = await student.post(
        export_world.submit_url,
        json={"code": "print(sum(map(int, input().split())))"},
    )
    assert created.status_code == 202
    assert (await student.get(f"/api/v1/submissions/{created.json()['id']}")).json()[
        "status"
    ] == "done"
    stored = stored_submissions(export_world)
    assert [row["status"] for row in stored] == ["done"]
    scores = {row["email"]: row["score"] for row in stored}

    teacher = await export_world.client(export_world.teacher)
    response = await teacher.get(export_world.summary_url)

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment;" in response.headers["content-disposition"]
    expected = [["Student Number", "Student Name", "Add two numbers", "Total"]]
    for member in stored_members(export_world):
        score = scores.get(member["email"])
        expected.append(
            [
                student_number(str(member["email"])),
                formula_safe(str(member["full_name"])),
                "" if score is None else str(score),
                "0" if score is None else str(score),
            ]
        )
    assert parse_csv(response.content) == expected


@pytest.mark.anyio
async def test_student_cannot_export_classroom_summary(
    export_world: ExportWorld,
) -> None:
    client = await export_world.client(export_world.student)

    response = await client.get(export_world.summary_url)

    assert response.status_code == 403


@pytest.mark.anyio
async def test_submissions_csv_matches_the_database(
    export_world: ExportWorld,
) -> None:
    student = await export_world.client(export_world.student)
    created = await student.post(
        export_world.submit_url,
        json={"code": "print(sum(map(int, input().split())))"},
    )
    assert created.status_code == 202
    # A second Student's Submission is received but never judged.
    export_world.app.state.submission_service.submit(
        export_world.formula_student,
        export_world.classroom.id,
        export_world.assignment.id,
        "print(0)",
    )
    stored = stored_submissions(export_world)
    assert [row["status"] for row in stored] == ["done", "received"]

    teacher = await export_world.client(export_world.teacher)
    response = await teacher.get(export_world.submissions_url)

    assert response.status_code == 200
    expected = [
        [
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
    ]
    for row in stored:
        judged = row["status"] == "done"
        submitted_at = row["submitted_at"]
        assert isinstance(submitted_at, datetime)
        expected.append(
            [
                "1",
                formula_safe(str(row["title"])),
                str(row["id"]),
                student_number(str(row["email"])),
                formula_safe(str(row["full_name"])),
                str(row["attempt"]),
                str(row["version_number"]),
                submitted_at.astimezone(ZoneInfo("Asia/Bangkok")).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                str(row["status"]),
                str(row["score"]) if judged else "",
                str(row["max_score"]),
                str(row["passed"]) if judged else "",
                str(row["total"]) if judged else "",
                str(row["is_late"]),
            ]
        )
    assert parse_csv(response.content) == expected
    assert expected[1][:2] == ["1", "Add two numbers"]
    assert expected[1][3:5] == ["66010001", STUDENT_NAME]
    assert expected[1][9:] == ["10", "10", "1", "1", "False"]
    assert expected[2][3:5] == ["66010003", "'" + FORMULA_NAME]
    assert expected[2][8:] == ["received", "", "10", "", "", "False"]


@pytest.mark.anyio
async def test_student_cannot_export_class_wide_submission_history(
    export_world: ExportWorld,
) -> None:
    client = await export_world.client(export_world.student)

    response = await client.get(export_world.submissions_url)

    assert response.status_code == 403


@pytest.mark.anyio
async def test_non_member_cannot_export_classroom_data(
    export_world: ExportWorld,
) -> None:
    client = await export_world.client(export_world.outsider)

    summary = await client.get(export_world.summary_url)
    submissions = await client.get(export_world.submissions_url)

    assert summary.status_code == 404
    assert submissions.status_code == 404
