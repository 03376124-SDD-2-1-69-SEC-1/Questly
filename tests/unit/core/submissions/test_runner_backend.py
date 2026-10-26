"""The fakes' BatchRunner half: start/collect over a RunnerBackend.

A RunnerBackend stands in for Judge0's own store. Two runners sharing one
model "Judge0 survived our restart"; a fresh one models "Judge0 lost it".
"""

import pytest

from questly.core.submissions.models import (
    BatchResult,
    BatchState,
    Execution,
    ExecutionStatus,
    RunnerUnavailableError,
)
from questly.core.submissions.ports import BatchRunner
from tests.fakes.submissions import LocalUnsafeRunner, RunnerBackend, ScriptedCodeRunner

OUTPUTS = {"1": "one", "2": "two"}


def start(runner: BatchRunner, code: str = "print(input())") -> tuple[str, ...]:
    return runner.start(
        code=code, language="python3", stdins=["1", "2"], time_limit_seconds=1.0
    )


def finished(*stdouts: str) -> BatchResult:
    return BatchResult(
        state=BatchState.FINISHED,
        executions=tuple(
            Execution(ExecutionStatus.OK, stdout, "", 0.02) for stdout in stdouts
        ),
    )


def test_collect_returns_each_execution_in_token_order() -> None:
    runner = ScriptedCodeRunner(OUTPUTS)
    first, second = start(runner)

    assert runner.collect([second, first]) == finished("two", "one")


def test_a_held_batch_is_running_until_resumed() -> None:
    backend = RunnerBackend()
    runner = ScriptedCodeRunner(OUTPUTS, backend=backend)
    backend.hold()
    tokens = start(runner)

    assert runner.collect(tokens) == BatchResult(state=BatchState.RUNNING)

    backend.resume()
    assert runner.collect(tokens) == finished("one", "two")


def test_a_runner_built_over_the_same_backend_collects_the_old_tokens() -> None:
    backend = RunnerBackend()
    tokens = start(ScriptedCodeRunner(OUTPUTS, backend=backend))

    after_restart = ScriptedCodeRunner(OUTPUTS, backend=backend)

    assert after_restart.collect(tokens) == finished("one", "two")


def test_a_new_backend_or_a_forgetful_one_reports_the_batch_gone() -> None:
    backend = RunnerBackend()
    tokens = start(ScriptedCodeRunner(OUTPUTS, backend=backend))
    gone = BatchResult(state=BatchState.GONE)

    assert ScriptedCodeRunner(OUTPUTS).collect(tokens) == gone

    backend.forget()
    assert ScriptedCodeRunner(OUTPUTS, backend=backend).collect(tokens) == gone


def test_a_down_backend_takes_no_batch_and_answers_no_collect() -> None:
    backend = RunnerBackend()
    runner = ScriptedCodeRunner(OUTPUTS, backend=backend)
    tokens = start(runner)
    backend.down()

    with pytest.raises(RunnerUnavailableError):
        start(runner)
    with pytest.raises(RunnerUnavailableError):
        runner.collect(tokens)
    assert runner.batches == [("1", "2")]

    backend.resume()
    assert runner.collect(tokens) == finished("one", "two")


def test_the_judge_down_marker_refuses_the_batch() -> None:
    with pytest.raises(RunnerUnavailableError):
        start(ScriptedCodeRunner(OUTPUTS), code="# judge down\nprint(1)")


def test_the_local_runner_judges_a_batch_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("ALLOW_UNSAFE_RUNNER", "1")
    runner = LocalUnsafeRunner()

    result = runner.collect(start(runner))

    assert result.state is BatchState.FINISHED
    assert [execution.stdout for execution in result.executions] == ["1\n", "2\n"]
