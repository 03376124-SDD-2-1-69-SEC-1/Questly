"""HTTP integration tests for the Topic reference API."""

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.auth.models import Role
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user

TEACHER = "somchai.p@kmitl.ac.th"
STUDENT = "66010001@kmitl.ac.th"


def _app():
    users = FakeAuthRepository()
    seed_user(users, email=TEACHER, full_name="Somchai", role=Role.INSTRUCTOR)
    seed_user(users, email=STUDENT, full_name="Nattapong", role=Role.STUDENT)
    return build_app(auth_repository=users)


@asynccontextmanager
async def _client(application, email: str | None):
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        if email is not None:
            await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": DEFAULT_PASSWORD},
            )
        yield client


@pytest.fixture()
async def client():
    async with _client(_app(), TEACHER) as test_client:
        yield test_client


@pytest.mark.anyio
async def test_topic_writes_need_a_login_and_the_instructor_role() -> None:
    application = _app()
    async with _client(application, TEACHER) as teacher:
        topic = (await teacher.post("/api/v1/topics", json={"name": "Graphs"})).json()
    path = f"/api/v1/topics/{topic['id']}"

    for email, expected in ((None, 401), (STUDENT, 403)):
        async with _client(application, email) as caller:
            attempts = [
                await caller.post("/api/v1/topics", json={"name": "Trees"}),
                await caller.put(path, json={"name": "Trees", "description": None}),
                await caller.patch(path, json={"name": "Trees"}),
                await caller.delete(path),
            ]
            assert [r.status_code for r in attempts] == [expected] * 4
            assert (await caller.get(path)).json()["name"] == "Graphs"


@pytest.mark.anyio
async def test_topic_crud_lifecycle(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/topics",
        json={"name": "Graphs", "description": "Network problems"},
    )

    assert created.status_code == 201
    topic = created.json()
    assert topic["name"] == "Graphs"

    listed = await client.get("/api/v1/topics")
    assert listed.status_code == 200
    assert listed.json() == [topic]

    updated = await client.put(
        f"/api/v1/topics/{topic['id']}",
        json={"name": "Trees", "description": None},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Trees"

    deleted = await client.delete(f"/api/v1/topics/{topic['id']}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/topics/{topic['id']}")).status_code == 404


@pytest.mark.anyio
async def test_topic_patch_updates_only_the_given_field(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/topics",
        json={"name": "Graphs", "description": "Network problems"},
    )
    topic = created.json()

    patched = await client.patch(
        f"/api/v1/topics/{topic['id']}",
        json={"name": "Trees"},
    )

    assert patched.status_code == 200
    assert patched.json()["name"] == "Trees"
    assert patched.json()["description"] == "Network problems"


@pytest.mark.anyio
async def test_topic_patch_empty_body_is_a_no_op(client: AsyncClient) -> None:
    created = await client.post("/api/v1/topics", json={"name": "Graphs"})
    topic = created.json()

    patched = await client.patch(f"/api/v1/topics/{topic['id']}", json={})

    assert patched.status_code == 200
    assert patched.json() == topic


@pytest.mark.anyio
async def test_topic_patch_unknown_id_returns_404(client: AsyncClient) -> None:
    response = await client.patch("/api/v1/topics/999999", json={"name": "Trees"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "topic_not_found"


@pytest.mark.anyio
async def test_topic_patch_duplicate_name_returns_409(client: AsyncClient) -> None:
    await client.post("/api/v1/topics", json={"name": "Graphs"})
    created = await client.post("/api/v1/topics", json={"name": "Trees"})
    topic = created.json()

    response = await client.patch(
        f"/api/v1/topics/{topic['id']}",
        json={"name": " graphs "},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "topic_name_conflict"


@pytest.mark.anyio
async def test_topic_patch_blank_name_returns_422(client: AsyncClient) -> None:
    created = await client.post("/api/v1/topics", json={"name": "Graphs"})
    topic = created.json()

    response = await client.patch(
        f"/api/v1/topics/{topic['id']}",
        json={"name": "   "},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_topic_duplicate_name_returns_stable_error_code(
    client: AsyncClient,
) -> None:
    await client.post("/api/v1/topics", json={"name": "Graphs"})

    response = await client.post("/api/v1/topics", json={"name": " graphs "})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "topic_name_conflict"


@pytest.mark.anyio
async def test_openapi_describes_every_topic_operation(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert set(response.json()["paths"]["/api/v1/topics"]) == {"get", "post"}
    assert set(response.json()["paths"]["/api/v1/topics/{topic_id}"]) == {
        "get",
        "put",
        "patch",
        "delete",
    }
