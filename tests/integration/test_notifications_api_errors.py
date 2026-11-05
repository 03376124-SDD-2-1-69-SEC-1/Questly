"""Characterise the error bodies of the Notifications JSON routes."""

import pytest

from tests.integration.test_notifications_api import CLASSMATE, STUDENT, Campus

NOT_FOUND = {
    "detail": {"code": "notification_not_found", "message": "Notification not found"}
}


@pytest.mark.anyio
async def test_mark_read_of_a_missing_notification_is_404_with_its_message() -> None:
    student = await Campus().client_for(STUDENT)

    response = await student.post("/api/v1/notifications/999999/read")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.anyio
async def test_mark_read_of_someone_elses_notification_looks_like_a_missing_one() -> (
    None
):
    campus = Campus()
    student = await campus.client_for(STUDENT)
    classmate = await campus.client_for(CLASSMATE)
    (theirs,) = (await classmate.get("/api/v1/notifications")).json()["items"]

    response = await student.post(f"/api/v1/notifications/{theirs['id']}/read")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
