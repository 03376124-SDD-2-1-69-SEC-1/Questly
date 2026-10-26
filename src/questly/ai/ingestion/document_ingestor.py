"""Core's DocumentIngestor port, filled by the existing PDF pipeline."""

from collections.abc import Mapping
from typing import Protocol

from questly.ai.app.models import SourceCreationResult
from questly.ai.app.repository import VectorRepositoryError
from questly.ai.app.service import VectorValidationError
from questly.ai.embeddings.ports import EmbeddingError
from questly.ai.ingestion.pipeline import IngestionError
from questly.ai.ingestion.ports import PageExtractionError
from questly.core.documents.ports import DocumentIngestionError

_INGESTION_FAILURES = (
    IngestionError,
    EmbeddingError,
    PageExtractionError,
    VectorValidationError,
    VectorRepositoryError,
)


class PdfIngester(Protocol):
    """The `PdfIngestionService.ingest` shape."""

    def ingest(
        self,
        pdf_bytes: bytes,
        *,
        core_document_id: int,
        r2_object_key: str,
        metadata: Mapping[str, object] | None = None,
    ) -> SourceCreationResult: ...


class VectorSourceRemover(Protocol):
    """The `VectorService` operation that clears a document's earlier source."""

    def delete_source_by_core_document_id(self, core_document_id: int) -> None: ...


class PdfDocumentIngestor:
    """Ingest one uploaded PDF, replacing whatever an earlier attempt stored."""

    def __init__(self, ingester: PdfIngester, sources: VectorSourceRemover) -> None:
        self._ingester = ingester
        self._sources = sources

    def ingest(
        self,
        *,
        document_id: int,
        object_key: str,
        data: bytes,
        metadata: Mapping[str, str],
    ) -> None:
        try:
            # A run that died after writing the source would otherwise make
            # every retry fail with DuplicateSourceError.
            self._sources.delete_source_by_core_document_id(document_id)
            self._ingester.ingest(
                data,
                core_document_id=document_id,
                r2_object_key=object_key,
                metadata=metadata,
            )
        except _INGESTION_FAILURES as error:
            raise DocumentIngestionError(
                f"document {document_id} could not be ingested"
            ) from error
