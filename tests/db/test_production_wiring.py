"""`create_app` with nothing injected persists to Postgres (OPS-15).

Built the way production builds it — only settings, R2 and the vector store
swapped out — and pointed at the throwaway branch. The services on `app.state`
then write through whatever `main.py` wired, so rows in the database prove the
SQL adapters are the ones in use. (HTTP-level checks live in
`tests/integration/`, which runs on fakes.)
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from questly.core.admin.models import AISettingsChange
from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    Schedule,
    TestCase,
)
from questly.core.auth.models import Actor, InstructorRequest, Role, User
from questly.core.auth.service import hash_password
from questly.database.core.admin_repository import SQLAdminRepository
from questly.database.core.auth_repository import SQLAuthRepository
from questly.database.core.draft_repository import SQLDraftRepository
from questly.database.core.tables import User as UserRow
from questly.database.session import SessionFactory
from questly.integrations.email import StubEmailSender
from questly.main import create_app
from tests.fakes.app import fake_settings
from tests.fakes.auth import verification_path
from tests.fakes.documents import FakeObjectStorage
from tests.fakes.vector import FakeVectorRepository
from tests.integration.forms import asgi_client, log_in

pytestmark = pytest.mark.postgres


@pytest.mark.usefixtures("empty_core_tables")
def test_sign_up_verify_and_log_in_write_to_postgres(
    postgres_url: str, session_factory: SessionFactory
) -> None:
    mailer = StubEmailSender()
    app = create_app(
        settings=replace(fake_settings(), database_url=postgres_url),
        email_sender=mailer,
        object_storage=FakeObjectStorage(),
        vector_repository=FakeVectorRepository(),
    )
    auth = app.state.auth_service

    auth.sign_up(
        full_name="Somchai Prasert",
        email="somchai@kmitl.ac.th",
        password="correct horse",
        confirm_password="correct horse",
        wants_instructor=True,
        faculty="Engineering",
    )
    auth.verify_email(verification_path(mailer).split("token=", 1)[1])
    login = auth.log_in(email="somchai@kmitl.ac.th", password="correct horse")

    assert auth.resolve_session(login.token).full_name == "Somchai Prasert"
    with session_factory() as session:
        stored = session.execute(
            text(
                "SELECT u.full_name, u.email_verified_at IS NOT NULL, "
                "(SELECT count(*) FROM core.sessions s WHERE s.user_id = u.id), "
                "(SELECT faculty FROM core.instructor_requests r "
                " WHERE r.user_id = u.id) "
                "FROM core.users u"
            )
        ).one()
    assert tuple(stored) == ("Somchai Prasert", True, 1, "Engineering")


def _account(session_factory: SessionFactory, email: str, role: Role) -> Actor:
    with session_factory() as session:
        row = UserRow(
            email=email,
            full_name=email,
            password_hash="scrypt$x",
            role=role.value,
            email_verified_at=datetime.now(UTC),
        )
        session.add(row)
        session.commit()
        return Actor(user_id=row.id, role=role, full_name=email, email=email)


@pytest.mark.usefixtures("empty_core_tables")
def test_publishing_stores_a_notification_per_member_in_postgres(
    postgres_url: str, session_factory: SessionFactory
) -> None:
    mailer = StubEmailSender()
    app = create_app(
        settings=replace(fake_settings(), database_url=postgres_url),
        email_sender=mailer,
        object_storage=FakeObjectStorage(),
        vector_repository=FakeVectorRepository(),
    )
    teacher = _account(session_factory, "somchai.p@kmitl.ac.th", Role.INSTRUCTOR)
    student = _account(session_factory, "66010001@kmitl.ac.th", Role.STUDENT)
    classrooms = app.state.classroom_service
    classroom = classrooms.create(
        teacher,
        course_code="01076001",
        course_name="Programming I",
        section="1",
        semester="1/2569",
    )
    classrooms.join(student, classroom.join_code)

    published = app.state.assignment_service.publish(
        teacher,
        AssignmentContent(
            title="Binary search",
            problem_statement="Print the index of x, or -1.",
            difficulty=Difficulty.MEDIUM,
            test_cases=[TestCase(input_data="1\n4\n4", expected_output="0")],
        ),
        Schedule(deadline=datetime.now(UTC) + timedelta(days=7)),
        [classroom.id],
    )

    with session_factory() as session:
        stored = session.execute(
            text("SELECT user_id, kind, payload, read_at FROM core.notifications")
        ).all()
    assert [tuple(row) for row in stored] == [
        (
            student.user_id,
            "assignment_published",
            {
                "classroom_id": classroom.id,
                "assignment_id": published.assignment.id,
                "title": "Binary search",
            },
            None,
        )
    ]
    assert [m.to for m in mailer.messages] == [student.email]


@pytest.mark.usefixtures("empty_core_tables")
def test_admin_services_persist_settings_and_generation_reads_quota(
    postgres_url: str, session_factory: SessionFactory
) -> None:
    settings_repository = SQLAdminRepository(session_factory)
    original_settings = settings_repository.get_ai_settings()
    auth_repository = SQLAuthRepository(session_factory)
    admin = auth_repository.create_user(
        User(
            email="admin@kmitl.ac.th",
            full_name="Admin",
            password_hash=hash_password("admin-pass"),
            role=Role.ADMIN,
            email_verified=True,
        )
    )
    applicant = auth_repository.create_user(
        User(
            email="applicant@kmitl.ac.th",
            full_name="Applicant",
            password_hash=hash_password("applicant-pass"),
            email_verified=True,
        )
    )
    request = auth_repository.create_instructor_request(
        InstructorRequest(applicant.id, "Engineering", datetime.now(UTC))
    )
    app = create_app(
        settings=replace(fake_settings(), database_url=postgres_url),
        object_storage=FakeObjectStorage(),
        vector_repository=FakeVectorRepository(),
    )
    admin_actor = Actor(
        user_id=admin.id,
        role=Role.ADMIN,
        full_name=admin.full_name,
        email=admin.email,
    )

    try:
        approved, _ = app.state.admin_service.approve_instructor_request(
            admin_actor, request.id
        )
        changed_settings = app.state.admin_service.update_ai_settings(
            admin_actor, AISettingsChange(daily_quota=1)
        )
        read_settings = app.state.admin_service.get_ai_settings(admin_actor)

        assert approved.reviewed_by == admin.id
        assert approved.reviewed_at is not None
        assert changed_settings.settings.daily_quota == 1
        assert read_settings.settings.daily_quota == 1
        assert auth_repository.get_user(applicant.id).role is Role.INSTRUCTOR

        instructor = Actor(
            user_id=applicant.id,
            role=Role.INSTRUCTOR,
            full_name=applicant.full_name,
            email=applicant.email,
        )
        app.state.classroom_service.create(
            instructor,
            course_code="01076001",
            course_name="Programming I",
            section="1",
            semester="1/2569",
        )
        SQLDraftRepository(session_factory).record_generation(
            applicant.id, datetime.now(UTC)
        )
        quota = app.state.generation_service.quota(instructor)
        assert quota.used == quota.limit == 1
    finally:
        settings_repository.update_ai_settings(original_settings)


def _chat_completion(content: str) -> MagicMock:
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = content
    return completion


@pytest.mark.usefixtures("empty_core_tables")
def test_admin_model_change_reaches_the_generation_client(
    postgres_url: str, session_factory: SessionFactory
) -> None:
    settings_repository = SQLAdminRepository(session_factory)
    original_settings = settings_repository.get_ai_settings()
    auth_repository = SQLAuthRepository(session_factory)
    admin = auth_repository.create_user(
        User(
            email="admin@kmitl.ac.th",
            full_name="Admin",
            password_hash=hash_password("admin-pass"),
            role=Role.ADMIN,
            email_verified=True,
        )
    )
    teacher = auth_repository.create_user(
        User(
            email="teacher@kmitl.ac.th",
            full_name="Teacher",
            password_hash=hash_password("teacher-pass"),
            role=Role.INSTRUCTOR,
            email_verified=True,
        )
    )
    app = create_app(
        settings=replace(
            fake_settings(),
            database_url=postgres_url,
            openrouter_api_key="not-a-real-key",
            generation_model_allowlist=(original_settings.model, "chosen/by-admin"),
        ),
        object_storage=FakeObjectStorage(),
        vector_repository=FakeVectorRepository(),
    )
    admin_actor = Actor(admin.id, Role.ADMIN, admin.full_name, admin.email)
    instructor = Actor(teacher.id, Role.INSTRUCTOR, teacher.full_name, teacher.email)
    classroom = app.state.classroom_service.create(
        instructor,
        course_code="01076001",
        course_name="Programming I",
        section="1",
        semester="1/2569",
    )
    reply = json.dumps(
        {
            "title": "Echo",
            "statement": "Print the input.",
            "test_cases": [{"input_data": "1"}],
            "reference_solution": "print(input())",
        }
    )

    try:
        app.state.admin_service.update_ai_settings(
            admin_actor, AISettingsChange(model="chosen/by-admin")
        )
        with patch(
            "openrouter.chat.Chat.send",
            return_value=_chat_completion(reply),
        ) as create:
            app.state.generation_service.generate(
                instructor,
                classroom.id,
                prompt="Echo the input",
                difficulty=Difficulty.EASY,
            )

        assert create.call_args.kwargs["model"] == "chosen/by-admin"
    finally:
        settings_repository.update_ai_settings(original_settings)


@pytest.mark.anyio
@pytest.mark.usefixtures("empty_core_tables")
async def test_uploading_a_document_records_the_logged_in_uploader_in_postgres(
    postgres_url: str, session_factory: SessionFactory
) -> None:
    storage = FakeObjectStorage()
    app = create_app(
        settings=replace(fake_settings(), database_url=postgres_url),
        email_sender=StubEmailSender(),
        object_storage=storage,
        vector_repository=FakeVectorRepository(),
        secure_cookies=False,
    )
    SQLAuthRepository(session_factory).create_user(
        User(
            email="somchai.p@kmitl.ac.th",
            full_name="Somchai Prasert",
            password_hash=hash_password("correct horse battery"),
            role=Role.INSTRUCTOR,
            email_verified=True,
        )
    )
    client = asgi_client(app)
    await log_in(client, "somchai.p@kmitl.ac.th", "correct horse battery")

    response = await client.post(
        "/api/v1/documents",
        data={"uploaded_by": "999999"},
        files={"file": ("lecture.pdf", b"%PDF-1.4 lecture", "application/pdf")},
    )

    assert response.status_code == 201
    item = response.json()
    with session_factory() as session:
        rows = session.execute(
            text(
                "SELECT d.id, d.r2_object_key, u.email "
                "FROM core.knowledge_documents d JOIN core.users u "
                "ON u.id = d.uploaded_by"
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        (item["id"], item["object_key"], "somchai.p@kmitl.ac.th")
    ]
    assert item["object_key"] in storage.objects
    listed = await client.get("/api/v1/documents")
    assert [row["id"] for row in listed.json()] == [item["id"]]
