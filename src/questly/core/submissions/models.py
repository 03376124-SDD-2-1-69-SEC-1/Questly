"""Submission and Run domain model (ADR-0007 §5).

A Submission is graded against every Test Case of a Posting and always kept;
a Run executes the sample Test Cases only and is never kept. No FastAPI,
ORM or runner client imports here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from questly.core.assignments.models import (
    AssignmentVersion,
    InstructorProblem,
    StudentProblem,
    TestCase,
    TestCaseKind,
)
from questly.core.rounding import average


class Verdict(StrEnum):
    PASSED = "passed"
    WRONG_ANSWER = "wrong_answer"
    TIME_LIMIT = "time_limit"
    RUNTIME_ERROR = "runtime_error"


class ExecutionStatus(StrEnum):
    OK = "ok"
    TIME_LIMIT = "time_limit"
    RUNTIME_ERROR = "runtime_error"


RUNNER_NOT_CONFIGURED_STDERR = "The code runner is not configured."


@dataclass(frozen=True, slots=True)
class Execution:
    """What the CodeRunner reports for one program run on one input."""

    status: ExecutionStatus
    stdout: str
    stderr: str
    time_seconds: float

    @property
    def is_runner_not_configured(self) -> bool:
        """True when no runner ran the program, as opposed to the program failing."""
        return (
            self.status is ExecutionStatus.RUNTIME_ERROR
            and self.stderr == RUNNER_NOT_CONFIGURED_STDERR
        )


@dataclass(frozen=True, slots=True)
class TestResult:
    """One Test Case's outcome.

    `position` is the Test Case's 1-based place in the Version, `ordinal`
    its 1-based place among Test Cases of the same kind ("Hidden 2").
    `actual_output` and `error` are kept for every test; the page decides
    what a Student may see.
    """

    position: int
    ordinal: int
    kind: TestCaseKind
    note: str
    verdict: Verdict
    time_seconds: float
    expected_output: str
    actual_output: str
    error: str

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.PASSED


class SubmissionStatus(StrEnum):
    """Where a Submission is in judging; `done` and `failed` are terminal."""

    RECEIVED = "received"
    JUDGING = "judging"
    DONE = "done"
    FAILED = "failed"

    @property
    def pending(self) -> bool:
        return self in (SubmissionStatus.RECEIVED, SubmissionStatus.JUDGING)

    def can_move(self, target: "SubmissionStatus") -> bool:
        return target in _MOVES[self]


# The one transition table. `received` is written by receive, not by a move.
_MOVES: dict[SubmissionStatus, frozenset[SubmissionStatus]] = {
    SubmissionStatus.RECEIVED: frozenset(
        {SubmissionStatus.JUDGING, SubmissionStatus.FAILED}
    ),
    SubmissionStatus.JUDGING: frozenset(
        {SubmissionStatus.DONE, SubmissionStatus.RECEIVED, SubmissionStatus.FAILED}
    ),
    SubmissionStatus.DONE: frozenset(),
    SubmissionStatus.FAILED: frozenset(),
}


class Recovery(StrEnum):
    """What judging did after the original run was interrupted.

    Set when the worker that started it vanished (its lease expired) or the
    runner lost the batch. `reconnected`: the saved runner tokens were
    collected. `rejudged`: the code was sent to the runner again. Declared in
    rank order.
    """

    NONE = "none"
    RECONNECTED = "reconnected"
    REJUDGED = "rejudged"

    def after(self, event: "Recovery") -> "Recovery":
        """The marker once `event` happened; it never moves down."""
        rank = list(Recovery)
        return max(self, event, key=rank.index)


@dataclass(frozen=True, slots=True)
class Submission:
    """A Student's code for one Posting. `id` is `None` before save.

    Only a `done` Submission has a score and results. A Submission built
    without a status is `done`, as every Submission was before async Submit.
    """

    posting_id: int
    classroom_id: int
    assignment_id: int
    student_id: int
    version_number: int
    code: str
    language: str
    submitted_at: datetime
    is_late: bool
    score: int | None
    max_score: int
    attempt: int
    results: tuple[TestResult, ...] = field(default_factory=tuple)
    status: SubmissionStatus = SubmissionStatus.DONE
    recovery: Recovery = Recovery.NONE
    id: int | None = None

    def __post_init__(self) -> None:
        done = self.status is SubmissionStatus.DONE
        if done != (self.score is not None):
            raise ValueError(f"a {self.status} Submission has a score iff done")
        if self.results and not done:
            raise ValueError(f"a {self.status} Submission has no results")

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def all_passed(self) -> bool:
        return self.passed_count == len(self.results)

    @property
    def failed(self) -> tuple[TestResult, ...]:
        return tuple(result for result in self.results if not result.passed)


@dataclass(frozen=True, slots=True)
class Claim:
    """A pending Submission a worker now holds the lease on.

    `tokens` are the runner tokens `started` saved, in Test Case order; a
    `received` Submission has none. `interrupted` is True when the previous
    lease expired (its worker vanished) and False when the row was released.
    """

    submission: Submission
    tokens: tuple[str, ...]
    interrupted: bool

    def __post_init__(self) -> None:
        if not self.submission.status.pending:
            raise ValueError("only a pending Submission can be claimed")
        if self.submission.status is SubmissionStatus.RECEIVED and self.tokens:
            raise ValueError("a received Submission has no runner tokens")


class BatchState(StrEnum):
    FINISHED = "finished"
    RUNNING = "running"
    GONE = "gone"


@dataclass(frozen=True, slots=True)
class BatchResult:
    """What BatchRunner.collect saw. Only a finished batch has executions,
    one per token in token order; `gone` means the runner lost the batch."""

    state: BatchState
    executions: tuple[Execution, ...] = ()

    def __post_init__(self) -> None:
        if self.executions and self.state is not BatchState.FINISHED:
            raise ValueError(f"a {self.state} batch has no executions")


@dataclass(frozen=True, slots=True)
class RunReport:
    """An ungraded Run on the sample Test Cases; never stored."""

    code: str
    results: tuple[TestResult, ...]

    @property
    def time_seconds(self) -> float:
        return sum(result.time_seconds for result in self.results)


class StudentTab(StrEnum):
    PROBLEM = "problem"
    HISTORY = "history"


class InstructorTab(StrEnum):
    RESULTS = "results"
    VERSIONS = "versions"


@dataclass(frozen=True, slots=True)
class StudentSolveView:
    """S-02 for a Member: the problem, their Submissions and what they may do.

    `counted` is the latest `done` Submission, or None before the first one.
    `pending` is the Submission being judged (S-02a), else None. `failed` is
    the latest Submission when judging it failed, else None.
    `update` is the current Version when the counted Submission is on an
    older one (S-02e), else None.
    """

    problem: StudentProblem
    samples: tuple[TestCase, ...]
    hidden_count: int
    submissions: tuple[Submission, ...]
    counted: Submission | None
    pending: Submission | None
    failed: Submission | None
    update: AssignmentVersion | None
    closed: bool
    archived: bool
    can_submit: bool


@dataclass(frozen=True, slots=True)
class TestFailure:
    """T-02 "Failing": share of Counted Submissions failing one Test Case."""

    position: int
    ordinal: int
    kind: TestCaseKind
    note: str
    rate: float | None


@dataclass(frozen=True, slots=True)
class StudentResult:
    """One Member's row on T-02.

    `counted` is None until a Submission of theirs is `done`; `pending` says
    one is being judged.
    """

    student_id: int
    name: str
    student_number: str | None
    counted: Submission | None
    pending: bool
    attempts: int

    @property
    def listed(self) -> bool:
        """Whether the T-02 table shows a row for them."""
        return self.counted is not None or self.pending


@dataclass(frozen=True, slots=True)
class InstructorResultsView:
    """T-02 for the owning Instructor.

    `rows` is every Member and feeds the totals; `shown` is the rows matching
    the search `query` (all of them when it is empty) and feeds the table.
    """

    problem: InstructorProblem
    rows: tuple[StudentResult, ...]
    failures: tuple[TestFailure, ...]
    versions: tuple[AssignmentVersion, ...]
    closed: bool
    shown: tuple[StudentResult, ...]
    query: str

    @property
    def submitted(self) -> tuple[StudentResult, ...]:
        return tuple(row for row in self.rows if row.counted is not None)

    @property
    def average_score(self) -> float | None:
        return average([row.counted.score for row in self.submitted if row.counted])

    @property
    def all_passed(self) -> int:
        return sum(
            1 for row in self.submitted if row.counted and row.counted.all_passed
        )

    @property
    def late(self) -> int:
        return sum(1 for row in self.submitted if row.counted and row.counted.is_late)


@dataclass(frozen=True, slots=True)
class Judged:
    """What a `done` Submission scored; all three numbers exist together."""

    score: int
    passed_count: int
    total_tests: int


@dataclass(frozen=True, slots=True)
class NotJudged:
    """A Submission with no verdict: `received`, `judging` or `failed`."""


@dataclass(frozen=True, slots=True)
class SubmissionExportRow:
    """A row of the instructor's submissions.csv export.

    `problem_number` is the Posting's number in the Classroom, as in T-01.
    `student_number` is empty when the account has none (its email is not a
    student ID); both it and `student_name` are empty once the Student is no
    longer a Member.
    """

    problem_number: int
    assignment_title: str
    id: int
    student_number: str
    student_name: str
    attempt: int
    version_number: int
    submitted_at: datetime
    status: SubmissionStatus
    outcome: Judged | NotJudged
    max_score: int
    is_late: bool


class SubmissionNotFoundError(LookupError):
    pass


class PostingClosedError(Exception):
    """The Posting accepts no more Submissions or Runs."""


class ClassroomArchivedError(PostingClosedError):
    """An Archived Classroom takes no Submissions or Runs (ADR-0007 §2)."""


class ResubmissionNotAllowedError(Exception):
    """The Posting allows one Submit only and the Student has used it."""


class SubmissionPendingError(Exception):
    """The Student's earlier Submission on this Posting is still being judged,
    or this attempt number is already taken (a double-clicked Submit)."""


class EmptyCodeError(ValueError):
    pass


class RunnerUnavailableError(Exception):
    """The CodeRunner could not judge the code; nothing was graded or kept."""


class UnsupportedLanguageError(ValueError):
    """The CodeRunner has no runtime for this language."""
