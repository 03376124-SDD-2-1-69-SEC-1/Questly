"""CSRF on every HTML form post, Secure cookies, and `/` (G-00 or a redirect)."""

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.auth.models import Role
from questly.main import create_app
from tests.fakes.app import build_app, fake_settings
from tests.fakes.assignments import FakeAssignmentRepository
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user
from tests.fakes.classrooms import FakeClassroomRepository, FakeClassroomStats
from tests.fakes.documents import FakeKnowledgeDocumentRepository, FakeObjectStorage
from tests.fakes.notifications import FakeNotificationRepository
from tests.fakes.topics import FakeTopicRepository
from tests.fakes.vector import FakeVectorRepository
from tests.integration.forms import csrf_token, log_in, post_form

TEACHER = "somchai.p@kmitl.ac.th"
STUDENT = "66010001@kmitl.ac.th"
ADMIN = "admin@kmitl.ac.th"
CLASSROOM = {
    "course_code": "01076001",
    "course_name": "Programming I",
    "section": "1",
    "semester": "1/2569",
}


def _users() -> FakeAuthRepository:
    users = FakeAuthRepository()
    seed_user(users, email=TEACHER, full_name="Somchai Prasert", role=Role.INSTRUCTOR)
    seed_user(users, email=STUDENT, full_name="Nattapong Suwan")
    seed_user(users, email=ADMIN, full_name="Admin", role=Role.ADMIN)
    return users


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.anyio
@pytest.mark.parametrize("token", [None, "", "forged"])
async def test_login_without_the_page_token_is_403(token: str | None) -> None:
    async with _client(build_app(auth_repository=_users())) as client:
        await client.get("/login")
        data = {"email": TEACHER, "password": DEFAULT_PASSWORD}
        if token is not None:
            data["csrf_token"] = token
        response = await client.post("/login", data=data)

    assert response.status_code == 403
    assert "questly_session" not in response.headers.get("set-cookie", "")


@pytest.mark.anyio
async def test_anonymous_pages_set_the_csrf_cookie_they_check() -> None:
    async with _client(build_app(auth_repository=_users())) as client:
        page = await client.get("/signup")
        login = await post_form(
            client,
            "/login",
            {"email": TEACHER, "password": DEFAULT_PASSWORD},
            page="/login",
        )

    assert "questly_csrf=" in page.headers["set-cookie"]
    assert login.status_code == 303


@pytest.mark.anyio
async def test_logged_in_forms_need_the_session_token() -> None:
    async with _client(build_app(auth_repository=_users())) as client:
        anonymous_token = await csrf_token(client, "/login")
        await log_in(client, TEACHER, DEFAULT_PASSWORD)
        missing = await client.post("/classes", data=CLASSROOM)
        stale = await client.post(
            "/classes", data={**CLASSROOM, "csrf_token": anonymous_token}
        )
        good = await post_form(client, "/classes", CLASSROOM, page="/classes")

    assert missing.status_code == 403
    assert stale.status_code == 403
    assert good.status_code == 303


@pytest.mark.anyio
async def test_another_sessions_token_is_refused() -> None:
    app = build_app(auth_repository=_users())
    async with _client(app) as teacher, _client(app) as other:
        await log_in(teacher, TEACHER, DEFAULT_PASSWORD)
        await log_in(other, TEACHER, DEFAULT_PASSWORD)
        others_token = await csrf_token(other, "/classes")
        response = await teacher.post(
            "/classes", data={**CLASSROOM, "csrf_token": others_token}
        )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_logout_needs_the_token() -> None:
    async with _client(build_app(auth_repository=_users())) as client:
        await log_in(client, STUDENT, DEFAULT_PASSWORD)
        refused = await client.post("/logout")
        still_in = await client.get("/api/v1/auth/me")

    assert refused.status_code == 403
    assert still_in.status_code == 200


@pytest.mark.anyio
async def test_cookies_are_secure_unless_turned_off() -> None:
    app = create_app(
        settings=fake_settings(),
        topic_repository=FakeTopicRepository(),
        assignment_repository=FakeAssignmentRepository(),
        knowledge_document_repository=FakeKnowledgeDocumentRepository(),
        object_storage=FakeObjectStorage(),
        vector_repository=FakeVectorRepository(),
        auth_repository=_users(),
        notification_repository=FakeNotificationRepository(),
        classroom_repository=FakeClassroomRepository(),
        classroom_stats=FakeClassroomStats(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        page = await client.get("/login")
        login = await client.post(
            "/api/v1/auth/login", json={"email": STUDENT, "password": DEFAULT_PASSWORD}
        )

    assert "secure" in page.headers["set-cookie"].lower()
    assert "secure" in login.headers["set-cookie"].lower()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("email", "location"),
    [
        (STUDENT, "/classes"),
        (TEACHER, "/classes"),
        (ADMIN, "/admin/settings"),
    ],
)
async def test_root_redirects_to_the_landing(email: str, location: str) -> None:
    async with _client(build_app(auth_repository=_users())) as client:
        await log_in(client, email, DEFAULT_PASSWORD)
        response = await client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == location


@pytest.mark.anyio
async def test_root_shows_the_landing_page_to_a_visitor() -> None:
    async with _client(build_app(auth_repository=_users())) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert 'data-page="G-00"' in response.text
    assert "set-cookie" not in response.headers


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("label", "href"),
    [
        ("เข้าสู่ระบบ", "/login"),
        ("สมัครสมาชิก", "/signup"),
        ("สมัครด้วยอีเมล @kmitl.ac.th", "/signup"),
    ],
)
async def test_landing_calls_to_action_lead_to_live_pages(
    label: str, href: str
) -> None:
    async with _client(build_app(auth_repository=_users())) as client:
        landing = await client.get("/")
        target = await client.get(href)

    assert f'href="{href}"' in landing.text and f">{label}</a>" in landing.text
    assert target.status_code == 200


@pytest.mark.anyio
async def test_landing_in_page_links_point_at_its_sections() -> None:
    async with _client(build_app(auth_repository=_users())) as client:
        landing = await client.get("/")

    for section in ("features", "how", "problems"):
        assert f'href="#{section}"' in landing.text
        assert f'id="{section}"' in landing.text


@pytest.mark.anyio
async def test_landing_footer_credits_the_author_on_github() -> None:
    async with _client(build_app(auth_repository=_users())) as client:
        landing = await client.get("/")

    assert 'href="https://github.com/Doonminus2"' in landing.text
    assert ">Doonminus2</a>" in landing.text
