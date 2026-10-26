"""AI-01's global validation handler preserves the application error contract."""

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fakes.app import build_app


@pytest.fixture()
async def client():
    application = build_app()

    @application.get("/validation-probe/{value}")
    def validation_probe(value: int, count: int):
        return {"value": value, "count": count}

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as test_client:
        yield test_client


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/topics", {"name": " "}),
        ("/api/v1/classrooms/1/drafts", {"prompt": "", "difficulty": "private input"}),
        ("/api/v1/documents", {}),
    ],
)
async def test_validation_envelope_is_consistent_across_modules(client, path, payload):
    response = await client.post(path, json=payload)
    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "request_validation_error",
            "message": "Request body or parameters are invalid",
        }
    }
    assert "private input" not in response.text


@pytest.mark.anyio
async def test_malformed_json_uses_same_envelope(client):
    response = await client.post(
        "/api/v1/topics",
        content='{"secret":',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "request_validation_error"
    assert "secret" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["bad?count=1", "1?count=bad", "1"])
async def test_path_and_query_validation_use_same_envelope(client, path):
    response = await client.get(f"/validation-probe/{path}")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "request_validation_error"


@pytest.mark.anyio
async def test_openapi_documents_shared_validation_envelope(client):
    document = (await client.get("/openapi.json")).json()
    for path in (
        "/api/v1/topics",
        "/api/v1/classrooms/{classroom_id}/drafts",
        "/api/v1/documents",
    ):
        schema = document["paths"][path]["post"]["responses"]["422"]["content"][
            "application/json"
        ]["schema"]
        assert schema == {"$ref": "#/components/schemas/ApplicationErrorResponse"}


@pytest.mark.anyio
async def test_existing_topic_error_codes_are_unchanged(client):
    missing = await client.get("/api/v1/topics/999999")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "topic_not_found"
    assert (
        await client.post("/api/v1/topics", json={"name": "Graphs"})
    ).status_code == 201
    duplicate = await client.post("/api/v1/topics", json={"name": "Graphs"})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "topic_name_conflict"
