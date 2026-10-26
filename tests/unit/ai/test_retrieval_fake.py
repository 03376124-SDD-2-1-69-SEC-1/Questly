"""Filtered retrieval over the in-memory vector adapter."""

from questly.ai.app.models import NewKnowledgeChunk, NewKnowledgeSource
from questly.ai.retrieval.models import RetrievalFilters
from questly.ai.retrieval.service import RetrievalService
from tests.fakes.vector import FakeVectorRepository

MODEL = "test-embedding"
QUERY = (1.0,) + (0.0,) * 767


def _add(
    repository: FakeVectorRepository,
    document_id: int,
    *,
    topic: str,
    difficulty: str,
    status: str = "ready",
    model: str = MODEL,
) -> None:
    repository.create_source_with_chunks(
        NewKnowledgeSource(
            core_document_id=document_id,
            r2_object_key=f"documents/{document_id}.pdf",
            content_hash=f"source-{document_id}",
            status=status,
            embedding_model=model,
            embedding_dim=768,
            metadata={"topic": topic, "difficulty": difficulty},
        ),
        (
            NewKnowledgeChunk(
                chunk_index=0,
                page=document_id,
                text=f"document {document_id}",
                content_hash=f"chunk-{document_id}",
                embedding_model=model,
                embedding=QUERY,
            ),
        ),
    )


def test_topic_stays_filtered_when_difficulty_is_relaxed() -> None:
    repository = FakeVectorRepository()
    _add(repository, 1, topic="graphs", difficulty="hard")
    _add(repository, 2, topic="graphs", difficulty="hard")
    _add(repository, 3, topic="graphs", difficulty="easy")
    _add(repository, 4, topic="graphs", difficulty="easy")
    _add(repository, 5, topic="arrays", difficulty="hard")
    _add(repository, 6, topic="graphs", difficulty="hard", status="pending")
    _add(repository, 7, topic="graphs", difficulty="hard", model="other-model")

    result = RetrievalService(repository).retrieve(
        QUERY,
        embedding_model=MODEL,
        top_k=4,
        filters=RetrievalFilters(topic="graphs", difficulty="hard"),
    )

    assert result.difficulty_relaxed is True
    assert [match.page for match in result.matches] == [1, 2, 3, 4]
    assert all(match.score == 1.0 for match in result.matches)


def test_three_ready_matches_keep_both_filters() -> None:
    repository = FakeVectorRepository()
    for document_id in (1, 2, 3):
        _add(repository, document_id, topic="graphs", difficulty="hard")
    _add(repository, 4, topic="graphs", difficulty="easy")

    result = RetrievalService(repository).retrieve(
        QUERY,
        embedding_model=MODEL,
        top_k=2,
        filters=RetrievalFilters(topic="graphs", difficulty="hard"),
    )

    assert result.difficulty_relaxed is False
    assert [match.page for match in result.matches] == [1, 2]


def test_selected_documents_are_enforced_even_after_difficulty_relaxes() -> None:
    repository = FakeVectorRepository()
    _add(repository, 1, topic="graphs", difficulty="hard")
    _add(repository, 2, topic="graphs", difficulty="easy")
    _add(repository, 3, topic="graphs", difficulty="easy")

    result = RetrievalService(repository).retrieve(
        QUERY,
        embedding_model=MODEL,
        top_k=3,
        filters=RetrievalFilters(
            topic="graphs", difficulty="hard", core_document_ids=(1, 2)
        ),
    )

    assert result.difficulty_relaxed is True
    assert [match.page for match in result.matches] == [1, 2]
