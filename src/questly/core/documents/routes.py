"""FastAPI adapter for the Instructor-owned document API."""

from typing import NoReturn

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
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

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


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


def _raise_not_found() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "knowledge_document_not_found",
            "message": "Knowledge document not found",
        },
    )


def _raise_too_large() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail={"code": "upload_too_large", "message": "File exceeds size limit"},
    )


def _raise_empty_upload() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "empty_upload",
            "message": "Upload needs a filename and at least one byte",
        },
    )


def _raise_in_use() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "knowledge_document_in_use",
            "message": "Document is referenced or has already been ingested",
        },
    )


def _raise_not_retryable() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "knowledge_document_not_retryable",
            "message": "Only a document whose ingestion failed can be retried",
        },
    )


def _raise_delete_failed() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "document_delete_failed",
            "message": "Document could not be deleted safely",
        },
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
    filename = file.filename
    if filename is None:
        _raise_empty_upload()

    data = file.file.read(service.max_upload_size_bytes + 1)
    try:
        document = service.store(actor, filename=filename, data=data)
        if document.id is None:
            raise RuntimeError("Document repository returned an unpersisted document")
        background_tasks.add_task(service.ingest, actor, document.id)
        return _response(service.get(actor, document.id))
    except UploadTooLargeError:
        _raise_too_large()
    except EmptyUploadError:
        _raise_empty_upload()
    except KnowledgeDocumentNotFoundError:
        _raise_not_found()


@router.get("", response_model=list[KnowledgeDocumentResponse])
def list_documents(request: Request) -> list[KnowledgeDocumentResponse]:
    """List the current Instructor's library and usage counts."""
    return [_response(item) for item in _service(request).list(current_actor(request))]


@router.get("/{document_id}", response_model=KnowledgeDocumentResponse)
def get_document(request: Request, document_id: int) -> KnowledgeDocumentResponse:
    """Get one document from the current Instructor's library."""
    try:
        return _response(_service(request).get(current_actor(request), document_id))
    except KnowledgeDocumentNotFoundError:
        _raise_not_found()


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(request: Request, document_id: int) -> Response:
    """Delete an owned, unused upload."""
    try:
        _service(request).delete(current_actor(request), document_id)
    except KnowledgeDocumentNotFoundError:
        _raise_not_found()
    except DocumentInUseError:
        _raise_in_use()
    except (
        DocumentDeletionCompensationError,
        DocumentDeletionFailedError,
    ):
        _raise_delete_failed()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/retry", response_model=KnowledgeDocumentResponse)
def retry_document(
    request: Request, document_id: int, background_tasks: BackgroundTasks
) -> KnowledgeDocumentResponse:
    """Return a failed document to uploaded, then ingest it again."""
    service = _service(request)
    actor = current_actor(request)
    try:
        item = service.retry(actor, document_id)
    except KnowledgeDocumentNotFoundError:
        _raise_not_found()
    except DocumentNotRetryableError:
        _raise_not_retryable()
    background_tasks.add_task(service.ingest, actor, document_id)
    return _response(item)
