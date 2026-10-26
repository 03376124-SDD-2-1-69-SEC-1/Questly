"""Uploads and retries are ingested in the background after the response.

httpx's ASGITransport runs background tasks before the response returns, so
the next request already sees the job's final status.
"""

import pytest
from httpx import AsyncClient

from questly.core.auth.models import Role
from questly.core.documents.ports import DocumentIngestionError
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user
from tests.fakes.documents import FakeDocumentIngestor, failing_ingestor
from tests.integration.forms import asgi_client, csrf_token, log_in

INSTRUCTOR = "instructor@kmitl.ac.th"
PDF = ("lecture.pdf", b"%PDF-1.7 lecture", "application/pdf")


async def _instructor(ingestor: FakeDocumentIngestor | None) -> AsyncClient:
    users = FakeAuthRepository()
    seed_user(users, email=INSTRUCTOR, full_name="Instructor", role=Role.INSTRUCTOR)
    client = asgi_client(build_app(auth_repository=users, document_ingestor=ingestor))
    await log_in(client, INSTRUCTOR, DEFAULT_PASSWORD)
    return client


async def _upload(client: AsyncClient) -> int:
    response = await client.post("/api/v1/documents", files={"file": PDF})
    assert response.status_code == 201
    return response.json()["id"]


async def _status(client: AsyncClient, document_id: int) -> str:
    response = await client.get(f"/api/v1/documents/{document_id}")
    assert response.status_code == 200
    return response.json()["status"]


@pytest.mark.anyio
async def test_an_uploaded_pdf_becomes_ready() -> None:
    ingestor = FakeDocumentIngestor()
    client = await _instructor(ingestor)

    document_id = await _upload(client)

    assert await _status(client, document_id) == "ready"
    assert [request.data for request in ingestor.requests] == [PDF[1]]


@pytest.mark.anyio
async def test_a_pdf_the_ingestor_rejects_becomes_failed() -> None:
    client = await _instructor(failing_ingestor())

    document_id = await _upload(client)

    assert await _status(client, document_id) == "failed"


@pytest.mark.anyio
async def test_retrying_a_failed_pdf_ingests_it_again() -> None:
    ingestor = failing_ingestor()
    client = await _instructor(ingestor)
    document_id = await _upload(client)
    assert await _status(client, document_id) == "failed"

    ingestor.failure = None
    retried = await client.post(f"/api/v1/documents/{document_id}/retry")

    assert retried.status_code == 200
    assert await _status(client, document_id) == "ready"
    assert len(ingestor.requests) == 2


@pytest.mark.anyio
async def test_without_an_ingestor_an_upload_stays_uploaded() -> None:
    client = await _instructor(None)

    document_id = await _upload(client)

    assert await _status(client, document_id) == "uploaded"


@pytest.mark.anyio
async def test_a_t06_upload_is_shown_ready() -> None:
    client = await _instructor(FakeDocumentIngestor())

    uploaded = await client.post(
        "/documents/upload",
        data={"csrf_token": await csrf_token(client, "/documents")},
        files={"file": PDF},
    )
    page = await client.get("/documents")

    assert uploaded.status_code == 303
    assert "lecture.pdf" in page.text
    assert "พร้อมใช้" in page.text


@pytest.mark.anyio
async def test_a_failing_t06_upload_is_shown_failed() -> None:
    client = await _instructor(
        FakeDocumentIngestor(failure=DocumentIngestionError("scan"))
    )

    await client.post(
        "/documents/upload",
        data={"csrf_token": await csrf_token(client, "/documents")},
        files={"file": PDF},
    )
    page = await client.get("/documents")

    assert "ล้มเหลว" in page.text
