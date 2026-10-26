"""Business validation tests for vector storage and search."""

from dataclasses import replace

import pytest

from questly.ai.app.models import NewKnowledgeChunk, NewKnowledgeSource
from questly.ai.app.service import VectorService, VectorValidationError
from tests.fakes.vector import FakeVectorRepository

MODEL = "text-embedding-sample"


def _embedding(first: float = 1.0, second: float = 0.0) -> tuple[float, ...]:
    return (first, second, *(0.0 for _ in range(766)))


def _source(
    *, core_document_id: int = 1, embedding_model: str | None = MODEL
) -> NewKnowledgeSource:
    return NewKnowledgeSource(
        core_document_id=core_document_id,
        r2_object_key=f"documents/{core_document_id}.pdf",
        content_hash=f"source-{core_document_id}",
        embedding_model=embedding_model,
        embedding_dim=768 if embedding_model else None,
    )


def _chunk(
    *,
    index: int = 0,
    embedding: tuple[float, ...] | None = None,
    embedding_model: str | None = MODEL,
) -> NewKnowledgeChunk:
    return NewKnowledgeChunk(
        chunk_index=index,
        page=index + 1,
        text=f"chunk {index}",
        content_hash=f"chunk-{index}",
        embedding_model=embedding_model,
        embedding=embedding,
    )


def _service() -> VectorService:
    return VectorService(FakeVectorRepository())


def test_delete_source_removes_its_chunks() -> None:
    service = _service()
    service.create_source_with_chunks(
        _source(core_document_id=42), (_chunk(embedding=_embedding()),)
    )

    service.delete_source_by_core_document_id(42)

    assert service.search(_embedding(), embedding_model=MODEL, top_k=10) == []


@pytest.mark.parametrize("core_document_id", [0, -1, True, 1.5, "1"])
def test_delete_rejects_invalid_core_document_id(core_document_id: object) -> None:
    with pytest.raises(VectorValidationError, match="core_document_id"):
        _service().delete_source_by_core_document_id(  # type: ignore[arg-type]
            core_document_id
        )


def test_create_source_with_chunks_returns_database_shaped_ids() -> None:
    service = _service()

    result = service.create_source_with_chunks(
        _source(), (_chunk(index=0), _chunk(index=1))
    )

    assert result.source.id == 1
    assert [chunk.id for chunk in result.chunks] == [1, 2]
    assert {chunk.source_id for chunk in result.chunks} == {result.source.id}


@pytest.mark.parametrize(
    "embedding",
    [
        (1.0, 0.0),
        (1.0, *(0.0 for _ in range(768))),
    ],
)
def test_rejects_wrong_vector_dimension(embedding: tuple[float, ...]) -> None:
    with pytest.raises(VectorValidationError, match="exactly 768"):
        _service().search(embedding, embedding_model=MODEL, top_k=1)


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), "1", 10**1000])
def test_rejects_non_finite_or_non_numeric_values(invalid_value: object) -> None:
    embedding = (invalid_value, *(0.0 for _ in range(767)))

    with pytest.raises(VectorValidationError, match="numeric and finite"):
        _service().search(  # type: ignore[arg-type]
            embedding, embedding_model=MODEL, top_k=1
        )


def test_rejects_zero_query_vector() -> None:
    with pytest.raises(VectorValidationError, match="zero vector"):
        _service().search((0.0,) * 768, embedding_model=MODEL, top_k=1)


@pytest.mark.parametrize("embedding_model", ["", "   ", None])
def test_rejects_invalid_embedding_model(embedding_model: object) -> None:
    with pytest.raises(VectorValidationError, match="embedding model"):
        _service().search(  # type: ignore[arg-type]
            _embedding(), embedding_model=embedding_model, top_k=1
        )


@pytest.mark.parametrize("top_k", [0, 101, 1.5, True])
def test_rejects_top_k_outside_documented_range(top_k: object) -> None:
    with pytest.raises(VectorValidationError, match="top_k"):
        _service().search(  # type: ignore[arg-type]
            _embedding(), embedding_model=MODEL, top_k=top_k
        )


@pytest.mark.parametrize(
    "source",
    [
        replace(_source(), core_document_id=0),
        replace(_source(), r2_object_key=" "),
        replace(_source(), content_hash=""),
        replace(_source(), status="unknown"),
        replace(_source(), embedding_dim=12),
    ],
)
def test_rejects_invalid_source_fields(source: NewKnowledgeSource) -> None:
    with pytest.raises(VectorValidationError):
        _service().create_source_with_chunks(source, ())


@pytest.mark.parametrize(
    "chunk",
    [
        replace(_chunk(), chunk_index=-1),
        replace(_chunk(), text=" "),
        replace(_chunk(), content_hash=""),
        replace(_chunk(), page=0),
        replace(_chunk(), token_count=-1),
        replace(_chunk(), embedding_model="different-model"),
        replace(_chunk(embedding=_embedding()), embedding_model=None),
        replace(_chunk(embedding=_embedding()), embedding=(1.0, 0.0)),
        replace(
            _chunk(embedding=_embedding()),
            embedding=(float("nan"), *(0.0 for _ in range(767))),
        ),
        replace(_chunk(embedding=_embedding()), embedding=(0.0,) * 768),
    ],
)
def test_rejects_invalid_chunk_fields(chunk: NewKnowledgeChunk) -> None:
    with pytest.raises(VectorValidationError):
        _service().create_source_with_chunks(_source(), (chunk,))
