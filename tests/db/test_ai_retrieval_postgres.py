"""AI-05 filtered retrieval against a throwaway PostgreSQL branch only."""

from secrets import randbelow

import pytest
from sqlalchemy import delete

from questly.ai.app.models import NewKnowledgeChunk, NewKnowledgeSource
from questly.ai.retrieval.models import RetrievalFilters
from questly.ai.retrieval.service import RetrievalService
from questly.database.rag.tables import KnowledgeSource
from questly.database.rag.vector_repository import PostgresVectorRepository
from questly.database.session import SessionFactory

pytestmark = pytest.mark.postgres
MODEL = "ai-05-test-model"
QUERY = (1.0,) + (0.0,) * 767


def test_filtered_search_falls_back_only_on_difficulty(
    session_factory: SessionFactory,
) -> None:
    repository = PostgresVectorRepository(session_factory)
    service = RetrievalService(repository)
    base_id = randbelow(2**60) + 1
    source_ids: list[int] = []

    def add(offset: int, *, topic: str, difficulty: str, status: str = "ready") -> None:
        result = repository.create_source_with_chunks(
            NewKnowledgeSource(
                core_document_id=base_id + offset,
                r2_object_key=f"tests/ai-05/{base_id + offset}.pdf",
                content_hash=f"source-{base_id + offset}",
                status=status,
                embedding_model=MODEL,
                embedding_dim=768,
                metadata={"topic": topic, "difficulty": difficulty},
            ),
            (
                NewKnowledgeChunk(
                    chunk_index=0,
                    page=offset + 1,
                    text=f"document {offset}",
                    content_hash=f"chunk-{base_id + offset}",
                    embedding_model=MODEL,
                    embedding=QUERY,
                ),
            ),
        )
        source_ids.append(result.source.id)

    try:
        add(0, topic="graphs", difficulty="hard")
        add(1, topic="graphs", difficulty="hard")
        add(2, topic="graphs", difficulty="easy")
        add(3, topic="graphs", difficulty="easy")
        add(4, topic="arrays", difficulty="hard")
        add(5, topic="graphs", difficulty="hard", status="pending")

        result = service.retrieve(
            QUERY,
            embedding_model=MODEL,
            top_k=4,
            filters=RetrievalFilters(topic="graphs", difficulty="hard"),
        )

        assert result.difficulty_relaxed is True
        assert len(result.matches) == 4
        assert {match.source_id for match in result.matches} == set(source_ids[:4])
        assert all(match.page is not None for match in result.matches)
        assert all(0.0 <= match.score <= 1.0 for match in result.matches)

        selected = service.retrieve(
            QUERY,
            embedding_model=MODEL,
            top_k=4,
            filters=RetrievalFilters(
                topic="graphs",
                difficulty="hard",
                core_document_ids=(base_id, base_id + 2),
            ),
        )
        assert selected.difficulty_relaxed is True
        assert {match.source_id for match in selected.matches} == {
            source_ids[0],
            source_ids[2],
        }

        add(6, topic="graphs", difficulty="hard")
        strict = service.retrieve(
            QUERY,
            embedding_model=MODEL,
            top_k=2,
            filters=RetrievalFilters(topic="graphs", difficulty="hard"),
        )
        assert strict.difficulty_relaxed is False
        assert {match.source_id for match in strict.matches}.issubset(
            {source_ids[0], source_ids[1], source_ids[6]}
        )
    finally:
        if source_ids:
            with session_factory() as session:
                session.execute(
                    delete(KnowledgeSource.__table__).where(
                        KnowledgeSource.__table__.c.id.in_(source_ids)
                    )
                )
                session.commit()
