"""T-06 Instructor document-library pages."""

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.auth.models import Actor, Role
from questly.core.documents.models import DocumentStatus
from tests.fakes.app import build_app
from tests.fakes.auth import (
    DEFAULT_PASSWORD,
    FakeAuthRepository,
    seed_user,
)
from tests.fakes.documents import FakeKnowledgeDocumentRepository
from tests.integration.forms import csrf_token, log_in, post_form

TEACHER = "somchai.p@kmitl.ac.th"
OTHER_TEACHER = "warunee.p@kmitl.ac.th"
STUDENT = "66010001@kmitl.ac.th"


class World:
    def __init__(self) -> None:
        users = FakeAuthRepository()
        self.teacher = seed_user(
            users, email=TEACHER, full_name="Somchai Prasert", role=Role.INSTRUCTOR
        )
        self.other_teacher = seed_user(
            users,
            email=OTHER_TEACHER,
            full_name="Warunee Pimsai",
            role=Role.INSTRUCTOR,
        )
        seed_user(users, email=STUDENT, full_name="Nattapong Suwan")
        self.repository = FakeKnowledgeDocumentRepository()
        self.app = build_app(
            auth_repository=users,
            knowledge_document_repository=self.repository,
        )

    def actor(self, user) -> Actor:
        return Actor(user.id, user.role, user.full_name, user.email)

    def document(
        self, user, filename: str, state: DocumentStatus = DocumentStatus.UPLOADED
    ) -> int:
        document = self.app.state.document_service.store(
            self.actor(user), filename=filename, data=f"bytes for {filename}".encode()
        )
        if document.id is None:
            raise AssertionError("fake document was not persisted")
        if state is not DocumentStatus.UPLOADED:
            self.repository.seed_status(document.id, state)
        return document.id


@pytest.fixture()
def world() -> World:
    return World()


async def _client(world: World, email: str | None = TEACHER) -> AsyncClient:
    client = AsyncClient(
        transport=ASGITransport(app=world.app), base_url="http://testserver"
    )
    if email is not None:
        await log_in(client, email, DEFAULT_PASSWORD)
    return client


@pytest.mark.anyio
async def test_t06_shows_empty_and_processing_and_failed_states(world: World) -> None:
    client = await _client(world)

    empty = await client.get("/documents")
    assert empty.status_code == 200
    assert 'data-page="T-06"' in empty.text
    assert 'data-state="T-06a"' in empty.text
    assert 'http-equiv="refresh"' not in empty.text

    world.document(world.teacher, "processing.pdf", DocumentStatus.INGESTING)
    world.document(world.teacher, "failed.pdf", DocumentStatus.FAILED)
    page = await client.get("/documents")

    assert page.status_code == 200
    assert "กำลังประมวลผล" in page.text
    assert "ล้มเหลว" in page.text
    assert 'data-state="ingesting"' in page.text
    assert 'data-state="failed"' in page.text
    assert '<meta http-equiv="refresh" content="5">' in page.text


@pytest.mark.anyio
async def test_t06_is_instructor_only_and_hides_other_owners_documents(
    world: World,
) -> None:
    own_id = world.document(world.teacher, "mine.pdf", DocumentStatus.READY)
    foreign_id = world.document(world.other_teacher, "theirs.pdf")
    teacher = await _client(world)
    student = await _client(world, STUDENT)
    anonymous = await _client(world, None)

    page = await teacher.get("/documents")
    assert page.status_code == 200
    assert "mine.pdf" in page.text
    assert "theirs.pdf" not in page.text
    assert (await teacher.get(f"/api/v1/documents/{foreign_id}")).status_code == 404
    assert (await teacher.get(f"/api/v1/documents/{own_id}")).status_code == 200
    forbidden_delete = await post_form(
        teacher,
        f"/documents/{foreign_id}/delete",
        {},
        page="/documents",
    )
    assert forbidden_delete.status_code == 404
    assert (await student.get("/documents")).status_code == 403
    login = await anonymous.get("/documents")
    assert login.status_code == 303
    assert login.headers["location"] == "/login"


@pytest.mark.anyio
async def test_t06_upload_and_delete_forms_require_csrf(world: World) -> None:
    client = await _client(world)
    file = ("lecture.pdf", b"%PDF-1.4 fixture", "application/pdf")

    missing_csrf = await client.post("/documents/upload", files={"file": file})
    assert missing_csrf.status_code == 403

    token = await csrf_token(client, "/documents")
    uploaded = await client.post(
        "/documents/upload",
        data={"csrf_token": token},
        files={"file": file},
    )
    assert uploaded.status_code == 303
    assert uploaded.headers["location"] == "/documents?notice=uploaded"
    page = await client.get("/documents")
    assert "lecture.pdf" in page.text

    document_id = world.repository.list_for_owner(world.teacher.id)[0].document.id
    deleted = await post_form(
        client,
        f"/documents/{document_id}/delete",
        {},
        page="/documents",
    )
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/documents?notice=deleted"
    assert "lecture.pdf" not in (await client.get("/documents")).text
