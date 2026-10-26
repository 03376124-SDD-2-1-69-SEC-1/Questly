"""Request and response schemas for the document API."""

from pydantic import BaseModel

from questly.core.documents.models import DocumentStatus


class KnowledgeDocumentResponse(BaseModel):
    """Public representation of a document in the current Instructor's library."""

    id: int
    filename: str
    object_key: str
    content_hash: str
    status: DocumentStatus
    used_in: int
