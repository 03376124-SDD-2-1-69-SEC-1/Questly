"""Filtered retrieval with one-step difficulty relaxation."""

from math import isfinite

from questly.ai.app.models import EMBEDDING_DIMENSION, Embedding
from questly.ai.retrieval.models import RetrievalFilters, RetrievalResult
from questly.ai.retrieval.ports import FilteredVectorRepository

MIN_MATCHES_BEFORE_FALLBACK = 3
MAX_TOP_K = 100


class RetrievalValidationError(ValueError):
    """Retrieval input violates the domain contract."""


class RetrievalService:
    """Search within filters, then drop difficulty once if matches are sparse."""

    def __init__(self, repository: FilteredVectorRepository) -> None:
        self._repository = repository

    def retrieve(
        self,
        embedding: Embedding,
        *,
        embedding_model: str,
        top_k: int,
        filters: RetrievalFilters,
    ) -> RetrievalResult:
        _validate(embedding, embedding_model, top_k, filters)
        # Probe at least three matches even when the caller asks for fewer.
        probe_limit = max(top_k, MIN_MATCHES_BEFORE_FALLBACK)
        matches = self._repository.search_filtered(
            embedding,
            embedding_model=embedding_model,
            limit=probe_limit,
            filters=filters,
        )
        if filters.difficulty is None or len(matches) >= MIN_MATCHES_BEFORE_FALLBACK:
            return RetrievalResult(tuple(matches[:top_k]), difficulty_relaxed=False)

        broader = self._repository.search_filtered(
            embedding,
            embedding_model=embedding_model,
            limit=top_k,
            filters=RetrievalFilters(
                topic=filters.topic,
                core_document_ids=filters.core_document_ids,
            ),
        )
        return RetrievalResult(tuple(broader), difficulty_relaxed=True)


def _validate(
    embedding: Embedding,
    embedding_model: str,
    top_k: int,
    filters: RetrievalFilters,
) -> None:
    if not isinstance(embedding, tuple) or len(embedding) != EMBEDDING_DIMENSION:
        raise RetrievalValidationError("embedding must contain 768 values")
    if any(not _finite_number(value) for value in embedding) or all(
        value == 0 for value in embedding
    ):
        raise RetrievalValidationError("embedding must be finite and non-zero")
    if (
        not isinstance(embedding_model, str)
        or not embedding_model.strip()
        or embedding_model != embedding_model.strip()
    ):
        raise RetrievalValidationError("embedding_model is required")
    if (
        isinstance(top_k, bool)
        or not isinstance(top_k, int)
        or not 1 <= top_k <= MAX_TOP_K
    ):
        raise RetrievalValidationError("top_k must be from 1 through 100")
    if not isinstance(filters, RetrievalFilters):
        raise RetrievalValidationError("filters must be RetrievalFilters")
    for value in (filters.topic, filters.difficulty):
        if value is not None and (
            not isinstance(value, str) or not value.strip() or value != value.strip()
        ):
            raise RetrievalValidationError("filters must be non-empty text")
    ids = filters.core_document_ids
    if ids is not None and (
        not isinstance(ids, tuple)
        or not ids
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in ids
        )
        or len(ids) != len(set(ids))
    ):
        raise RetrievalValidationError(
            "core_document_ids must be unique positive integers"
        )


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False
