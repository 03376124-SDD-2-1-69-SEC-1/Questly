"""Seed-specific metadata is carried through the existing PDF pipeline."""

from dataclasses import dataclass
from hashlib import sha256

import pytest

from questly.ai.ingestion.models import (
    DocumentExtraction,
    ExtractedPage,
    PageExtractionOutcome,
)
from questly.ai.ingestion.pipeline import PdfIngestionService
from questly.ai.ingestion.seed import SeedItem, SeedPreflightError, prepare_seed
from questly.core.documents.models import DocumentStatus, KnowledgeDocument
from tests.fakes.documents import FakeKnowledgeDocumentRepository, FakeObjectStorage
from tests.fakes.vector import FakeVectorRepository


@dataclass(slots=True)
class _Extractor:
    def extract(self, pdf_bytes: bytes) -> DocumentExtraction:
        assert pdf_bytes == b"seed pdf"
        return DocumentExtraction(
            (
                ExtractedPage(
                    1,
                    "โจทย์\nข้อมูลนำเข้า\nN\n",
                    PageExtractionOutcome.EXTRACTED,
                ),
            )
        )


class _Embedder:
    @property
    def model(self) -> str:
        return "test-embedding-model"

    def embed(self, text: str) -> tuple[float, ...]:
        assert text.strip()
        return (1.0, *(0.0 for _ in range(767)))


def test_seed_task_id_is_stored_on_source_and_each_chunk() -> None:
    repository = FakeVectorRepository()
    service = PdfIngestionService(_Extractor(), _Embedder(), repository)

    result = service.ingest(
        b"seed pdf",
        core_document_id=7,
        r2_object_key="sources/programming-in-th/7.pdf",
        metadata={"task_id": "7", "topic": "arrays", "difficulty": "easy"},
    )

    assert result.source.metadata == {
        "task_id": "7",
        "topic": "arrays",
        "difficulty": "easy",
    }
    assert len(result.chunks) == 2
    assert [chunk.metadata["task_id"] for chunk in result.chunks] == ["7", "7"]
    assert all("topic" not in chunk.metadata for chunk in result.chunks)


def _item() -> SeedItem:
    return SeedItem(
        task_id="codecube_001",
        r2_key="sources/programming-in-th/codecube_001.pdf",
        topic="io-arithmetic",
        difficulty="easy",
    )


def _catalog_document(
    repository: FakeKnowledgeDocumentRepository,
    *,
    object_key: str,
    data: bytes = b"seed pdf",
    metadata: dict[str, str] | None = None,
    owner_id: int = 6,
) -> KnowledgeDocument:
    if metadata is None:
        metadata = {"topic": "io-arithmetic", "difficulty": "easy"}
    return repository.create(
        KnowledgeDocument(
            filename="seed.pdf",
            object_key=object_key,
            content_hash=sha256(data).hexdigest(),
            metadata=metadata,
        ),
        owner_id,
    )


def test_preflight_copies_labels_from_core_without_writing() -> None:
    repository = FakeKnowledgeDocumentRepository()
    storage = FakeObjectStorage()
    item = _item()
    storage.put(item.r2_key, b"seed pdf")
    document = _catalog_document(repository, object_key=item.r2_key)

    (prepared,) = prepare_seed(
        (item,), owner_id=6, documents=repository, storage=storage
    )

    assert prepared.core_document_id == document.id
    assert prepared.r2_object_key == item.r2_key
    assert prepared.pdf_bytes == b"seed pdf"
    assert prepared.core_status is DocumentStatus.UPLOADED
    assert prepared.metadata == {
        "topic": "io-arithmetic",
        "difficulty": "easy",
        "task_id": "codecube_001",
        "course": "seed-pith",
    }
    assert isinstance(document.id, int)
    unchanged = repository.get_for_owner(document.id, 6)
    assert unchanged is not None
    assert unchanged.document.status is DocumentStatus.UPLOADED


def test_preflight_matches_preexisting_upload_by_hash_and_verifies_bytes() -> None:
    repository = FakeKnowledgeDocumentRepository()
    storage = FakeObjectStorage()
    item = _item()
    storage.put(item.r2_key, b"seed pdf")
    storage.put("uploads/existing.pdf", b"seed pdf")
    document = _catalog_document(repository, object_key="uploads/existing.pdf")

    (prepared,) = prepare_seed(
        (item,), owner_id=6, documents=repository, storage=storage
    )

    assert prepared.core_document_id == document.id
    assert prepared.r2_object_key == "uploads/existing.pdf"


@pytest.mark.parametrize(
    "metadata",
    [
        {"difficulty": "easy"},
        {"topic": "wrong", "difficulty": "easy"},
        {"topic": "io-arithmetic", "difficulty": "easy", "task_id": "other"},
    ],
)
def test_preflight_rejects_core_metadata_mismatch(metadata: dict[str, str]) -> None:
    repository = FakeKnowledgeDocumentRepository()
    storage = FakeObjectStorage()
    item = _item()
    storage.put(item.r2_key, b"seed pdf")
    document = _catalog_document(repository, object_key=item.r2_key, metadata=metadata)

    with pytest.raises(SeedPreflightError):
        prepare_seed((item,), owner_id=6, documents=repository, storage=storage)

    assert isinstance(document.id, int)
    unchanged = repository.get_for_owner(document.id, 6)
    assert unchanged is not None
    assert unchanged.document.status is DocumentStatus.UPLOADED


def test_preflight_rejects_foreign_document() -> None:
    repository = FakeKnowledgeDocumentRepository()
    storage = FakeObjectStorage()
    item = _item()
    storage.put(item.r2_key, b"seed pdf")
    _catalog_document(repository, object_key=item.r2_key, owner_id=10)

    with pytest.raises(SeedPreflightError, match="no matching document"):
        prepare_seed((item,), owner_id=6, documents=repository, storage=storage)


def test_preflight_rejects_preexisting_upload_with_different_bytes() -> None:
    repository = FakeKnowledgeDocumentRepository()
    storage = FakeObjectStorage()
    item = _item()
    storage.put(item.r2_key, b"seed pdf")
    storage.put("uploads/existing.pdf", b"changed pdf")
    _catalog_document(repository, object_key="uploads/existing.pdf")

    with pytest.raises(SeedPreflightError, match="object differs"):
        prepare_seed((item,), owner_id=6, documents=repository, storage=storage)
