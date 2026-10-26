"""Read-only preparation of the 100 registered seed PDFs for ingestion."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from questly.ai.app.models import EMBEDDING_DIMENSION, SourceCreationResult
from questly.core.documents.models import DocumentStatus
from questly.core.documents.ports import KnowledgeDocumentRepository, ObjectStorage

SEED_COURSE = "seed-pith"


class SeedPreflightError(ValueError):
    """Seed data does not satisfy the ingestion prerequisites."""


class SeedIngestionError(RuntimeError):
    """A prepared seed could not be safely written or verified."""


@dataclass(frozen=True, slots=True)
class SeedItem:
    """One manifest entry with the labels expected in the Core catalog."""

    task_id: str
    r2_key: str
    topic: str
    difficulty: str


@dataclass(frozen=True, slots=True)
class PreparedSeed:
    """Verified Core document and bytes that can be ingested without lookup."""

    item: SeedItem
    core_document_id: int
    r2_object_key: str
    pdf_bytes: bytes
    core_status: DocumentStatus
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class SeedStoredChunk:
    """Only the stored chunk fields needed for post-write verification."""

    chunk_index: int
    page: int | None
    embedding_model: str | None
    has_embedding: bool
    task_id: object


@dataclass(frozen=True, slots=True)
class SeedStoredSource:
    """A read-only RAG snapshot for one Core document."""

    core_document_id: int
    r2_object_key: str
    content_hash: str
    status: str
    embedding_model: str | None
    embedding_dim: int | None
    metadata: Mapping[str, object]
    chunks: tuple[SeedStoredChunk, ...]


class SeedSourceInspector(Protocol):
    """Read-only check of RAG rows, implemented by the command-line runner."""

    def get_by_core_document_id(
        self, core_document_id: int
    ) -> SeedStoredSource | None: ...


class SeedIngester(Protocol):
    """The existing PDF pipeline, without exposing its concrete adapters."""

    def ingest(
        self,
        pdf_bytes: bytes,
        *,
        core_document_id: int,
        r2_object_key: str,
        metadata: Mapping[str, object] | None = None,
    ) -> SourceCreationResult: ...


@dataclass(frozen=True, slots=True)
class SeedRunSummary:
    created: int
    existing: int


def prepare_seed(
    items: Sequence[SeedItem],
    *,
    owner_id: int,
    documents: KnowledgeDocumentRepository,
    storage: ObjectStorage,
) -> tuple[PreparedSeed, ...]:
    """Match manifest PDFs to the owner's Core rows without writing anything.

    A seed may already be registered under an ``uploads/`` key instead of its
    manifest key. Match by content hash, then verify that Core's object holds
    the same bytes. Topic and difficulty must come from Core, not be silently
    substituted from the manifest.
    """
    prepared: list[PreparedSeed] = []
    seen_document_ids: set[int] = set()
    for item in items:
        data = storage.get(item.r2_key)
        content_hash = sha256(data).hexdigest()
        document = documents.get_by_content_hash(content_hash, owner_id)
        if document is None or document.id is None:
            raise SeedPreflightError(
                f"{item.task_id}: no matching document owned by instructor"
            )
        if document.id in seen_document_ids:
            raise SeedPreflightError(
                f"{item.task_id}: two manifest entries map to document {document.id}"
            )
        for label, expected in (
            ("topic", item.topic),
            ("difficulty", item.difficulty),
        ):
            actual = document.metadata.get(label)
            if actual != expected:
                raise SeedPreflightError(
                    f"{item.task_id}: document {document.id} has {label}={actual!r}; "
                    f"manifest expects {expected!r}"
                )
        core_task_id = document.metadata.get("task_id")
        if core_task_id is not None and core_task_id != item.task_id:
            raise SeedPreflightError(
                f"{item.task_id}: document {document.id} has a different task_id"
            )
        core_course = document.metadata.get("course")
        if core_course is not None and core_course != SEED_COURSE:
            raise SeedPreflightError(
                f"{item.task_id}: document {document.id} belongs to another course"
            )
        if document.object_key != item.r2_key:
            core_data = storage.get(document.object_key)
            if sha256(core_data).hexdigest() != content_hash:
                raise SeedPreflightError(
                    f"{item.task_id}: document {document.id} object differs from seed"
                )
            data = core_data
        seen_document_ids.add(document.id)
        prepared.append(
            PreparedSeed(
                item=item,
                core_document_id=document.id,
                r2_object_key=document.object_key,
                pdf_bytes=data,
                core_status=document.status,
                metadata={
                    "topic": document.metadata["topic"],
                    "difficulty": document.metadata["difficulty"],
                    "task_id": item.task_id,
                    "course": SEED_COURSE,
                },
            )
        )
    return tuple(prepared)


def validate_stored_source(
    prepared: PreparedSeed,
    source: SeedStoredSource,
    *,
    embedding_model: str,
) -> None:
    """Reject incomplete or conflicting RAG rows instead of silently skipping."""
    item = prepared.item
    if (
        source.core_document_id != prepared.core_document_id
        or source.r2_object_key != prepared.r2_object_key
        or source.content_hash != sha256(prepared.pdf_bytes).hexdigest()
        or source.status != "ready"
        or source.embedding_model != embedding_model
        or source.embedding_dim != EMBEDDING_DIMENSION
        or any(
            source.metadata.get(key) != value
            for key, value in prepared.metadata.items()
        )
    ):
        raise SeedIngestionError(f"{item.task_id}: existing RAG source is inconsistent")
    if not 1 <= len(source.chunks) <= 2:
        raise SeedIngestionError(f"{item.task_id}: expected one or two stored chunks")
    if any(
        chunk.chunk_index != index
        or chunk.page is None
        or chunk.page < 1
        or chunk.embedding_model != embedding_model
        or not chunk.has_embedding
        or chunk.task_id != item.task_id
        for index, chunk in enumerate(source.chunks)
    ):
        raise SeedIngestionError(f"{item.task_id}: stored chunks are incomplete")


def inspect_existing(
    prepared: Sequence[PreparedSeed],
    inspector: SeedSourceInspector,
    *,
    embedding_model: str,
) -> int:
    """Check every already-ingested source before the batch can write."""
    existing = 0
    for seed in prepared:
        source = inspector.get_by_core_document_id(seed.core_document_id)
        if source is not None:
            validate_stored_source(seed, source, embedding_model=embedding_model)
            existing += 1
        elif seed.core_status is DocumentStatus.READY:
            raise SeedIngestionError(
                f"{seed.item.task_id}: Core is ready but RAG source is missing"
            )
        elif seed.core_status is DocumentStatus.INGESTING:
            raise SeedIngestionError(
                f"{seed.item.task_id}: Core is ingesting without RAG; "
                "inspect the interrupted run before retrying"
            )
    return existing


def ingest_prepared(
    prepared: Sequence[PreparedSeed],
    *,
    owner_id: int,
    documents: KnowledgeDocumentRepository,
    inspector: SeedSourceInspector,
    ingester: SeedIngester,
    embedding_model: str,
) -> SeedRunSummary:
    """Resume a batch safely; mark Core ready only after RAG is verified."""
    inspect_existing(prepared, inspector, embedding_model=embedding_model)
    created = existing = 0
    for seed in prepared:
        document_id = seed.core_document_id
        source = inspector.get_by_core_document_id(document_id)
        if source is not None:
            validate_stored_source(seed, source, embedding_model=embedding_model)
            _set_ready(documents, document_id, owner_id, seed.core_status)
            existing += 1
            continue
        if seed.core_status is DocumentStatus.INGESTING:
            raise SeedIngestionError(
                f"{seed.item.task_id}: Core is already ingesting without RAG; "
                "inspect the interrupted run before retrying"
            )
        if not documents.update_status_for_owner(
            document_id,
            owner_id,
            expected_status=seed.core_status,
            new_status=DocumentStatus.INGESTING,
        ):
            raise SeedIngestionError(
                f"{seed.item.task_id}: Core status changed during ingestion"
            )
        try:
            ingester.ingest(
                seed.pdf_bytes,
                core_document_id=document_id,
                r2_object_key=seed.r2_object_key,
                metadata=seed.metadata,
            )
        except Exception as error:
            if not documents.update_status_for_owner(
                document_id,
                owner_id,
                expected_status=DocumentStatus.INGESTING,
                new_status=DocumentStatus.FAILED,
            ):
                raise SeedIngestionError(
                    f"{seed.item.task_id}: ingestion failed and Core status changed"
                ) from error
            raise SeedIngestionError(
                f"{seed.item.task_id}: ingestion failed"
            ) from error
        stored = inspector.get_by_core_document_id(document_id)
        if stored is None:
            raise SeedIngestionError(
                f"{seed.item.task_id}: RAG source missing after successful write"
            )
        validate_stored_source(seed, stored, embedding_model=embedding_model)
        _set_ready(documents, document_id, owner_id, DocumentStatus.INGESTING)
        created += 1
    return SeedRunSummary(created=created, existing=existing)


def _set_ready(
    documents: KnowledgeDocumentRepository,
    document_id: int,
    owner_id: int,
    current_status: DocumentStatus,
) -> None:
    if current_status is DocumentStatus.READY:
        return
    if not documents.update_status_for_owner(
        document_id,
        owner_id,
        expected_status=current_status,
        new_status=DocumentStatus.READY,
    ):
        raise SeedIngestionError(f"document {document_id}: could not mark Core ready")
