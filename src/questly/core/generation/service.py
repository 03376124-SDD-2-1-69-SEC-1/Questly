"""Generation use cases: T-03 generate, T-04 review stepper, publish (ADR-0007 §4).

Every public method takes the Actor first. A Draft belongs to the Instructor
who asked for it; anyone else is told it does not exist (404). The Classroom
it is generated in must be one the Instructor owns.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    JudgingSettings,
    PublishedAssignment,
    Schedule,
    TestCase,
    TestCaseKind,
)
from questly.core.auth.models import Actor, PermissionDeniedError, Role
from questly.core.classrooms.models import Classroom, ClassroomRole
from questly.core.generation.models import (
    Citation,
    DocumentSummary,
    Draft,
    DraftPart,
    DraftSettings,
    DraftStatus,
    GenerationAbandonedError,
    GenerationForm,
    GenerationJob,
    GenerationStep,
    JobError,
    JobProgress,
    JobStatus,
    ProviderQuotaSnapshot,
    QuotaStatus,
    StartedJob,
)
from questly.core.generation.ports import (
    AssignmentPublisher,
    ClassroomLookup,
    Clock,
    DocumentCatalog,
    DraftRepository,
    GenerationClient,
    GenerationJobStore,
    ProviderQuota,
)
from questly.core.generation.schemas import AssignmentDraft as DraftSchema
from questly.core.generation.schemas import Citation as CitationSchema
from questly.core.generation.schemas import (
    GenerationFilters,
    GenerationRequest,
    GenerationResponse,
)

logger = logging.getLogger(__name__)

QUOTA_ZONE = ZoneInfo("Asia/Bangkok")
STEPS = (1, 2, 3, 4)


class GenerationFailedError(Exception):
    """T-03d: the model did not respond. Nothing was saved, no quota used."""


class QuotaExceededError(Exception):
    """No generations left today."""


class ProviderQuotaExceededError(Exception):
    """The provider's own free quota is spent until `resets_at`."""

    def __init__(self, resets_at: datetime) -> None:
        super().__init__(f"provider quota spent until {resets_at.isoformat()}")
        self.resets_at = resets_at


class GenerationJobGoneError(Exception):
    """No such job: it never existed here, or the server restarted since."""


class DraftNotFoundError(Exception):
    """Missing, published already, or not this Instructor's."""


class ClassroomNotVisibleError(Exception):
    """The Classroom is missing or the Actor is not in it (404)."""


class DocumentNotAllowedError(Exception):
    """A chosen Document is not in the Instructor's library."""


