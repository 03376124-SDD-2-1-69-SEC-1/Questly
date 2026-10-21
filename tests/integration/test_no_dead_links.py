"""No link on a built page leads to a 404 while its slice is missing."""

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.auth.models import Role
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user
from tests.integration.forms import log_in, post_form

TEACHER = "somchai.p@kmitl.ac.th"
STUDENT = "66010001@kmitl.ac.th"
ADMIN = "admin@kmitl.ac.th"


def _app():
    users = FakeAuthRepository()
    seed_user(users, email=TEACHER, full_name="Somchai Prasert", role=Role.INSTRUCTOR)
    seed_user(users, email=STUDENT, full_name="Nattapong Suwan")
    seed_user(users, email=ADMIN, full_name="Admin", role=Role.ADMIN)
    return build_app(auth_repository=users)


async def _client(app, email: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    await log_in(client, email, DEFAULT_PASSWORD)
    return client


@pytest.mark.anyio
async def test_admin_lands_on_a_page_that_exists() -> None:
    client = await _client(_app(), ADMIN)

    landing = await client.get("/", follow_redirects=True)

    assert landing.status_code == 200
    assert str(landing.url).endswith("/admin/settings")
    assert 'data-page="A-01"' in landing.text


@pytest.mark.anyio
@pytest.mark.parametrize("email", [TEACHER, STUDENT])
async def test_admin_settings_is_admin_only(email: str) -> None:
    client = await _client(_app(), email)

    assert (await client.get("/admin/settings")).status_code == 403


@pytest.mark.anyio
async def test_admin_settings_needs_a_login() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://testserver"
    ) as client:
        response = await client.get("/admin/settings")

    assert response.status_code == 303 and response.headers["location"] == "/login"


@pytest.mark.anyio
async def test_instructor_pages_link_to_the_documents_library() -> None:
    client = await _client(_app(), TEACHER)
    created = await post_form(
        client,
        "/classes",
        {
            "course_code": "01076001",
            "course_name": "Programming I",
            "section": "1",
            "semester": "1/2569",
        },
        page="/classes",
    )
    classroom = await client.get(created.headers["location"].split("?")[0])

    for page in (await client.get("/classes"), classroom):
        assert page.status_code == 200
        assert 'href="/documents"' in page.text
    documents = await client.get("/documents")
    assert documents.status_code == 200
    assert 'data-page="T-06"' in documents.text
