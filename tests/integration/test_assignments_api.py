"""HTTP tests for Postings, Versions, test cases and the Summary per role."""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    Schedule,
    TestCase,
    TestCaseKind,
)
from questly.core.auth.models import Actor, Role
from tests.fakes.app import build_app
from tests.fakes.auth import DEFAULT_PASSWORD, FakeAuthRepository, seed_user

DEADLINE = datetime(2026, 10, 15, 16, 59, tzinfo=UTC)
TEACHER = "somchai.p@kmitl.ac.th"
STUDENT = "66010001@kmitl.ac.th"
OUTSIDER = "66010002@kmitl.ac.th"

HIDDEN_CASES = [
    TestCase(
        input_data="3\n1 2 3\n9",
        expected_output="secret-hidden-answer",
        kind=TestCaseKind.HIDDEN,
    ),
    TestCase(
        input_data="0\n\n1",
        expected_output="secret-edge-answer",
        kind=TestCaseKind.EDGE,
    ),
]


class Campus:
    """Somchai owns Sec 1 with one published problem; Nattapong is a Member."""

    def __init__(self, *, with_hidden_cases: bool = False) -> None:
        users = FakeAuthRepository()
        accounts = {}
        for email, name, role in [
            (TEACHER, "Somchai Prasert", Role.INSTRUCTOR),
            (STUDENT, "Nattapong Suwan", Role.STUDENT),
            (OUTSIDER, "Pimchanok Wong", Role.STUDENT),
        ]:
            user = seed_user(users, email=email, full_name=name, role=role)
            accounts[email] = Actor(
                user_id=user.id, role=role, full_name=name, email=email
            )
        self.app = build_app(auth_repository=users)
        classrooms = self.app.state.classroom_service
        self.classroom = classrooms.create(
            accounts[TEACHER],
            course_code="01076001",
            course_name="Programming I",
            section="1",
            semester="1/2569",
        )
        classrooms.join(accounts[STUDENT], self.classroom.join_code)
        published = self.app.state.assignment_service.publish(
            accounts[TEACHER],
            AssignmentContent(
                title="Binary search",
                problem_statement="Print the index of x, or -1.",
                difficulty=Difficulty.MEDIUM,
                test_cases=[
                    TestCase(input_data="1\n4\n4", expected_output="0"),
                    *(HIDDEN_CASES if with_hidden_cases else []),
                ],
            ),
            Schedule(deadline=DEADLINE),
            [self.classroom.id],
        )
        self.assignment_id = published.assignment.id
        self.posting_id = published.postings[0].id

    async def client_for(self, email: str) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver"
        )
        await client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
        )
        return client

    @property
    def posting_path(self) -> str:
        return (
            f"/api/v1/classrooms/{self.classroom.id}/assignments/{self.assignment_id}"
        )


@pytest.mark.anyio
async def test_problem_list_per_role() -> None:
    campus = Campus()
    path = f"/api/v1/classrooms/{campus.classroom.id}/assignments"
    teacher = await campus.client_for(TEACHER)
    student = await campus.client_for(STUDENT)
    outsider = await campus.client_for(OUTSIDER)

    teacher_view = await teacher.get(path)
    student_view = await student.get(path)

    assert teacher_view.json()["variant"] == "instructor"
    assert teacher_view.json()["student_count"] == 1
    assert teacher_view.json()["problems"][0]["submitted"] == 0
    assert student_view.json()["variant"] == "student"
    assert student_view.json()["problems"][0]["state"] == "not_started"
    assert (await outsider.get(path)).status_code == 404


@pytest.mark.anyio
async def test_get_problem_and_assignment() -> None:
    campus = Campus(with_hidden_cases=True)
    student = await campus.client_for(STUDENT)
    outsider = await campus.client_for(OUTSIDER)

    problem = await student.get(campus.posting_path)
    assignment = await student.get(f"/api/v1/assignments/{campus.assignment_id}")
    hidden = await outsider.get(f"/api/v1/assignments/{campus.assignment_id}")

    assert problem.json()["number"] == 1
    assert assignment.json()["title"] == "Binary search"
    assert {case["kind"] for case in assignment.json()["test_cases"]} == {"sample"}
    assert "secret" not in assignment.text
    assert hidden.status_code == 404


