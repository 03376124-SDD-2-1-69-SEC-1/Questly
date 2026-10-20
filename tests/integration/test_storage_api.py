"""HTTP tests for the Instructor-owned knowledge document API."""

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.auth.models import Role
from questly.core.documents.models import DocumentStatus
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user
from tests.fakes.documents import FakeKnowledgeDocumentRepository, FakeObjectStorage

INSTRUCTOR = "instructor@kmitl.ac.th"
OTHER_INSTRUCTOR = "other-instructor@kmitl.ac.th"
STUDENT = "student@kmitl.ac.th"


class DocumentLibrary:
    def __init__(self) -> None:
        self.auth = FakeAuthRepository()
        seed_user(
            self.auth, email=INSTRUCTOR, full_name="Instructor", role=Role.INSTRUCTOR
        )
        seed_user(
            self.auth,
            email=OTHER_INSTRUCTOR,
            full_name="Other Instructor",
            role=Role.INSTRUCTOR,
        )
        seed_user(self.auth, email=STUDENT, full_name="Student", role=Role.STUDENT)
        self.repository = FakeKnowledgeDocumentRepository()
        self.storage = FakeObjectStorage()
        self.app = build_app(
            auth_repository=self.auth,
            knowledge_document_repository=self.repository,
            object_storage=self.storage,
        )

    async def client_for(self, email: str) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver"
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": DEFAULT_PASSWORD},
        )
        assert login.status_code == 200
        return client


@pytest.mark.anyio
async def test_upload_records_authenticated_owner_and_ignores_spoofed_form_value() -> (
    None
):
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)

    response = await instructor.post(
        "/api/v1/documents",
        data={"uploaded_by": "999999"},
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    listed = await instructor.get("/api/v1/documents")

    assert response.status_code == 201
    item = response.json()
    assert item["filename"] == "notes.txt"
    assert item["object_key"].startswith("uploads/")
    assert item["status"] == "uploaded"
    assert item["used_in"] == 0
    assert [row["id"] for row in listed.json()] == [item["id"]]
    owner = library.auth.find_user_by_email(INSTRUCTOR)
    assert owner is not None
    assert library.repository.owner_of(item["id"]) == owner.id
    assert list(library.storage.objects) == [item["object_key"]]


@pytest.mark.anyio
async def test_identical_upload_is_idempotent_per_owner() -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    other = await library.client_for(OTHER_INSTRUCTOR)

    first = await instructor.post(
        "/api/v1/documents", files={"file": ("first.txt", b"same bytes")}
    )
    repeat = await instructor.post(
        "/api/v1/documents", files={"file": ("repeat.txt", b"same bytes")}
    )
    other_owner = await other.post(
        "/api/v1/documents", files={"file": ("other.txt", b"same bytes")}
    )

    assert first.status_code == repeat.status_code == other_owner.status_code == 201
    assert repeat.json()["id"] == first.json()["id"]
    assert other_owner.json()["id"] != first.json()["id"]
    assert len(library.storage.objects) == 2


@pytest.mark.anyio
async def test_listing_is_owner_scoped_and_includes_saved_draft_usage_counts() -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    other = await library.client_for(OTHER_INSTRUCTOR)
    created = []
    for filename, content in (
        ("zero.txt", b"zero"),
        ("one.txt", b"one"),
        ("multiple.txt", b"multiple"),
    ):
        response = await instructor.post(
            "/api/v1/documents", files={"file": (filename, content)}
        )
        created.append(response.json())
    other_doc = await other.post(
        "/api/v1/documents", files={"file": ("private.txt", b"private")}
    )
    library.repository.seed_usage(created[1]["id"], 1)
    library.repository.seed_usage(created[2]["id"], 3)

    response = await instructor.get("/api/v1/documents")

    assert [item["used_in"] for item in response.json()] == [0, 1, 3]
    assert [item["id"] for item in response.json()] == [item["id"] for item in created]
    assert other_doc.json()["id"] not in {item["id"] for item in response.json()}


@pytest.mark.anyio
async def test_instructor_can_get_own_document_but_not_another_instructors() -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    other = await library.client_for(OTHER_INSTRUCTOR)
    created = await instructor.post(
        "/api/v1/documents", files={"file": ("notes.txt", b"private")}
    )
    document_id = created.json()["id"]

    own = await instructor.get(f"/api/v1/documents/{document_id}")
    foreign = await other.get(f"/api/v1/documents/{document_id}")

    assert own.status_code == 200
    assert own.json()["id"] == document_id
    assert own.json()["used_in"] == 0
    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "knowledge_document_not_found"


@pytest.mark.anyio
async def test_instructor_can_delete_own_unused_document_and_storage_object() -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    created = await instructor.post(
        "/api/v1/documents", files={"file": ("notes.txt", b"unused")}
    )
    object_key = created.json()["object_key"]

    deleted = await instructor.delete(f"/api/v1/documents/{created.json()['id']}")
    missing = await instructor.get(f"/api/v1/documents/{created.json()['id']}")

    assert deleted.status_code == 204
    assert object_key not in library.storage.objects
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_delete_is_hidden_for_other_owners_and_refused_when_referenced() -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    other = await library.client_for(OTHER_INSTRUCTOR)
    created = await instructor.post(
        "/api/v1/documents", files={"file": ("notes.txt", b"referenced")}
    )
    document_id = created.json()["id"]
    library.repository.seed_usage(document_id, 1)

    foreign = await other.delete(f"/api/v1/documents/{document_id}")
    referenced = await instructor.delete(f"/api/v1/documents/{document_id}")

    assert foreign.status_code == 404
    assert referenced.status_code == 409
    assert created.json()["object_key"] in library.storage.objects


@pytest.mark.anyio
async def test_missing_document_ids_return_404_for_get_delete_and_retry() -> None:
    instructor = await DocumentLibrary().client_for(INSTRUCTOR)

    get_response = await instructor.get("/api/v1/documents/999999")
    delete_response = await instructor.delete("/api/v1/documents/999999")
    retry_response = await instructor.post("/api/v1/documents/999999/retry")

    assert get_response.status_code == 404
    assert delete_response.status_code == retry_response.status_code == 404
    assert get_response.json()["detail"]["code"] == "knowledge_document_not_found"


@pytest.mark.anyio
async def test_instructor_can_retry_own_failed_document() -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    created = await instructor.post(
        "/api/v1/documents", files={"file": ("notes.pdf", b"broken")}
    )
    document_id = created.json()["id"]
    library.repository.seed_status(document_id, DocumentStatus.FAILED)

    retried = await instructor.post(f"/api/v1/documents/{document_id}/retry")
    detail = await instructor.get(f"/api/v1/documents/{document_id}")

    assert retried.status_code == 200
    assert retried.json()["id"] == document_id
    assert retried.json()["status"] == "uploaded"
    assert detail.json()["status"] == "uploaded"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [DocumentStatus.UPLOADED, DocumentStatus.INGESTING, DocumentStatus.READY],
)
async def test_retry_is_refused_unless_ingestion_failed(
    status: DocumentStatus,
) -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    created = await instructor.post(
        "/api/v1/documents", files={"file": ("notes.pdf", b"fine")}
    )
    document_id = created.json()["id"]
    library.repository.seed_status(document_id, status)

    response = await instructor.post(f"/api/v1/documents/{document_id}/retry")
    detail = await instructor.get(f"/api/v1/documents/{document_id}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "knowledge_document_not_retryable"
    assert detail.json()["status"] == status.value


@pytest.mark.anyio
async def test_retry_is_hidden_for_another_instructors_document() -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)
    other = await library.client_for(OTHER_INSTRUCTOR)
    created = await instructor.post(
        "/api/v1/documents", files={"file": ("notes.pdf", b"broken")}
    )
    document_id = created.json()["id"]
    library.repository.seed_status(document_id, DocumentStatus.FAILED)

    foreign = await other.post(f"/api/v1/documents/{document_id}/retry")
    detail = await instructor.get(f"/api/v1/documents/{document_id}")

    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "knowledge_document_not_found"
    assert detail.json()["status"] == "failed"


