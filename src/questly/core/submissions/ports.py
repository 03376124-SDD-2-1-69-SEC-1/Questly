"""Ports the submissions slice depends on."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from questly.core.assignments.models import (
    Assignment,
    AssignmentVersion,
    InstructorProblem,
    StudentProblem,
)
from questly.core.auth.models import Actor
from questly.core.classrooms.models import MemberView
from questly.core.submissions.models import (
    BatchResult,
    Claim,
    Execution,
    Recovery,
    Submission,
    TestResult,
)


class CodeRunner(Protocol):
    """Runs a program synchronously (Judge0 CE in prod).

    Both methods raise RunnerUnavailableError when the code could not be
    judged at all.
    """

    def run(
        self, *, code: str, language: str, stdin: str, time_limit_seconds: float
    ) -> Execution:
        """One input."""
        ...

    def run_many(
        self,
        *,
        code: str,
        language: str,
        stdins: Sequence[str],
        time_limit_seconds: float,
    ) -> tuple[Execution, ...]:
        """One batch: an Execution per input, in the order of `stdins`."""
        ...


class BatchRunner(Protocol):
    """Judges a batch without holding a thread for it (Judge0 CE in prod).

    `start` raises RunnerUnavailableError when the runner cannot take the
    batch; `collect` raises it when the runner cannot be reached.
    """

    def start(
        self,
        *,
        code: str,
        language: str,
        stdins: Sequence[str],
        time_limit_seconds: float,
    ) -> tuple[str, ...]:
        """Hands the batch over; one token per input, in the order of `stdins`."""
        ...

    def collect(self, tokens: Sequence[str]) -> BatchResult:
        """`finished` with an Execution per token in token order, `running`
        when any token is still being judged, or `gone` when the runner no
        longer knows a token."""
        ...


class SubmissionRepository(Protocol):
    """Submissions and the lease a judging worker holds on a pending one.

    Each write below is a compare-and-set on the stored status: it changes the
    row only when the row is in the status it names, and otherwise changes
    nothing and returns False, meaning the row was deleted (unposted) or has
    moved on, so the worker stops. A lease is live while `claimed_until` is
    after now; a pending row with no live lease is due for `claim_due`.
    `get` and the `list_*` reads return Submissions in every status.
    """

    def add(self, submission: Submission) -> Submission:
        """Stores a `done` Submission as given and assigns its id."""
        ...

    def receive(self, submission: Submission, lease_until: datetime) -> Submission:
        """Stores a new `received` Submission and assigns its id.

        `submission` has status `received`, no score, no results, recovery
        `none` and no id. Saves it with no runner tokens and its lease held
        until `lease_until`. Raises SubmissionPendingError, storing nothing,
        when the Student already has a pending Submission on the Posting or
        already has one with this attempt number.
        """
        ...

    def claim_due(
        self, now: datetime, lease_until: datetime, limit: int
    ) -> tuple[Claim, ...]:
        """Takes the lease on up to `limit` due rows, oldest submitted first.

        Due means pending with `claimed_until` unset (released) or at or
        before `now` (expired). Sets `claimed_until` to `lease_until`, nothing
        else. Each Claim holds the row as stored, its saved runner tokens, and
        `interrupted=True` when the lease had expired, False when it had been
        released. Concurrent callers never get the same row. Terminal rows and
        live leases are never returned.
        """
        ...

    def started(
        self,
        submission_id: int,
        *,
        tokens: Sequence[str],
        lease_until: datetime,
        recovery: Recovery,
    ) -> bool:
        """`received` -> `judging`: saves `tokens` in order, the lease and
        `recovery`."""
        ...

    def renew(self, submission_id: int, *, lease_until: datetime) -> bool:
        """`judging` only: moves the lease to `lease_until`; status unchanged."""
        ...

    def finished(
        self,
        submission_id: int,
        *,
        tokens: Sequence[str],
        score: int,
        results: Sequence[TestResult],
        recovery: Recovery,
    ) -> bool:
        """`judging` -> `done`, only when the saved runner tokens equal `tokens`
        in order. Writes score, results and `recovery` and clears the lease in
        one transaction."""
        ...

    def requeued(self, submission_id: int) -> bool:
        """`judging` -> `received` after the runner lost the batch: clears the
        runner tokens; the lease and recovery stay."""
        ...

    def released(self, submission_id: int) -> bool:
        """Pending with a lease set: clears the lease (runner unreachable) so
        the row is due again at once; status and tokens stay."""
        ...

    def failed(self, submission_id: int) -> bool:
        """`received` or `judging` -> `failed`: clears the lease; the score
        stays unset and nothing else changes."""
        ...

    def get(self, submission_id: int) -> Submission | None: ...

    def list_for_student(
        self, posting_id: int, student_id: int
    ) -> tuple[Submission, ...]:
        """Oldest first."""
        ...

    def list_for_posting(self, posting_id: int) -> tuple[Submission, ...]:
        """Every Student's Submissions, oldest first."""
        ...


class Problems(Protocol):
    """The Postings a Submission is for; filled by AssignmentService."""

    def problem(
        self, actor: Actor, classroom_id: int, assignment_id: int
    ) -> InstructorProblem | StudentProblem: ...

    def current_version(
        self, actor: Actor, classroom_id: int, assignment_id: int
    ) -> AssignmentVersion: ...

    def versions(self, actor: Actor, assignment_id: int) -> list[AssignmentVersion]: ...


class Roster(Protocol):
    """Who a Classroom's Members are; filled by ClassroomService."""

    def members(self, actor: Actor, classroom_id: int) -> list[MemberView]: ...

    def is_archived(self, actor: Actor, classroom_id: int) -> bool: ...


class VersionLookup(Protocol):
    """Reads the Version a Submission was received on; filled by
    VersionRepository."""

    def get(self, assignment_id: int, number: int) -> AssignmentVersion | None: ...


class AssignmentLookup(Protocol):
    """Reads an Assignment's current Version number for PostingStats."""

    def get(self, assignment_id: int) -> Assignment | None: ...
