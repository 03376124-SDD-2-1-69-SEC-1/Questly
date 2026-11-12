"""scripts/ingest_seed.py reads an existing RAG source and its chunks (OPS-57)."""

from secrets import randbelow

import pymupdf
import pytest
from scripts.ingest_seed import SQLSeedSourceInspector
from sqlalchemy import delete

from questly.ai.app.service import VectorService
from questly.ai.ingestion.pdf_extractor import PyMuPDFPageExtractor
from questly.ai.ingestion.pipeline import PdfIngestionService
from questly.database.rag.tables import KnowledgeSource
from questly.database.rag.vector_repository import PostgresVectorRepository
from questly.database.session import SessionFactory

pytestmark = pytest.mark.postgres


class FixedEmbedder:
    @property
    def model(self) -> str:
        return "test-embedding-model"

    def embed(self, text: str) -> tuple[float, ...]:
        return (1.0,) + (0.0,) * 767


def test_inspector_returns_the_stored_source_and_its_chunk(
    session_factory: SessionFactory,
) -> None:
    pdf = pymupdf.open()
    try:
        pdf.new_page().insert_text((72, 72), "One programming problem")
        pdf_bytes = pdf.tobytes()
    finally:
        pdf.close()

    core_document_id = randbelow(2**60) + 1
    result = PdfIngestionService(
        PyMuPDFPageExtractor(),
        FixedEmbedder(),
        VectorService(PostgresVectorRepository(session_factory)),
    ).ingest(
        pdf_bytes,
        core_document_id=core_document_id,
        r2_object_key=f"tests/ops-57/{core_document_id}.pdf",
        metadata={"topic": "basics"},
    )

    try:
        stored = SQLSeedSourceInspector(session_factory).get_by_core_document_id(
            core_document_id
        )

        assert stored is not None
        assert stored.core_document_id == core_document_id
        assert stored.status == "ready"
        assert stored.metadata == {"topic": "basics"}
        assert [(c.page, c.has_embedding) for c in stored.chunks] == [(1, True)]
    finally:
        with session_factory() as session:
            session.execute(
                delete(KnowledgeSource.__table__).where(
                    KnowledgeSource.__table__.c.id == result.source.id
                )
            )
            session.commit()
