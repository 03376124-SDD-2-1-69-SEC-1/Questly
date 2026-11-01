"""Drafts over HTTP: the JSON API and the T-03 → T-04 → T-04b pages."""

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.auth.models import Actor, Role
from questly.core.generation.models import DocumentSummary
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, FakeClock, seed_user
from tests.fakes.generation import (
    FakeDocumentCatalog,
    FakeGenerationClient,
    FakeGenerationJobStore,
    FakeProviderQuota,
    binary_search_response,
)
from tests.integration.forms import csrf_token, log_in, post_form

TEACHER = "somchai.p@kmitl.ac.th"
OTHER_TEACHER = "warunee.k@kmitl.ac.th"
STUDENT = "66010001@kmitl.ac.th"
OUTSIDER = "66010002@kmitl.ac.th"
ADMIN = "admin@kmitl.ac.th"


class Campus:
    def __init__(self) -> None:
        users = FakeAuthRepository()
        teacher = seed_user(
            users, email=TEACHER, full_name="Somchai", role=Role.INSTRUCTOR
        )
        student = seed_user(users, email=STUDENT, full_name="Nattapong")
        seed_user(users, email=OTHER_TEACHER, full_name="Warunee", role=Role.INSTRUCTOR)
        seed_user(users, email=OUTSIDER, full_name="Outsider")
        seed_user(users, email=ADMIN, full_name="Admin", role=Role.ADMIN)
        catalog = FakeDocumentCatalog()
        catalog.by_owner[teacher.id] = [
            DocumentSummary(id=1, filename="lecture-06-searching.pdf", pages=24)
        ]
        self.client_stub = FakeGenerationClient(binary_search_response())
        self.provider_quota = FakeProviderQuota()
        self.job_store = FakeGenerationJobStore()
        self.clock = FakeClock()
        self.app = build_app(
            auth_repository=users,
            document_catalog=catalog,
            generation_client=self.client_stub,
            provider_quota=self.provider_quota,
            generation_job_store=self.job_store,
            clock=self.clock,
        )
        classrooms = self.app.state.classroom_service
        owner = Actor(
            user_id=teacher.id, role=Role.INSTRUCTOR, full_name="S", email=TEACHER
        )
        self.classroom = classrooms.create(
            owner,
            course_code="01076001",
            course_name="Programming I",
            section="1",
            semester="1/2569",
        )
        classrooms.join(
            Actor(user_id=student.id, role=Role.STUDENT, full_name="N", email=STUDENT),
            self.classroom.join_code,
        )

    async def client_for(self, email: str) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver"
        )
        await log_in(client, email, DEFAULT_PASSWORD)
        return client


@pytest.mark.anyio
async def test_api_generate_save_publish_round_trip() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    base = f"/api/v1/classrooms/{campus.classroom.id}/drafts"

    created = await teacher.post(
        base, json={"prompt": "Binary search", "document_ids": [1]}
    )
    draft_id = created.json()["id"]
    listed = await teacher.get(base)
    early = await teacher.post(f"/api/v1/drafts/{draft_id}/publish", json={})
    saved = await teacher.patch(
        f"/api/v1/drafts/{draft_id}",
        json={"deadline": "2026-10-15T16:59:00Z", "max_score": 20},
    )
    regenerated = await teacher.post(
        f"/api/v1/drafts/{draft_id}/regenerate", json={"part": "title"}
    )
    published = await teacher.post(f"/api/v1/drafts/{draft_id}/publish", json={})
    gone = await teacher.get(f"/api/v1/drafts/{draft_id}")
    problems = await teacher.get(
        f"/api/v1/classrooms/{campus.classroom.id}/assignments"
    )

    assert created.status_code == 201
    assert [d["id"] for d in listed.json()] == [draft_id]
    assert early.status_code == 422
    assert saved.json()["max_score"] == 20
    assert regenerated.status_code == 200
    assert published.status_code == 200
    assert gone.status_code == 404
    assert problems.json()["problems"][0]["title"] == "Binary search on sorted input"


@pytest.mark.anyio
async def test_api_failures_and_roles() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    student = await campus.client_for(STUDENT)
    base = f"/api/v1/classrooms/{campus.classroom.id}/drafts"

    forbidden = await student.post(base, json={"prompt": "x"})
    campus.client_stub.fail = True
    failed = await teacher.post(base, json={"prompt": "x"})

    assert forbidden.status_code == 403
    assert failed.status_code == 502
    assert failed.json()["detail"]["code"] == "generation_failed"


