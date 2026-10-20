"""Classroom CSV exports over the real PostgreSQL database."""

from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime

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
from tests.fakes.app import fake_settings
from tests.fakes.submissions import ScriptedCodeRunner
from tests.fakes.uploads import FakeObjectStorage
from tests.fakes.vector import FakeVectorRepository
from tests.integration.forms import asgi_client, log_in

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery"
INSTRUCTOR_EMAIL = "exports.teacher@example.com"
STUDENT_EMAIL = "66010001@kmitl.ac.th"
OUTSIDER_EMAIL = "exports.outsider@example.com"


class ExportWorld:
    """A real PostgreSQL classroom with one assignment and one submission."""

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

        def account(email: str, role: Role) -> Actor:
            user = users.create_user(
                User(
                    email=email,
                    full_name=email,
                    password_hash=hash_password(PASSWORD),
                    role=role,
                    email_verified=True,
                )
            )
            return Actor(user.id, role, email, email)

        self.teacher = account(INSTRUCTOR_EMAIL, Role.INSTRUCTOR)
        self.student = account(STUDENT_EMAIL, Role.STUDENT)
        self.outsider = account(OUTSIDER_EMAIL, Role.STUDENT)

        classrooms = self.app.state.classroom_service
        self.classroom = classrooms.create(
            self.teacher,
            course_code="EXPORT01",
            course_name="CSV Export Test",
            section="1",
            semester="1/2569",
        )
        classrooms.join(self.student, self.classroom.join_code)

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


@pytest.mark.anyio
async def test_instructor_can_export_classroom_summary(
    export_world: ExportWorld,
) -> None:
    client = await export_world.client(export_world.teacher)

    response = await client.get(export_world.summary_url)

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment;" in response.headers["content-disposition"]
    assert response.content.startswith(b"\xef\xbb\xbf")

    csv_text = response.content.decode("utf-8-sig")
    assert "Student" in csv_text
    assert "Total" in csv_text
    assert STUDENT_EMAIL in csv_text


@pytest.mark.anyio
async def test_student_cannot_export_classroom_summary(
    export_world: ExportWorld,
) -> None:
    client = await export_world.client(export_world.student)

    response = await client.get(export_world.summary_url)

    assert response.status_code == 403


@pytest.mark.anyio
async def test_instructor_can_export_submission_history(
    export_world: ExportWorld,
) -> None:
    student = await export_world.client(export_world.student)
    created = await student.post(
        export_world.submit_url,
        json={"code": "print(sum(map(int, input().split())))"},
    )
    assert created.status_code == 202

    # Wait for the asynchronous judge to finish before checking the export.
    submission_id = created.json()["id"]
    polled = await student.get(f"/api/v1/submissions/{submission_id}")
    assert polled.status_code == 200

    teacher = await export_world.client(export_world.teacher)
    response = await teacher.get(export_world.submissions_url)

    assert response.status_code == 200
    csv_text = response.content.decode("utf-8-sig")
    assert "Submission ID" in csv_text
    assert "Student ID" in csv_text
    assert str(submission_id) in csv_text
    assert "66010001" in csv_text


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


def test_export_fixture_uses_real_database(
    export_world: ExportWorld,
) -> None:
    with export_world.session_factory() as session:
        users = session.execute(
            text(
                "SELECT count(*) FROM core.users "
                "WHERE email IN (:teacher, :student, :outsider)"
            ),
            {
                "teacher": INSTRUCTOR_EMAIL,
                "student": STUDENT_EMAIL,
                "outsider": OUTSIDER_EMAIL,
            },
        ).scalar_one()

    assert users == 3
