"""The Instructor's T-06 document library."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from questly.core.auth.csrf import require_csrf
from questly.core.auth.current import current_actor
from questly.core.auth.models import Actor
from questly.core.documents.models import DocumentStatus
from questly.core.documents.service import (
    DocumentInUseError,
    DocumentService,
    EmptyUploadError,
    KnowledgeDocumentNotFoundError,
    UploadTooLargeError,
)

router = APIRouter(tags=["pages"], include_in_schema=False)

STATUS_LABELS = {
    DocumentStatus.UPLOADED: "Uploaded",
    DocumentStatus.INGESTING: "Processing",
    DocumentStatus.READY: "Ready",
    DocumentStatus.FAILED: "Failed",
}
NOTICES = {
    "uploaded": "Document uploaded.",
    "deleted": "Document deleted.",
}
ERRORS = {
    "empty": "Choose a file to upload.",
    "too_large": "The file exceeds the upload size limit.",
    "in_use": (
        "This document is being processed or used by a draft and cannot be deleted."
    ),
}


def _service(request: Request) -> DocumentService:
    return request.app.state.document_service


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def _page(
    request: Request,
    actor: Actor,
    *,
    notice: str = "",
    error: str = "",
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    service = _service(request)
    documents = service.list(actor)
    return _templates(request).TemplateResponse(
        request,
        "instructor/t06_documents.html",
        {
            "actor": actor,
            "documents": documents,
            "status_labels": STATUS_LABELS,
            "notice": NOTICES.get(notice, ""),
            "error": ERRORS.get(error, ""),
            "has_refresh_state": any(
                item.document.status
                in {DocumentStatus.INGESTING, DocumentStatus.FAILED}
                for item in documents
            ),
        },
        status_code=status_code,
    )


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/documents", response_class=HTMLResponse)
def documents_page(request: Request, notice: str = "", error: str = "") -> HTMLResponse:
    actor = current_actor(request)
    return _page(request, actor, notice=notice, error=error)


@router.post("/documents/upload", response_class=HTMLResponse)
def upload_document(
    request: Request,
    file: UploadFile,
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    require_csrf(request, csrf_token)
    actor = current_actor(request)
    service = _service(request)
    service.authorize(actor)
    if file.filename is None:
        return _page(request, actor, error="empty", status_code=422)

    data = file.file.read(service.max_upload_size_bytes + 1)
    try:
        service.store(actor, filename=file.filename, data=data)
    except EmptyUploadError:
        return _page(request, actor, error="empty", status_code=422)
    except UploadTooLargeError:
        return _page(request, actor, error="too_large", status_code=413)
    return _see_other("/documents?notice=uploaded")


@router.post("/documents/{document_id}/delete")
def delete_document(
    request: Request,
    document_id: int,
    csrf_token: Annotated[str, Form()] = "",
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    actor = current_actor(request)
    try:
        _service(request).delete(actor, document_id)
    except KnowledgeDocumentNotFoundError:
        raise HTTPException(status_code=404) from None
    except DocumentInUseError:
        return _see_other("/documents?error=in_use")
    return _see_other("/documents?notice=deleted")
