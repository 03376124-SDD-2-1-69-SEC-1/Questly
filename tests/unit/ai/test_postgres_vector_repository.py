"""PostgreSQL vector adapter tests using mocked synchronous sessions.

These tests verify mapping, transaction calls, and PostgreSQL query construction.
They do not verify real PostgreSQL persistence or pgvector execution.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError

from questly.ai.app.models import (
    ChunkSearchResult,
    KnowledgeChunk,
    KnowledgeSource,
    NewKnowledgeChunk,
    NewKnowledgeSource,
    SourceCreationResult,
)
from questly.ai.app.repository import (
    DuplicateChunkError,
    DuplicateSourceError,
    VectorRepositoryError,
    VectorRepositoryUnavailableError,
)
from questly.ai.retrieval.models import RetrievalFilters
from questly.database.rag.vector_repository import PostgresVectorRepository

MODEL = "model-a"


def _embedding() -> tuple[float, ...]:
    return (1.0, *(0.0 for _ in range(767)))


def _source() -> NewKnowledgeSource:
    return NewKnowledgeSource(
        core_document_id=7,
        r2_object_key="documents/7.pdf",
        content_hash="source-hash",
        embedding_model=MODEL,
        embedding_dim=768,
        metadata={"topic": "graphs"},
    )


def _chunk(index: int = 0) -> NewKnowledgeChunk:
    return NewKnowledgeChunk(
        chunk_index=index,
        page=index + 1,
        text=f"chunk {index}",
        token_count=2,
        content_hash=f"chunk-{index}",
        embedding_model=MODEL,
        metadata={"content_type": "lesson"},
        embedding=_embedding(),
    )


def _source_row() -> dict[str, object]:
    return {
        "id": 11,
        "core_document_id": 7,
        "r2_object_key": "documents/7.pdf",
        "content_hash": "source-hash",
        "status": "pending",
        "embedding_model": MODEL,
        "embedding_dim": 768,
        "metadata": {"topic": "graphs"},
    }


def _chunk_row(index: int = 0) -> dict[str, object]:
    return {
        "id": 21 + index,
        "source_id": 11,
        "chunk_index": index,
        "page": index + 1,
        "text": f"chunk {index}",
        "token_count": 2,
        "content_hash": f"chunk-{index}",
        "embedding_model": MODEL,
        "metadata": {"content_type": "lesson"},
        "embedding": list(_embedding()),
    }


def _result_for_one(row: dict[str, object]) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.one.return_value = row
    return result


def _repository(session: MagicMock) -> PostgresVectorRepository:
    @contextmanager
    def session_factory():
        yield session

    return PostgresVectorRepository(session_factory)


def _integrity_error(constraint_name: str) -> IntegrityError:
    original = Exception("database constraint")
    original.diag = MagicMock(constraint_name=constraint_name)  # type: ignore[attr-defined]
    return IntegrityError("INSERT", {}, original)


def test_creates_source_and_chunks_in_one_commit() -> None:
    session = MagicMock()
    session.execute.side_effect = [
        _result_for_one(_source_row()),
        _result_for_one(_chunk_row(0)),
        _result_for_one(_chunk_row(1)),
    ]

    result = _repository(session).create_source_with_chunks(
        _source(), (_chunk(0), _chunk(1))
    )

    assert result == SourceCreationResult(
        source=KnowledgeSource(
            id=11,
            core_document_id=7,
            r2_object_key="documents/7.pdf",
            content_hash="source-hash",
            status="pending",
            embedding_model=MODEL,
            embedding_dim=768,
            metadata={"topic": "graphs"},
        ),
        chunks=(
            KnowledgeChunk(
                id=21,
                source_id=11,
                chunk_index=0,
                page=1,
                text="chunk 0",
                token_count=2,
                content_hash="chunk-0",
                embedding_model=MODEL,
                metadata={"content_type": "lesson"},
                embedding=_embedding(),
            ),
            KnowledgeChunk(
                id=22,
                source_id=11,
                chunk_index=1,
                page=2,
                text="chunk 1",
                token_count=2,
                content_hash="chunk-1",
                embedding_model=MODEL,
                metadata={"content_type": "lesson"},
                embedding=_embedding(),
            ),
        ),
    )
    assert session.execute.call_count == 3
    source_params = session.execute.call_args_list[0].args[0].compile().params
    first_chunk_params = session.execute.call_args_list[1].args[0].compile().params
    assert source_params == {
        "content_hash": "source-hash",
        "core_document_id": 7,
        "embedding_dim": 768,
        "embedding_model": MODEL,
        "metadata": {"topic": "graphs"},
        "r2_object_key": "documents/7.pdf",
        "status": "pending",
    }
    assert first_chunk_params == {
        "chunk_index": 0,
        "content_hash": "chunk-0",
        "embedding": list(_embedding()),
        "embedding_model": MODEL,
        "metadata": {"content_type": "lesson"},
        "page": 1,
        "source_id": 11,
        "text": "chunk 0",
        "token_count": 2,
    }
    session.flush.assert_not_called()
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    ("constraint_name", "expected_error"),
    [
        ("uq_knowledge_sources_core_document_id", DuplicateSourceError),
        ("uq_knowledge_chunks_source_content_hash", DuplicateChunkError),
    ],
)
def test_translates_unique_constraints_and_rolls_back(
    constraint_name: str, expected_error: type[VectorRepositoryError]
) -> None:
    session = MagicMock()
    error = _integrity_error(constraint_name)
    if expected_error is DuplicateChunkError:
        session.execute.side_effect = [_result_for_one(_source_row()), error]
    else:
        session.execute.side_effect = error

    with pytest.raises(expected_error):
        _repository(session).create_source_with_chunks(_source(), (_chunk(),))

    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_chunk_database_failure_rolls_back_source_and_does_not_commit() -> None:
    session = MagicMock()
    session.execute.side_effect = [
        _result_for_one(_source_row()),
        OperationalError("INSERT", {}, Exception("offline")),
    ]

    with pytest.raises(
        VectorRepositoryUnavailableError, match="storage operation failed"
    ):
        _repository(session).create_source_with_chunks(_source(), (_chunk(),))

    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_translates_unrecognized_integrity_error_without_exposing_sqlalchemy() -> None:
    session = MagicMock()
    session.execute.side_effect = _integrity_error("unexpected_constraint")

    with pytest.raises(
        VectorRepositoryError, match="storage integrity constraint failed"
    ) as raised:
        _repository(session).create_source_with_chunks(_source(), ())

    assert not isinstance(raised.value, IntegrityError)
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_search_maps_results_and_builds_model_scoped_ordered_query() -> None:
    session = MagicMock()
    search_result = MagicMock()
    search_result.mappings.return_value.all.return_value = [
        {
            "chunk_id": 21,
            "source_id": 11,
            "page": 1,
            "text": "chunk 0",
            "score": 0.75,
        }
    ]
    session.execute.return_value = search_result

    results = _repository(session).search(_embedding(), embedding_model=MODEL, limit=5)

    assert results == [
        ChunkSearchResult(
            chunk_id=21,
            source_id=11,
            page=1,
            text="chunk 0",
            score=0.75,
        )
    ]
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "knowledge_chunks.embedding IS NOT NULL" in sql
    assert "knowledge_chunks.embedding_model =" in sql
    assert " <=> " in sql
    assert " AS score" in sql
    assert "ORDER BY score DESC, rag.knowledge_chunks.id ASC" in sql
    assert "LIMIT" in sql
    assert MODEL in compiled.params.values()
    assert 1.0 in compiled.params.values()
    assert 5 in compiled.params.values()


def test_search_returns_empty_list() -> None:
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = []

    assert (
        _repository(session).search(_embedding(), embedding_model=MODEL, limit=5) == []
    )


def test_filtered_search_joins_ready_sources_and_filters_before_limit() -> None:
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = [
        {"chunk_id": 21, "source_id": 11, "page": 2, "text": "sample", "score": 0.8}
    ]

    results = _repository(session).search_filtered(
        _embedding(),
        embedding_model=MODEL,
        limit=3,
        filters=RetrievalFilters(topic="graphs", difficulty="hard"),
    )

    assert results == [ChunkSearchResult(21, 11, 2, "sample", 0.8)]
    compiled = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "JOIN rag.knowledge_sources" in sql
    assert "knowledge_sources.status =" in sql
    assert sql.count("knowledge_sources.metadata ->>") == 2
    assert "ORDER BY score DESC, rag.knowledge_chunks.id ASC" in sql
    assert all(
        value in compiled.params.values()
        for value in ("ready", "graphs", "hard", MODEL, 3)
    )


def test_filtered_search_without_difficulty_keeps_topic_condition() -> None:
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = []

    _repository(session).search_filtered(
        _embedding(),
        embedding_model=MODEL,
        limit=3,
        filters=RetrievalFilters(topic="graphs"),
    )

    compiled = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert str(compiled).count("knowledge_sources.metadata ->>") == 1
    assert "graphs" in compiled.params.values()


def test_filtered_search_limits_to_selected_core_documents_in_sql() -> None:
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = []

    _repository(session).search_filtered(
        _embedding(),
        embedding_model=MODEL,
        limit=3,
        filters=RetrievalFilters(core_document_ids=(7, 9)),
    )

    compiled = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "knowledge_sources.core_document_id IN" in str(compiled)
    assert [7, 9] in compiled.params.values()


def test_filtered_search_translates_database_failure() -> None:
    session = MagicMock()
    session.execute.side_effect = OperationalError("SELECT", {}, Exception("offline"))

    with pytest.raises(
        VectorRepositoryUnavailableError, match="search operation failed"
    ):
        _repository(session).search_filtered(
            _embedding(),
            embedding_model=MODEL,
            limit=3,
            filters=RetrievalFilters(topic="graphs"),
        )

    session.rollback.assert_called_once_with()


def test_search_translates_database_failure() -> None:
    session = MagicMock()
    session.execute.side_effect = OperationalError("SELECT", {}, Exception("offline"))

    with pytest.raises(
        VectorRepositoryUnavailableError, match="search operation failed"
    ):
        _repository(session).search(_embedding(), embedding_model=MODEL, limit=5)

    session.rollback.assert_called_once_with()


@pytest.mark.parametrize("operation", ["create", "search"])
@pytest.mark.parametrize("phase", ["open", "close", "rollback"])
def test_session_lifecycle_errors_do_not_escape_port(
    operation: str, phase: str
) -> None:
    error = OperationalError("private SQL", {}, Exception("offline"))
    session = MagicMock()
    session.execute.return_value = _result_for_one(_source_row())
    session.execute.return_value.mappings.return_value.all.return_value = []
    if phase == "rollback":
        session.execute.side_effect = error
        session.rollback.side_effect = error

    @contextmanager
    def factory():
        if phase == "open":
            raise error
        yield session
        if phase == "close":
            raise error

    repository = PostgresVectorRepository(factory)
    with pytest.raises(VectorRepositoryUnavailableError):
        if operation == "create":
            repository.create_source_with_chunks(_source(), ())
        else:
            repository.search(_embedding(), embedding_model=MODEL, limit=1)


def test_commit_failure_rolls_back_and_translates() -> None:
    session = MagicMock()
    session.execute.return_value = _result_for_one(_source_row())
    session.commit.side_effect = OperationalError("COMMIT", {}, Exception("offline"))
    with pytest.raises(VectorRepositoryUnavailableError):
        _repository(session).create_source_with_chunks(_source(), ())
    session.rollback.assert_called_once_with()
