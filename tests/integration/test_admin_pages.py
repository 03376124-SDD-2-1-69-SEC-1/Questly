"""HTTP tests for the A-01 admin pages: Users, AI and System status."""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.auth.models import InstructorRequest, Role
from questly.core.generation.models import ProviderQuotaSnapshot, QuotaKind
from tests.fakes.admin import FakeAdminRepository, FakeSystemHealthChecker
from tests.fakes.app import build_app
from tests.fakes.auth import (
    DEFAULT_NOW,
    DEFAULT_PASSWORD,
    FakeAuthRepository,
    seed_user,
)
from tests.fakes.generation import FakeProviderQuota
from tests.integration.forms import log_in, post_form
from tests.integration.markup import loading_icons

ADMIN = "admin@kmitl.ac.th"
INSTRUCTOR = "instructor@kmitl.ac.th"
STUDENT = "66010001@kmitl.ac.th"
OTHER_MODEL = "other/model"

TABS = [
    ("/admin/settings", "users"),
    ("/admin/settings/ai", "ai"),
    ("/admin/settings/status", "status"),
]


class Admin:
    def __init__(
        self,
        health: FakeSystemHealthChecker | None = None,
        provider: FakeProviderQuota | None = None,
    ) -> None:
        self.auth = FakeAuthRepository()
        self.admin = seed_user(
            self.auth, email=ADMIN, full_name="Admin", role=Role.ADMIN
        )
        self.instructor = seed_user(
            self.auth, email=INSTRUCTOR, full_name="Somchai", role=Role.INSTRUCTOR
        )
        self.student = seed_user(self.auth, email=STUDENT, full_name="Nattapong")
        self.settings = FakeAdminRepository()
        self.app = build_app(
            auth_repository=self.auth,
            admin_repository=self.settings,
            system_health_checker=health,
            provider_quota=provider,
        )
        self.app.state.admin_service.allowed_models = (
            self.settings.settings.model,
            OTHER_MODEL,
        )

    def request_from(self, email: str, name: str, faculty: str = "Science") -> int:
        applicant = seed_user(self.auth, email=email, full_name=name)
        created = self.auth.create_instructor_request(
            InstructorRequest(applicant.id, faculty, DEFAULT_NOW)
        )
        return created.id

    async def client_for(self, email: str) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver"
        )
        await log_in(client, email, DEFAULT_PASSWORD)
        return client


# ---- access ------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(("path", "tab"), TABS)
async def test_admin_sees_each_tab_on_its_own_url(path: str, tab: str) -> None:
    client = await Admin().client_for(ADMIN)

    response = await client.get(path)

    assert response.status_code == 200
    assert 'data-page="A-01"' in response.text
    assert f'data-tab="{tab}"' in response.text
    assert response.text.count('aria-current="page"') == 1
    assert "<script" not in response.text.lower()


@pytest.mark.anyio
@pytest.mark.parametrize("email", [INSTRUCTOR, STUDENT])
@pytest.mark.parametrize("path", [path for path, _ in TABS])
async def test_other_roles_get_403_on_every_tab(email: str, path: str) -> None:
    client = await Admin().client_for(email)

    response = await client.get(path)

    assert response.status_code == 403
    assert loading_icons(response.text) == []


@pytest.mark.anyio
@pytest.mark.parametrize("path", [path for path, _ in TABS])
async def test_anonymous_visitor_is_sent_to_login(path: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=Admin().app), base_url="http://testserver"
    ) as client:
        response = await client.get(path)

    assert response.status_code == 303 and response.headers["location"] == "/login"


@pytest.mark.anyio
@pytest.mark.parametrize("email", [INSTRUCTOR, STUDENT])
@pytest.mark.parametrize(
    ("action", "data"),
    [
        ("/admin/settings/requests/1/approve", {}),
        ("/admin/settings/requests/1/reject", {}),
        ("/admin/settings/users/1/deactivate", {}),
        (
            "/admin/settings/ai",
            {"model": "m", "daily_quota": "1", "max_pages": "1"},
        ),
    ],
)
async def test_other_roles_get_403_on_every_post(
    email: str, action: str, data: dict[str, str]
) -> None:
    api = Admin()
    client = await api.client_for(email)

    response = await post_form(client, action, data, page="/classes")

    assert response.status_code == 403
    assert api.settings.settings.daily_quota == 20


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action",
    [
        "/admin/settings/requests/1/approve",
        "/admin/settings/requests/1/reject",
        "/admin/settings/users/1/deactivate",
        "/admin/settings/ai",
    ],
)
async def test_admin_post_without_the_csrf_token_is_refused(action: str) -> None:
    client = await Admin().client_for(ADMIN)

    assert (await client.post(action, data={})).status_code == 403


