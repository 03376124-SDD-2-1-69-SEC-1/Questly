"""Tests for document ownership and upload rules."""

from hashlib import sha256

import pytest

from questly.core.auth.models import Actor, PermissionDeniedError, Role
from questly.core.documents.models import DocumentStatus
from questly.core.documents.ports import DocumentRepositoryError
from questly.core.documents.service import (
    DocumentDeletionFailedError,
    DocumentInUseError,
    DocumentNotRetryableError,
    DocumentService,
    EmptyUploadError,
    KnowledgeDocumentNotFoundError,
    UploadTooLargeError,
)
from tests.fakes.documents import FakeKnowledgeDocumentRepository, FakeObjectStorage


def _actor(user_id: int = 7, role: Role = Role.INSTRUCTOR) -> Actor:
    return Actor(
        user_id=user_id,
        role=role,
        full_name="Instructor",
        email="instructor@kmitl.ac.th",
    )


def _setup(
    max_upload_size_bytes: int = 1024,
) -> tuple[DocumentService, FakeObjectStorage, FakeKnowledgeDocumentRepository]:
    storage = FakeObjectStorage()
    repository = FakeKnowledgeDocumentRepository()
    return (
        DocumentService(
            storage, repository, max_upload_size_bytes=max_upload_size_bytes
        ),
        storage,
        repository,
    )


def test_store_indexes_owned_document_and_hashes_content() -> None:
    service, _, _ = _setup()

    document = service.store(_actor(), filename="notes.txt", data=b"hello world")

    assert isinstance(document.id, int)
    assert document.filename == "notes.txt"
    assert document.object_key.startswith("uploads/")
    assert document.object_key.endswith("-notes.txt")
    assert document.content_hash == sha256(b"hello world").hexdigest()
    assert document.status is DocumentStatus.UPLOADED


def test_store_rejects_a_file_over_the_limit() -> None:
    service, _, _ = _setup(max_upload_size_bytes=4)

    with pytest.raises(UploadTooLargeError):
        service.store(_actor(), filename="big.bin", data=b"12345")


def test_store_rejects_an_empty_file_or_filename() -> None:
    service, _, _ = _setup()

    with pytest.raises(EmptyUploadError):
        service.store(_actor(), filename="empty.txt", data=b"")
    with pytest.raises(EmptyUploadError):
        service.store(_actor(), filename="../", data=b"x")


def test_store_strips_path_segments_from_the_filename() -> None:
    service, _, _ = _setup()

    document = service.store(_actor(), filename="../../etc/passwd", data=b"x")

    assert document.filename == "passwd"
    assert document.object_key.startswith("uploads/")
    assert ".." not in document.object_key


def test_identical_content_is_idempotent_only_within_the_same_owner() -> None:
    service, storage, repository = _setup()

    first = service.store(_actor(1), filename="notes.txt", data=b"same bytes")
    repeat = service.store(_actor(1), filename="notes-copy.txt", data=b"same bytes")
    other_owner = service.store(_actor(2), filename="notes.txt", data=b"same bytes")

    assert repeat == first
    assert other_owner.id != first.id
    assert len(storage.objects) == 2
    assert len(repository.list_for_owner(1)) == 1
    assert len(repository.list_for_owner(2)) == 1


def test_listing_returns_usage_count_and_is_owner_scoped() -> None:
    service, _, repository = _setup()
    first = service.store(_actor(1), filename="first.txt", data=b"first")
    second = service.store(_actor(1), filename="second.txt", data=b"second")
    service.store(_actor(2), filename="other.txt", data=b"other")
    repository.seed_usage(second.id, 2)

    library = service.list(_actor(1))

    assert [(item.document.id, item.used_in) for item in library] == [
        (first.id, 0),
        (second.id, 2),
    ]


def test_get_hides_another_owners_document_as_not_found() -> None:
    service, _, _ = _setup()
    document = service.store(_actor(1), filename="private.txt", data=b"private")

    with pytest.raises(KnowledgeDocumentNotFoundError):
        service.get(_actor(2), document.id)


