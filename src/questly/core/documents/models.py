"""Domain representation of an uploaded knowledge document."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "DocumentLibraryItem",
    "DocumentStatus",
    "KnowledgeDocument",
    "StoredObject",
]


class DocumentStatus(StrEnum):
    """Where a document is in the ingestion pipeline.

    The same four values the `core.knowledge_documents` CHECK constraint allows,
    declared here so an invalid status fails in the domain instead of at the
    database.
    """

    UPLOADED = "uploaded"
    INGESTING = "ingesting"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Where object storage put the bytes.

    `bucket` is deployment configuration, not a fact about the document, so it
    stops here and never reaches `KnowledgeDocument` or the HTTP response.
    """

    bucket: str
    key: str


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """A file an instructor uploaded, and the object key holding its bytes.

    `id` is `None` only before the repository has persisted the document — see
    KnowledgeDocumentRepository.create(). Anything a repository returns has a
    non-None id. `metadata` holds catalog labels such as topic and difficulty.
    """

    filename: str
    object_key: str
    content_hash: str
    status: DocumentStatus = DocumentStatus.UPLOADED
    metadata: Mapping[str, str] = field(default_factory=dict)
    id: int | None = None


@dataclass(frozen=True, slots=True)
class DocumentLibraryItem:
    """A document in an Instructor's library with its saved-draft usage count."""

    document: KnowledgeDocument
    used_in: int
