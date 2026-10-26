"""Judge0CodeRunner against a real Judge0 CE named by JUDGE0_TEST_URL."""

import uuid

import httpx
import pytest

from questly.core.submissions.models import BatchState, ExecutionStatus
from questly.integrations.judge0 import Judge0CodeRunner

pytestmark = pytest.mark.judge0


@pytest.fixture()
def runner(judge0_test_url: str, judge0_test_auth_token: str) -> Judge0CodeRunner:
    return Judge0CodeRunner(
        base_url=judge0_test_url,
        auth_token=judge0_test_auth_token,
        client=httpx.Client(timeout=10.0),
        poll_interval_seconds=0.5,
        timeout_seconds=60.0,
    )


def test_a_correct_program_runs_once_per_input_in_order(
    runner: Judge0CodeRunner,
) -> None:
    executions = runner.run_many(
        code="print(int(input()) * 2)",
        language="python3",
        stdins=["21", "5"],
        time_limit_seconds=2.0,
    )

    assert [e.status for e in executions] == [ExecutionStatus.OK] * 2
    assert [e.stdout for e in executions] == ["42\n", "10\n"]


def test_a_program_that_never_ends_hits_the_time_limit(
    runner: Judge0CodeRunner,
) -> None:
    execution = runner.run(
        code="while True:\n    pass\n",
        language="python3",
        stdin="",
        time_limit_seconds=1.0,
    )

    assert execution.status is ExecutionStatus.TIME_LIMIT


def test_start_then_collect_finishes_each_input_in_order(
    runner: Judge0CodeRunner,
) -> None:
    tokens = runner.start(
        code="print(int(input()) + 1)",
        language="python3",
        stdins=["1", "2"],
        time_limit_seconds=2.0,
    )

    batch = runner.collect(tokens)
    while batch.state is BatchState.RUNNING:
        batch = runner.collect(tokens)

    assert batch.state is BatchState.FINISHED
    assert [e.stdout for e in batch.executions] == ["2\n", "3\n"]


def test_a_token_judge0_never_issued_is_gone(runner: Judge0CodeRunner) -> None:
    assert runner.collect([str(uuid.uuid4())]).state is BatchState.GONE
