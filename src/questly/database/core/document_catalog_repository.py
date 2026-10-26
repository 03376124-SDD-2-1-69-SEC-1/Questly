"""SQL adapter for generation's DocumentCatalog port (OPS-15)."""

from __future__ import annotations

from sqlmodel import select

from questly.core.documents.models import DocumentStatus
from questly.core.generation.models import DocumentSummary
from questly.database.core.tables import KnowledgeDocument as DocumentRow
from questly.database.session import SessionFactory


def _summary(row: DocumentRow) -> DocumentSummary:
    # A page count is unknown until ingestion reads the PDF.
    pages = 0 if row.page_count is None else row.page_count
    return DocumentSummary(id=row.id, filename=row.filename, pages=pages)


class SQLDocumentCatalog:
    """The T-03 picker: an Instructor's Documents that generation may read.

    Owned means `uploaded_by` (ADR-0007 §4.2). Only `ready` Documents are
    listed, because generation reads ingested chunks; a row with no uploader
    (ADR-0008 left the column nullable) belongs to no library.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        """Initialize the adapter with a session factory."""
        self._session_factory = session_factory

    def documents_of(self, owner_id: int) -> list[DocumentSummary]:
        """Return the owner's ready Documents in id order."""
        with self._session_factory() as db:
            rows = db.exec(
                select(DocumentRow)
                .where(
                    DocumentRow.uploaded_by == owner_id,
                    DocumentRow.status == DocumentStatus.READY.value,
                )
                .order_by(DocumentRow.id)
            ).all()
            return [_summary(row) for row in rows]
