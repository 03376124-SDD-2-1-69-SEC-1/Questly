"""Vector-storage use cases and business validation."""

from collections.abc import Mapping
from math import isfinite

from questly.ai.app.models import (
    EMBEDDING_DIMENSION,
    ChunkSearchResult,
    Embedding,
    NewKnowledgeChunk,
    NewKnowledgeSource,
    SourceCreationResult,
)
from questly.ai.app.repository import VectorRepository

MIN_TOP_K = 1
MAX_TOP_K = 100
SOURCE_STATUSES = frozenset({"pending", "processing", "ready", "failed"})


class VectorValidationError(ValueError):
    """Raised when vector-storage input violates the domain contract."""


class VectorService:
    """Validate vector operations and delegate persistence through the port."""

    def __init__(self, repository: VectorRepository) -> None:
        self._repository = repository

    def delete_source_by_core_document_id(self, core_document_id: int) -> None:
        """Remove a document's vector source and chunks, if present."""
        _validate_positive_integer(core_document_id, "core_document_id")
        self._repository.delete_source_by_core_document_id(core_document_id)

    def create_source_with_chunks(
        self,
        source: NewKnowledgeSource,
        chunks: tuple[NewKnowledgeChunk, ...],
    ) -> SourceCreationResult:
        """Validate and atomically create one source with all its chunks."""
        _validate_source(source)
        if not isinstance(chunks, tuple):
            raise VectorValidationError("chunks must be a tuple")
        for chunk in chunks:
            _validate_chunk(chunk, source_embedding_model=source.embedding_model)
        return self._repository.create_source_with_chunks(source, chunks)

    def search(
        self,
        embedding: Embedding,
        *,
        embedding_model: str,
        top_k: int,
    ) -> list[ChunkSearchResult]:
        """Search one model with ``top_k`` constrained to 1 through 100."""
        _validate_embedding(embedding, field_name="query embedding")
        _validate_embedding_model(embedding_model)
        if (
            not isinstance(top_k, int)
            or isinstance(top_k, bool)
            or not MIN_TOP_K <= top_k <= MAX_TOP_K
        ):
            raise VectorValidationError(
                f"top_k must be an integer from {MIN_TOP_K} through {MAX_TOP_K}"
            )
        return self._repository.search(
            embedding, embedding_model=embedding_model, limit=top_k
        )


def _validate_source(source: NewKnowledgeSource) -> None:
    if not isinstance(source, NewKnowledgeSource):
        raise VectorValidationError("source must be NewKnowledgeSource")
    _validate_positive_integer(source.core_document_id, "core_document_id")
    _validate_required_text(source.r2_object_key, "r2_object_key")
    _validate_required_text(source.content_hash, "source content_hash")
    if source.status not in SOURCE_STATUSES:
        raise VectorValidationError("source status is invalid")
    if source.embedding_model is not None:
        _validate_embedding_model(source.embedding_model)
    if source.embedding_dim is not None:
        if source.embedding_model is None:
            raise VectorValidationError("embedding_dim requires an embedding model")
        if source.embedding_dim != EMBEDDING_DIMENSION:
            raise VectorValidationError(
                f"embedding_dim must equal {EMBEDDING_DIMENSION}"
            )
    if not isinstance(source.metadata, Mapping):
        raise VectorValidationError("source metadata must be a mapping")


def _validate_chunk(
    chunk: NewKnowledgeChunk, *, source_embedding_model: str | None
) -> None:
    if not isinstance(chunk, NewKnowledgeChunk):
        raise VectorValidationError("chunks must contain NewKnowledgeChunk values")
    _validate_non_negative_integer(chunk.chunk_index, "chunk_index")
    _validate_required_text(chunk.text, "chunk text")
    _validate_required_text(chunk.content_hash, "chunk content_hash")
    if chunk.page is not None:
        _validate_positive_integer(chunk.page, "page")
    if chunk.token_count is not None:
        _validate_non_negative_integer(chunk.token_count, "token_count")
    if chunk.embedding_model is not None:
        _validate_embedding_model(chunk.embedding_model)
        if chunk.embedding_model != source_embedding_model:
            raise VectorValidationError(
                "chunk embedding model must match source embedding model"
            )
    if not isinstance(chunk.metadata, Mapping):
        raise VectorValidationError("chunk metadata must be a mapping")
    if chunk.embedding is not None:
        _validate_embedding(chunk.embedding, field_name="chunk embedding")
        if chunk.embedding_model is None:
            raise VectorValidationError("chunk embedding requires an embedding model")


def _validate_embedding(embedding: Embedding, *, field_name: str) -> None:
    if not isinstance(embedding, tuple):
        raise VectorValidationError(f"{field_name} must be a tuple")
    if len(embedding) != EMBEDDING_DIMENSION:
        raise VectorValidationError(
            f"{field_name} must contain exactly {EMBEDDING_DIMENSION} values"
        )
    for value in embedding:
        try:
            finite = isfinite(value) if isinstance(value, (int, float)) else False
        except OverflowError:
            finite = False
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not finite:
            raise VectorValidationError(
                f"{field_name} values must be numeric and finite"
            )
    if all(value == 0 for value in embedding):
        raise VectorValidationError(f"{field_name} must not be a zero vector")


def _validate_embedding_model(embedding_model: object) -> None:
    if (
        not isinstance(embedding_model, str)
        or not embedding_model.strip()
        or embedding_model != embedding_model.strip()
    ):
        raise VectorValidationError("embedding model must be a non-empty string")


def _validate_required_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise VectorValidationError(f"{field_name} must be a non-empty string")


def _validate_positive_integer(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VectorValidationError(f"{field_name} must be a positive integer")


def _validate_non_negative_integer(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise VectorValidationError(f"{field_name} must be a non-negative integer")