@pytest.mark.anyio
async def test_owner_still_sees_every_test_case() -> None:
    campus = Campus(with_hidden_cases=True)
    teacher = await campus.client_for(TEACHER)

    assignment = await teacher.get(f"/api/v1/assignments/{campus.assignment_id}")

    kinds = [case["kind"] for case in assignment.json()["test_cases"]]
    assert kinds == ["sample", "hidden", "edge"]


@pytest.mark.anyio
async def test_posting_settings_close_and_unpost_are_instructor_only() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    student = await campus.client_for(STUDENT)

    forbidden = await student.patch(campus.posting_path, json={"max_score": 20})
    patched = await teacher.patch(campus.posting_path, json={"max_score": 20})
    closed = await teacher.post(f"{campus.posting_path}/close")
    removed = await teacher.delete(campus.posting_path)
    gone = await teacher.get(campus.posting_path)

    assert forbidden.status_code == 403
    assert patched.json()["max_score"] == 20
    assert patched.json()["allow_late"] is False
    assert closed.json()["closed_at"] is not None
    assert removed.status_code == 204
    assert gone.status_code == 404


@pytest.mark.anyio
async def test_edit_creates_a_version_and_extends_a_deadline() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    path = f"/api/v1/assignments/{campus.assignment_id}"

    edited = await teacher.patch(
        path,
        json={
            "reason": "Clarify the input format",
            "problem_statement": "Read n, the list and x. Print the index or -1.",
            "extend_deadline": {str(campus.posting_id): "2026-10-18T16:59:00Z"},
        },
    )
    versions = await teacher.get(f"{path}/versions")
    first = await teacher.get(f"{path}/versions/1")
    posting = await teacher.get(campus.posting_path)
    no_reason = await teacher.patch(path, json={"title": "x"})

    assert edited.json()["current_version"] == 2
    assert edited.json()["title"] == "Binary search"
    assert [v["number"] for v in versions.json()] == [2, 1]
    assert first.json()["reason"] == "Published"
    assert posting.json()["posting"]["deadline"].startswith("2026-10-18")
    assert no_reason.status_code == 422


@pytest.mark.anyio
async def test_test_case_crud_needs_a_reason_and_the_owner() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    student = await campus.client_for(STUDENT)
    path = f"/api/v1/assignments/{campus.assignment_id}/test-cases"
    body = {
        "input_data": "0\n\n3",
        "expected_output": "-1",
        "kind": "edge",
        "note": "empty list",
        "reason": "Add the empty-list edge case",
    }

    created = await teacher.post(path, json=body)
    test_case_id = created.json()["id"]
    replaced = await teacher.put(
        f"{path}/{test_case_id}", json={**body, "note": "empty", "reason": "Rename"}
    )
    listed = await teacher.get(path)
    denied = await student.get(path)
    deleted = await teacher.delete(f"{path}/{test_case_id}", params={"reason": "Drop"})
    missing_reason = await teacher.delete(f"{path}/{test_case_id}")

    assert created.status_code == 201
    assert created.json()["kind"] == "edge"
    assert replaced.json()["note"] == "empty"
    assert len(listed.json()) == 2
    assert denied.status_code == 403
    assert deleted.status_code == 204
    assert missing_reason.status_code == 422


@pytest.mark.anyio
async def test_summary_per_role() -> None:
    campus = Campus()
    path = f"/api/v1/classrooms/{campus.classroom.id}/summary"
    teacher = await campus.client_for(TEACHER)
    student = await campus.client_for(STUDENT)

    teacher_view = await teacher.get(path)
    student_view = await student.get(path)

    assert teacher_view.json()["variant"] == "instructor"
    assert teacher_view.json()["problem_count"] == 1
    assert teacher_view.json()["never_submitted"] == 1
    assert student_view.json()["variant"] == "student"
    assert student_view.json()["total"] == 1


@pytest.mark.anyio
async def test_old_crud_endpoints_are_gone() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)

    listed = await teacher.get("/api/v1/assignments")
    created = await teacher.post("/api/v1/assignments", json={})

    assert listed.status_code in (404, 405)
    assert created.status_code in (404, 405)
