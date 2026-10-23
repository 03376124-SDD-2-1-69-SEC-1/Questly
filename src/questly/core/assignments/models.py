"""Assignment domain model: content, Versions, Postings (ADR-0007 §3).

No FastAPI or database imports. An Assignment is the current content; every
publish or edit appends an immutable AssignmentVersion; each Classroom it is
published to has its own Posting with schedule and policy.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Difficulty(StrEnum):
    """How hard an Assignment is; the `core.assignments` CHECK constraint's values."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class TestCaseKind(StrEnum):
    """Sample tests are shown to Students; hidden and edge tests show pass/fail only."""

    SAMPLE = "sample"
    HIDDEN = "hidden"
    EDGE = "edge"


class Language(StrEnum):
    """The config allowlist of submission languages (ADR-0007 §3.4)."""

    PYTHON3 = "python3"


@dataclass(frozen=True, slots=True)
class TestCase:
    """One input and its expected output. `note` labels it ("empty list")."""

    input_data: str
    expected_output: str
    kind: TestCaseKind = TestCaseKind.SAMPLE
    note: str = ""
    order_index: int = 0
    id: int | None = None

    @property
    def visible_to_students(self) -> bool:
        return self.kind is TestCaseKind.SAMPLE


@dataclass(frozen=True, slots=True)
class JudgingSettings:
    """T-04 step 3 settings that belong to the content, not to a Classroom."""

    time_limit_ms: int = 1000
    language: Language = Language.PYTHON3
    show_hidden_names: bool = False


@dataclass(frozen=True, slots=True)
class Assignment:
    """The current content of a programming Assignment.

    `id` is `None` only before the repository persisted it; `create` and
    `update` assign ids to the Assignment and its test cases alike.
    `owner_id` is the Instructor who published it; `topic_id` its one Topic.
    """

    title: str
    problem_statement: str
    difficulty: Difficulty
    id: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    artifact_id: int | None = None
    test_cases: list[TestCase] = field(default_factory=list)
    owner_id: int | None = None
    topic_id: int | None = None
    settings: JudgingSettings = field(default_factory=JudgingSettings)
    current_version: int = 0


@dataclass(frozen=True, slots=True)
class AssignmentVersion:
    """An immutable snapshot of an Assignment's content and why it changed."""

    assignment_id: int
    number: int
    title: str
    problem_statement: str
    difficulty: Difficulty
    settings: JudgingSettings
    reason: str
    changed_at: datetime
    test_cases: list[TestCase] = field(default_factory=list)
    id: int | None = None


@dataclass(frozen=True, slots=True)
class AssignmentContent:
    """What T-04 steps 1-3 collect about the content itself."""

    title: str
    problem_statement: str
    difficulty: Difficulty
    test_cases: list[TestCase] = field(default_factory=list)
    topic_id: int | None = None
    settings: JudgingSettings = field(default_factory=JudgingSettings)


@dataclass(frozen=True, slots=True)
class Schedule:
    """T-04 step 3 settings copied into every Posting, editable per Classroom."""

    deadline: datetime
    max_score: int = 10
    allow_late: bool = False
    allow_resubmission: bool = True


@dataclass(frozen=True, slots=True)
class Posting:
    """One Assignment published to one Classroom."""

    classroom_id: int
    assignment_id: int
    schedule: Schedule
    published_at: datetime
    closed_at: datetime | None = None
    id: int | None = None

    def is_closed(self, now: datetime) -> bool:
        """Closed by the Instructor, or past the deadline without late work."""
        if self.closed_at is not None:
            return True
        return not self.schedule.allow_late and now > self.schedule.deadline


class ProblemFilter(StrEnum):
    """S-01 filter chips."""

    ALL = "all"
    NOT_STARTED = "not_started"
    SUBMITTED = "submitted"


class StudentState(StrEnum):
    """S-01 status badges (S-01b)."""

    NOT_STARTED = "not_started"
    PARTIAL = "partial"
    PASSED = "passed"
    RESUBMIT_NEEDED = "resubmit_needed"
    LATE = "late"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class PostingSummary:
    """T-01 Problems row numbers from Submissions."""

    submitted: int = 0
    avg_score: float | None = None


@dataclass(frozen=True, slots=True)
class StudentStanding:
    """One Student's standing on one Posting (S-01 row)."""

    state: StudentState = StudentState.NOT_STARTED
    score: float | None = None


@dataclass(frozen=True, slots=True)
class ScoreRow:
    """A row of the T-01 Summary score table, one score per Posting."""

    student_name: str
    student_number: str | None
    scores: list[float | None] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(score for score in self.scores if score is not None)


@dataclass(frozen=True, slots=True)
class FailRate:
    title: str
    rate: float


@dataclass(frozen=True, slots=True)
class InstructorProblem:
    """A T-01 Problems row: the Posting, its content, and its numbers."""

    number: int
    posting: Posting
    assignment: Assignment
    summary: PostingSummary


@dataclass(frozen=True, slots=True)
class StudentProblem:
    """An S-01 Problems row."""

    number: int
    posting: Posting
    assignment: Assignment
    standing: StudentStanding


@dataclass(frozen=True, slots=True)
class InstructorProblemList:
    student_count: int
    problems: list[InstructorProblem] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StudentProblemList:
    """S-01 rows after the filter; `total` counts every Posting."""

    problems: list[StudentProblem] = field(default_factory=list)
    total: int = 0


@dataclass(frozen=True, slots=True)
class InstructorSummary:
    """T-01 Summary tab."""

    student_count: int
    problem_count: int
    avg_pass_rate: float | None
    never_submitted: int
    most_failed: list[FailRate] = field(default_factory=list)
    problem_titles: list[str] = field(default_factory=list)
    score_table: list[ScoreRow] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StudentSummary:
    """S-01 Summary tab: counts for this Classroom only."""

    solved: int
    passed: int
    total: int
    avg_score: float | None


@dataclass(frozen=True, slots=True)
class PublishedAssignment:
    """What publishing returns: the Assignment and its new Postings."""

    assignment: Assignment
    postings: list[Posting] = field(default_factory=list)
