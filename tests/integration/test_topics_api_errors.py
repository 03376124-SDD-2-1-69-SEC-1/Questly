"""Characterise the error bodies of the Topics JSON routes."""

import pytest

from tests.integration.test_topics_api import TEACHER, _app, _client


def error(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"detail": {"code": code, "message": message}}


NOT_FOUND = error("topic_not_found", "Topic not found")
CONFLICT = error("topic_name_conflict", "Topic name already in use")


@pytest.mark.anyio
async def test_unknown_topic_is_404_on_every_topic_route() -> None:
    async with _client(_app(), TEACHER) as teacher:
        path = "/api/v1/topics/99999"
        responses = [
            await teacher.get(path),
            await teacher.put(path, json={"name": "Trees", "description": None}),
            await teacher.patch(path, json={"name": "Trees"}),
            await teacher.delete(path),
        ]

    assert [r.status_code for r in responses] == [404] * 4
    assert [r.json() for r in responses] == [NOT_FOUND] * 4


@pytest.mark.anyio
async def test_duplicate_topic_name_is_409_on_create_replace_and_patch() -> None:
    async with _client(_app(), TEACHER) as teacher:
        await teacher.post("/api/v1/topics", json={"name": "Graphs"})
        trees = (await teacher.post("/api/v1/topics", json={"name": "Trees"})).json()
        path = f"/api/v1/topics/{trees['id']}"

        responses = [
            await teacher.post("/api/v1/topics", json={"name": " graphs "}),
            await teacher.put(path, json={"name": "GRAPHS", "description": None}),
            await teacher.patch(path, json={"name": " graphs "}),
        ]

    assert [r.status_code for r in responses] == [409] * 3
    assert [r.json() for r in responses] == [CONFLICT] * 3
