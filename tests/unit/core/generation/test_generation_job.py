"""GenerationJob: the pure state machine behind T-03c."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from questly.core.assignments.models import Difficulty
from questly.core.auth.models import Actor, Role
from questly.core.generation.models import (
    STALE_AFTER,
    GenerationForm,
    GenerationJob,
    GenerationStep,
    JobError,
    JobStatus,
    StepProgress,
    StepState,
)
from questly.core.generation.service import GenerationAbandonedError

T0 = datetime(2026, 10, 12, 3, 0, tzinfo=UTC)
ACTOR = Actor(user_id=1, role=Role.INSTRUCTOR, full_name="Somchai", email="s@x.th")


def new_job() -> GenerationJob:
    return GenerationJob(
        actor=ACTOR,
        classroom_id=7,
        form=GenerationForm(
            prompt="Binary search",
            difficulty=Difficulty.MEDIUM,
            topic_id=None,
            document_ids=[1],
        ),
        started_at=T0,
    )


def test_a_new_job_is_running_with_no_step_yet() -> None:
    job = new_job()

    assert job.status is JobStatus.RUNNING
    assert job.step is None
    assert job.completed == []


def test_advance_closes_the_previous_step_with_its_timestamps() -> None:
    job = new_job().advance(GenerationStep.CALLING_MODEL, T0)
    job = job.advance(GenerationStep.VALIDATING, T0 + timedelta(seconds=12))

    assert job.step is GenerationStep.VALIDATING
    [done] = job.completed
    assert done.step is GenerationStep.CALLING_MODEL
    assert done.started_at == T0
    assert done.finished_at == T0 + timedelta(seconds=12)
    assert done.seconds == 12


def test_finish_closes_the_last_step_and_keeps_the_draft_id() -> None:
    job = new_job().advance(GenerationStep.SAVING, T0)

    done = job.finish(draft_id=5, now=T0 + timedelta(seconds=3))

    assert done.status is JobStatus.DONE
    assert done.draft_id == 5
    assert [t.step for t in done.completed] == [GenerationStep.SAVING]


def test_fail_records_why() -> None:
    failed = new_job().fail(JobError.FAILED)

    assert failed.status is JobStatus.FAILED
    assert failed.error is JobError.FAILED


def test_cancel_marks_the_job_cancelled() -> None:
    assert new_job().cancel().status is JobStatus.CANCELLED


@pytest.mark.parametrize("ended", ["fail", "cancel"])
def test_advancing_an_ended_job_abandons_the_run(ended: str) -> None:
    job = new_job()
    job = job.fail(JobError.FAILED) if ended == "fail" else job.cancel()

    with pytest.raises(GenerationAbandonedError):
        job.advance(GenerationStep.VALIDATING, T0)


def test_advancing_a_stale_job_abandons_the_run() -> None:
    job = new_job()

    with pytest.raises(GenerationAbandonedError):
        job.advance(GenerationStep.VALIDATING, T0 + STALE_AFTER + timedelta(seconds=1))


def test_ending_a_job_that_already_ended_changes_nothing() -> None:
    cancelled = new_job().cancel()

    assert cancelled.fail(JobError.FAILED) == cancelled
    assert cancelled.finish(draft_id=9, now=T0) == cancelled
    assert cancelled.cancel() == cancelled


def test_a_running_job_is_stale_only_after_three_minutes() -> None:
    job = new_job()

    assert not job.is_stale(T0 + timedelta(minutes=3))
    assert job.is_stale(T0 + timedelta(minutes=3, seconds=1))
    assert not job.fail(JobError.FAILED).is_stale(T0 + timedelta(hours=1))


def test_progress_marks_done_active_and_pending_steps_in_pipeline_order() -> None:
    job = new_job().advance(GenerationStep.CALLING_MODEL, T0)
    job = job.advance(GenerationStep.VALIDATING, T0 + timedelta(seconds=9))

    progress = job.progress(T0 + timedelta(seconds=11))

    assert progress.elapsed_seconds == 11
    assert progress.steps == [
        StepProgress(GenerationStep.RETRIEVING, StepState.PENDING, seconds=None),
        StepProgress(GenerationStep.CALLING_MODEL, StepState.DONE, seconds=9),
        StepProgress(GenerationStep.VALIDATING, StepState.ACTIVE, seconds=None),
        StepProgress(GenerationStep.RUNNING_SOLUTION, StepState.PENDING, seconds=None),
        StepProgress(GenerationStep.SAVING, StepState.PENDING, seconds=None),
    ]
    assert progress.done_count == 1


def test_progress_shows_retrieving_for_selected_documents() -> None:
    steps = [s.step for s in new_job().progress(T0).steps]

    assert GenerationStep.RETRIEVING in steps


def test_progress_hides_retrieving_without_selected_documents() -> None:
    job = new_job()
    job = replace(job, form=replace(job.form, document_ids=[]))

    assert GenerationStep.RETRIEVING not in [
        step.step for step in job.progress(T0).steps
    ]
