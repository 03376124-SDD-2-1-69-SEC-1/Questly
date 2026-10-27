"""What production_app() passes to create_app (OPS-44)."""

import dataclasses
from collections.abc import Iterator

import pytest
from fastapi import FastAPI

from questly import main
from questly.config import PRODUCTION, Settings
from tests.fakes.app import build_app, fake_settings


class RecordingCreateApp:
    """Stands in for create_app: keeps the keyword arguments, returns a fake app."""

    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def __call__(self, **kwargs: object) -> FastAPI:
        self.kwargs = kwargs
        return build_app()


@pytest.fixture()
def recorded(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingCreateApp]:
    recorder = RecordingCreateApp()
    monkeypatch.setattr(main, "create_app", recorder)
    monkeypatch.setattr(main, "start_sweeper", lambda recover: None)
    main.production_app.cache_clear()
    yield recorder
    main.production_app.cache_clear()


def _use_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(main, "get_settings", lambda: settings)


def test_production_hides_the_api_docs(
    recorded: RecordingCreateApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_settings(monkeypatch, dataclasses.replace(fake_settings(), app_env=PRODUCTION))

    main.production_app()

    assert recorded.kwargs["expose_docs"] is False


def test_development_keeps_the_api_docs(
    recorded: RecordingCreateApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_settings(monkeypatch, fake_settings())

    main.production_app()

    assert recorded.kwargs["expose_docs"] is True
