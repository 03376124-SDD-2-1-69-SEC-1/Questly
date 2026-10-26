"""Storage, persistence and ingestion ports for the documents use cases."""

from collections.abc import Mapping
from typing import Protocol

from questly.core.documents.models import (
    DocumentLibraryItem,
    DocumentStatus,
    KnowledgeDocument,
    StoredObject,
)


class ObjectStorageError(Exception):
    """Base error raised by an object-storage adapter."""


class DocumentRepositoryError(Exception):
    """Base error raised by a document-repository adapter."""


class DocumentIngestionError(Exception):
    """Raised by a document ingestor when the PDF could not be made searchable."""


class ObjectStorage(Protocol):
    """Somewhere to put uploaded bytes and retrieve or remove them later."""

    def put(self, key: str, data: bytes) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


class KnowledgeDocumentRepository(Protocol):
    """Persistence operations for the owner-scoped document library.

    The owner is passed separately rather than added to KnowledgeDocument so
    this domain change does not conflict with the OPS-18 model work.
    """

    def create(
        self, document: KnowledgeDocument, uploaded_by: int
    ) -> KnowledgeDocument: ...

    def get_by_content_hash(
        self, content_hash: str, uploaded_by: int
    ) -> KnowledgeDocument | None: ...

    def list_for_owner(self, uploaded_by: int) -> list[DocumentLibraryItem]: ...

    def get_for_owner(
        self, document_id: int, uploaded_by: int
    ) -> DocumentLibraryItem | None: ...

    def delete_for_owner(self, document_id: int, uploaded_by: int) -> bool: ...

    def update_status_for_owner(
        self,
        document_id: int,
        uploaded_by: int,
        *,
        expected_status: DocumentStatus,
        new_status: DocumentStatus,
    ) -> bool: ...

    def fail_ingesting(self) -> int:
        """Move every owner's `ingesting` document to `failed`; return how many."""
        ...


class DocumentIngestor(Protocol):
    """Turn a stored PDF into searchable chunks for retrieval."""

    def ingest(
        self,
        *,
        document_id: int,
        object_key: str,
        data: bytes,
        metadata: Mapping[str, str],
    ) -> None:
        """Replace any earlier result for `document_id`.

        Raises DocumentIngestionError when the document cannot be ingested.
        """
        ...
