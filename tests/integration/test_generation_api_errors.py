"""Characterise the error bodies of the Drafts JSON routes."""

import pytest

from tests.integration.test_drafts import OUTSIDER, TEACHER, Campus


def error(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"detail": {"code": code, "message": message}}


@pytest.mark.anyio
async def test_unknown_draft_is_404_on_every_draft_route() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    path = "/api/v1/drafts/99999"

    responses = [
        await teacher.get(path),
        await teacher.patch(path, json={"max_score": 20}),
        await teacher.delete(path),
        await teacher.post(f"{path}/regenerate", json={"part": "title"}),
        await teacher.post(f"{path}/publish", json={}),
    ]

    assert [r.status_code for r in responses] == [404] * 5
    assert [r.json() for r in responses] == [
        error("draft_not_found", "Draft not found")
    ] * 5


@pytest.mark.anyio
async def test_classroom_not_visible_is_404_on_both_classroom_routes() -> None:
    campus = Campus()
    outsider = await campus.client_for(OUTSIDER)
    base = f"/api/v1/classrooms/{campus.classroom.id}/drafts"

    responses = [
        await outsider.post(base, json={"prompt": "x"}),
        await outsider.get(base),
    ]

    assert [r.status_code for r in responses] == [404] * 2
    assert [r.json() for r in responses] == [
        error("classroom_not_found", "Classroom not found")
    ] * 2


@pytest.mark.anyio
async def test_a_document_outside_the_library_is_422() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)

    response = await teacher.post(
        f"/api/v1/classrooms/{campus.classroom.id}/drafts",
        json={"prompt": "x", "document_ids": [99]},
    )

    assert response.status_code == 422
    assert response.json() == error(
        "document_not_allowed", "Choose documents from your library"
    )


@pytest.mark.anyio
async def test_a_spent_quota_is_429() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    base = f"/api/v1/classrooms/{campus.classroom.id}/drafts"

    statuses = [
        (await teacher.post(base, json={"prompt": "x"})).status_code for _ in range(50)
    ]
    refused = await teacher.post(base, json={"prompt": "x"})

    assert 201 in statuses
    assert refused.status_code == 429
    assert refused.json() == error("quota_exceeded", "No generations left today")


@pytest.mark.anyio
async def test_a_model_failure_is_502() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    campus.client_stub.fail = True

    response = await teacher.post(
        f"/api/v1/classrooms/{campus.classroom.id}/drafts", json={"prompt": "x"}
    )

    assert response.status_code == 502
    assert response.json() == error(
        "generation_failed", "The model did not respond; no quota used"
    )


@pytest.mark.anyio
async def test_a_rule_violation_is_422_with_the_rule_as_message() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)

    response = await teacher.post(
        f"/api/v1/classrooms/{campus.classroom.id}/drafts", json={"prompt": "  "}
    )

    assert response.status_code == 422
    assert response.json() == error("invalid_draft", "describe the problem you want")


@pytest.mark.anyio
async def test_publishing_an_unfinished_draft_names_the_missing_fields() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    created = await teacher.post(
        f"/api/v1/classrooms/{campus.classroom.id}/drafts", json={"prompt": "x"}
    )

    response = await teacher.post(
        f"/api/v1/drafts/{created.json()['id']}/publish", json={}
    )

    assert response.status_code == 422
    assert response.json() == error("missing_fields", "Fill in: deadline")