# ---- Users tab ---------------------------------------------------------------


@pytest.mark.anyio
async def test_users_tab_lists_pending_requests_and_all_users() -> None:
    api = Admin()
    request_id = api.request_from(
        "anan.t@kmitl.ac.th", "Anan Thong", "Information Technology"
    )
    client = await api.client_for(ADMIN)

    page = (await client.get("/admin/settings")).text

    assert "Anan Thong" in page and "Information Technology" in page
    assert f"/admin/settings/requests/{request_id}/approve" in page
    assert f"/admin/settings/requests/{request_id}/reject" in page
    assert "Somchai" in page and "Nattapong" in page
    assert 'data-state="A-01b"' not in page


@pytest.mark.anyio
async def test_no_pending_request_shows_the_empty_state() -> None:
    client = await Admin().client_for(ADMIN)

    page = (await client.get("/admin/settings")).text

    assert 'data-state="A-01b"' in page
    assert "No pending instructor requests" in page


@pytest.mark.anyio
async def test_approving_a_request_makes_the_applicant_an_instructor() -> None:
    api = Admin()
    request_id = api.request_from("anan.t@kmitl.ac.th", "Anan Thong")
    client = await api.client_for(ADMIN)

    response = await post_form(
        client,
        f"/admin/settings/requests/{request_id}/approve",
        {},
        page="/admin/settings",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings"
    applicant = api.auth.find_user_by_email("anan.t@kmitl.ac.th")
    assert applicant.role is Role.INSTRUCTOR
    assert 'data-state="A-01b"' in (await client.get("/admin/settings")).text


@pytest.mark.anyio
async def test_rejecting_a_request_keeps_the_applicant_a_student() -> None:
    api = Admin()
    request_id = api.request_from("anan.t@kmitl.ac.th", "Anan Thong")
    client = await api.client_for(ADMIN)

    response = await post_form(
        client,
        f"/admin/settings/requests/{request_id}/reject",
        {},
        page="/admin/settings",
    )

    assert response.status_code == 303
    assert api.auth.find_user_by_email("anan.t@kmitl.ac.th").role is Role.STUDENT
    assert 'data-state="A-01b"' in (await client.get("/admin/settings")).text


@pytest.mark.anyio
async def test_reviewing_the_same_request_twice_shows_an_alert() -> None:
    api = Admin()
    request_id = api.request_from("anan.t@kmitl.ac.th", "Anan Thong")
    client = await api.client_for(ADMIN)
    action = f"/admin/settings/requests/{request_id}/approve"
    await post_form(client, action, {}, page="/admin/settings")

    again = await post_form(client, action, {}, page="/admin/settings")

    assert again.status_code == 409
    assert "already approved or rejected" in again.text
    assert 'data-page="A-01"' in again.text


@pytest.mark.anyio
@pytest.mark.parametrize("verb", ["approve", "reject"])
async def test_reviewing_an_unknown_request_is_404(verb: str) -> None:
    client = await Admin().client_for(ADMIN)

    response = await post_form(
        client, f"/admin/settings/requests/999/{verb}", {}, page="/admin/settings"
    )

    assert response.status_code == 404


def _many_students(api: Admin, count: int) -> None:
    for number in range(count):
        seed_user(
            api.auth,
            email=f"6602{number:04d}@kmitl.ac.th",
            full_name=f"Student {number:02d}",
        )


@pytest.mark.anyio
async def test_all_users_are_paged_25_at_a_time() -> None:
    api = Admin()
    _many_students(api, 30)  # 33 accounts with the admin, instructor and student
    client = await api.client_for(ADMIN)

    first = (await client.get("/admin/settings")).text
    second = (await client.get("/admin/settings?page=2")).text

    assert first.count("/deactivate") == 25
    assert second.count("/deactivate") == 8
    assert "Page 1 of 2" in first and "Page 2 of 2" in second
    assert 'href="/admin/settings?page=2"' in first
    assert "Previous" not in first and "Next" not in second
    assert 'href="/admin/settings"' in second


@pytest.mark.anyio
async def test_search_matches_name_or_email() -> None:
    api = Admin()
    _many_students(api, 3)
    client = await api.client_for(ADMIN)

    by_name = (await client.get("/admin/settings?q=nattapong")).text
    by_email = (await client.get("/admin/settings?q=66020001")).text

    assert "Nattapong" in by_name and "Somchai" not in by_name
    assert "Student 01" in by_email and "Student 02" not in by_email


@pytest.mark.anyio
async def test_role_filter_keeps_one_role() -> None:
    client = await Admin().client_for(ADMIN)

    page = (await client.get("/admin/settings?role=instructor")).text

    assert "Somchai" in page and "Nattapong" not in page


@pytest.mark.anyio
async def test_an_unknown_role_filter_shows_everyone() -> None:
    client = await Admin().client_for(ADMIN)

    page = (await client.get("/admin/settings?role=wizard")).text

    assert "Somchai" in page and "Nattapong" in page


@pytest.mark.anyio
async def test_paging_links_keep_the_search_and_role() -> None:
    api = Admin()
    _many_students(api, 40)
    client = await api.client_for(ADMIN)

    page = (await client.get("/admin/settings?q=student&role=student")).text

    assert 'href="/admin/settings?q=student&amp;role=student&amp;page=2"' in page


@pytest.mark.anyio
async def test_deactivating_a_user_closes_the_account() -> None:
    api = Admin()
    client = await api.client_for(ADMIN)

    response = await post_form(
        client,
        f"/admin/settings/users/{api.student.id}/deactivate",
        {"q": "natta", "role": "student", "page": "1"},
        page="/admin/settings",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings?q=natta&role=student"
    assert api.auth.get_user(api.student.id).is_active is False
    page = (await client.get("/admin/settings?q=natta")).text
    assert "Deactivated" in page
    assert f"/users/{api.student.id}/deactivate" not in page


@pytest.mark.anyio
async def test_deactivating_yourself_shows_an_alert_not_a_500() -> None:
    api = Admin()
    client = await api.client_for(ADMIN)

    response = await post_form(
        client,
        f"/admin/settings/users/{api.admin.id}/deactivate",
        {},
        page="/admin/settings",
    )

    assert response.status_code == 400
    assert "cannot deactivate your own account" in response.text
    assert api.auth.get_user(api.admin.id).is_active is True


@pytest.mark.anyio
async def test_deactivating_an_unknown_user_is_404() -> None:
    client = await Admin().client_for(ADMIN)

    response = await post_form(
        client, "/admin/settings/users/999/deactivate", {}, page="/admin/settings"
    )

    assert response.status_code == 404


# ---- AI tab ------------------------------------------------------------------


@pytest.mark.anyio
async def test_ai_tab_shows_the_settings_and_the_allowlist() -> None:
    api = Admin()
    client = await api.client_for(ADMIN)

    page = (await client.get("/admin/settings/ai")).text

    assert api.settings.settings.model in page and OTHER_MODEL in page
    assert 'name="daily_quota" type="number" value="20"' in page
    assert 'name="max_pages" type="number" value="10"' in page
    assert 'name="require_citations" checked' in page
    assert "Usage this month" in page and "0 generations" in page


@pytest.mark.anyio
async def test_ai_tab_prints_the_exact_provider_quota_line() -> None:
    client = await Admin().client_for(ADMIN)

    page = (await client.get("/admin/settings/ai")).text

    assert "OpenRouter free quota: 37 of 50 left today · resets 07:00" in page


@pytest.mark.anyio
async def test_ai_tab_prints_the_note_when_the_quota_is_not_exact() -> None:
    provider = FakeProviderQuota(
        ProviderQuotaSnapshot(
            kind=QuotaKind.UNKNOWN,
            resets_at=datetime(2026, 10, 13, 0, 0, tzinfo=UTC),
            note="OpenRouter quota is unavailable right now.",
        )
    )
    client = await Admin(provider=provider).client_for(ADMIN)

    page = (await client.get("/admin/settings/ai")).text

    assert "OpenRouter quota is unavailable right now." in page
    assert "free quota:" not in page


@pytest.mark.anyio
async def test_saving_ai_settings_changes_every_field() -> None:
    api = Admin()
    client = await api.client_for(ADMIN)

    response = await post_form(
        client,
        "/admin/settings/ai",
        {
            "model": OTHER_MODEL,
            "daily_quota": "5",
            "max_pages": "30",
            "require_citations": "on",
        },
        page="/admin/settings/ai",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/ai?saved=1"
    saved = api.settings.settings
    assert (saved.model, saved.daily_quota, saved.max_pages) == (OTHER_MODEL, 5, 30)
    assert saved.require_citations is True
    assert "AI settings saved" in (await client.get(response.headers["location"])).text


@pytest.mark.anyio
async def test_an_unticked_citations_box_turns_the_setting_off() -> None:
    api = Admin()
    client = await api.client_for(ADMIN)

    await post_form(
        client,
        "/admin/settings/ai",
        {
            "model": api.settings.settings.model,
            "daily_quota": "20",
            "max_pages": "10",
        },
        page="/admin/settings/ai",
    )

    assert api.settings.settings.require_citations is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"daily_quota": "0"}, "Daily quota must be at least 1"),
        ({"max_pages": "-3"}, "Max pages must be at least 1"),
        ({"daily_quota": "many"}, "Daily quota must be a whole number"),
        ({"max_pages": ""}, "Max pages must be a whole number"),
        ({"model": "unlisted/model"}, "unlisted/model is not an allowed model"),
    ],
)
async def test_invalid_ai_settings_show_an_alert_and_save_nothing(
    data: dict[str, str], message: str
) -> None:
    api = Admin()
    client = await api.client_for(ADMIN)
    before = api.settings.settings
    form = {
        "model": before.model,
        "daily_quota": "20",
        "max_pages": "10",
        **data,
    }

    response = await post_form(
        client, "/admin/settings/ai", form, page="/admin/settings/ai"
    )

    assert response.status_code == 422
    assert message in response.text
    assert 'data-page="A-01"' in response.text
    assert api.settings.settings == before


