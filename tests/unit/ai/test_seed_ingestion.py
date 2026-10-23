"""Seed-specific metadata is carried through the existing PDF pipeline."""

from dataclasses import dataclass

from questly.ai.ingestion.models import (
    DocumentExtraction,
    ExtractedPage,
    PageExtractionOutcome,
)
from questly.ai.ingestion.pipeline import PdfIngestionService
from tests.fakes.vector import FakeVectorRepository


@dataclass(slots=True)
class _Extractor:
    def extract(self, pdf_bytes: bytes) -> DocumentExtraction:
        assert pdf_bytes == b"seed pdf"
        return DocumentExtraction(
            (
                ExtractedPage(
                    1,
                    "โจทย์\nข้อมูลนำเข้า\nN\n",
                    PageExtractionOutcome.EXTRACTED,
                ),
            )
        )


class _Embedder:
    @property
    def model(self) -> str:
        return "test-embedding-model"

    def embed(self, text: str) -> tuple[float, ...]:
        assert text.strip()
        return (1.0, *(0.0 for _ in range(767)))


def test_seed_task_id_is_stored_on_source_and_each_chunk() -> None:
    repository = FakeVectorRepository()
    service = PdfIngestionService(_Extractor(), _Embedder(), repository)

    result = service.ingest(
        b"seed pdf",
        core_document_id=7,
        r2_object_key="sources/programming-in-th/7.pdf",
        metadata={"task_id": "7", "topic": "arrays", "difficulty": "easy"},
    )

    assert result.source.metadata == {
        "task_id": "7",
        "topic": "arrays",
        "difficulty": "easy",
    }
    assert len(result.chunks) == 2
    assert [chunk.metadata["task_id"] for chunk in result.chunks] == ["7", "7"]
    assert all("topic" not in chunk.metadata for chunk in result.chunks)
