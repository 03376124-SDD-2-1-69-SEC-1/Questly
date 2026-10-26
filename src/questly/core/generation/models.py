"""Domain representation of a generated Assignment draft and its citations."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    TestCaseKind,
)
from questly.core.auth.models import Actor


class ReviewStatus(StrEnum):
    """Where a generation artifact stands in instructor review.

    The same three values the `core.generation_artifacts` CHECK constraint
    allows, declared here so an invalid status fails in the domain instead of
    at the database.
    """

    PENDING = "pending"
    APPLIED = "applied"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class TestCaseDraft:
    """A single generated input/output pair awaiting instructor review."""

    input_data: str
    expected_output: str
    is_hidden: bool = False
    order_index: int = 0


@dataclass(frozen=True, slots=True)
class AssignmentDraft:
    """Generated Assignment content awaiting instructor review."""

    title: str
    statement: str
    test_cases: list[TestCaseDraft] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Citation:
    """Source excerpt supporting generated Assignment content.

    `chunk_id`/`source_id` point at rows in the `rag` schema. There is no FK
    between `core` and `rag` -- they never reference each other by key -- so these are
    business facts a citation carries about itself, not columns that exist
    only to satisfy a table. Allowlisted in
    `test_domain_models_hold_no_table_only_fields`.
    """

    chunk_id: int
    source_id: int
    page: int | None
    score: float
    text_snapshot: str


@dataclass(frozen=True, slots=True)
class GenerationArtifact:
    """A generated draft, its citations, and its review state.

    `id` is `None` only before the repository has persisted the artifact --
    see GenerationRepository.create_artifact(). Anything a repository returns
    has a non-None id.
    """

    draft: AssignmentDraft
    citations: list[Citation]
    id: int | None = None
    review_status: ReviewStatus = ReviewStatus.PENDING


# ---- ADR-0007 §4: Drafts reviewed in the T-04 stepper -----------------------


class DraftStatus(StrEnum):
    """A Draft leaves the Drafts list once it is published."""

    REVIEWING = "reviewing"
    PUBLISHED = "published"


class DraftPart(StrEnum):
    """What T-03a "Regenerate this part" can redo."""

    TITLE = "title"
    DESCRIPTION = "description"


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    """A Document an Instructor may let generation read (T-03 picker)."""

    id: int
    filename: str
    pages: int


@dataclass(frozen=True, slots=True)
class DraftSettings:
    """T-04 step 3's schedule and policy (the judging half lives on the content).

    `deadline` stays `None` until the Instructor picks one.
    """

    deadline: datetime | None = None
    max_score: int = 10
    allow_late: bool = False
    allow_resubmission: bool = True


@dataclass(frozen=True, slots=True)
class Draft:
    """A generated Assignment under review, owned by the Instructor who asked.

    `content` is what steps 1-2 edit, `settings` step 3, `classroom_ids`
    step 4 (the Classroom it was generated in is pre-chosen). After
    publishing, `assignment_id` points at the new Assignment.
    """

    classroom_id: int
    requested_by: int
    prompt: str
    content: AssignmentContent
    generated_at: datetime
    citations: list[Citation] = field(default_factory=list)
    document_ids: list[int] = field(default_factory=list)
    settings: DraftSettings = field(default_factory=DraftSettings)
    classroom_ids: list[int] = field(default_factory=list)
    status: DraftStatus = DraftStatus.REVIEWING
    assignment_id: int | None = None
    id: int | None = None

    @property
    def sample_count(self) -> int:
        return sum(
            1 for tc in self.content.test_cases if tc.kind is TestCaseKind.SAMPLE
        )


class GenerationStep(StrEnum):
    """Where a running generation is, in the order T-03c lists them.

    Retrieval is shown only when the Instructor selected Documents.
    """

    RETRIEVING = "retrieving"
    CALLING_MODEL = "calling_model"
    VALIDATING = "validating"
    RUNNING_SOLUTION = "running_solution"
    SAVING = "saving"


PIPELINE = (
    GenerationStep.RETRIEVING,
    GenerationStep.CALLING_MODEL,
    GenerationStep.VALIDATING,
    GenerationStep.RUNNING_SOLUTION,
    GenerationStep.SAVING,
)


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    """T-03 "Uses 1 of your 20 daily generations" / A-01 "18 / 20 today"."""

    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)


class QuotaKind(StrEnum):
    """How far T-03 can trust a ProviderQuotaSnapshot's numbers."""

    EXACT = "exact"
    UNKNOWN = "unknown"
    DEMO = "demo"


