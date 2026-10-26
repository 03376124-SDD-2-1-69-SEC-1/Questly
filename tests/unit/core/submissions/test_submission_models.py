"""The Submission lifecycle: status moves, the Recovery marker, score and status."""

from dataclasses import replace
from datetime import UTC, datetime
from itertools import product

import pytest

from questly.core.assignments.models import TestCaseKind
from questly.core.submissions.models import (
    BatchResult,
    BatchState,
    Claim,
    Execution,
    ExecutionStatus,
    Recovery,
    Submission,
    SubmissionStatus,
    TestResult,
    Verdict,
)

RECEIVED = SubmissionStatus.RECEIVED
JUDGING = SubmissionStatus.JUDGING
DONE = SubmissionStatus.DONE
FAILED = SubmissionStatus.FAILED

LEGAL_MOVES = {
    (RECEIVED, JUDGING),
    (JUDGING, DONE),
    (JUDGING, RECEIVED),
    (RECEIVED, FAILED),
    (JUDGING, FAILED),
}

PASSED = TestResult(
    position=1,
    ordinal=1,
    kind=TestCaseKind.SAMPLE,
    note="",
    verdict=Verdict.PASSED,
    time_seconds=0.1,
    expected_output="3",
    actual_output="3",
    error="",
)


def graded(**changes: object) -> Submission:
    done = Submission(
        posting_id=1,
        classroom_id=1,
        assignment_id=1,
        student_id=1,
        version_number=1,
        code="print(3)",
        language="python3",
        submitted_at=datetime(2026, 10, 13, 9, 0, tzinfo=UTC),
        is_late=False,
        score=10,
        max_score=10,
        attempt=1,
        results=(PASSED,),
    )
    return replace(done, **changes)


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL_MOVES))
def test_a_legal_move_is_allowed(
    current: SubmissionStatus, target: SubmissionStatus
) -> None:
    assert current.can_move(target)


@pytest.mark.parametrize(
    ("current", "target"),
    sorted(set(product(SubmissionStatus, repeat=2)) - LEGAL_MOVES),
)
def test_every_other_move_is_refused(
    current: SubmissionStatus, target: SubmissionStatus
) -> None:
    assert not current.can_move(target)


def test_done_and_failed_are_terminal_and_the_rest_pending() -> None:
    assert [status for status in SubmissionStatus if status.pending] == [
        RECEIVED,
        JUDGING,
    ]


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (Recovery.NONE, Recovery.NONE, Recovery.NONE),
        (Recovery.NONE, Recovery.RECONNECTED, Recovery.RECONNECTED),
        (Recovery.NONE, Recovery.REJUDGED, Recovery.REJUDGED),
        (Recovery.RECONNECTED, Recovery.NONE, Recovery.RECONNECTED),
        (Recovery.RECONNECTED, Recovery.RECONNECTED, Recovery.RECONNECTED),
        (Recovery.RECONNECTED, Recovery.REJUDGED, Recovery.REJUDGED),
        (Recovery.REJUDGED, Recovery.NONE, Recovery.REJUDGED),
        (Recovery.REJUDGED, Recovery.RECONNECTED, Recovery.REJUDGED),
        (Recovery.REJUDGED, Recovery.REJUDGED, Recovery.REJUDGED),
    ],
)
def test_recovery_only_moves_up(
    current: Recovery, event: Recovery, expected: Recovery
) -> None:
    assert Recovery.after(current, event) is expected


def test_a_submission_built_the_old_way_is_done_with_no_marker() -> None:
    submission = graded()

    assert submission.status is DONE
    assert submission.recovery is Recovery.NONE


def test_a_done_submission_needs_a_score() -> None:
    with pytest.raises(ValueError, match="score"):
        graded(score=None)


@pytest.mark.parametrize("status", [RECEIVED, JUDGING, FAILED])
def test_only_a_done_submission_has_a_score(status: SubmissionStatus) -> None:
    with pytest.raises(ValueError, match="score"):
        graded(status=status, results=())


@pytest.mark.parametrize("status", [RECEIVED, JUDGING, FAILED])
def test_only_a_done_submission_has_results(status: SubmissionStatus) -> None:
    with pytest.raises(ValueError, match="results"):
        graded(status=status, score=None)


@pytest.mark.parametrize("status", [RECEIVED, JUDGING, FAILED])
def test_an_unscored_submission_is_valid_before_done(
    status: SubmissionStatus,
) -> None:
    assert graded(status=status, score=None, results=()).score is None


def test_a_claim_is_for_a_pending_submission_only() -> None:
    with pytest.raises(ValueError, match="pending"):
        Claim(submission=graded(), tokens=(), interrupted=False)


def test_a_received_claim_has_no_tokens() -> None:
    received = graded(status=RECEIVED, score=None, results=())

    with pytest.raises(ValueError, match="tokens"):
        Claim(submission=received, tokens=("t1",), interrupted=True)


def test_a_judging_claim_carries_its_tokens() -> None:
    judging = graded(status=JUDGING, score=None, results=())

    claim = Claim(submission=judging, tokens=("t1", "t2"), interrupted=True)

    assert claim.tokens == ("t1", "t2")


@pytest.mark.parametrize("state", [BatchState.RUNNING, BatchState.GONE])
def test_only_a_finished_batch_has_executions(state: BatchState) -> None:
    execution = Execution(ExecutionStatus.OK, "3", "", 0.1)

    with pytest.raises(ValueError, match="executions"):
        BatchResult(state=state, executions=(execution,))


def test_an_unfinished_batch_has_none() -> None:
    assert BatchResult(state=BatchState.RUNNING).executions == ()
