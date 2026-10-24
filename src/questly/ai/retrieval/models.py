"""Provider-independent retrieval input and output."""

from dataclasses import dataclass

from questly.ai.app.models import ChunkSearchResult


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    """Exact source-metadata filters; missing values mean no filter."""

    topic: str | None = None
    difficulty: str | None = None
    core_document_ids: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Ranked matches and whether the difficulty filter was relaxed."""

    matches: tuple[ChunkSearchResult, ...]
    difficulty_relaxed: bool
