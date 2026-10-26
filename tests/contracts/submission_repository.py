"""Contract every SubmissionRepository implementation must satisfy.

Bound to the in-memory adapter in `tests/unit/core/submissions/` and to
`SQLSubmissionRepository` in `tests/db/`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from questly.core.assignments.models import TestCaseKind
from questly.core.submissions.models import (
    Claim,
    Recovery,
    Submission,
    SubmissionPendingError,
    SubmissionStatus,
    TestResult,
    Verdict,
)
from questly.core.submissions.ports import SubmissionRepository
from tests.contracts.support import IdFactory, NeedsPostings, PostingFactory, PostingIds

NOW = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


def result(position: int, verdict: Verdict = Verdict.PASSED) -> TestResult:
    return TestResult(
        position=position,
        ordinal=position,
        kind=TestCaseKind.HIDDEN if position > 1 else TestCaseKind.SAMPLE,
        note="" if position == 1 else f"case {position}",
        verdict=verdict,
        time_seconds=0.25,
        expected_output="3",
        actual_output="3" if verdict is Verdict.PASSED else "4",
        error="",
    )


def submission(
    where: PostingIds, student_id: int, attempt: int = 1, at: datetime = NOW
) -> Submission:
    return Submission(
        posting_id=where.posting_id,
        classroom_id=where.classroom_id,
        assignment_id=where.assignment_id,
        student_id=student_id,
        version_number=1,
        code="print(sum(map(int, input().split())))",
        language="python3",
        submitted_at=at,
        is_late=False,
        score=6,
        max_score=10,
        attempt=attempt,
        results=(result(1), result(2, Verdict.WRONG_ANSWER), result(3)),
    )


LEASE = timedelta(seconds=90)


def received(where: PostingIds, student_id: int, attempt: int = 1) -> Submission:
    return replace(
        submission(where, student_id, attempt),
        status=SubmissionStatus.RECEIVED,
        score=None,
        results=(),
    )


def saved_id(stored: Submission) -> int:
    assert stored.id is not None
    return stored.id


class SubmissionRepositoryContract(NeedsPostings):
    """Checks that hold for any store behind SubmissionRepository."""

    def test_add_assigns_id_and_round_trips_every_result(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        added = repository.add(submission(new_posting(), new_user()))

        assert isinstance(added.id, int)
        assert repository.get(added.id) == added
        assert [r.verdict for r in added.results] == [
            Verdict.PASSED,
            Verdict.WRONG_ANSWER,
            Verdict.PASSED,
        ]

    def test_get_on_unknown_id_returns_none(
        self, repository: SubmissionRepository
    ) -> None:
        assert repository.get(999_999) is None

    def test_list_for_student_is_oldest_first_and_filtered(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where, other_place = new_posting(), new_posting()
        alice, bob = new_user(), new_user()
        later = repository.add(
            submission(where, alice, attempt=2, at=NOW + timedelta(hours=1))
        )
        earlier = repository.add(submission(where, alice, attempt=1, at=NOW))
        repository.add(submission(where, bob))
        repository.add(submission(other_place, alice))

        assert repository.list_for_student(where.posting_id, alice) == (
            earlier,
            later,
        )

    def test_list_for_posting_returns_every_student_oldest_first(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where = new_posting()
        alice, bob = new_user(), new_user()
        second = repository.add(submission(where, bob, at=NOW + timedelta(minutes=5)))
        first = repository.add(submission(where, alice, at=NOW))

        assert repository.list_for_posting(where.posting_id) == (first, second)
        assert repository.list_for_posting(999_999) == ()

    # ---- Async Submit: receive, the lease and the status compare-and-sets ----

    def test_receive_stores_a_received_submission_without_a_score(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        stored = repository.receive(received(new_posting(), new_user()), NOW + LEASE)

        assert repository.get(saved_id(stored)) == stored
        assert stored.status is SubmissionStatus.RECEIVED
        assert stored.score is None
        assert stored.results == ()
        assert stored.recovery is Recovery.NONE

    def test_a_second_pending_submission_is_refused_and_not_stored(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where, student = new_posting(), new_user()
        first = repository.receive(received(where, student, 1), NOW + LEASE)

        with pytest.raises(SubmissionPendingError):
            repository.receive(received(where, student, 2), NOW + LEASE)

        assert repository.list_for_student(where.posting_id, student) == (first,)

    def test_a_taken_attempt_number_is_refused_even_when_nothing_is_pending(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where, student = new_posting(), new_user()
        first = repository.receive(received(where, student, 1), NOW + LEASE)
        assert repository.failed(saved_id(first))

        with pytest.raises(SubmissionPendingError):
            repository.receive(received(where, student, 1), NOW + LEASE)

    def test_a_new_attempt_is_received_once_the_last_one_ended(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where, student, other = new_posting(), new_user(), new_user()
        repository.add(submission(where, student, attempt=1))
        failed = repository.receive(received(where, student, 2), NOW + LEASE)
        assert repository.failed(saved_id(failed))
        repository.receive(received(where, other, 1), NOW + LEASE)

        third = repository.receive(received(where, student, 3), NOW + LEASE)

        assert [
            row.status for row in repository.list_for_student(where.posting_id, student)
        ] == [SubmissionStatus.DONE, SubmissionStatus.FAILED, third.status]

    def test_started_moves_received_to_judging_once(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        stored = repository.receive(received(new_posting(), new_user()), NOW + LEASE)
        sid = saved_id(stored)

        assert repository.started(
            sid, tokens=("a", "b"), lease_until=NOW + LEASE, recovery=Recovery.REJUDGED
        )
        assert not repository.started(
            sid, tokens=("c",), lease_until=NOW + LEASE, recovery=Recovery.NONE
        )
        assert repository.get(sid) == replace(
            stored, status=SubmissionStatus.JUDGING, recovery=Recovery.REJUDGED
        )

    def test_renew_holds_only_a_judging_row(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        sid = saved_id(
            repository.receive(received(new_posting(), new_user()), NOW + LEASE)
        )
        assert not repository.renew(sid, lease_until=NOW + 2 * LEASE)
        repository.started(
            sid, tokens=("a",), lease_until=NOW + LEASE, recovery=Recovery.NONE
        )

        assert repository.renew(sid, lease_until=NOW + 2 * LEASE)
        assert repository.claim_due(NOW + LEASE, NOW + 3 * LEASE, 10) == ()

        repository.finished(
            sid, tokens=("a",), score=10, results=(result(1),), recovery=Recovery.NONE
        )
        assert not repository.renew(sid, lease_until=NOW + 4 * LEASE)

    def test_finished_with_the_saved_tokens_writes_the_result_once(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        stored = repository.receive(received(new_posting(), new_user()), NOW + LEASE)
        sid = saved_id(stored)
        repository.started(
            sid, tokens=("a", "b"), lease_until=NOW + LEASE, recovery=Recovery.NONE
        )
        results = (result(1), result(2, Verdict.WRONG_ANSWER))

        assert not repository.finished(
            sid, tokens=("b", "a"), score=5, results=results, recovery=Recovery.NONE
        )
        assert repository.get(sid) == replace(stored, status=SubmissionStatus.JUDGING)

        assert repository.finished(
            sid,
            tokens=("a", "b"),
            score=5,
            results=results,
            recovery=Recovery.RECONNECTED,
        )
        assert not repository.finished(
            sid, tokens=("a", "b"), score=0, results=(), recovery=Recovery.NONE
        )
        assert repository.get(sid) == replace(
            stored,
            status=SubmissionStatus.DONE,
            score=5,
            results=results,
            recovery=Recovery.RECONNECTED,
        )
        assert repository.claim_due(NOW + 10 * LEASE, NOW + 11 * LEASE, 10) == ()

    def test_requeued_sends_a_judging_row_back_without_its_tokens_once(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        stored = repository.receive(received(new_posting(), new_user()), NOW + LEASE)
        sid = saved_id(stored)
        assert not repository.requeued(sid)
        repository.started(
            sid, tokens=("a",), lease_until=NOW + LEASE, recovery=Recovery.NONE
        )

        assert repository.requeued(sid)
        assert not repository.requeued(sid)
        assert not repository.finished(
            sid, tokens=("a",), score=10, results=(result(1),), recovery=Recovery.NONE
        )
        (claim,) = repository.claim_due(NOW + LEASE, NOW + 2 * LEASE, 10)
        assert claim == Claim(submission=stored, tokens=(), interrupted=True)

    def test_released_makes_the_row_due_at_once_without_interruption(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        stored = repository.receive(received(new_posting(), new_user()), NOW + LEASE)
        sid = saved_id(stored)
        repository.started(
            sid, tokens=("a", "b"), lease_until=NOW + LEASE, recovery=Recovery.NONE
        )

        assert repository.released(sid)
        assert not repository.released(sid)
        (claim,) = repository.claim_due(NOW, NOW + LEASE, 10)
        assert claim == Claim(
            submission=replace(stored, status=SubmissionStatus.JUDGING),
            tokens=("a", "b"),
            interrupted=False,
        )

    def test_failed_ends_a_pending_row_once_and_never_a_done_one(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where = new_posting()
        waiting = repository.receive(received(where, new_user()), NOW + LEASE)
        judging = repository.receive(received(where, new_user()), NOW + LEASE)
        repository.started(
            saved_id(judging),
            tokens=("a",),
            lease_until=NOW + LEASE,
            recovery=Recovery.NONE,
        )
        done = repository.add(submission(where, new_user()))

        assert repository.failed(saved_id(waiting))
        assert repository.failed(saved_id(judging))
        assert not repository.failed(saved_id(waiting))
        assert not repository.failed(saved_id(done))
        assert repository.get(saved_id(done)) == done
        assert repository.get(saved_id(waiting)) == replace(
            waiting, status=SubmissionStatus.FAILED
        )
        assert repository.claim_due(NOW + 10 * LEASE, NOW + 11 * LEASE, 10) == ()

    def test_claim_due_takes_expired_and_released_rows_oldest_first(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where = new_posting()
        live = repository.receive(received(where, new_user()), NOW + 3 * LEASE)
        expired = repository.receive(
            replace(
                received(where, new_user()), submitted_at=NOW + timedelta(minutes=1)
            ),
            NOW + LEASE,
        )
        released = repository.receive(
            replace(received(where, new_user()), submitted_at=NOW),
            NOW + 3 * LEASE,
        )
        repository.released(saved_id(released))
        repository.add(submission(where, new_user()))
        now = NOW + LEASE

        claims = repository.claim_due(now, now + LEASE, 10)

        assert claims == (
            Claim(submission=released, tokens=(), interrupted=False),
            Claim(submission=expired, tokens=(), interrupted=True),
        )
        assert repository.claim_due(now, now + LEASE, 10) == ()
        assert repository.claim_due(now + LEASE, now + 2 * LEASE, 10) == (
            Claim(submission=released, tokens=(), interrupted=True),
            Claim(submission=expired, tokens=(), interrupted=True),
        )
        assert saved_id(live) not in {saved_id(c.submission) for c in claims}

    def test_claim_due_stops_at_the_limit(
        self,
        repository: SubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where = new_posting()
        first = repository.receive(received(where, new_user()), NOW)
        repository.receive(
            replace(received(where, new_user()), submitted_at=NOW + LEASE), NOW
        )

        assert repository.claim_due(NOW, NOW + LEASE, 1) == (
            Claim(submission=first, tokens=(), interrupted=True),
        )

    def test_writes_to_an_unknown_submission_return_false(
        self, repository: SubmissionRepository
    ) -> None:
        unknown = 999_999

        assert not repository.started(
            unknown, tokens=(), lease_until=NOW, recovery=Recovery.NONE
        )
        assert not repository.renew(unknown, lease_until=NOW)
        assert not repository.finished(
            unknown, tokens=(), score=0, results=(), recovery=Recovery.NONE
        )
        assert not repository.requeued(unknown)
        assert not repository.released(unknown)
        assert not repository.failed(unknown)
