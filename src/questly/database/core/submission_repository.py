"""SQL adapter for the SubmissionRepository port (OPS-15, OPS-30)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, select

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
from questly.database.core.tables import Posting as PostingRow
from questly.database.core.tables import SubmissionRow
from questly.database.core.tables import SubmissionTestResult as ResultRow
from questly.database.session import SessionFactory

# Written as the predicate of uq_submissions_one_unfinished, with literals, so
# Postgres can prove the index covers the query under a generic plan too.
PENDING = "status IN ('received', 'judging')"
# Either one means the Student may not start this Submission (ADR-0011).
REFUSING_CONSTRAINTS = frozenset(
    {"uq_submissions_attempt", "uq_submissions_one_unfinished"}
)


def _result(row: ResultRow) -> TestResult:
    return TestResult(
        position=row.position,
        ordinal=row.ordinal,
        kind=TestCaseKind(row.kind),
        note=row.note,
        verdict=Verdict(row.verdict),
        time_seconds=row.time_seconds,
        expected_output=row.expected_output,
        actual_output=row.actual_output,
        error=row.error,
    )


def _result_row(submission_id: int, result: TestResult) -> ResultRow:
    return ResultRow(
        submission_id=submission_id,
        position=result.position,
        ordinal=result.ordinal,
        kind=result.kind.value,
        note=result.note,
        verdict=result.verdict.value,
        time_seconds=result.time_seconds,
        expected_output=result.expected_output,
        actual_output=result.actual_output,
        error=result.error,
    )


def _submission(
    row: SubmissionRow, posting: PostingRow, results: list[ResultRow]
) -> Submission:
    return Submission(
        id=row.id,
        posting_id=row.posting_id,
        classroom_id=posting.classroom_id,
        assignment_id=posting.assignment_id,
        student_id=row.student_id,
        version_number=row.version_number,
        code=row.code,
        language=row.language,
        submitted_at=row.submitted_at,
        is_late=row.is_late,
        score=row.score,
        max_score=row.max_score,
        attempt=row.attempt,
        results=tuple(_result(result) for result in results),
        status=SubmissionStatus(row.status),
        recovery=Recovery(row.recovery),
    )


class SQLSubmissionRepository:
    """Store Submissions, one result row per Test Case, and the judging lease.

    A Submission's Classroom and Assignment are read from its Posting, not
    stored twice (ADR-0008); its max score is stored, because the Posting's
    can change after grading. Every status write is one UPDATE whose WHERE
    names the status it moves from (ADR-0011).
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        """Initialize the adapter with a session factory."""
        self._session_factory = session_factory

    def add(self, submission: Submission) -> Submission:
        """Insert a judged Submission with its results, in one transaction."""
        with self._session_factory() as db:
            submission_id = self._insert(db, submission, claimed_until=None)
            db.add_all(_result_row(submission_id, r) for r in submission.results)
            db.commit()
            return self._load(db, SubmissionRow.id == submission_id)[0]

    def receive(self, submission: Submission, lease_until: datetime) -> Submission:
        """Insert a `received` Submission holding its lease until `lease_until`."""
        with self._session_factory() as db:
            try:
                submission_id = self._insert(db, submission, claimed_until=lease_until)
            except IntegrityError as error:
                if error.orig.diag.constraint_name not in REFUSING_CONSTRAINTS:
                    raise
                raise SubmissionPendingError from error
            db.commit()
            return self._load(db, SubmissionRow.id == submission_id)[0]

    def claim_due(
        self, now: datetime, lease_until: datetime, limit: int
    ) -> tuple[Claim, ...]:
        """Lease up to `limit` due rows in one statement, oldest submitted first.

        SKIP LOCKED makes a concurrent caller pass over the rows this one
        locked, so no row is claimed twice. The CTE returns `claimed_until` as
        it was before the update: set means the lease expired.
        """
        due = (
            select(SubmissionRow.id, SubmissionRow.claimed_until)
            .where(
                text(PENDING),
                (col(SubmissionRow.claimed_until).is_(None))
                | (col(SubmissionRow.claimed_until) <= now),
            )
            .order_by(SubmissionRow.submitted_at, SubmissionRow.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("due")
        )
        with self._session_factory() as db:
            taken = db.exec(
                update(SubmissionRow)
                .where(col(SubmissionRow.id) == due.c.id)
                .values(claimed_until=lease_until)
                .returning(
                    SubmissionRow.id, SubmissionRow.runner_tokens, due.c.claimed_until
                )
            ).all()
            if not taken:
                return ()
            held = {
                submission_id: (tuple(tokens), prior is not None)
                for submission_id, tokens, prior in taken
            }
            claims = tuple(
                Claim(
                    submission=submission,
                    tokens=held[submission.id][0],
                    interrupted=held[submission.id][1],
                )
                for submission in self._load(db, col(SubmissionRow.id).in_(held))
                if submission.id is not None
            )
            db.commit()
            return claims

    def started(
        self,
        submission_id: int,
        *,
        tokens: Sequence[str],
        lease_until: datetime,
        recovery: Recovery,
    ) -> bool:
        """`received` -> `judging`, saving the tokens, the lease and `recovery`."""
        with self._session_factory() as db:
            moved = self._move(
                db,
                submission_id,
                (SubmissionStatus.RECEIVED,),
                status=SubmissionStatus.JUDGING.value,
                runner_tokens=list(tokens),
                claimed_until=lease_until,
                recovery=recovery.value,
            )
            db.commit()
            return moved

    def renew(self, submission_id: int, *, lease_until: datetime) -> bool:
        """Move a `judging` row's lease to `lease_until`."""
        with self._session_factory() as db:
            moved = self._move(
                db,
                submission_id,
                (SubmissionStatus.JUDGING,),
                claimed_until=lease_until,
            )
            db.commit()
            return moved

    def finished(
        self,
        submission_id: int,
        *,
        tokens: Sequence[str],
        score: int,
        results: Sequence[TestResult],
        recovery: Recovery,
    ) -> bool:
        """`judging` -> `done` with score and results, if the tokens match."""
        with self._session_factory() as db:
            if not self._move(
                db,
                submission_id,
                (SubmissionStatus.JUDGING,),
                col(SubmissionRow.runner_tokens) == list(tokens),
                status=SubmissionStatus.DONE.value,
                score=score,
                recovery=recovery.value,
                claimed_until=None,
            ):
                return False
            db.add_all(_result_row(submission_id, r) for r in results)
            db.commit()
            return True

    def requeued(self, submission_id: int) -> bool:
        """`judging` -> `received`, dropping the tokens the runner lost."""
        with self._session_factory() as db:
            moved = self._move(
                db,
                submission_id,
                (SubmissionStatus.JUDGING,),
                status=SubmissionStatus.RECEIVED.value,
                runner_tokens=[],
            )
            db.commit()
            return moved

    def released(self, submission_id: int) -> bool:
        """Clear a pending row's lease so it is due at once."""
        with self._session_factory() as db:
            moved = self._move(
                db,
                submission_id,
                (SubmissionStatus.RECEIVED, SubmissionStatus.JUDGING),
                col(SubmissionRow.claimed_until).is_not(None),
                claimed_until=None,
            )
            db.commit()
            return moved

    def failed(self, submission_id: int) -> bool:
        """`received` or `judging` -> `failed`, clearing the lease."""
        with self._session_factory() as db:
            moved = self._move(
                db,
                submission_id,
                (SubmissionStatus.RECEIVED, SubmissionStatus.JUDGING),
                status=SubmissionStatus.FAILED.value,
                claimed_until=None,
            )
            db.commit()
            return moved

    def get(self, submission_id: int) -> Submission | None:
        """Return one Submission with its results, if it exists."""
        with self._session_factory() as db:
            found = self._load(db, SubmissionRow.id == submission_id)
            if not found:
                return None
            return found[0]

    def list_for_student(
        self, posting_id: int, student_id: int
    ) -> tuple[Submission, ...]:
        """One Student's Submissions on a Posting, oldest first."""
        with self._session_factory() as db:
            return tuple(
                self._load(
                    db,
                    SubmissionRow.posting_id == posting_id,
                    SubmissionRow.student_id == student_id,
                )
            )

    def list_for_posting(self, posting_id: int) -> tuple[Submission, ...]:
        """Every Student's Submissions on a Posting, oldest first."""
        with self._session_factory() as db:
            return tuple(self._load(db, SubmissionRow.posting_id == posting_id))

    def _insert(
        self, db: Session, submission: Submission, *, claimed_until: datetime | None
    ) -> int:
        """Insert the Submission row as given and return its id."""
        row = SubmissionRow(
            posting_id=submission.posting_id,
            student_id=submission.student_id,
            version_number=submission.version_number,
            code=submission.code,
            language=submission.language,
            submitted_at=submission.submitted_at,
            is_late=submission.is_late,
            score=submission.score,
            max_score=submission.max_score,
            attempt=submission.attempt,
            status=submission.status.value,
            recovery=submission.recovery.value,
            claimed_until=claimed_until,
        )
        db.add(row)
        db.flush()
        assert row.id is not None
        return row.id

    def _move(
        self,
        db: Session,
        submission_id: int,
        source: tuple[SubmissionStatus, ...],
        *conditions: ColumnElement[bool],
        **values: object,
    ) -> bool:
        """Apply `values` only while the row is in a `source` status."""
        result = db.exec(
            update(SubmissionRow)
            .where(
                col(SubmissionRow.id) == submission_id,
                col(SubmissionRow.status).in_([status.value for status in source]),
                *conditions,
            )
            .values(**values)
        )
        return result.rowcount == 1

    def _load(self, db: Session, *conditions: ColumnElement[bool]) -> list[Submission]:
        """Read matching Submissions oldest first, with Posting and results."""
        pairs: Sequence[tuple[SubmissionRow, PostingRow]] = db.exec(
            select(SubmissionRow, PostingRow)
            .join(PostingRow, PostingRow.id == SubmissionRow.posting_id)
            .where(*conditions)
            .order_by(SubmissionRow.submitted_at, SubmissionRow.id)
        ).all()
        if not pairs:
            return []
        ids = [row.id for row, _ in pairs]
        results: dict[int, list[ResultRow]] = defaultdict(list)
        for result in db.exec(
            select(ResultRow)
            .where(ResultRow.submission_id.in_(ids))
            .order_by(ResultRow.submission_id, ResultRow.position)
        ).all():
            results[result.submission_id].append(result)
        return [_submission(row, posting, results[row.id]) for row, posting in pairs]
