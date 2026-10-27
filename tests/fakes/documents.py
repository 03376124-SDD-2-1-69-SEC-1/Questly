"""In-memory ObjectStorage and KnowledgeDocumentRepository for tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from questly.core.documents.models import (
    DocumentLibraryItem,
    DocumentStatus,
    KnowledgeDocument,
    StoredObject,
)
from questly.core.documents.ports import DocumentIngestionError, ObjectStorageError

FAKE_BUCKET = "fake-bucket"


class FakeObjectStorage:
    """Keep uploaded bytes in a dict instead of calling R2."""

    def __init__(self, bucket: str = FAKE_BUCKET) -> None:
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> StoredObject:
        self.objects[key] = data
        return StoredObject(bucket=self.bucket, key=key)

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as error:
            raise ObjectStorageError from error

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class FakeKnowledgeDocumentRepository:
    """Store documents, owners and saved-draft usage in process for tests."""

    def __init__(self) -> None:
        self._items: dict[int, KnowledgeDocument] = {}
        self._owners: dict[int, int] = {}
        self._usage: dict[int, int] = {}
        self._next_id = 1

    def create(
        self, document: KnowledgeDocument, uploaded_by: int
    ) -> KnowledgeDocument:
        new_id = self._next_id
        self._next_id += 1
        created = replace(document, id=new_id)
        self._items[new_id] = created
        self._owners[new_id] = uploaded_by
        self._usage[new_id] = 0
        return created

    def get_by_content_hash(
        self, content_hash: str, uploaded_by: int
    ) -> KnowledgeDocument | None:
        for document_id, document in self._items.items():
            if (
                document.content_hash == content_hash
                and self._owners[document_id] == uploaded_by
            ):
                return document
        return None

    def list_for_owner(self, uploaded_by: int) -> list[DocumentLibraryItem]:
        return [
            self._library_item(document_id)
            for document_id in sorted(self._items)
            if self._owners[document_id] == uploaded_by
        ]

    def get_for_owner(
        self, document_id: int, uploaded_by: int
    ) -> DocumentLibraryItem | None:
        if self._owners.get(document_id) != uploaded_by:
            return None
        return self._library_item(document_id)

    def delete_for_owner(self, document_id: int, uploaded_by: int) -> bool:
        document = self._items.get(document_id)

        if document is None:
            return False

        if self._owners.get(document_id) != uploaded_by:
            return False

        if document.status != DocumentStatus.UPLOADED:
            return False

        if self._usage.get(document_id, 0) > 0:
            return False

        del self._items[document_id]
        del self._owners[document_id]
        del self._usage[document_id]
        return True

    def update_status_for_owner(
        self,
        document_id: int,
        uploaded_by: int,
        *,
        expected_status: DocumentStatus,
        new_status: DocumentStatus,
    ) -> bool:
        document = self._items.get(document_id)

        if document is None:
            return False

        if self._owners.get(document_id) != uploaded_by:
            return False

        if document.status != expected_status:
            return False

        self._items[document_id] = replace(document, status=new_status)
        return True

    def fail_ingesting(self) -> int:
        stuck = [
            document_id
            for document_id, document in self._items.items()
            if document.status is DocumentStatus.INGESTING
        ]
        for document_id in stuck:
            self._items[document_id] = replace(
                self._items[document_id], status=DocumentStatus.FAILED
            )
        return len(stuck)

    def seed_usage(self, document_id: int, count: int = 1) -> None:
        """Set saved-draft usage for deterministic contract and service tests."""
        self._usage[document_id] += count

    def seed_status(self, document_id: int, status: DocumentStatus) -> None:
        """Set an ingestion state for deterministic service tests."""
        self._items[document_id] = replace(self._items[document_id], status=status)

    def owner_of(self, document_id: int) -> int:
        """Return the recorded owner for upload persistence assertions."""
        return self._owners[document_id]

    def _library_item(self, document_id: int) -> DocumentLibraryItem:
        return DocumentLibraryItem(
            document=self._items[document_id],
            used_in=self._usage[document_id],
        )


@dataclass(frozen=True, slots=True)
class IngestRequest:
    """What one `DocumentIngestor.ingest` call received."""

    document_id: int
    object_key: str
    data: bytes
    metadata: Mapping[str, str]


@dataclass(slots=True)
class FakeDocumentIngestor:
    """Record ingest calls; raise `failure` from each one when set."""

    failure: Exception | None = None
    requests: list[IngestRequest] = field(default_factory=list)

    def ingest(
        self,
        *,
        document_id: int,
        object_key: str,
        data: bytes,
        metadata: Mapping[str, str],
    ) -> None:
        self.requests.append(IngestRequest(document_id, object_key, data, metadata))
        if self.failure is not None:
            raise self.failure


def failing_ingestor() -> FakeDocumentIngestor:
    """An ingestor that rejects every document the way the port's adapter does."""
    return FakeDocumentIngestor(failure=DocumentIngestionError("unreadable PDF"))
