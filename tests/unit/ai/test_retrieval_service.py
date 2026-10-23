"""AI-05 filter relaxation is deterministic and never broadens topic."""

from dataclasses import dataclass, field

import pytest

from questly.ai.app.models import ChunkSearchResult, Embedding
from questly.ai.retrieval.models import RetrievalFilters
from questly.ai.retrieval.service import RetrievalService, RetrievalValidationError

MODEL = "test-embedding"
VECTOR = (1.0,) + (0.0,) * 767


def _match(chunk_id: int) -> ChunkSearchResult:
    return ChunkSearchResult(
        chunk_id=chunk_id,
        source_id=chunk_id + 10,
        page=chunk_id,
        text=f"chunk {chunk_id}",
        score=1.0 - chunk_id / 100,
    )


@dataclass
class RecordingRepository:
    narrow: list[ChunkSearchResult]
    broad: list[ChunkSearchResult]
    calls: list[tuple[int, RetrievalFilters]] = field(default_factory=list)

    def search_filtered(
        self,
        embedding: Embedding,
        *,
        embedding_model: str,
        limit: int,
        filters: RetrievalFilters,
    ) -> list[ChunkSearchResult]:
        assert embedding == VECTOR
        assert embedding_model == MODEL
        self.calls.append((limit, filters))
        matches = self.narrow if filters.difficulty is not None else self.broad
        return matches[:limit]


def test_three_filtered_matches_keep_difficulty_even_when_top_k_is_one() -> None:
    repository = RecordingRepository([_match(1), _match(2), _match(3)], [])
    filters = RetrievalFilters(topic="graphs", difficulty="hard")

    result = RetrievalService(repository).retrieve(
        VECTOR, embedding_model=MODEL, top_k=1, filters=filters
    )

    assert result.matches == (_match(1),)
    assert result.difficulty_relaxed is False
    assert repository.calls == [(3, filters)]


def test_fewer_than_three_matches_drop_only_difficulty_once() -> None:
    repository = RecordingRepository(
        [_match(1), _match(2)], [_match(1), _match(2), _match(3)]
    )

    result = RetrievalService(repository).retrieve(
        VECTOR,
        embedding_model=MODEL,
        top_k=2,
        filters=RetrievalFilters(topic="graphs", difficulty="hard"),
    )

    assert result.matches == (_match(1), _match(2))
    assert result.difficulty_relaxed is True
    assert repository.calls == [
        (3, RetrievalFilters(topic="graphs", difficulty="hard")),
        (2, RetrievalFilters(topic="graphs")),
    ]


def test_fallback_preserves_selected_document_ids() -> None:
    repository = RecordingRepository([], [_match(1)])
    filters = RetrievalFilters(difficulty="hard", core_document_ids=(7, 9))

    RetrievalService(repository).retrieve(
        VECTOR, embedding_model=MODEL, top_k=2, filters=filters
    )

    assert repository.calls == [
        (3, filters),
        (2, RetrievalFilters(core_document_ids=(7, 9))),
    ]


def test_without_difficulty_never_falls_back() -> None:
    repository = RecordingRepository([], [_match(1)])
    filters = RetrievalFilters(topic="graphs")

    result = RetrievalService(repository).retrieve(
        VECTOR, embedding_model=MODEL, top_k=5, filters=filters
    )

    assert result.matches == (_match(1),)
    assert result.difficulty_relaxed is False
    assert repository.calls == [(5, filters)]


@pytest.mark.parametrize(
    ("embedding", "model", "top_k", "filters"),
    [
        ((1.0,), MODEL, 3, RetrievalFilters()),
        ((0.0,) * 768, MODEL, 3, RetrievalFilters()),
        ((float("nan"),) + (0.0,) * 767, MODEL, 3, RetrievalFilters()),
        (VECTOR, " ", 3, RetrievalFilters()),
        (VECTOR, " test-embedding ", 3, RetrievalFilters()),
        (VECTOR, MODEL, 0, RetrievalFilters()),
        (VECTOR, MODEL, True, RetrievalFilters()),
        (VECTOR, MODEL, 3, RetrievalFilters(topic=" ")),
        (VECTOR, MODEL, 3, RetrievalFilters(topic=" graphs ")),
        (VECTOR, MODEL, 3, RetrievalFilters(difficulty="")),
        (VECTOR, MODEL, 3, RetrievalFilters(core_document_ids=())),
        (VECTOR, MODEL, 3, RetrievalFilters(core_document_ids=(0,))),
        (VECTOR, MODEL, 3, RetrievalFilters(core_document_ids=(1, 1))),
        (VECTOR, MODEL, 3, RetrievalFilters(core_document_ids=(True,))),
    ],
)
def test_invalid_input_does_not_query_repository(
    embedding: Embedding, model: str, top_k: int, filters: RetrievalFilters
) -> None:
    repository = RecordingRepository([], [])
    with pytest.raises(RetrievalValidationError):
        RetrievalService(repository).retrieve(
            embedding, embedding_model=model, top_k=top_k, filters=filters
        )
    assert repository.calls == []
