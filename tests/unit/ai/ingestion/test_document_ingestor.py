"""PdfDocumentIngestor fills Core's DocumentIngestor port with the PDF pipeline."""

from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from questly.ai.app.models import (
    KnowledgeSource,
    NewKnowledgeChunk,
    NewKnowledgeSource,
    SourceCreationResult,
)
from questly.ai.app.repository import DuplicateSourceError, VectorRepositoryError
from questly.ai.app.service import VectorService, VectorValidationError
from questly.ai.embeddings.ports import EmbeddingError
from questly.ai.ingestion.document_ingestor import PdfDocumentIngestor
from questly.ai.ingestion.models import (
    DocumentExtraction,
    ExtractedPage,
    PageExtractionOutcome,
)
from questly.ai.ingestion.pipeline import IngestionError, PdfIngestionService
from questly.ai.ingestion.ports import CorruptDocumentError, EncryptedDocumentError
from questly.core.documents.ports import DocumentIngestionError
from tests.fakes.vector import FakeVectorRepository

MODEL = "test-embedding-model"
VECTOR = (1.0,) + (0.0,) * 767


@dataclass(slots=True)
class IngestCall:
    pdf_bytes: bytes
    core_document_id: int
    r2_object_key: str
    metadata: Mapping[str, object] | None


@dataclass(slots=True)
class Recorder:
    events: list[str] = field(default_factory=list)
    calls: list[IngestCall] = field(default_factory=list)
    failure: Exception | None = None

    def delete_source_by_core_document_id(self, core_document_id: int) -> None:
        self.events.append(f"delete {core_document_id}")

    def ingest(
        self,
        pdf_bytes: bytes,
        *,
        core_document_id: int,
        r2_object_key: str,
        metadata: Mapping[str, object] | None = None,
    ) -> SourceCreationResult:
        self.events.append(f"ingest {core_document_id}")
        self.calls.append(
            IngestCall(pdf_bytes, core_document_id, r2_object_key, metadata)
        )
        if self.failure is not None:
            raise self.failure
        return SourceCreationResult(
            source=KnowledgeSource(
                id=1,
                core_document_id=core_document_id,
                r2_object_key=r2_object_key,
                content_hash="hash",
                status="ready",
                embedding_model=MODEL,
                embedding_dim=768,
                metadata={},
            ),
            chunks=(),
        )


@dataclass(slots=True)
class OnePageExtractor:
    text: str = "โจทย์\nหาผลบวกของ A และ B\n"
    outcome: PageExtractionOutcome = PageExtractionOutcome.EXTRACTED
    failure: Exception | None = None

    def extract(self, pdf_bytes: bytes) -> DocumentExtraction:
        if self.failure is not None:
            raise self.failure
        return DocumentExtraction((ExtractedPage(1, self.text, self.outcome),))


@dataclass(slots=True)
class FixedEmbedder:
    failure: Exception | None = None

    @property
    def model(self) -> str:
        return MODEL

    def embed(self, text: str) -> tuple[float, ...]:
        if self.failure is not None:
            raise self.failure
        return VECTOR


def _ingest(ingestor: PdfDocumentIngestor, document_id: int = 5) -> None:
    ingestor.ingest(
        document_id=document_id,
        object_key="uploads/abc-notes.pdf",
        data=b"%PDF bytes",
        metadata={"topic": "arrays"},
    )


def test_passes_the_core_document_through_to_the_pipeline() -> None:
    recorder = Recorder()

    _ingest(PdfDocumentIngestor(recorder, recorder))

    assert recorder.calls == [
        IngestCall(
            pdf_bytes=b"%PDF bytes",
            core_document_id=5,
            r2_object_key="uploads/abc-notes.pdf",
            metadata={"topic": "arrays"},
        )
    ]


def test_deletes_the_earlier_source_before_ingesting() -> None:
    recorder = Recorder()

    _ingest(PdfDocumentIngestor(recorder, recorder))

    assert recorder.events == ["delete 5", "ingest 5"]


def test_replaces_a_source_left_by_an_interrupted_run() -> None:
    repository = FakeVectorRepository()
    vectors = VectorService(repository)
    vectors.create_source_with_chunks(
        NewKnowledgeSource(
            core_document_id=5,
            r2_object_key="uploads/abc-notes.pdf",
            content_hash="old",
            status="ready",
            embedding_model=MODEL,
            embedding_dim=768,
        ),
        (
            NewKnowledgeChunk(
                chunk_index=0,
                text="old chunk",
                content_hash="old-chunk",
                embedding_model=MODEL,
                embedding=VECTOR,
            ),
        ),
    )
    pipeline = PdfIngestionService(OnePageExtractor(), FixedEmbedder(), vectors)

    _ingest(PdfDocumentIngestor(pipeline, vectors))

    texts = [
        match.text
        for match in repository.search(VECTOR, embedding_model=MODEL, limit=100)
    ]
    assert texts == ["โจทย์\nหาผลบวกของ A และ B"]


@pytest.mark.parametrize(
    "failure",
    [
        IngestionError("PDF has no usable text"),
        EmbeddingError("provider unavailable"),
        CorruptDocumentError("not a PDF"),
        EncryptedDocumentError("needs a password"),
        VectorValidationError("bad source"),
        DuplicateSourceError(),
        VectorRepositoryError(),
    ],
)
def test_pipeline_failures_become_the_port_error(failure: Exception) -> None:
    recorder = Recorder(failure=failure)

    with pytest.raises(DocumentIngestionError) as raised:
        _ingest(PdfDocumentIngestor(recorder, recorder))

    assert raised.value.__cause__ is failure


@pytest.mark.parametrize(
    ("extractor", "embedder"),
    [
        (OnePageExtractor(failure=CorruptDocumentError("broken")), FixedEmbedder()),
        (OnePageExtractor(), FixedEmbedder(failure=EmbeddingError("timeout"))),
        (
            OnePageExtractor(outcome=PageExtractionOutcome.NEEDS_OCR),
            FixedEmbedder(),
        ),
    ],
    ids=["extractor", "embedder", "needs-ocr"],
)
def test_real_pipeline_failures_become_the_port_error(
    extractor: OnePageExtractor, embedder: FixedEmbedder
) -> None:
    vectors = VectorService(FakeVectorRepository())
    pipeline = PdfIngestionService(extractor, embedder, vectors)

    with pytest.raises(DocumentIngestionError):
        _ingest(PdfDocumentIngestor(pipeline, vectors))


def test_an_unexpected_error_is_not_disguised() -> None:
    recorder = Recorder(failure=RuntimeError("bug"))

    with pytest.raises(RuntimeError, match="bug"):
        _ingest(PdfDocumentIngestor(recorder, recorder))
