"""FastAPI adapter for the Instructor-owned document API."""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Request,
    Response,
    UploadFile,
    status,
)

from questly.core.auth.current import current_actor
from questly.core.documents.models import DocumentLibraryItem, KnowledgeDocument
from questly.core.documents.schemas import KnowledgeDocumentResponse
from questly.core.documents.service import (
    DocumentDeletionCompensationError,
    DocumentDeletionFailedError,
    DocumentInUseError,
    DocumentNotRetryableError,
    DocumentService,
    EmptyUploadError,
    KnowledgeDocumentNotFoundError,
    UploadTooLargeError,
)
from questly.core.http_errors import ErrorRule, ErrorTable

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

_DELETE_FAILED = ErrorRule(
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    "document_delete_failed",
    "Document could not be deleted safely",
)

DOCUMENT_ERRORS = ErrorTable(
    {
        KnowledgeDocumentNotFoundError: ErrorRule(
            status.HTTP_404_NOT_FOUND,
            "knowledge_document_not_found",
            "Knowledge document not found",
        ),
        UploadTooLargeError: ErrorRule(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "upload_too_large",
            "File exceeds size limit",
        ),
        EmptyUploadError: ErrorRule(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "empty_upload",
            "Upload needs a filename and at least one byte",
        ),
        DocumentInUseError: ErrorRule(
            status.HTTP_409_CONFLICT,
            "knowledge_document_in_use",
            "Document is referenced or has already been ingested",
        ),
        DocumentNotRetryableError: ErrorRule(
            status.HTTP_409_CONFLICT,
            "knowledge_document_not_retryable",
            "Only a document whose ingestion failed can be retried",
        ),
        DocumentDeletionCompensationError: _DELETE_FAILED,
        DocumentDeletionFailedError: _DELETE_FAILED,
    }
)


def _service(request: Request) -> DocumentService:
    return request.app.state.document_service


def _response(item: DocumentLibraryItem) -> KnowledgeDocumentResponse:
    document: KnowledgeDocument = item.document
    return KnowledgeDocumentResponse(
        id=document.id,
        filename=document.filename,
        object_key=document.object_key,
        content_hash=document.content_hash,
        status=document.status,
        used_in=item.used_in,
    )


@router.post(
    "",
    response_model=KnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_document(
    request: Request, file: UploadFile, background_tasks: BackgroundTasks
) -> KnowledgeDocumentResponse:
    """Store the upload for the authenticated Instructor, then ingest it."""
    service = _service(request)
    actor = current_actor(request)
    service.authorize(actor)
    with DOCUMENT_ERRORS.translate():
        filename = file.filename
        if filename is None:
            raise EmptyUploadError
        data = file.file.read(service.max_upload_size_bytes + 1)
        document = service.store(actor, filename=filename, data=data)
        if document.id is None:
            raise RuntimeError("Document repository returned an unpersisted document")
        if service.ingestion_enabled:
            background_tasks.add_task(service.ingest, actor, document.id)
        return _response(service.get(actor, document.id))


@router.get("", response_model=list[KnowledgeDocumentResponse])
def list_documents(request: Request) -> list[KnowledgeDocumentResponse]:
    """List the current Instructor's library and usage counts."""
    return [_response(item) for item in _service(request).list(current_actor(request))]


@router.get("/{document_id}", response_model=KnowledgeDocumentResponse)
def get_document(request: Request, document_id: int) -> KnowledgeDocumentResponse:
    """Get one document from the current Instructor's library."""
    with DOCUMENT_ERRORS.translate():
        return _response(_service(request).get(current_actor(request), document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(request: Request, document_id: int) -> Response:
    """Delete an owned, unused upload."""
    with DOCUMENT_ERRORS.translate():
        _service(request).delete(current_actor(request), document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/retry", response_model=KnowledgeDocumentResponse)
def retry_document(
    request: Request, document_id: int, background_tasks: BackgroundTasks
) -> KnowledgeDocumentResponse:
    """Return a failed document to uploaded, then ingest it again."""
    service = _service(request)
    actor = current_actor(request)
    with DOCUMENT_ERRORS.translate():
        item = service.retry(actor, document_id)
    if service.ingestion_enabled:
        background_tasks.add_task(service.ingest, actor, document_id)
    return _response(item)
