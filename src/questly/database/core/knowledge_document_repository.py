"""SQL adapter for the KnowledgeDocumentRepository port (CORE-09, OPS-20).

Translates between the `core.knowledge_documents` row and the plain dataclass in
`core/documents/models.py`. This seam must live here, not on the `core/` side,
because it is the only layer allowed to see both.
"""

from __future__ import annotations

from sqlalchemy import (
    ColumnElement,
    Delete,
    ScalarSelect,
    Update,
    any_,
    delete,
    exists,
    func,
    update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from questly.core.documents.models import (
    DocumentLibraryItem,
    DocumentStatus,
    KnowledgeDocument,
)
from questly.core.documents.ports import DocumentRepositoryError
from questly.database.core.tables import DraftRow
from questly.database.core.tables import KnowledgeDocument as KnowledgeDocumentRow
from questly.database.session import SessionFactory


def _to_domain(row: KnowledgeDocumentRow) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=row.id,
        filename=row.filename,
        object_key=row.r2_object_key,
        content_hash=row.content_hash,
        status=DocumentStatus(row.status),
        metadata=dict(row.metadata_),
    )


def _draft_uses(document_id: ColumnElement[int]) -> ColumnElement[bool]:
    """Match the drafts that list this document as a source."""
    return document_id == any_(DraftRow.document_ids)


class SQLKnowledgeDocumentRepository:
    """Store knowledge documents in the `core` schema, scoped to their uploader.

    One session per method: the service is built once at startup, so there is no
    per-request session to join. Each call is its own unit of work. "Used in"
    counts the drafts whose `document_ids` hold the document, published or not.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        """Initialize the adapter with a session factory."""
        self._session_factory = session_factory

    def create(
        self, document: KnowledgeDocument, uploaded_by: int
    ) -> KnowledgeDocument:
        """Insert a document owned by `uploaded_by` and return it with its id."""
        with self._session_factory() as session:
            row = KnowledgeDocumentRow(
                r2_object_key=document.object_key,
                filename=document.filename,
                content_hash=document.content_hash,
                status=document.status.value,
                metadata_=dict(document.metadata),
                uploaded_by=uploaded_by,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_domain(row)

    def get_by_content_hash(
        self, content_hash: str, uploaded_by: int
    ) -> KnowledgeDocument | None:
        """Return the owner's document already holding this content, if any."""
        with self._session_factory() as session:
            row = session.exec(
                select(KnowledgeDocumentRow).where(
                    KnowledgeDocumentRow.content_hash == content_hash,
                    KnowledgeDocumentRow.uploaded_by == uploaded_by,
                )
            ).first()
            if row is None:
                return None
            return _to_domain(row)

    def list_for_owner(self, uploaded_by: int) -> list[DocumentLibraryItem]:
        """Return the owner's documents in id order with their usage counts."""
        with self._session_factory() as session:
            rows = session.exec(
                select(KnowledgeDocumentRow, self._used_in())
                .where(KnowledgeDocumentRow.uploaded_by == uploaded_by)
                .order_by(KnowledgeDocumentRow.id)
            ).all()
            return [DocumentLibraryItem(_to_domain(row), used) for row, used in rows]

    def get_for_owner(
        self, document_id: int, uploaded_by: int
    ) -> DocumentLibraryItem | None:
        """Return the document only when `uploaded_by` owns it."""
        with self._session_factory() as session:
            found = session.exec(
                select(KnowledgeDocumentRow, self._used_in()).where(
                    KnowledgeDocumentRow.id == document_id,
                    KnowledgeDocumentRow.uploaded_by == uploaded_by,
                )
            ).first()
            if found is None:
                return None
            row, used = found
            return DocumentLibraryItem(_to_domain(row), used)

    def delete_for_owner(self, document_id: int, uploaded_by: int) -> bool:
        """Delete an owned, unused, not-yet-ingested document.

        Report whether a row went away. The status check sits in the same
        statement as the delete, so a document that started ingesting after the
        service read it is not removed. No foreign key ties
        `drafts.document_ids` to this table, so a draft saved after the
        statement's snapshot can still name a deleted id.
        """
        statement = delete(KnowledgeDocumentRow).where(
            KnowledgeDocumentRow.id == document_id,
            KnowledgeDocumentRow.uploaded_by == uploaded_by,
            KnowledgeDocumentRow.status == DocumentStatus.UPLOADED.value,
            ~exists().where(_draft_uses(KnowledgeDocumentRow.id)),
        )
        return self._execute_one_row(statement)

    def update_status_for_owner(
        self,
        document_id: int,
        uploaded_by: int,
        *,
        expected_status: DocumentStatus,
        new_status: DocumentStatus,
    ) -> bool:
        """Move an owned document from `expected_status` to `new_status`.

        Compare-and-set in one UPDATE: it reports False, and changes nothing,
        when the document is missing, belongs to someone else, or is no longer
        in `expected_status`.
        """
        statement = (
            update(KnowledgeDocumentRow)
            .where(
                KnowledgeDocumentRow.id == document_id,
                KnowledgeDocumentRow.uploaded_by == uploaded_by,
                KnowledgeDocumentRow.status == expected_status.value,
            )
            .values(status=new_status.value)
        )
        return self._execute_one_row(statement)

    def _execute_one_row(self, statement: Delete | Update) -> bool:
        """Run one DELETE or UPDATE in its own unit of work; wrap DB errors."""
        try:
            with self._session_factory() as session:
                result = session.execute(statement)
                session.commit()
        except SQLAlchemyError as error:
            raise DocumentRepositoryError from error
        return result.rowcount == 1

    @staticmethod
    def _used_in() -> ScalarSelect[int]:
        return (
            select(func.count())
            .select_from(DraftRow)
            .where(_draft_uses(KnowledgeDocumentRow.id))
            .correlate(KnowledgeDocumentRow)
            .scalar_subquery()
        )