@pytest.mark.anyio
async def generate_draft(client: AsyncClient, cid: int, prompt: str = "x") -> str:
    """Generate through the job flow and return the Draft's page URL."""
    submitted = await post_form(
        client,
        f"/classes/{cid}/generate",
        {"prompt": prompt, "difficulty": "medium", "document_ids": "1"},
        page=f"/classes/{cid}/generate",
    )
    assert submitted.status_code == 303
    assert "/generate/jobs/" in submitted.headers["location"]
    finished = await client.get(submitted.headers["location"])
    assert finished.status_code == 303
    return finished.headers["location"]


@pytest.mark.anyio
async def test_pages_generate_review_and_publish() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    cid = campus.classroom.id

    t03 = await teacher.get(f"/classes/{cid}/generate")
    draft_url = await generate_draft(teacher, cid, "Binary search")
    t03a = await teacher.get(draft_url)
    draft_id = draft_url.split("draft=")[1]
    base = f"/classes/{cid}/drafts/{draft_id}"
    token = await csrf_token(teacher, f"{base}?step=1")

    step1 = await teacher.post(
        f"{base}/step1",
        data={
            "title": "Binary search",
            "problem_statement": "Find x.",
            "difficulty": "medium",
            "action": "next",
            "csrf_token": token,
        },
    )
    step2_page = await teacher.get(f"{base}?step=2")
    step2 = await teacher.post(
        f"{base}/step2",
        data={
            "input_data": ["1\n4\n4"],
            "expected_output": ["0"],
            "kind": ["sample"],
            "note": ["single"],
            "action": "next",
            "csrf_token": token,
        },
    )
    blocked = await teacher.post(
        f"{base}/step3", data={"action": "next", "csrf_token": token}
    )
    step3 = await teacher.post(
        f"{base}/step3",
        data={
            "deadline": "2026-10-15T23:59",
            "max_score": "10",
            "time_limit_s": "1.0",
            "allow_resubmission": "on",
            "action": "next",
            "csrf_token": token,
        },
    )
    step4_page = await teacher.get(f"{base}?step=4")
    published = await teacher.post(
        f"{base}/step4",
        data={"classroom_ids": [str(cid)], "action": "publish", "csrf_token": token},
    )
    t04b = await teacher.get(published.headers["location"])
    t01 = await teacher.get(f"/classes/{cid}?tab=problems")

    assert 'data-page="T-03"' in t03.text and "lecture-06-searching.pdf" in t03.text
    assert 'data-state="T-03a"' in t03a.text
    assert step1.headers["location"] == f"{base}?step=2"
    assert 'data-step="2"' in step2_page.text
    assert step2.headers["location"] == f"{base}?step=3"
    assert blocked.status_code == 422 and 'data-state="T-04a"' in blocked.text
    assert step3.headers["location"] == f"{base}?step=4"
    assert "เผยแพร่โจทย์" in step4_page.text
    assert 'data-state="T-04b"' in t04b.text
    assert "Binary search" in t01.text and "Problem 1" in t01.text


@pytest.mark.anyio
async def test_page_shows_t03d_when_the_model_fails_and_t03b_without_documents() -> (
    None
):
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    cid = campus.classroom.id
    campus.client_stub.fail = True

    submitted = await post_form(
        teacher,
        f"/classes/{cid}/generate",
        {"prompt": "x"},
        page=f"/classes/{cid}/generate",
    )
    failed = await teacher.get(submitted.headers["location"])

    assert submitted.status_code == 303
    assert failed.status_code == 200
    assert 'data-state="T-03d"' in failed.text
    assert "no quota was used" in failed.text


@pytest.mark.anyio
async def test_students_cannot_open_generation_pages() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)

    response = await student.get(f"/classes/{campus.classroom.id}/generate")

    assert response.status_code == 403


@pytest.mark.anyio
async def test_step2_keeps_multi_line_test_input() -> None:
    """Test input spans lines ("n / list / x"); a save must not flatten it."""
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    cid = campus.classroom.id
    draft_id = int((await generate_draft(teacher, cid)).split("draft=")[1])
    page = await teacher.get(f"/classes/{cid}/drafts/{draft_id}?step=2")
    token = await csrf_token(teacher, f"/classes/{cid}/drafts/{draft_id}?step=2")

    await teacher.post(
        f"/classes/{cid}/drafts/{draft_id}/step2",
        data={
            "input_data": ["5\n1 3 5 7 9\n7"],
            "expected_output": ["3"],
            "kind": ["sample"],
            "note": [""],
            "action": "save",
            "csrf_token": token,
        },
    )
    service = campus.app.state.generation_service
    owner = campus.app.state.auth_service.resolve_session(
        teacher.cookies["questly_session"]
    )

    assert "<textarea" in page.text and "5\n1 3 5 7 9\n7" in page.text
    saved = service.draft(owner, draft_id).content.test_cases
    assert saved[0].input_data == "5\n1 3 5 7 9\n7"
