"""Repository port for atomic vector storage and similarity search.

Adapters must translate storage failures into ``VectorRepositoryError`` types;
SQLAlchemy, driver, and provider exceptions must not cross this boundary.

``core_document_id`` is UNIQUE for sources. ``(source_id, content_hash)`` is
UNIQUE for chunks. An adapter raises ``DuplicateSourceError`` or
``DuplicateChunkError`` for those conflicts and rolls back the source and every
chunk. No partial source or chunk data may remain after any creation failure.
"""

from typing import Protocol

from questly.ai.app.models import (
    ChunkSearchResult,
    Embedding,
    NewKnowledgeChunk,
    NewKnowledgeSource,
    SourceCreationResult,
)


class VectorRepositoryError(Exception):
    """Base error for all adapter failures exposed through this port."""


class VectorRepositoryUnavailableError(VectorRepositoryError):
    """Raised when the backing vector store is unavailable."""


class DuplicateSourceError(VectorRepositoryError):
    """Raised when a source repeats the UNIQUE ``core_document_id`` value."""


class DuplicateChunkError(VectorRepositoryError):
    """Raised when chunks repeat ``content_hash`` under the same source."""


class VectorRepository(Protocol):
    """Persistence operations required by vector-storage use cases."""

    def delete_source_by_core_document_id(self, core_document_id: int) -> None:
        """Atomically delete a source and all its chunks by Core document ID.

        Deleting an unknown document is a successful no-op. Adapter failures
        follow the module-level error contract.
        """
        ...

    def create_source_with_chunks(
        self,
        source: NewKnowledgeSource,
        chunks: tuple[NewKnowledgeChunk, ...],
    ) -> SourceCreationResult:
        """Create one source and all chunks in one transaction.

        The adapter assigns integer IDs. Any failure rolls back the complete
        operation. Duplicate errors follow the module-level contract.
        """
        ...

    def search(
        self,
        embedding: Embedding,
        *,
        embedding_model: str,
        limit: int,
    ) -> list[ChunkSearchResult]:
        """Return matches for exactly one embedding model.

        Score equals ``1 - cosine distance`` and measures vector similarity,
        not confidence or probability. Results use descending score, then
        ascending ``chunk_id`` for ties. Return an empty list when nothing
        matches the requested embedding model.
        """
        ...