# ---- System status tab, A-01a ----------------------------------------------------


@pytest.mark.anyio
async def test_status_tab_lists_the_six_services() -> None:
    client = await Admin().client_for(ADMIN)

    page = (await client.get("/admin/settings/status")).text

    for name in (
        "Web application",
        "Database",
        "PDF processing",
        "AI generation",
        "Code execution sandbox",
        "Email delivery",
    ):
        assert name in page
    assert page.count("Operational") == 6
    assert 'data-state="A-01a"' not in page


@pytest.mark.anyio
async def test_a_service_that_is_down_shows_an_alert_and_a_down_row() -> None:
    health = FakeSystemHealthChecker(down=("code execution sandbox",))
    client = await Admin(health=health).client_for(ADMIN)

    page = (await client.get("/admin/settings/status")).text

    assert page.count('data-state="A-01a"') == 2  # the alert and the row
    assert "Code execution sandbox</strong> is not responding" in page
    assert page.count("Operational") == 5 and page.count(">Down<") == 1
    assert "Restart" not in page and "View logs" not in page


@pytest.mark.anyio
@pytest.mark.parametrize(("path", "tab"), TABS)
async def test_only_the_status_tab_refreshes_itself(path: str, tab: str) -> None:
    client = await Admin().client_for(ADMIN)

    page = (await client.get(path)).text

    assert ('http-equiv="refresh" content="60"' in page) is (tab == "status")


@pytest.mark.anyio
@pytest.mark.parametrize(("path", "tab"), TABS)
async def test_only_the_status_tab_shows_that_it_is_live(path: str, tab: str) -> None:
    client = await Admin().client_for(ADMIN)

    response = await client.get(path)

    assert response.status_code == 200
    icons = loading_icons(response.text)
    if tab == "status":
        [icon] = icons
        assert 'data-loading="generic"' in icon
        assert 'aria-hidden="true"' in icon
        assert "Refreshes every 60 seconds." in response.text
    else:
        assert icons == []


# ---- G-01 landing ----------------------------------------------------------------


@pytest.mark.anyio
async def test_admin_login_still_lands_on_a_page_that_exists() -> None:
    client = await Admin().client_for(ADMIN)

    landing = await client.get("/", follow_redirects=True)

    assert landing.status_code == 200
    assert str(landing.url).endswith("/admin/settings")
    assert 'data-page="A-01"' in landing.text
