"""Document-library use cases, independent from HTTP and storage technology."""

from __future__ import annotations

import logging
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import uuid4

from questly.core.auth.models import Actor, PermissionDeniedError, Role
from questly.core.documents.models import (
    DocumentLibraryItem,
    DocumentStatus,
    KnowledgeDocument,
)
from questly.core.documents.ports import (
    DocumentIngestor,
    DocumentRepositoryError,
    KnowledgeDocumentRepository,
    ObjectStorage,
    ObjectStorageError,
)

KEY_PREFIX = "uploads/"

logger = logging.getLogger(__name__)


class UploadTooLargeError(Exception):
    """Raised when an uploaded file exceeds the configured size limit."""


class EmptyUploadError(Exception):
    """Raised when an upload carries no bytes or no usable filename."""


class KnowledgeDocumentNotFoundError(Exception):
    """Raised when a requested document is absent from the current user's library."""


class DocumentInUseError(Exception):
    """Raised when a document is referenced or has left the uploaded state."""


class DocumentNotRetryableError(Exception):
    """Raised when a retry targets a document whose ingestion has not failed."""


class DocumentDeletionFailedError(Exception):
    """Raised when deleting the document row fails after removing its object."""


class DocumentDeletionCompensationError(Exception):
    """Raised when the object cannot be restored after a failed row deletion."""


class DocumentService:
    """Store and manage an Instructor's documents."""

    def __init__(
        self,
        storage: ObjectStorage,
        repository: KnowledgeDocumentRepository,
        *,
        max_upload_size_bytes: int,
        ingestor: DocumentIngestor | None = None,
    ) -> None:
        self._storage = storage
        self._repository = repository
        self._ingestor = ingestor
        self.max_upload_size_bytes = max_upload_size_bytes

    def store(self, actor: Actor, *, filename: str, data: bytes) -> KnowledgeDocument:
        """Store bytes under the authenticated Instructor's ownership."""
        self.authorize(actor)
        name = _safe_filename(filename)
        if not data:
            raise EmptyUploadError
        if len(data) > self.max_upload_size_bytes:
            raise UploadTooLargeError

        content_hash = sha256(data).hexdigest()
        existing = self._repository.get_by_content_hash(content_hash, actor.user_id)
        if existing is not None:
            return existing

        stored = self._storage.put(f"{KEY_PREFIX}{uuid4().hex}-{name}", data)
        return self._repository.create(
            KnowledgeDocument(
                filename=name,
                object_key=stored.key,
                content_hash=content_hash,
                status=DocumentStatus.UPLOADED,
            ),
            actor.user_id,
        )

    def list(self, actor: Actor) -> list[DocumentLibraryItem]:
        """List this Instructor's library, with usage counts."""
        self.authorize(actor)
        return self._repository.list_for_owner(actor.user_id)

    def get(self, actor: Actor, document_id: int) -> DocumentLibraryItem:
        """Return a document only when it belongs to this Instructor."""
        self.authorize(actor)
        item = self._repository.get_for_owner(document_id, actor.user_id)
        if item is None:
            raise KnowledgeDocumentNotFoundError
        return item

    def delete(self, actor: Actor, document_id: int) -> None:
        """Delete an unused, not-yet-ingested document and its stored object."""
        self.authorize(actor)
        item = self._repository.get_for_owner(document_id, actor.user_id)
        if item is None:
            raise KnowledgeDocumentNotFoundError
        if item.used_in or item.document.status is not DocumentStatus.UPLOADED:
            raise DocumentInUseError

        data = self._storage.get(item.document.object_key)
        self._storage.delete(item.document.object_key)
        try:
            deleted = self._repository.delete_for_owner(document_id, actor.user_id)
        except DocumentRepositoryError as error:
            try:
                self._storage.put(item.document.object_key, data)
            except ObjectStorageError as restore_error:
                raise DocumentDeletionCompensationError from restore_error
            raise DocumentDeletionFailedError from error

        if deleted:
            return

        try:
            self._storage.put(item.document.object_key, data)
        except ObjectStorageError as restore_error:
            raise DocumentDeletionCompensationError from restore_error
        current = self._repository.get_for_owner(document_id, actor.user_id)
        if current is None:
            raise KnowledgeDocumentNotFoundError
        raise DocumentInUseError

    def retry(self, actor: Actor, document_id: int) -> DocumentLibraryItem:
        """Queue a failed document for ingestion again by returning it to uploaded."""
        self.authorize(actor)
        moved = self._repository.update_status_for_owner(
            document_id,
            actor.user_id,
            expected_status=DocumentStatus.FAILED,
            new_status=DocumentStatus.UPLOADED,
        )
        item = self.get(actor, document_id)
        if not moved:
            raise DocumentNotRetryableError
        return item

    def ingest(self, actor: Actor, document_id: int) -> None:
        """Ingest an uploaded document, ending `ready` or `failed`.

        The background job scheduled after an upload or a retry. It never
        raises, because it runs after the response and an error would reach no
        one. A document that is not `uploaded` is left alone, so a re-upload of
        an ingested file or a second scheduling of the same job does nothing.
        Without an ingestor the document stays `uploaded`.
        """
        if self._ingestor is None:
            return
        try:
            document = self.get(actor, document_id).document
            started = self._repository.update_status_for_owner(
                document_id,
                actor.user_id,
                expected_status=DocumentStatus.UPLOADED,
                new_status=DocumentStatus.INGESTING,
            )
        except Exception:
            logger.exception("could not start ingesting document %s", document_id)
            return
        if not started:
            return
        try:
            self._ingestor.ingest(
                document_id=document_id,
                object_key=document.object_key,
                data=self._storage.get(document.object_key),
                metadata=document.metadata,
            )
            self._repository.update_status_for_owner(
                document_id,
                actor.user_id,
                expected_status=DocumentStatus.INGESTING,
                new_status=DocumentStatus.READY,
            )
        except Exception:
            logger.exception("ingesting document %s failed", document_id)
            self._mark_failed(actor, document_id)

    def authorize(self, actor: Actor) -> None:
        """Reject non-Instructors before an upload body is read."""
        if actor.role is not Role.INSTRUCTOR:
            raise PermissionDeniedError

    def _mark_failed(self, actor: Actor, document_id: int) -> None:
        try:
            self._repository.update_status_for_owner(
                document_id,
                actor.user_id,
                expected_status=DocumentStatus.INGESTING,
                new_status=DocumentStatus.FAILED,
            )
        except Exception:
            logger.exception("could not mark document %s failed", document_id)


def fail_stuck_ingestions(repository: KnowledgeDocumentRepository) -> int:
    """At startup, fail every document a previous process left `ingesting`.

    Those documents can then be retried. This assumes one app instance: with
    several, or with scripts/ingest_seed.py running at the same time, a live
    ingestion elsewhere would be marked failed too.
    """
    stuck = repository.fail_ingesting()
    if stuck:
        logger.warning("marked %d documents left ingesting as failed", stuck)
    return stuck


def _safe_filename(filename: str) -> str:
    """Keep client-controlled filenames from escaping the storage prefix."""
    name = PurePosixPath(filename.strip().replace("\\", "/")).name
    if not name or name in {".", ".."}:
        raise EmptyUploadError
    return name
