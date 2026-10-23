"""Read-only preparation of the 100 registered seed PDFs for ingestion."""

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from questly.core.documents.models import DocumentStatus
from questly.core.documents.ports import KnowledgeDocumentRepository, ObjectStorage

SEED_COURSE = "seed-pith"


class SeedPreflightError(ValueError):
    """Seed data does not satisfy the ingestion prerequisites."""


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
