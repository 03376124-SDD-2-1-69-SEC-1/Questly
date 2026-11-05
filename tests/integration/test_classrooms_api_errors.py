"""Characterise the error bodies of the Classrooms JSON routes."""

import pytest

from tests.integration.test_classrooms_api import (
    NEW_CLASSROOM,
    OTHER_TEACHER,
    STUDENT,
    TEACHER,
    Campus,
)


def error(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"detail": {"code": code, "message": message}}


@pytest.mark.anyio
async def test_classroom_hidden_from_an_outsider_is_404_on_every_classroom_route() -> (
    None
):
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    created = (await teacher.post("/api/v1/classrooms", json=NEW_CLASSROOM)).json()
    outsider = await campus.client_for(OTHER_TEACHER)
    path = f"/api/v1/classrooms/{created['id']}"
    expected = error("classroom_not_found", "Classroom not found")

    responses = [
        await outsider.get(path),
        await outsider.patch(path, json={"section": "9"}),
        await outsider.post(f"{path}/archive"),
        await outsider.post(f"{path}/unarchive"),
        await outsider.post(f"{path}/join-code"),
        await outsider.delete(f"{path}/join-code"),
        await outsider.get(f"{path}/members"),
        await outsider.delete(f"{path}/members/1"),
    ]

    assert [r.status_code for r in responses] == [404] * 8
    assert [r.json() for r in responses] == [expected] * 8


@pytest.mark.anyio
async def test_unknown_classroom_is_404_on_every_classroom_route() -> None:
    teacher = await Campus().client_for(TEACHER)
    path = "/api/v1/classrooms/99999"
    expected = error("classroom_not_found", "Classroom not found")

    responses = [
        await teacher.get(path),
        await teacher.patch(path, json={"section": "9"}),
        await teacher.post(f"{path}/archive"),
        await teacher.post(f"{path}/unarchive"),
        await teacher.post(f"{path}/join-code"),
        await teacher.delete(f"{path}/join-code"),
        await teacher.get(f"{path}/members"),
        await teacher.delete(f"{path}/members/1"),
    ]

    assert [r.status_code for r in responses] == [404] * 8
    assert [r.json() for r in responses] == [expected] * 8


@pytest.mark.anyio
async def test_join_with_an_unknown_code_is_422_with_its_message() -> None:
    student = await Campus().client_for(STUDENT)

    response = await student.post(
        "/api/v1/classrooms/join", json={"join_code": "ZZZZZZ"}
    )

    assert response.status_code == 422
    assert response.json() == error("invalid_join_code", "No classroom has this code")


@pytest.mark.anyio
async def test_removing_a_non_member_is_404_member_not_found() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    created = (await teacher.post("/api/v1/classrooms", json=NEW_CLASSROOM)).json()

    response = await teacher.delete(f"/api/v1/classrooms/{created['id']}/members/12345")

    assert response.status_code == 404
    assert response.json() == error("member_not_found", "Not a member")
