"""The background ingestion job and startup recovery in DocumentService."""

import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI

from questly import main
from questly.core.auth.models import Actor, Role
from questly.core.documents.models import DocumentStatus, KnowledgeDocument
from questly.core.documents.ports import DocumentRepositoryError
from questly.core.documents.service import DocumentService, fail_stuck_ingestions
from tests.fakes.app import build_app, fake_settings
from tests.fakes.documents import (
    FakeDocumentIngestor,
    FakeKnowledgeDocumentRepository,
    FakeObjectStorage,
    IngestRequest,
    failing_ingestor,
)

PDF = b"%PDF-1.7 lecture"


def _actor(user_id: int = 7, role: Role = Role.INSTRUCTOR) -> Actor:
    return Actor(
        user_id=user_id,
        role=role,
        full_name="Instructor",
        email="instructor@kmitl.ac.th",
    )


class BrokenTransitions(FakeKnowledgeDocumentRepository):
    """Raise a database error whenever a document would move to `broken`."""

    def __init__(self, *broken: DocumentStatus) -> None:
        super().__init__()
        self._broken = set(broken)

    def update_status_for_owner(
        self,
        document_id: int,
        uploaded_by: int,
        *,
        expected_status: DocumentStatus,
        new_status: DocumentStatus,
    ) -> bool:
        if new_status in self._broken:
            raise DocumentRepositoryError
        return super().update_status_for_owner(
            document_id,
            uploaded_by,
            expected_status=expected_status,
            new_status=new_status,
        )


class UnreadableRepository(FakeKnowledgeDocumentRepository):
    def get_for_owner(self, document_id: int, uploaded_by: int) -> None:
        raise DocumentRepositoryError


def _service(
    ingestor: FakeDocumentIngestor | None,
    repository: FakeKnowledgeDocumentRepository | None = None,
) -> tuple[DocumentService, FakeObjectStorage, FakeKnowledgeDocumentRepository]:
    storage = FakeObjectStorage()
    if repository is None:
        repository = FakeKnowledgeDocumentRepository()
    service = DocumentService(
        storage, repository, max_upload_size_bytes=1024, ingestor=ingestor
    )
    return service, storage, repository


def _status(
    service: DocumentService, document_id: int, actor: Actor | None = None
) -> DocumentStatus:
    if actor is None:
        actor = _actor()
    return service.get(actor, document_id).document.status


def test_an_ingested_upload_ends_ready() -> None:
    ingestor = FakeDocumentIngestor()
    service, _, _ = _service(ingestor)
    document = service.store(_actor(), filename="notes.pdf", data=PDF)

    service.ingest(_actor(), document.id)

    assert _status(service, document.id) is DocumentStatus.READY
    assert ingestor.requests == [
        IngestRequest(
            document_id=document.id,
            object_key=document.object_key,
            data=PDF,
            metadata={},
        )
    ]


def test_the_document_metadata_reaches_the_ingestor() -> None:
    ingestor = FakeDocumentIngestor()
    service, storage, repository = _service(ingestor)
    storage.put("sources/seed.pdf", PDF)
    seeded = repository.create(
        KnowledgeDocument(
            filename="seed.pdf",
            object_key="sources/seed.pdf",
            content_hash="seed",
            metadata={"topic": "math", "difficulty": "easy"},
        ),
        7,
    )

    service.ingest(_actor(), seeded.id)

    assert ingestor.requests[0].metadata == {"topic": "math", "difficulty": "easy"}


def test_a_rejected_document_ends_failed_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _, _ = _service(failing_ingestor())
    document = service.store(_actor(), filename="scan.pdf", data=PDF)

    with caplog.at_level(logging.ERROR):
        service.ingest(_actor(), document.id)

    assert _status(service, document.id) is DocumentStatus.FAILED
    assert f"ingesting document {document.id} failed" in caplog.text


def test_an_unexpected_ingestor_error_also_ends_failed() -> None:
    service, _, _ = _service(FakeDocumentIngestor(failure=RuntimeError("bug")))
    document = service.store(_actor(), filename="notes.pdf", data=PDF)

    service.ingest(_actor(), document.id)

    assert _status(service, document.id) is DocumentStatus.FAILED


def test_a_missing_object_ends_failed_without_calling_the_ingestor() -> None:
    ingestor = FakeDocumentIngestor()
    service, storage, _ = _service(ingestor)
    document = service.store(_actor(), filename="notes.pdf", data=PDF)
    storage.delete(document.object_key)

    service.ingest(_actor(), document.id)

    assert _status(service, document.id) is DocumentStatus.FAILED
    assert ingestor.requests == []


def test_a_failure_to_mark_ready_ends_failed() -> None:
    service, _, _ = _service(
        FakeDocumentIngestor(), BrokenTransitions(DocumentStatus.READY)
    )
    document = service.store(_actor(), filename="notes.pdf", data=PDF)

    service.ingest(_actor(), document.id)

    assert _status(service, document.id) is DocumentStatus.FAILED


@pytest.mark.parametrize(
    "status", [DocumentStatus.INGESTING, DocumentStatus.READY, DocumentStatus.FAILED]
)
def test_a_document_that_is_not_uploaded_is_left_alone(
    status: DocumentStatus,
) -> None:
    ingestor = FakeDocumentIngestor()
    service, _, repository = _service(ingestor)
    document = service.store(_actor(), filename="notes.pdf", data=PDF)
    repository.seed_status(document.id, status)

    service.ingest(_actor(), document.id)

    assert _status(service, document.id) is status
    assert ingestor.requests == []


