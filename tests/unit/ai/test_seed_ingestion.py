"""Seed-specific metadata is carried through the existing PDF pipeline."""

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

import pytest
from scripts.ingest_seed import load_manifest

from questly.ai.ingestion.models import (
    DocumentExtraction,
    ExtractedPage,
    PageExtractionOutcome,
)
from questly.ai.ingestion.pipeline import PdfIngestionService
from questly.ai.ingestion.seed import (
    SeedIngestionError,
    SeedItem,
    SeedPreflightError,
    SeedStoredChunk,
    SeedStoredSource,
    ingest_prepared,
    inspect_existing,
    prepare_seed,
)
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


def _write_manifest(path: Path, *, duplicate: bool = False) -> None:
    rows = ["task_id,r2_key,topic,difficulty"]
    for index in range(100):
        task_id = "task-0" if duplicate and index == 99 else f"task-{index}"
        rows.append(f"{task_id},sources/programming-in-th/{task_id}.pdf,arrays,easy")
    path.write_text("\ufeff" + "\n".join(rows), encoding="utf-8")


def test_manifest_loads_exactly_100_unique_seed_items(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest)

    items = load_manifest(manifest)

    assert len(items) == 100
    assert items[0].task_id == "task-0"
    assert items[-1].r2_key == "sources/programming-in-th/task-99.pdf"


def test_manifest_rejects_duplicate_task_ids(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest, duplicate=True)

    with pytest.raises(SeedPreflightError, match="duplicate task IDs"):
        load_manifest(manifest)


class _Inspector:
    def __init__(self) -> None:
        self.sources: dict[int, SeedStoredSource] = {}

    def get_by_core_document_id(self, core_document_id: int) -> SeedStoredSource | None:
        return self.sources.get(core_document_id)


class _Ingester:
    def __init__(self, inspector: _Inspector) -> None:
        self.inspector = inspector
        self.pipeline = PdfIngestionService(
            _Extractor(), _Embedder(), FakeVectorRepository()
        )
        self.calls = 0
        self.fail = False

    def ingest(
        self,
        pdf_bytes: bytes,
        *,
        core_document_id: int,
        r2_object_key: str,
        metadata: dict[str, object] | None = None,
    ):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated embedding failure")
        result = self.pipeline.ingest(
            pdf_bytes,
            core_document_id=core_document_id,
            r2_object_key=r2_object_key,
            metadata=metadata,
        )
        self.inspector.sources[core_document_id] = SeedStoredSource(
            core_document_id=core_document_id,
            r2_object_key=result.source.r2_object_key,
            content_hash=result.source.content_hash,
            status=result.source.status,
            embedding_model=result.source.embedding_model,
            embedding_dim=result.source.embedding_dim,
            metadata=result.source.metadata,
            chunks=tuple(
                SeedStoredChunk(
                    chunk_index=chunk.chunk_index,
                    page=chunk.page,
                    embedding_model=chunk.embedding_model,
                    has_embedding=chunk.embedding is not None,
                    task_id=chunk.metadata.get("task_id"),
                )
                for chunk in result.chunks
            ),
        )
        return result


def _batch():
    documents = FakeKnowledgeDocumentRepository()
    storage = FakeObjectStorage()
    item = _item()
    storage.put(item.r2_key, b"seed pdf")
    document = _catalog_document(documents, object_key=item.r2_key)
    prepared = prepare_seed((item,), owner_id=6, documents=documents, storage=storage)
    inspector = _Inspector()
    ingester = _Ingester(inspector)
    return documents, document, prepared, inspector, ingester


def test_seed_batch_creates_then_skips_verified_existing_source() -> None:
    documents, document, prepared, inspector, ingester = _batch()

    first = ingest_prepared(
        prepared,
        owner_id=6,
        documents=documents,
        inspector=inspector,
        ingester=ingester,
        embedding_model="test-embedding-model",
    )
    refreshed = prepare_seed(
        (_item(),),
        owner_id=6,
        documents=documents,
        storage=_storage_with_seed_pdf(),
    )
    second = ingest_prepared(
        refreshed,
        owner_id=6,
        documents=documents,
        inspector=inspector,
        ingester=ingester,
        embedding_model="test-embedding-model",
    )

    assert (first.created, first.existing) == (1, 0)
    assert (second.created, second.existing) == (0, 1)
    assert ingester.calls == 1
    assert isinstance(document.id, int)
    assert (
        documents.get_for_owner(document.id, 6).document.status is DocumentStatus.READY
    )


def _storage_with_seed_pdf() -> FakeObjectStorage:
    storage = FakeObjectStorage()
    storage.put(_item().r2_key, b"seed pdf")
    return storage


def test_seed_batch_marks_core_failed_when_pipeline_fails() -> None:
    documents, document, prepared, inspector, ingester = _batch()
    ingester.fail = True

    with pytest.raises(SeedIngestionError, match="ingestion failed"):
        ingest_prepared(
            prepared,
            owner_id=6,
            documents=documents,
            inspector=inspector,
            ingester=ingester,
            embedding_model="test-embedding-model",
        )

    assert isinstance(document.id, int)
    assert (
        documents.get_for_owner(document.id, 6).document.status is DocumentStatus.FAILED
    )


def test_seed_batch_rejects_incomplete_existing_rag_without_writes() -> None:
    documents, document, prepared, inspector, ingester = _batch()
    assert isinstance(document.id, int)
    ingester.ingest(
        b"seed pdf",
        core_document_id=document.id,
        r2_object_key=_item().r2_key,
        metadata=prepared[0].metadata,
    )
    source = inspector.sources[document.id]
    inspector.sources[document.id] = replace(
        source, chunks=(replace(source.chunks[0], task_id="wrong"),)
    )

    with pytest.raises(SeedIngestionError, match="stored chunks are incomplete"):
        inspect_existing(prepared, inspector, embedding_model="test-embedding-model")

    assert (
        documents.get_for_owner(document.id, 6).document.status
        is DocumentStatus.UPLOADED
    )


def test_seed_batch_rejects_interrupted_core_before_any_writes() -> None:
    documents, document, prepared, inspector, ingester = _batch()
    assert isinstance(document.id, int)
    documents.seed_status(document.id, DocumentStatus.INGESTING)
    refreshed = prepare_seed(
        (_item(),),
        owner_id=6,
        documents=documents,
        storage=_storage_with_seed_pdf(),
    )

    with pytest.raises(SeedIngestionError, match="ingesting without RAG"):
        ingest_prepared(
            refreshed,
            owner_id=6,
            documents=documents,
            inspector=inspector,
            ingester=ingester,
            embedding_model="test-embedding-model",
        )

    assert ingester.calls == 0