@pytest.mark.anyio
async def test_student_and_anonymous_document_requests_follow_auth_rules() -> None:
    library = DocumentLibrary()
    student = await library.client_for(STUDENT)
    anonymous = AsyncClient(
        transport=ASGITransport(app=library.app), base_url="http://testserver"
    )

    student_list = await student.get("/api/v1/documents")
    student_upload = await student.post(
        "/api/v1/documents", files={"file": ("notes.txt", b"student")}
    )
    student_detail = await student.get("/api/v1/documents/1")
    student_delete = await student.delete("/api/v1/documents/1")
    student_retry = await student.post("/api/v1/documents/1/retry")
    anonymous_list = await anonymous.get("/api/v1/documents")
    anonymous_upload = await anonymous.post(
        "/api/v1/documents", files={"file": ("notes.txt", b"anonymous")}
    )
    anonymous_detail = await anonymous.get("/api/v1/documents/1")
    anonymous_delete = await anonymous.delete("/api/v1/documents/1")
    anonymous_retry = await anonymous.post("/api/v1/documents/1/retry")

    assert student_list.status_code == student_upload.status_code == 403
    assert student_detail.status_code == student_delete.status_code == 403
    assert student_retry.status_code == 403
    assert anonymous_list.status_code == anonymous_upload.status_code == 401
    assert anonymous_detail.status_code == anonymous_delete.status_code == 401
    assert anonymous_retry.status_code == 401


@pytest.mark.anyio
async def test_invalid_document_id_uses_standard_validation_response() -> None:
    instructor = await DocumentLibrary().client_for(INSTRUCTOR)

    response = await instructor.get("/api/v1/documents/not-an-integer")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "request_validation_error"


@pytest.mark.anyio
async def test_openapi_exposes_documents_and_not_uploads() -> None:
    library = DocumentLibrary()
    instructor = await library.client_for(INSTRUCTOR)

    paths = (await instructor.get("/openapi.json")).json()["paths"]

    assert set(paths["/api/v1/documents"]) == {"post", "get"}
    assert set(paths["/api/v1/documents/{document_id}"]) == {"get", "delete"}
    assert set(paths["/api/v1/documents/{document_id}/retry"]) == {"post"}
    assert "/api/v1/uploads" not in paths