def test_re_uploading_an_ingested_file_does_not_ingest_it_again() -> None:
    ingestor = FakeDocumentIngestor()
    service, _, _ = _service(ingestor)
    first = service.store(_actor(), filename="notes.pdf", data=PDF)
    service.ingest(_actor(), first.id)

    again = service.store(_actor(), filename="notes-copy.pdf", data=PDF)
    service.ingest(_actor(), again.id)

    assert again.id == first.id
    assert _status(service, first.id) is DocumentStatus.READY
    assert len(ingestor.requests) == 1


def test_without_an_ingestor_the_document_stays_uploaded() -> None:
    service, _, _ = _service(None)
    document = service.store(_actor(), filename="notes.pdf", data=PDF)

    service.ingest(_actor(), document.id)

    assert _status(service, document.id) is DocumentStatus.UPLOADED


def test_another_owners_document_is_not_ingested(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ingestor = FakeDocumentIngestor()
    service, _, _ = _service(ingestor)
    document = service.store(_actor(7), filename="notes.pdf", data=PDF)

    with caplog.at_level(logging.ERROR):
        service.ingest(_actor(8), document.id)
        service.ingest(_actor(7, Role.STUDENT), document.id)

    assert _status(service, document.id) is DocumentStatus.UPLOADED
    assert ingestor.requests == []
    assert caplog.text.count(f"could not start ingesting document {document.id}") == 2


def test_the_job_never_raises_when_the_repository_is_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _, _ = _service(FakeDocumentIngestor(), UnreadableRepository())

    with caplog.at_level(logging.ERROR):
        service.ingest(_actor(), 1)

    assert "could not start ingesting document 1" in caplog.text


def test_the_job_never_raises_when_marking_failed_also_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _, _ = _service(
        failing_ingestor(),
        BrokenTransitions(DocumentStatus.READY, DocumentStatus.FAILED),
    )
    document = service.store(_actor(), filename="notes.pdf", data=PDF)

    with caplog.at_level(logging.ERROR):
        service.ingest(_actor(), document.id)

    assert _status(service, document.id) is DocumentStatus.INGESTING
    assert f"could not mark document {document.id} failed" in caplog.text


def test_startup_recovery_fails_stuck_documents_so_they_can_be_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ingestor = FakeDocumentIngestor()
    service, _, repository = _service(ingestor)
    stuck = service.store(_actor(7), filename="a.pdf", data=b"a")
    other = service.store(_actor(8), filename="b.pdf", data=b"b")
    idle = service.store(_actor(7), filename="c.pdf", data=b"c")
    repository.seed_status(stuck.id, DocumentStatus.INGESTING)
    repository.seed_status(other.id, DocumentStatus.INGESTING)

    with caplog.at_level(logging.WARNING):
        assert fail_stuck_ingestions(repository) == 2

    assert _status(service, stuck.id) is DocumentStatus.FAILED
    assert _status(service, other.id, _actor(8)) is DocumentStatus.FAILED
    assert _status(service, idle.id) is DocumentStatus.UPLOADED
    assert "marked 2 documents left ingesting as failed" in caplog.text

    service.retry(_actor(7), stuck.id)
    service.ingest(_actor(7), stuck.id)
    assert _status(service, stuck.id) is DocumentStatus.READY


def test_startup_recovery_is_quiet_when_nothing_is_stuck(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        assert fail_stuck_ingestions(FakeKnowledgeDocumentRepository()) == 0

    assert caplog.text == ""


@pytest.fixture()
def stuck_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[FakeKnowledgeDocumentRepository]:
    """`questly.main:app` over fakes, with one document left `ingesting`."""
    repository = FakeKnowledgeDocumentRepository()
    stuck = repository.create(
        KnowledgeDocument(
            filename="a.pdf", object_key="uploads/a.pdf", content_hash="a"
        ),
        7,
    )
    repository.seed_status(stuck.id, DocumentStatus.INGESTING)

    def create_app(**_: object) -> FastAPI:
        return build_app(knowledge_document_repository=repository)

    monkeypatch.setattr(main, "get_settings", fake_settings)
    monkeypatch.setattr(main, "create_app", create_app)
    monkeypatch.setattr(main, "start_sweeper", lambda recover: None)
    main.production_app.cache_clear()
    yield repository
    main.production_app.cache_clear()


def test_only_the_production_app_fails_stuck_documents(
    stuck_repository: FakeKnowledgeDocumentRepository,
) -> None:
    build_app(knowledge_document_repository=stuck_repository)
    assert _stored_status(stuck_repository) is DocumentStatus.INGESTING

    main.production_app()

    assert _stored_status(stuck_repository) is DocumentStatus.FAILED


def test_the_production_app_starts_when_recovery_fails(
    stuck_repository: FakeKnowledgeDocumentRepository,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def unreachable() -> int:
        raise DocumentRepositoryError

    monkeypatch.setattr(stuck_repository, "fail_ingesting", unreachable)

    with caplog.at_level(logging.ERROR):
        application = main.production_app()

    assert isinstance(application, FastAPI)
    assert "could not fail documents left ingesting" in caplog.text


def _stored_status(repository: FakeKnowledgeDocumentRepository) -> DocumentStatus:
    item = repository.get_for_owner(1, 7)
    assert item is not None
    return item.document.status
