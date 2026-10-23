"""In-memory VectorRepository, behaviour-matched to the SQL adapter."""

from __future__ import annotations

from math import sqrt

from questly.ai.app.models import (
    ChunkSearchResult,
    Embedding,
    KnowledgeChunk,
    KnowledgeSource,
    NewKnowledgeChunk,
    NewKnowledgeSource,
    SourceCreationResult,
)
from questly.ai.app.repository import DuplicateChunkError, DuplicateSourceError
from questly.ai.retrieval.models import RetrievalFilters


class FakeVectorRepository:
    """Process-local adapter with database-like IDs and uniqueness rules."""

    def __init__(self) -> None:
        """Initialize an empty repository."""
        self._sources: dict[int, KnowledgeSource] = {}
        self._source_ids_by_core_document: dict[int, int] = {}
        self._chunks: dict[int, KnowledgeChunk] = {}
        self._next_source_id = 1
        self._next_chunk_id = 1

    def delete_source_by_core_document_id(self, core_document_id: int) -> None:
        """Remove a source and all its chunks; ignore unknown documents."""
        source_id = self._source_ids_by_core_document.get(core_document_id)
        if source_id is None:
            return
        del self._sources[source_id]
        del self._source_ids_by_core_document[core_document_id]
        self._chunks = {
            chunk_id: chunk
            for chunk_id, chunk in self._chunks.items()
            if chunk.source_id != source_id
        }

    def create_source_with_chunks(
        self,
        source: NewKnowledgeSource,
        chunks: tuple[NewKnowledgeChunk, ...],
    ) -> SourceCreationResult:
        """Insert one source and every chunk, enforcing the same uniqueness rules."""
        if source.core_document_id in self._source_ids_by_core_document:
            raise DuplicateSourceError

        content_hashes = [chunk.content_hash for chunk in chunks]
        if len(content_hashes) != len(set(content_hashes)):
            raise DuplicateChunkError

        source_id = self._next_source_id
        stored_source = KnowledgeSource(
            id=source_id,
            core_document_id=source.core_document_id,
            r2_object_key=source.r2_object_key,
            content_hash=source.content_hash,
            status=source.status,
            embedding_model=source.embedding_model,
            embedding_dim=source.embedding_dim,
            metadata=dict(source.metadata),
        )
        stored_chunks = tuple(
            KnowledgeChunk(
                id=self._next_chunk_id + offset,
                source_id=source_id,
                chunk_index=chunk.chunk_index,
                page=chunk.page,
                text=chunk.text,
                token_count=chunk.token_count,
                content_hash=chunk.content_hash,
                embedding_model=chunk.embedding_model,
                metadata=dict(chunk.metadata),
                embedding=chunk.embedding,
            )
            for offset, chunk in enumerate(chunks)
        )

        self._sources[source_id] = stored_source
        self._source_ids_by_core_document[source.core_document_id] = source_id
        self._chunks.update({chunk.id: chunk for chunk in stored_chunks})
        self._next_source_id += 1
        self._next_chunk_id += len(stored_chunks)
        return SourceCreationResult(source=stored_source, chunks=stored_chunks)

    def search(
        self,
        embedding: Embedding,
        *,
        embedding_model: str,
        limit: int,
    ) -> list[ChunkSearchResult]:
        """Search stored chunks for one embedding model using cosine similarity."""
        matches = [
            ChunkSearchResult(
                chunk_id=chunk.id,
                source_id=chunk.source_id,
                page=chunk.page,
                text=chunk.text,
                score=_cosine_similarity(embedding, chunk.embedding),
            )
            for chunk in self._chunks.values()
            if chunk.embedding is not None and chunk.embedding_model == embedding_model
        ]
        matches.sort(key=lambda match: (-match.score, match.chunk_id))
        return matches[:limit]

    def search_filtered(
        self,
        embedding: Embedding,
        *,
        embedding_model: str,
        limit: int,
        filters: RetrievalFilters,
    ) -> list[ChunkSearchResult]:
        """Mirror PostgreSQL's ready-source metadata filtering and ranking."""
        matches = [
            ChunkSearchResult(
                chunk_id=chunk.id,
                source_id=chunk.source_id,
                page=chunk.page,
                text=chunk.text,
                score=_cosine_similarity(embedding, chunk.embedding),
            )
            for chunk in self._chunks.values()
            if chunk.embedding is not None
            and chunk.embedding_model == embedding_model
            and (source := self._sources[chunk.source_id]).status == "ready"
            and (filters.topic is None or source.metadata.get("topic") == filters.topic)
            and (
                filters.difficulty is None
                or source.metadata.get("difficulty") == filters.difficulty
            )
        ]
        matches.sort(key=lambda match: (-match.score, match.chunk_id))
        return matches[:limit]


def _cosine_similarity(left: Embedding, right: Embedding) -> float:
    # Scale before squaring to avoid overflow or underflow for finite inputs.
    left_scale = max(abs(value) for value in left)
    right_scale = max(abs(value) for value in right)
    left = tuple(value / left_scale for value in left)
    right = tuple(value / right_scale for value in right)
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    return max(-1.0, min(1.0, dot_product / (left_norm * right_norm)))