class MissingFieldsError(Exception):
    """T-04a: fields a step needs before moving on."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


class _Unset:
    """Marks a stepper field left out of the form."""


UNSET = _Unset()


def _ignore_step(step: GenerationStep) -> None:
    """The `on_step` of a caller that shows no progress (the JSON API)."""


class GenerationService:
    def __init__(
        self,
        drafts: DraftRepository,
        client: GenerationClient,
        documents: DocumentCatalog,
        classrooms: ClassroomLookup,
        publisher: AssignmentPublisher,
        clock: Clock,
        daily_quota: Callable[[], int],
        jobs: GenerationJobStore,
        provider_quota: ProviderQuota,
    ) -> None:
        self._drafts = drafts
        self._client = client
        self._documents = documents
        self._classrooms = classrooms
        self._publisher = publisher
        self._clock = clock
        self._daily_quota = daily_quota
        self._jobs = jobs
        self._provider_quota = provider_quota

    # ---- T-03 ----------------------------------------------------------------

    def documents(self, actor: Actor) -> list[DocumentSummary]:
        """The T-03 picker; empty means T-03b."""
        self._require_instructor(actor)
        return self._documents.documents_of(actor.user_id)

    def quota(self, actor: Actor) -> QuotaStatus:
        self._require_instructor(actor)
        return QuotaStatus(
            used=self._drafts.count_generations(actor.user_id, self._day_start()),
            limit=self._daily_quota(),
        )

    def generate(
        self,
        actor: Actor,
        classroom_id: int,
        *,
        prompt: str,
        difficulty: Difficulty,
        topic_id: int | None = None,
        document_ids: list[int] | None = None,
        on_step: Callable[[GenerationStep], None] = _ignore_step,
    ) -> Draft:
        """Ask the model for a draft; charge the quota only when it answers.

        `on_step` hears each stage (T-03c); it may raise to abandon the run
        before anything is saved or charged.
        """
        prompt, document_ids = self._prepare(actor, classroom_id, prompt, document_ids)

        response = self._call(
            GenerationRequest(
                prompt=prompt,
                filters=GenerationFilters(difficulty=difficulty.value),
                document_ids=document_ids,
            ),
            on_step,
        )
        on_step(GenerationStep.SAVING)
        now = self._clock.now()
        draft = self._drafts.create(
            Draft(
                classroom_id=classroom_id,
                requested_by=actor.user_id,
                prompt=prompt,
                content=_content(response.draft, difficulty, topic_id),
                citations=[_citation(c) for c in response.citations],
                document_ids=list(document_ids),
                generated_at=now,
                classroom_ids=[classroom_id],
            )
        )
        self._drafts.record_generation(actor.user_id, now)
        return draft

    def regenerate(self, actor: Actor, draft_id: int, part: DraftPart) -> Draft:
        """T-03a "Regenerate this part"; a success counts against the quota."""
        draft = self.draft(actor, draft_id)
        self._require_quota(actor)
        response = self._call(
            GenerationRequest(
                prompt=draft.prompt,
                filters=GenerationFilters(difficulty=draft.content.difficulty.value),
                document_ids=draft.document_ids,
            ),
            _ignore_step,
        )
        if part is DraftPart.TITLE:
            content = replace(draft.content, title=response.draft.title)
        else:
            content = replace(draft.content, problem_statement=response.draft.statement)
        updated = self._drafts.update(replace(draft, content=content))
        self._drafts.record_generation(actor.user_id, self._clock.now())
        return updated

    # ---- T-03c: Generate as a background job (ADR-0010) --------------------------

    def provider_quota(self, actor: Actor) -> ProviderQuotaSnapshot:
        """T-03's second quota line: what the provider says is left today."""
        self._require_instructor(actor)
        return self._provider_quota.snapshot()

    def start_job(
        self,
        actor: Actor,
        classroom_id: int,
        *,
        prompt: str,
        difficulty: Difficulty,
        topic_id: int | None = None,
        document_ids: list[int] | None = None,
    ) -> StartedJob:
        """Validate the form, check both quotas and register a running job.

        A second Generate while one is still running returns that job, so the
        caller must run the work only when `created`.
        """
        self._require_owner(actor, classroom_id)
        running = self._jobs.running_for(actor.user_id, classroom_id)
        if running is not None:
            running = self._expire_if_stale(running)
            if running.status is JobStatus.RUNNING:
                return StartedJob(running, created=False)
        prompt, document_ids = self._prepare(actor, classroom_id, prompt, document_ids)
        snapshot = self._provider_quota.snapshot()
        if snapshot.is_spent:
            raise ProviderQuotaExceededError(snapshot.resets_at)
        return self._jobs.create_unless_running(
            GenerationJob(
                actor=actor,
                classroom_id=classroom_id,
                form=GenerationForm(prompt, difficulty, topic_id, document_ids),
                started_at=self._clock.now(),
            )
        )

    def run_job(self, actor: Actor, classroom_id: int, job_id: int) -> None:
        """Do the work of a job (the background task).

        A failed run does not raise: its outcome is the job's status.
        """
        job = self._own_job(actor, classroom_id, job_id)
        if job.status is not JobStatus.RUNNING:
            return

        def on_step(step: GenerationStep) -> None:
            self._jobs.update(job_id, lambda j: j.advance(step, self._clock.now()))

        try:
            draft = self.generate(
                job.actor,
                job.classroom_id,
                prompt=job.form.prompt,
                difficulty=job.form.difficulty,
                topic_id=job.form.topic_id,
                document_ids=job.form.document_ids,
                on_step=on_step,
            )
        except GenerationAbandonedError:
            return
        except GenerationFailedError:
            self._jobs.update(job_id, lambda j: j.fail(JobError.FAILED))
        except QuotaExceededError:
            self._jobs.update(job_id, lambda j: j.fail(JobError.QUOTA))
        except (ValueError, DocumentNotAllowedError, ClassroomNotVisibleError):
            self._jobs.update(job_id, lambda j: j.fail(JobError.INVALID))
        except Exception:
            self._jobs.update(job_id, lambda j: j.fail(JobError.FAILED))
            raise
        else:
            self._jobs.update(job_id, lambda j: j.finish(draft.id, self._clock.now()))
            self._provider_quota.invalidate()

    def job(self, actor: Actor, classroom_id: int, job_id: int) -> JobProgress:
        """T-03c: only the Instructor who started the job may read it."""
        job = self._expire_if_stale(self._own_job(actor, classroom_id, job_id))
        return job.progress(self._clock.now())

    def cancel_job(self, actor: Actor, classroom_id: int, job_id: int) -> GenerationJob:
        """T-03c "Cancel": stop waiting. The work stops at its next step."""
        self._own_job(actor, classroom_id, job_id)
        return self._jobs.update(job_id, lambda j: j.cancel())

    # ---- Drafts (T-01 list, T-04 stepper) --------------------------------------

    def drafts(self, actor: Actor, classroom_id: int) -> list[Draft]:
        """T-01 "Drafts": this Instructor's unpublished Drafts, newest first."""
        role = self._classrooms.role_of(actor, classroom_id)
        if role is ClassroomRole.OUTSIDER:
            raise ClassroomNotVisibleError
        if role is not ClassroomRole.OWNER:
            return []
        return sorted(
            (
                draft
                for draft in self._drafts.list_for_classroom(classroom_id)
                if draft.status is DraftStatus.REVIEWING
                and draft.requested_by == actor.user_id
            ),
            key=lambda draft: draft.generated_at,
            reverse=True,
        )

    def draft(self, actor: Actor, draft_id: int) -> Draft:
        draft = self._drafts.get(draft_id)
        if (
            draft is None
            or draft.requested_by != actor.user_id
            or draft.status is not DraftStatus.REVIEWING
        ):
            raise DraftNotFoundError
        return draft

    def published(self, actor: Actor, draft_id: int) -> Draft:
        """T-04b: a Draft this Instructor already published."""
        draft = self._drafts.get(draft_id)
        if (
            draft is None
            or draft.requested_by != actor.user_id
            or draft.status is not DraftStatus.PUBLISHED
        ):
            raise DraftNotFoundError
        return draft

    def save(
        self,
        actor: Actor,
        draft_id: int,
        *,
        title: str | _Unset = UNSET,
        problem_statement: str | _Unset = UNSET,
        difficulty: Difficulty | _Unset = UNSET,
        topic_id: int | None | _Unset = UNSET,
        test_cases: list[TestCase] | _Unset = UNSET,
        judging: JudgingSettings | _Unset = UNSET,
        settings: DraftSettings | _Unset = UNSET,
        classroom_ids: list[int] | _Unset = UNSET,
    ) -> Draft:
        """Save one stepper step. Fields left out keep their value."""
        draft = self.draft(actor, draft_id)
        content = draft.content
        content = replace(
            content,
            title=content.title if isinstance(title, _Unset) else title.strip(),
            problem_statement=content.problem_statement
            if isinstance(problem_statement, _Unset)
            else problem_statement.strip(),
            difficulty=content.difficulty
            if isinstance(difficulty, _Unset)
            else difficulty,
            topic_id=content.topic_id if isinstance(topic_id, _Unset) else topic_id,
            test_cases=content.test_cases
            if isinstance(test_cases, _Unset)
            else [tc for tc in test_cases if tc.input_data or tc.expected_output],
            settings=content.settings if isinstance(judging, _Unset) else judging,
        )
        new_settings = draft.settings if isinstance(settings, _Unset) else settings
        if new_settings.max_score <= 0 or content.settings.time_limit_ms <= 0:
            raise ValueError("max score and time limit must be positive")
        chosen = draft.classroom_ids
        if not isinstance(classroom_ids, _Unset):
            owned = {c.id for c in self._classrooms.owned_classrooms(actor)}
            if not set(classroom_ids) <= owned:
                raise ClassroomNotVisibleError
            chosen = sorted(set(classroom_ids))
        return self._drafts.update(
            replace(draft, content=content, settings=new_settings, classroom_ids=chosen)
        )

    def missing_fields(self, actor: Actor, draft_id: int, step: int) -> list[str]:
        """What must be filled before leaving `step` (T-04a); empty when done."""
        draft = self.draft(actor, draft_id)
        return _missing(draft, step)

    def publish_targets(self, actor: Actor) -> list[tuple[Classroom, int]]:
        """T-04 step 4: the Instructor's active Classrooms and student counts."""
        self._require_instructor(actor)
        return [
            (classroom, len(self._classrooms.member_ids(actor, classroom.id)))
            for classroom in self._classrooms.owned_classrooms(actor)
        ]

    def publish(self, actor: Actor, draft_id: int) -> PublishedAssignment:
        """T-04 step 4 "Approve and publish" to every chosen Classroom."""
        draft = self.draft(actor, draft_id)
        missing = [field for step in STEPS for field in _missing(draft, step)]
        if missing:
            raise MissingFieldsError(missing)
        settings = draft.settings
        published = self._publisher.publish(
            actor,
            draft.content,
            Schedule(
                deadline=settings.deadline,
                max_score=settings.max_score,
                allow_late=settings.allow_late,
                allow_resubmission=settings.allow_resubmission,
            ),
            draft.classroom_ids,
        )
        self._drafts.update(
            replace(
                draft,
                status=DraftStatus.PUBLISHED,
                assignment_id=published.assignment.id,
            )
        )
        return published

    def discard(self, actor: Actor, draft_id: int) -> None:
        self._drafts.delete(self.draft(actor, draft_id).id)

    # ---- Internals ---------------------------------------------------------

    def _call(
        self, request: GenerationRequest, on_step: Callable[[GenerationStep], None]
    ) -> GenerationResponse:
        try:
            return self._client.generate(request, on_step)
        except GenerationAbandonedError:
            raise
        except Exception as error:  # noqa: BLE001 -- any client failure is T-03d
            # The message and cause stay out of the T-03d text; the log is
            # where an operator finds why. No prompt or draft body is logged.
            logger.exception("generation failed")
            raise GenerationFailedError from error

    def _require_instructor(self, actor: Actor) -> None:
        if actor.role is not Role.INSTRUCTOR:
            raise PermissionDeniedError

    def _require_owner(self, actor: Actor, classroom_id: int) -> None:
        role = self._classrooms.role_of(actor, classroom_id)
        if role is ClassroomRole.OUTSIDER:
            raise ClassroomNotVisibleError
        if role is not ClassroomRole.OWNER:
            raise PermissionDeniedError

    def _prepare(
        self,
        actor: Actor,
        classroom_id: int,
        prompt: str,
        document_ids: list[int] | None,
    ) -> tuple[str, list[int]]:
        """What Generate checks before it spends anything."""
        self._require_owner(actor, classroom_id)
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("describe the problem you want")
        if document_ids is None:
            document_ids = []
        allowed = {
            document.id for document in self._documents.documents_of(actor.user_id)
        }
        if not set(document_ids) <= allowed:
            raise DocumentNotAllowedError
        self._require_quota(actor)
        return prompt, document_ids

    def _own_job(self, actor: Actor, classroom_id: int, job_id: int) -> GenerationJob:
        self._require_owner(actor, classroom_id)
        job = self._jobs.get(job_id)
        if job is None:
            raise GenerationJobGoneError
        if job.classroom_id != classroom_id or job.actor.user_id != actor.user_id:
            raise ClassroomNotVisibleError
        return job

    def _expire_if_stale(self, job: GenerationJob) -> GenerationJob:
        if not job.is_stale(self._clock.now()):
            return job
        return self._jobs.update(
            job.id,
            lambda j: j.fail(JobError.TIMEOUT) if j.is_stale(self._clock.now()) else j,
        )

    def _require_quota(self, actor: Actor) -> None:
        if self.quota(actor).remaining <= 0:
            raise QuotaExceededError

    def _day_start(self) -> datetime:
        local = self._clock.now().astimezone(QUOTA_ZONE)
        return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def _missing(draft: Draft, step: int) -> list[str]:
    content = draft.content
    if step == 1:
        missing = []
        if not content.title:
            missing.append("title")
        if not content.problem_statement:
            missing.append("description")
        return missing
    if step == 2:
        return [] if content.test_cases else ["test cases"]
    if step == 3:
        return [] if draft.settings.deadline is not None else ["deadline"]
    if step == 4:
        return [] if draft.classroom_ids else ["classrooms"]
    raise ValueError(f"unknown step {step}")


def _content(
    draft: DraftSchema, difficulty: Difficulty, topic_id: int | None
) -> AssignmentContent:
    return AssignmentContent(
        title=draft.title,
        problem_statement=draft.statement,
        difficulty=difficulty,
        topic_id=topic_id,
        settings=JudgingSettings(),
        test_cases=[
            TestCase(
                input_data=test_case.input_data,
                expected_output=test_case.expected_output,
                kind=TestCaseKind.HIDDEN
                if test_case.is_hidden
                else TestCaseKind.SAMPLE,
                order_index=index,
            )
            for index, test_case in enumerate(draft.test_cases)
        ],
    )


def _citation(citation: CitationSchema) -> Citation:
    return Citation(
        chunk_id=citation.chunk_id,
        source_id=citation.source_id,
        page=citation.page,
        score=citation.score,
        text_snapshot=citation.text_snapshot,
    )
