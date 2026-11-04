"""Characterise the error bodies of the Documents JSON routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.auth.models import Role
from questly.core.documents.models import DocumentStatus
from questly.core.documents.ports import DocumentRepositoryError, ObjectStorageError
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user
from tests.fakes.documents import FakeKnowledgeDocumentRepository, FakeObjectStorage
from tests.integration.test_storage_api import INSTRUCTOR, DocumentLibrary


def error(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"detail": {"code": code, "message": message}}


NOT_FOUND = error("knowledge_document_not_found", "Knowledge document not found")
DELETE_FAILED = error("document_delete_failed", "Document could not be deleted safely")


class _RowDeleteFails(FakeKnowledgeDocumentRepository):
    def delete_for_owner(self, document_id: int, uploaded_by: int) -> bool:
        raise DocumentRepositoryError


class _StorageCannotRestore(FakeObjectStorage):
    def __init__(self) -> None:
        super().__init__()
        self.armed = False

    def put(self, key: str, data: bytes):
        if self.armed:
            raise ObjectStorageError
        return super().put(key, data)


async def _login(app) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    await client.post(
        "/api/v1/auth/login",
        json={"email": INSTRUCTOR, "password": DEFAULT_PASSWORD},
    )
    return client


async def _upload(client: AsyncClient) -> int:
    response = await client.post(
        "/api/v1/documents", files={"file": ("notes.txt", b"hello")}
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.anyio
async def test_unknown_or_foreign_document_is_404_on_get_delete_and_retry() -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    other = await library.client_for("other-instructor@kmitl.ac.th")
    owned = await _upload(instructor)

    responses = [
        await instructor.get("/api/v1/documents/99999"),
        await instructor.delete("/api/v1/documents/99999"),
        await instructor.post("/api/v1/documents/99999/retry"),
        await other.get(f"/api/v1/documents/{owned}"),
        await other.delete(f"/api/v1/documents/{owned}"),
        await other.post(f"/api/v1/documents/{owned}/retry"),
    ]

    assert [r.status_code for r in responses] == [404] * 6
    assert [r.json() for r in responses] == [NOT_FOUND] * 6


@pytest.mark.anyio
async def test_upload_over_the_size_limit_is_413() -> None:
    users = FakeAuthRepository()
    seed_user(users, email=INSTRUCTOR, full_name="Instructor", role=Role.INSTRUCTOR)
    instructor = await _login(
        build_app(auth_repository=users, max_upload_size_bytes=4)
    )

    response = await instructor.post(
        "/api/v1/documents", files={"file": ("notes.txt", b"12345")}
    )

    assert response.status_code == 413
    assert response.json() == error("upload_too_large", "File exceeds size limit")


@pytest.mark.anyio
async def test_upload_of_zero_bytes_is_422_empty_upload() -> None:
    instructor = await DocumentLibrary().client_for(INSTRUCTOR)

    response = await instructor.post(
        "/api/v1/documents", files={"file": ("notes.txt", b"")}
    )

    assert response.status_code == 422
    assert response.json() == error(
        "empty_upload", "Upload needs a filename and at least one byte"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("seed", ["usage", DocumentStatus.READY])
async def test_deleting_a_referenced_or_ingested_document_is_409(
    seed: str | DocumentStatus,
) -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    document_id = await _upload(instructor)
    if seed == "usage":
        library.repository.seed_usage(document_id, 1)
    else:
        library.repository.seed_status(document_id, seed)

    response = await instructor.delete(f"/api/v1/documents/{document_id}")

    assert response.status_code == 409
    assert response.json() == error(
        "knowledge_document_in_use",
        "Document is referenced or has already been ingested",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [DocumentStatus.UPLOADED, DocumentStatus.INGESTING, DocumentStatus.READY],
)
async def test_retry_unless_failed_is_409_not_retryable(status: DocumentStatus) -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    document_id = await _upload(instructor)
    library.repository.seed_status(document_id, status)

    response = await instructor.post(f"/api/v1/documents/{document_id}/retry")

    assert response.status_code == 409
    assert response.json() == error(
        "knowledge_document_not_retryable",
        "Only a document whose ingestion failed can be retried",
    )


@pytest.mark.anyio
async def test_failed_row_delete_is_500_delete_failed() -> None:
    users = FakeAuthRepository()
    seed_user(users, email=INSTRUCTOR, full_name="Instructor", role=Role.INSTRUCTOR)
    instructor = await _login(
        build_app(
            auth_repository=users,
            knowledge_document_repository=_RowDeleteFails(),
            object_storage=FakeObjectStorage(),
        )
    )
    document_id = await _upload(instructor)

    response = await instructor.delete(f"/api/v1/documents/{document_id}")

    assert response.status_code == 500
    assert response.json() == DELETE_FAILED


@pytest.mark.anyio
async def test_failed_row_delete_and_failed_restore_is_also_500_delete_failed() -> None:
    users = FakeAuthRepository()
    seed_user(users, email=INSTRUCTOR, full_name="Instructor", role=Role.INSTRUCTOR)
    storage = _StorageCannotRestore()
    instructor = await _login(
        build_app(
            auth_repository=users,
            knowledge_document_repository=_RowDeleteFails(),
            object_storage=storage,
        )
    )
    document_id = await _upload(instructor)
    storage.armed = True

    response = await instructor.delete(f"/api/v1/documents/{document_id}")

    assert response.status_code == 500
    assert response.json() == DELETE_FAILED
