"""The judging sweeper: a daemon thread only the production entry starts."""

import threading
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI

from questly import main
from questly.main import run_sweeper
from tests.fakes.app import build_app


def test_create_app_starts_no_thread() -> None:
    before = set(threading.enumerate())

    build_app()

    assert set(threading.enumerate()) == before


def test_one_failing_sweep_does_not_stop_the_next() -> None:
    stop = threading.Event()
    calls: list[int] = []

    def recover() -> None:
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("one bad row")
        stop.set()

    run_sweeper(recover, stop, period_seconds=0)

    assert calls == [0, 1]


@pytest.fixture()
def production_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[Callable[[], None]]]:
    """`questly.main:app` over fakes, recording the sweepers it would start."""
    started: list[Callable[[], None]] = []
    monkeypatch.setattr(main, "create_app", build_app)
    monkeypatch.setattr(main, "start_sweeper", started.append)
    main.production_app.cache_clear()
    yield started
    main.production_app.cache_clear()


def test_the_production_app_is_built_once_and_starts_one_sweeper(
    production_entry: list[Callable[[], None]],
) -> None:
    first = main.app
    second = main.app

    assert isinstance(first, FastAPI) and first is second
    assert production_entry == [first.state.submission_service.recover]