@dataclass(frozen=True, slots=True)
class ProviderQuotaSnapshot:
    """What the model provider says is left today (T-03, A-01).

    `used`, `limit` and `remaining` are set only when `kind` is EXACT; an
    UNKNOWN or DEMO snapshot explains itself in `note` instead.
    """

    kind: QuotaKind
    resets_at: datetime
    note: str
    used: int | None = None
    limit: int | None = None
    remaining: int | None = None

    @property
    def is_spent(self) -> bool:
        """Only a measured, empty quota blocks Generate; unknown never does."""
        return self.kind is QuotaKind.EXACT and self.remaining == 0


# ---- ADR-0010: Generate runs as a background job -----------------------------

STALE_AFTER = timedelta(minutes=3)


class GenerationAbandonedError(Exception):
    """The job was cancelled or went stale; stop before saving or charging."""


class JobError(StrEnum):
    """Why a FAILED job failed; T-03 words each one differently."""

    FAILED = "failed"  # the model did not answer
    QUOTA = "quota"
    INVALID = "invalid"
    TIMEOUT = "timeout"  # still running after STALE_AFTER


class JobStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class GenerationForm:
    """What the Instructor typed on T-03, kept so a failed job can refill it."""

    prompt: str
    difficulty: Difficulty
    topic_id: int | None
    document_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StepTiming:
    """One finished step of a job."""

    step: GenerationStep
    started_at: datetime
    finished_at: datetime

    @property
    def seconds(self) -> int:
        return int((self.finished_at - self.started_at).total_seconds())


@dataclass(frozen=True, slots=True)
class GenerationJob:
    """One Generate click, from the POST to the Draft (or the failure).

    A job is replaced, never edited:
    every transition returns a new job, and the store applies it atomically.
    """

    actor: Actor
    classroom_id: int
    form: GenerationForm
    started_at: datetime
    status: JobStatus = JobStatus.RUNNING
    step: GenerationStep | None = None
    step_started_at: datetime | None = None
    completed: list[StepTiming] = field(default_factory=list)
    draft_id: int | None = None
    error: JobError | None = None
    id: int | None = None

    def is_stale(self, now: datetime) -> bool:
        return self.status is JobStatus.RUNNING and now - self.started_at > STALE_AFTER

    def progress(self, now: datetime) -> JobProgress:
        seconds = {timing.step: timing.seconds for timing in self.completed}
        pipeline = PIPELINE if self.form.document_ids else PIPELINE[1:]
        steps = [
            StepProgress(step, StepState.DONE, seconds[step])
            if step in seconds
            else StepProgress(
                step,
                StepState.ACTIVE if step is self.step else StepState.PENDING,
                None,
            )
            for step in pipeline
        ]
        return JobProgress(
            job=self,
            elapsed_seconds=int((now - self.started_at).total_seconds()),
            steps=steps,
        )

    def advance(self, step: GenerationStep, now: datetime) -> GenerationJob:
        """Move to `step`; stop the run when nobody is waiting for it any more."""
        if self.status is not JobStatus.RUNNING or self.is_stale(now):
            raise GenerationAbandonedError
        return replace(self._closing_step(now), step=step, step_started_at=now)

    def finish(self, draft_id: int, now: datetime) -> GenerationJob:
        if self.status is not JobStatus.RUNNING:
            return self
        return replace(
            self._closing_step(now),
            status=JobStatus.DONE,
            step=None,
            step_started_at=None,
            draft_id=draft_id,
        )

    def fail(self, error: JobError) -> GenerationJob:
        if self.status is not JobStatus.RUNNING:
            return self
        return replace(self, status=JobStatus.FAILED, error=error)

    def cancel(self) -> GenerationJob:
        if self.status is not JobStatus.RUNNING:
            return self
        return replace(self, status=JobStatus.CANCELLED)

    def _closing_step(self, now: datetime) -> GenerationJob:
        if self.step is None or self.step_started_at is None:
            return self
        timing = StepTiming(self.step, self.step_started_at, now)
        return replace(self, completed=[*self.completed, timing])


@dataclass(frozen=True, slots=True)
class StartedJob:
    """What Generate got: a new job to run, or the one already running."""

    job: GenerationJob
    created: bool


class StepState(StrEnum):
    DONE = "done"
    ACTIVE = "active"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class StepProgress:
    """One line of T-03c's step list."""

    step: GenerationStep
    state: StepState
    seconds: int | None


@dataclass(frozen=True, slots=True)
class JobProgress:
    """What T-03c shows: the job, its age and its step list."""

    job: GenerationJob
    elapsed_seconds: int
    steps: list[StepProgress]

    @property
    def done_count(self) -> int:
        return sum(1 for step in self.steps if step.state is StepState.DONE)
