"""SQLSubmissionRepository against the shared contract, on real Postgres."""

import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from questly.database.core.posting_repository import SQLPostingRepository
from questly.database.core.submission_repository import SQLSubmissionRepository
from questly.database.session import SessionFactory
from tests.contracts.submission_repository import (
    LEASE,
    NOW,
    SubmissionRepositoryContract,
    received,
    saved_id,
    submission,
)
from tests.contracts.support import IdFactory, PostingFactory, PostingIds
from tests.db.rows import RealRows

pytestmark = pytest.mark.postgres

INSERT = text(
    "INSERT INTO core.submissions (posting_id, student_id, version_number, code,"
    " language, submitted_at, is_late, max_score, attempt, status, score)"
    " VALUES (:posting, :student, 1, 'x', 'python3', now(), false, 10, :attempt,"
    " :status, :score)"
)


def refused_by(
    session_factory: SessionFactory, where: PostingIds, student: int, **row: object
) -> str:
    """Insert one raw row and return the name of the constraint refusing it."""
    with session_factory() as session:
        with pytest.raises(IntegrityError) as refused:
            session.execute(
                INSERT,
                {"posting": where.posting_id, "student": student, **row},
            )
        return refused.value.orig.diag.constraint_name


class TestSQLSubmissionRepository(RealRows, SubmissionRepositoryContract):
    """Run the contract against the SQL adapter."""

    @pytest.fixture()
    def repository(
        self, session_factory: SessionFactory, empty_core_tables: None
    ) -> SQLSubmissionRepository:
        return SQLSubmissionRepository(session_factory)

    def test_unposting_deletes_the_postings_submissions(
        self,
        repository: SQLSubmissionRepository,
        session_factory: SessionFactory,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        """ADR-0008: submissions.posting_id is ON DELETE CASCADE."""
        where = new_posting()
        added = repository.add(submission(where, new_user()))

        assert SQLPostingRepository(session_factory).delete(where.posting_id)
        assert repository.get(added.id) is None

    def test_concurrent_claimers_take_disjoint_rows(
        self,
        repository: SQLSubmissionRepository,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where = new_posting()
        due = {
            saved_id(repository.receive(received(where, new_user()), NOW))
            for _ in range(20)
        }
        start = threading.Barrier(2)

        def drain(_: int) -> list[int]:
            start.wait()
            taken: list[int] = []
            while claims := repository.claim_due(NOW, NOW + LEASE, 1):
                taken.extend(saved_id(claim.submission) for claim in claims)
            return taken

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(drain, range(2))

        assert set(first).isdisjoint(second)
        assert sorted(first + second) == sorted(due)

    def test_claim_due_skips_a_row_another_transaction_holds(
        self,
        repository: SQLSubmissionRepository,
        session_factory: SessionFactory,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        """SKIP LOCKED: a locked row is passed over at once, not waited for."""
        where = new_posting()
        locked = saved_id(repository.receive(received(where, new_user()), NOW))
        free = saved_id(repository.receive(received(where, new_user()), NOW))

        with session_factory() as holder, ThreadPoolExecutor(max_workers=1) as pool:
            holder.execute(
                text("SELECT 1 FROM core.submissions WHERE id = :id FOR UPDATE"),
                {"id": locked},
            )
            claiming = pool.submit(repository.claim_due, NOW, NOW + LEASE, 10)
            try:
                claims = claiming.result(timeout=10)
            except FutureTimeout:
                pytest.fail("claim_due waited for a row another transaction locked")
            finally:
                holder.rollback()

        assert [saved_id(claim.submission) for claim in claims] == [free]

    def test_a_score_must_match_the_status(
        self,
        session_factory: SessionFactory,
        empty_core_tables: None,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where, student = new_posting(), new_user()
        rule = "ck_submissions_score_when_done"

        assert (
            refused_by(
                session_factory, where, student, attempt=1, status="received", score=6
            )
            == rule
        )
        assert (
            refused_by(
                session_factory, where, student, attempt=1, status="failed", score=0
            )
            == rule
        )
        assert (
            refused_by(
                session_factory, where, student, attempt=1, status="done", score=None
            )
            == rule
        )
        assert (
            refused_by(
                session_factory, where, student, attempt=1, status="queued", score=None
            )
            == "ck_submissions_status"
        )

    def test_the_database_holds_one_unfinished_submission_per_student(
        self,
        session_factory: SessionFactory,
        empty_core_tables: None,
        new_posting: PostingFactory,
        new_user: IdFactory,
    ) -> None:
        where, student, other = new_posting(), new_user(), new_user()
        with session_factory() as session:
            for who, attempt, status in (
                (student, 1, "failed"),
                (student, 2, "received"),
                (other, 1, "judging"),
            ):
                session.execute(
                    INSERT,
                    {
                        "posting": where.posting_id,
                        "student": who,
                        "attempt": attempt,
                        "status": status,
                        "score": None,
                    },
                )
            session.commit()

        assert (
            refused_by(
                session_factory, where, student, attempt=3, status="judging", score=None
            )
            == "uq_submissions_one_unfinished"
        )
