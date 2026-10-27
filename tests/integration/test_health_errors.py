"""Failing health checks say what is down, never why (OPS-44)."""

import logging
from collections.abc import Iterator

import pytest

from questly import main
from questly.database.session import get_session
from tests.fakes.app import build_app
from tests.integration.forms import asgi_client

SECRET_CAUSE = "connection to db.internal:5432 failed for user neondb_owner"


class BrokenSession:
    def exec(self, statement: object) -> object:
        raise RuntimeError(SECRET_CAUSE)


class BrokenBucketClient:
    def head_bucket(self, *, Bucket: str) -> object:  # noqa: N803 — boto3 keyword
        raise RuntimeError(SECRET_CAUSE)


@pytest.mark.anyio
async def test_a_failing_database_check_hides_the_cause(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_session() -> Iterator[BrokenSession]:
        yield BrokenSession()

    app = build_app()
    app.dependency_overrides[get_session] = broken_session
    with caplog.at_level(logging.ERROR, logger="questly.main"):
        async with asgi_client(app) as client:
            response = await client.get("/health/db")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    assert SECRET_CAUSE not in response.text
    assert SECRET_CAUSE in caplog.text


@pytest.mark.anyio
async def test_a_failing_storage_check_hides_the_cause(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(main, "build_r2_client", lambda settings: BrokenBucketClient())
    with caplog.at_level(logging.ERROR, logger="questly.main"):
        async with asgi_client(build_app()) as client:
            response = await client.get("/health/r2")

    assert response.status_code == 503
    assert response.json() == {"detail": "storage unavailable"}
    assert SECRET_CAUSE not in response.text
    assert SECRET_CAUSE in caplog.text
