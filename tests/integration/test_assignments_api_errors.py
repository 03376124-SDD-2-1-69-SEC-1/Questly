"""Characterise the error bodies of the Assignments JSON routes."""

import pytest

from tests.integration.test_assignments_api import OUTSIDER, STUDENT, TEACHER, Campus


def error(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"detail": {"code": code, "message": message}}


@pytest.mark.anyio
async def test_unknown_assignment_is_404_on_every_assignment_route() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    path = "/api/v1/assignments/99999"
    expected = error("assignment_not_found", "Assignment not found")

    responses = [
        await teacher.get(path),
        await teacher.get(f"{path}/versions"),
        await teacher.get(f"{path}/versions/1"),
        await teacher.get(f"{path}/test-cases"),
        await teacher.get(f"{path}/test-cases/1"),
    ]

    assert [r.status_code for r in responses] == [404] * 5
    assert [r.json() for r in responses] == [expected] * 5


@pytest.mark.anyio
async def test_assignment_hidden_from_an_outsider_looks_like_a_missing_one() -> None:
    campus = Campus()
    outsider = await campus.client_for(OUTSIDER)

    response = await outsider.get(f"/api/v1/assignments/{campus.assignment_id}")

    assert response.status_code == 404
    assert response.json() == error("assignment_not_found", "Assignment not found")


@pytest.mark.anyio
async def test_classroom_not_visible_is_404_on_every_classroom_route() -> None:
    campus = Campus()
    outsider = await campus.client_for(OUTSIDER)
    base = f"/api/v1/classrooms/{campus.classroom.id}"
    expected = error("classroom_not_found", "Classroom not found")

    responses = [
        await outsider.get(f"{base}/assignments"),
        await outsider.get(f"{base}/summary"),
        await outsider.get(f"{base}/summary.csv"),
        await outsider.get(campus.posting_path),
        await outsider.get("/api/v1/classrooms/99999/assignments"),
    ]

    assert [r.status_code for r in responses] == [404] * 5
    assert [r.json() for r in responses] == [expected] * 5


@pytest.mark.anyio
async def test_assignment_not_posted_to_the_classroom_is_404() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    path = f"/api/v1/classrooms/{campus.classroom.id}/assignments/99999"

    responses = [
        await teacher.get(path),
        await teacher.post(f"{path}/close"),
        await teacher.delete(path),
    ]

    assert [r.status_code for r in responses] == [404] * 3
    assert [r.json() for r in responses] == [
        error("posting_not_found", "Not published to this classroom")
    ] * 3


@pytest.mark.anyio
async def test_unknown_version_is_404() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    path = f"/api/v1/assignments/{campus.assignment_id}/versions/9"

    response = await teacher.get(path)

    assert response.status_code == 404
    assert response.json() == error("version_not_found", "Version not found")


@pytest.mark.anyio
async def test_unknown_test_case_is_404_on_every_test_case_route() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    path = f"/api/v1/assignments/{campus.assignment_id}/test-cases/99999"
    body = {"input_data": "1", "expected_output": "2", "reason": "Fix"}

    responses = [
        await teacher.get(path),
        await teacher.put(path, json=body),
        await teacher.delete(path, params={"reason": "Drop"}),
    ]

    assert [r.status_code for r in responses] == [404] * 3
    assert [r.json() for r in responses] == [
        error("test_case_not_found", "Test case not found")
    ] * 3


@pytest.mark.anyio
async def test_a_rule_violation_is_422_with_the_rule_as_message() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    path = f"/api/v1/assignments/{campus.assignment_id}"

    blank_reason = await teacher.patch(path, json={"reason": "  "})
    blank_title = await teacher.patch(path, json={"reason": "Rename", "title": " "})

    assert blank_reason.status_code == 422
    assert blank_reason.json() == error(
        "invalid_assignment", "a reason for the change is required"
    )
    assert blank_title.status_code == 422
    assert blank_title.json() == error("invalid_assignment", "a title is required")


@pytest.mark.anyio
async def test_a_member_asking_for_an_instructor_use_case_is_403() -> None:
    campus = Campus()
    student = await campus.client_for(STUDENT)

    response = await student.patch(campus.posting_path, json={"max_score": 20})

    assert response.status_code == 403
