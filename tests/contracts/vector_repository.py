"""Deletion contract shared by in-memory and PostgreSQL vector adapters."""

from collections.abc import Callable

from questly.ai.app.models import NewKnowledgeChunk, NewKnowledgeSource
from questly.ai.app.repository import VectorRepository

MODEL = "contract-embedding-model"
EMBEDDING = (1.0, *(0.0 for _ in range(767)))
ChunkIdsForSource = Callable[[int], set[int]]


def _source(core_document_id: int) -> NewKnowledgeSource:
    return NewKnowledgeSource(
        core_document_id=core_document_id,
        r2_object_key=f"documents/{core_document_id}.pdf",
        content_hash=f"source-{core_document_id}",
        status="ready",
        embedding_model=MODEL,
        embedding_dim=768,
    )


def _chunk(index: int, *, embedded: bool = True) -> NewKnowledgeChunk:
    return NewKnowledgeChunk(
        chunk_index=index,
        page=index + 1,
        text=f"chunk {index}",
        content_hash=f"chunk-{index}",
        embedding_model=MODEL if embedded else None,
        embedding=EMBEDDING if embedded else None,
    )


class VectorRepositoryContract:
    """Every adapter must delete source and chunks without touching neighbors."""

    def test_delete_removes_all_chunks_and_preserves_other_sources(
        self,
        repository: VectorRepository,
        chunk_ids_for_source: ChunkIdsForSource,
    ) -> None:
        removed = repository.create_source_with_chunks(
            _source(101), (_chunk(0), _chunk(1), _chunk(2, embedded=False))
        )
        kept = repository.create_source_with_chunks(_source(202), (_chunk(0),))
        assert chunk_ids_for_source(removed.source.id) == {
            chunk.id for chunk in removed.chunks
        }

        repository.delete_source_by_core_document_id(101)

        assert chunk_ids_for_source(removed.source.id) == set()
        assert chunk_ids_for_source(kept.source.id) == {kept.chunks[0].id}
        results = repository.search(EMBEDDING, embedding_model=MODEL, limit=10)
        assert [result.chunk_id for result in results] == [kept.chunks[0].id]
        assert all(result.source_id != removed.source.id for result in results)

        recreated = repository.create_source_with_chunks(_source(101), (_chunk(0),))
        assert recreated.source.core_document_id == 101
        assert recreated.source.id != removed.source.id

    def test_delete_missing_source_is_idempotent(
        self, repository: VectorRepository
    ) -> None:
        repository.delete_source_by_core_document_id(999_999)
        repository.delete_source_by_core_document_id(999_999)

        created = repository.create_source_with_chunks(_source(101), ())
        repository.delete_source_by_core_document_id(101)
        repository.delete_source_by_core_document_id(101)

        replacement = repository.create_source_with_chunks(_source(101), ())
        assert replacement.source.id != created.source.id
