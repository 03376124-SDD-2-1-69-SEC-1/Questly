"""FE-08 pages on the production wiring, backed by Postgres.

The app is built the way `main.py` builds it for production, with only the
settings, R2 and the vector store swapped out and the database pointed at the
throwaway branch. The Instructor logs in over HTTP and posts the T-05 form, so
the rows asserted below were written by the SQL adapters.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    Schedule,
    TestCase,
    TestCaseKind,
)
from questly.core.auth.models import Actor, Role, User
from questly.core.auth.service import hash_password
from questly.database.core.auth_repository import SQLAuthRepository
from questly.database.session import SessionFactory
from questly.main import create_app
from tests.fakes.app import fake_settings
from tests.fakes.documents import FakeObjectStorage
from tests.fakes.vector import FakeVectorRepository
from tests.integration.forms import asgi_client, log_in, post_form

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery"
INSTRUCTOR_EMAIL = "somchai.p@kmitl.ac.th"
DEADLINE = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)


class World:
    """One Instructor, two Students in one Classroom, one published Assignment."""

    def __init__(self, postgres_url: str, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory
        self.app = create_app(
            settings=replace(fake_settings(), database_url=postgres_url),
            object_storage=FakeObjectStorage(),
            vector_repository=FakeVectorRepository(),
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

        self.teacher = account(INSTRUCTOR_EMAIL, "Somchai Prasert", Role.INSTRUCTOR)
        self.student = account("66010001@kmitl.ac.th", "Nattapong Suwan", Role.STUDENT)
        self.other = account("66010002@kmitl.ac.th", "Pimchanok Wong", Role.STUDENT)
        classrooms = self.app.state.classroom_service
        self.classroom = classrooms.create(
            self.teacher,
            course_code="01076001",
            course_name="Programming I",
            section="1",
            semester="1/2569",
        )
        classrooms.join(self.student, self.classroom.join_code)
        classrooms.join(self.other, self.classroom.join_code)
        published = self.app.state.assignment_service.publish(
            self.teacher,
            AssignmentContent(
                title="Binary search",
                problem_statement="Print the index of x, or -1.",
                difficulty=Difficulty.MEDIUM,
                test_cases=[
                    TestCase(input_data="1\n4\n4", expected_output="0"),
                    TestCase(
                        input_data="0\n\n4",
                        expected_output="-1",
                        kind=TestCaseKind.HIDDEN,
                        note="empty list",
                    ),
                ],
            ),
            Schedule(deadline=DEADLINE),
            [self.classroom.id],
        )
        self.assignment = published.assignment
        self.posting = published.postings[0]
        self.t02 = f"/classes/{self.classroom.id}/assignments/{self.assignment.id}"

    def rows(self, sql: str) -> list[tuple]:
        with self.session_factory() as session:
            return [tuple(row) for row in session.execute(text(sql)).all()]


@pytest.mark.anyio
@pytest.mark.usefixtures("empty_core_tables")
async def test_t05_submit_creates_a_new_version_and_moves_the_chosen_deadline(
    postgres_url: str, session_factory: SessionFactory
) -> None:
    world = World(postgres_url, session_factory)
    client = asgi_client(world.app)
    await log_in(client, INSTRUCTOR_EMAIL, PASSWORD)
    edit = world.t02 + "/edit"
    page = await client.get(edit)
    tests = world.assignment.test_cases

    response = await post_form(
        client,
        edit,
        {
            "title": "Binary search, revised",
            "problem_statement": world.assignment.problem_statement,
            "difficulty": "medium",
            "topic_id": "",
            "reason": "Test case 2 had a wrong expected output.",
            "kind": [t.kind.value for t in tests],
            "input_data": [t.input_data for t in tests],
            "expected_output": [t.expected_output for t in tests],
            "note": [t.note for t in tests],
            "test_case_id": [str(t.id) for t in tests],
            "time_limit_s": "1.0",
            "language": "python3",
            "posting_id": [str(world.posting.id)],
            "extend_to": ["2026-12-08T23:59"],
            "extend": [str(world.posting.id)],
        },
        page=edit,
    )

    assert page.status_code == 200 and 'data-page="T-05"' in page.text
    assert response.status_code == 303 and response.headers["location"] == world.t02
    assert world.rows(
        "SELECT number, title, reason FROM core.assignment_versions ORDER BY number"
    ) == [
        (1, "Binary search", "Published"),
        (2, "Binary search, revised", "Test case 2 had a wrong expected output."),
    ]
    assert world.rows("SELECT title, current_version FROM core.assignments") == [
        ("Binary search, revised", 2)
    ]
    # 23:59 Bangkok on 8 Dec is 16:59 UTC.
    assert world.rows("SELECT deadline FROM core.classroom_assignments") == [
        (datetime(2026, 12, 8, 16, 59, tzinfo=UTC),)
    ]
    assert world.rows(
        "SELECT user_id, kind FROM core.notifications "
        "WHERE kind = 'assignment_updated' ORDER BY user_id"
    ) == [
        (world.student.user_id, "assignment_updated"),
        (world.other.user_id, "assignment_updated"),
    ]


@pytest.mark.anyio
@pytest.mark.usefixtures("empty_core_tables")
async def test_t05_blank_reason_writes_no_version(
    postgres_url: str, session_factory: SessionFactory
) -> None:
    world = World(postgres_url, session_factory)
    client = asgi_client(world.app)
    await log_in(client, INSTRUCTOR_EMAIL, PASSWORD)
    edit = world.t02 + "/edit"
    tests = world.assignment.test_cases

    response = await post_form(
        client,
        edit,
        {
            "title": "Never saved",
            "problem_statement": world.assignment.problem_statement,
            "difficulty": "medium",
            "reason": " ",
            "kind": [t.kind.value for t in tests],
            "input_data": [t.input_data for t in tests],
            "expected_output": [t.expected_output for t in tests],
            "note": [t.note for t in tests],
            "test_case_id": [str(t.id) for t in tests],
            "posting_id": [str(world.posting.id)],
            "extend_to": [""],
        },
        page=edit,
    )

    assert response.status_code == 422
    assert world.rows("SELECT number FROM core.assignment_versions") == [(1,)]


@pytest.mark.anyio
@pytest.mark.usefixtures("empty_core_tables")
async def test_t02_search_and_notify_students_read_and_write_postgres(
    postgres_url: str, session_factory: SessionFactory
) -> None:
    world = World(postgres_url, session_factory)
    client = asgi_client(world.app)
    await log_in(client, INSTRUCTOR_EMAIL, PASSWORD)

    empty = await client.get(world.t02)
    notified = await post_form(client, world.t02 + "/notify", {}, page=world.t02)
    searched = await client.get(world.t02 + "?q=66010002")

    assert 'data-state="T-02c"' in empty.text
    assert notified.status_code == 303
    assert notified.headers["location"] == world.t02 + "?notified=1"
    assert world.rows(
        "SELECT count(*) FROM core.notifications WHERE kind = 'assignment_notified'"
    ) == [(2,)]
    assert searched.status_code == 200
    # Nobody has submitted yet, so T-02c stays; the search form needs a table.
    assert 'data-state="T-02c"' in searched.text


@pytest.mark.anyio
@pytest.mark.usefixtures("empty_core_tables")
async def test_t06_shows_the_instructors_postgres_document(
    postgres_url: str, session_factory: SessionFactory
) -> None:
    world = World(postgres_url, session_factory)
    document = world.app.state.document_service.store(
        world.teacher,
        filename="lecture-notes.pdf",
        data=b"%PDF-1.4 lecture notes",
    )
    client = asgi_client(world.app)
    await log_in(client, INSTRUCTOR_EMAIL, PASSWORD)

    page = await client.get("/documents")

    assert page.status_code == 200
    assert 'data-page="T-06"' in page.text
    assert "lecture-notes.pdf" in page.text
    assert world.rows(
        "SELECT id, uploaded_by, status FROM core.knowledge_documents"
    ) == [(document.id, world.teacher.user_id, "uploaded")]