def test_delete_removes_only_an_owned_unused_uploaded_object() -> None:
    service, storage, _ = _setup()
    document = service.store(_actor(), filename="notes.txt", data=b"unused")

    service.delete(_actor(), document.id)

    assert document.object_key not in storage.objects
    with pytest.raises(KnowledgeDocumentNotFoundError):
        service.get(_actor(), document.id)


def test_delete_refuses_documents_with_usage_or_ingestion() -> None:
    service, _, repository = _setup()
    referenced = service.store(_actor(), filename="used.txt", data=b"used")
    repository.seed_usage(referenced.id)
    ingested = service.store(_actor(), filename="ready.txt", data=b"ready")
    repository.seed_status(ingested.id, DocumentStatus.READY)

    with pytest.raises(DocumentInUseError):
        service.delete(_actor(), referenced.id)
    with pytest.raises(DocumentInUseError):
        service.delete(_actor(), ingested.id)


def test_delete_restores_storage_when_document_repository_fails() -> None:
    class FailedDeleteRepository(FakeKnowledgeDocumentRepository):
        def delete_for_owner(self, document_id: int, uploaded_by: int) -> bool:
            raise DocumentRepositoryError

    storage = FakeObjectStorage()
    repository = FailedDeleteRepository()
    service = DocumentService(storage, repository, max_upload_size_bytes=1024)
    document = service.store(_actor(), filename="notes.txt", data=b"keep me")

    with pytest.raises(DocumentDeletionFailedError):
        service.delete(_actor(), document.id)

    assert storage.objects[document.object_key] == b"keep me"
    assert service.get(_actor(), document.id).document == document


def test_retry_returns_a_failed_document_to_uploaded() -> None:
    service, _, repository = _setup()
    document = service.store(_actor(), filename="notes.pdf", data=b"broken")
    repository.seed_status(document.id, DocumentStatus.FAILED)

    retried = service.retry(_actor(), document.id)

    assert retried.document.id == document.id
    assert retried.document.status is DocumentStatus.UPLOADED
    assert service.get(_actor(), document.id).document.status is (
        DocumentStatus.UPLOADED
    )


@pytest.mark.parametrize(
    "status",
    [DocumentStatus.UPLOADED, DocumentStatus.INGESTING, DocumentStatus.READY],
)
def test_retry_refuses_a_document_whose_ingestion_has_not_failed(
    status: DocumentStatus,
) -> None:
    service, _, repository = _setup()
    document = service.store(_actor(), filename="notes.pdf", data=b"fine")
    repository.seed_status(document.id, status)

    with pytest.raises(DocumentNotRetryableError):
        service.retry(_actor(), document.id)

    assert service.get(_actor(), document.id).document.status is status


def test_retry_hides_another_owners_document_and_leaves_it_failed() -> None:
    service, _, repository = _setup()
    document = service.store(_actor(user_id=7), filename="notes.pdf", data=b"x")
    repository.seed_status(document.id, DocumentStatus.FAILED)

    with pytest.raises(KnowledgeDocumentNotFoundError):
        service.retry(_actor(user_id=8), document.id)
    with pytest.raises(KnowledgeDocumentNotFoundError):
        service.retry(_actor(user_id=7), 999999)

    assert service.get(_actor(user_id=7), document.id).document.status is (
        DocumentStatus.FAILED
    )


def test_non_instructors_cannot_use_document_service() -> None:
    service, _, _ = _setup()

    with pytest.raises(PermissionDeniedError):
        service.list(_actor(role=Role.STUDENT))
    with pytest.raises(PermissionDeniedError):
        service.store(_actor(role=Role.STUDENT), filename="x.txt", data=b"x")
    with pytest.raises(PermissionDeniedError):
        service.retry(_actor(role=Role.STUDENT), 1)
