"""Ingest one PDF into atomic source and chunk vector storage."""

from collections.abc import Mapping
from hashlib import sha256
from typing import Protocol

from questly.ai.app.models import (
    EMBEDDING_DIMENSION,
    NewKnowledgeChunk,
    NewKnowledgeSource,
    SourceCreationResult,
)
from questly.ai.embeddings.ports import EmbeddingProvider
from questly.ai.ingestion.chunking import ChunkingError, chunk_pages
from questly.ai.ingestion.models import PageExtractionOutcome
from questly.ai.ingestion.ports import PageExtractor


class IngestionError(Exception):
    """The document cannot be ingested without losing content."""


class VectorWriter(Protocol):
    """Atomic vector-storage operation supplied by AI-01's VectorService."""

    def create_source_with_chunks(
        self, source: NewKnowledgeSource, chunks: tuple[NewKnowledgeChunk, ...]
    ) -> SourceCreationResult: ...


class PdfIngestionService:
    """Extract, chunk, embed, then persist only after every step succeeds."""

    def __init__(
        self,
        extractor: PageExtractor,
        embedder: EmbeddingProvider,
        writer: VectorWriter,
    ) -> None:
        self._extractor = extractor
        self._embedder = embedder
        self._writer = writer

    def ingest(
        self,
        pdf_bytes: bytes,
        *,
        core_document_id: int,
        r2_object_key: str,
        metadata: Mapping[str, object] | None = None,
    ) -> SourceCreationResult:
        if metadata is None:
            metadata = {}
        extraction = self._extractor.extract(pdf_bytes)
        if any(
            page.outcome == PageExtractionOutcome.NEEDS_OCR for page in extraction.pages
        ):
            raise IngestionError("PDF has pages requiring OCR")
        try:
            chunks = chunk_pages([page.text for page in extraction.pages])
        except ChunkingError as exc:
            raise IngestionError("PDF has no usable text") from exc

        chunk_metadata: dict[str, object] = {}
        if "task_id" in metadata:
            chunk_metadata["task_id"] = metadata["task_id"]

        stored_chunks = tuple(
            NewKnowledgeChunk(
                chunk_index=index,
                text=chunk.text,
                content_hash=sha256(chunk.text.encode("utf-8")).hexdigest(),
                page=chunk.page_start,
                embedding_model=self._embedder.model,
                embedding=self._embedder.embed(chunk.text),
                metadata={
                    "page_end": chunk.page_end,
                    "kind": chunk.kind.value,
                    **chunk_metadata,
                },
            )
            for index, chunk in enumerate(chunks)
        )
        source = NewKnowledgeSource(
            core_document_id=core_document_id,
            r2_object_key=r2_object_key,
            content_hash=sha256(pdf_bytes).hexdigest(),
            status="ready",
            embedding_model=self._embedder.model,
            embedding_dim=EMBEDDING_DIMENSION,
            metadata=dict(metadata),
        )
        return self._writer.create_source_with_chunks(source, stored_chunks)
