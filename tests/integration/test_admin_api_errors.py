"""Characterise the error bodies of the Admin JSON routes."""

import pytest

from questly.core.auth.models import InstructorRequest
from tests.fakes.auth import DEFAULT_NOW
from tests.integration.test_admin_api import ADMIN, AdminApi

REQUESTS = "/api/v1/admin/instructor-requests"
USERS = "/api/v1/admin/users"


def error(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"detail": {"code": code, "message": message}}


@pytest.mark.anyio
async def test_unknown_instructor_request_is_404_on_approve_and_reject() -> None:
    api = AdminApi()
    client = await api.client_for(ADMIN)

    responses = [
        await client.post(f"{REQUESTS}/99999/approve"),
        await client.post(f"{REQUESTS}/99999/reject"),
    ]

    assert [r.status_code for r in responses] == [404] * 2
    assert [r.json() for r in responses] == [
        error("instructor_request_not_found", "Instructor request not found")
    ] * 2


@pytest.mark.anyio
async def test_a_reviewed_instructor_request_is_409_on_approve_and_reject() -> None:
    api = AdminApi()
    request = api.auth.create_instructor_request(
        InstructorRequest(api.student.id, "Engineering", DEFAULT_NOW)
    )
    client = await api.client_for(ADMIN)
    await client.post(f"{REQUESTS}/{request.id}/approve")

    responses = [
        await client.post(f"{REQUESTS}/{request.id}/approve"),
        await client.post(f"{REQUESTS}/{request.id}/reject"),
    ]

    assert [r.status_code for r in responses] == [409] * 2
    assert [r.json() for r in responses] == [
        error(
            "instructor_request_already_reviewed",
            "Instructor request has already been reviewed",
        )
    ] * 2


@pytest.mark.anyio
async def test_unknown_user_is_404_on_role_change_and_deactivate() -> None:
    api = AdminApi()
    client = await api.client_for(ADMIN)

    responses = [
        await client.patch(f"{USERS}/99999", json={"role": "student"}),
        await client.post(f"{USERS}/99999/deactivate"),
    ]

    assert [r.status_code for r in responses] == [404] * 2
    assert [r.json() for r in responses] == [error("user_not_found", "User not found")] * 2


@pytest.mark.anyio
async def test_an_admin_cannot_change_or_deactivate_themselves() -> None:
    api = AdminApi()
    client = await api.client_for(ADMIN)

    demote = await client.patch(f"{USERS}/{api.admin.id}", json={"role": "student"})
    deactivate = await client.post(f"{USERS}/{api.admin.id}/deactivate")

    assert demote.status_code == 400
    assert demote.json() == error("self_modification", "Cannot demote your own admin role")
    assert deactivate.status_code == 400
    assert deactivate.json() == error(
        "self_modification", "Cannot deactivate your own admin account"
    )


@pytest.mark.anyio
async def test_a_model_outside_the_allowlist_is_422() -> None:
    api = AdminApi()
    client = await api.client_for(ADMIN)

    response = await client.put(
        "/api/v1/admin/ai-settings", json={"model": "not-configured"}
    )

    assert response.status_code == 422
    assert response.json() == error(
        "model_not_allowed", "Model is not in the configured allowlist"
    )
