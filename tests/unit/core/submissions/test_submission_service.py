"""SubmissionService (Run vs Submit, Late, Closed, background judging and
recovery) and SubmissionPostingStats."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    Schedule,
    StudentState,
    TestCase,
    TestCaseKind,
)
from questly.core.assignments.service import (
    AssignmentService,
    ClassroomNotVisibleError,
    PostingNotFoundError,
)
from questly.core.auth.models import Actor, PermissionDeniedError, Role
from questly.core.auth.service import AuthService
from questly.core.classrooms.service import ClassroomService
from questly.core.submissions.models import (
    BatchResult,
    BatchState,
    ClassroomArchivedError,
    EmptyCodeError,
    Execution,
    ExecutionStatus,
    InstructorResultsView,
    Judged,
    NotJudged,
    PostingClosedError,
    Recovery,
    ResubmissionNotAllowedError,
    RunnerUnavailableError,
    StudentSolveView,
    Submission,
    SubmissionExportRow,
    SubmissionNotFoundError,
    SubmissionPendingError,
    SubmissionStatus,
    TestResult,
    Verdict,
)
from questly.core.submissions.service import (
    GIVE_UP,
    LEASE,
    SubmissionService,
    judge,
    outputs_match,
    score_for,
)
from questly.core.submissions.stats import SubmissionPostingStats
from questly.integrations.email import StubEmailSender
from tests.fakes.assignments import (
    FakeAssignmentRepository,
    FakePostingRepository,
    FakeVersionRepository,
)
from tests.fakes.auth import FakeAuthRepository, FakeClock, seed_user
from tests.fakes.classrooms import FakeClassroomRepository, FakeClassroomStats
from tests.fakes.notifications import RecordingMemberNotifier
from tests.fakes.submissions import (
    FakeSubmissionRepository,
    RunnerBackend,
    ScriptedCodeRunner,
)

NOW = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(days=7)
TESTS = [
    TestCase(input_data="1", expected_output="one"),
    TestCase(input_data="2", expected_output="two"),
    TestCase(input_data="3", expected_output="three", kind=TestCaseKind.HIDDEN),
    TestCase(
        input_data="", expected_output="none", kind=TestCaseKind.EDGE, note="empty"
    ),
]
CORRECT = {"1": "one", "2": "two", "3": "three", "": "none"}


class CascadingSubmissionRepository(FakeSubmissionRepository):
    """Can drop a row the way unposting cascades to Submissions in SQL."""

    def drop(self, submission_id: int) -> None:
        del self._rows[submission_id]


class World:
    def __init__(self) -> None:
        self.clock = FakeClock(NOW)
        users = self.users = FakeAuthRepository()
        auth = AuthService(users, StubEmailSender(), self.clock)
        self.classrooms = ClassroomService(
            FakeClassroomRepository(), auth, FakeClassroomStats(), self.clock
        )
        self.submissions = CascadingSubmissionRepository()
        self.versions = FakeVersionRepository()
        assignments = FakeAssignmentRepository()
        self.assignments = AssignmentService(
            assignments,
            self.versions,
            FakePostingRepository(),
            self.classrooms,
            SubmissionPostingStats(self.submissions, assignments),
            self.clock,
            RecordingMemberNotifier(),
        )
        self.runner = ScriptedCodeRunner(CORRECT)
        self.service = self.restart(self.runner)

        def actor(email: str, name: str, role: Role) -> Actor:
            user = seed_user(users, email=email, full_name=name, role=role)
            return Actor(user_id=user.id, role=role, full_name=name, email=email)

        self.teacher = actor(
            "somchai.p@kmitl.ac.th", "Somchai Prasert", Role.INSTRUCTOR
        )
        self.student = actor("66010001@kmitl.ac.th", "Nattapong Suwan", Role.STUDENT)
        self.other = actor("66010002@kmitl.ac.th", "Pimchanok Wong", Role.STUDENT)
        self.outsider = actor("66010009@kmitl.ac.th", "Out Sider", Role.STUDENT)
        self.room = self.classrooms.create(
            self.teacher,
            course_code="01076001",
            course_name="Programming I",
            section="1",
            semester="1/2569",
        )
        self.classrooms.join(self.student, self.room.join_code)
        self.classrooms.join(self.other, self.room.join_code)

    def publish(self, **schedule: object) -> int:
        published = self.assignments.publish(
            self.teacher,
            AssignmentContent(
                title="Numbers to words",
                problem_statement="Print the word.",
                difficulty=Difficulty.EASY,
                test_cases=TESTS,
            ),
            Schedule(deadline=DEADLINE, **schedule),
            [self.room.id],
        )
        return published.assignment.id

    def restart(self, runner: ScriptedCodeRunner) -> SubmissionService:
        """A service as a freshly started process builds it, over the same data."""
        return SubmissionService(
            self.submissions,
            runner,
            runner,
            self.assignments,
            self.classrooms,
            self.versions,
            self.clock,
        )

    def receive(
        self, aid: int, code: str = "print(answer)", who: Actor | None = None
    ) -> Submission:
        """Submit only: the Submission is `received` and nobody judged it yet."""
        if who is None:
            who = self.student
        return self.service.submit(who, self.room.id, aid, code)

    def submit(
        self, aid: int, code: str = "print(answer)", who: Actor | None = None
    ) -> Submission:
        """Submit, then the background judging the request schedules."""
        received = self.receive(aid, code, who)
        self.service.judge(received.id)
        return self.stored(received)

    def stored(self, submission: Submission) -> Submission:
        found = self.submissions.get(submission.id)
        assert found is not None
        return found


@pytest.fixture()
def world() -> World:
    return World()


def test_outputs_match_ignores_trailing_whitespace_only() -> None:
    assert outputs_match("1 2\n3", "1 2  \n3\n\n")
    assert not outputs_match("1 2", "1  2")


def test_judge_maps_runner_status_before_comparing() -> None:
    case = TestCase(input_data="", expected_output="x")
    assert judge(case, Execution(ExecutionStatus.OK, "x\n", "", 0)) is Verdict.PASSED
    assert judge(case, Execution(ExecutionStatus.OK, "y", "", 0)) is (
        Verdict.WRONG_ANSWER
    )
    assert judge(case, Execution(ExecutionStatus.TIME_LIMIT, "x", "", 1)) is (
        Verdict.TIME_LIMIT
    )


def test_score_rounds_down_the_passed_share(world: World) -> None:
    aid = world.publish()
    world.runner.outputs = {"1": "one", "2": "two"}

    submission = world.submit(aid)

    assert (submission.passed_count, submission.score) == (2, 5)
    assert score_for((), 10) == 0


def test_run_executes_sample_tests_only_and_keeps_nothing(world: World) -> None:
    aid = world.publish()

    report = world.service.run(world.student, world.room.id, aid, "print(x)")

    assert [r.kind for r in report.results] == [TestCaseKind.SAMPLE] * 2
    assert world.runner.batches == [("1", "2")]
    assert world.service.submissions(world.student, world.room.id, aid) == ()


def test_submit_grades_every_test_and_counts_attempts(world: World) -> None:
    aid = world.publish()

    first = world.submit(aid, "# crash")
    second = world.submit(aid)

    assert world.runner.batches == [("1", "2", "3", "")] * 2
    assert first.results[0].verdict is Verdict.RUNTIME_ERROR
    assert (first.attempt, first.score) == (1, 0)
    assert (second.attempt, second.score, second.all_passed) == (2, 10, True)
    assert second.version_number == 1 and not second.is_late


def test_latest_submission_counts_even_when_lower(world: World) -> None:
    aid = world.publish()
    world.submit(aid)
    world.submit(aid, "# tle")

    view = world.service.page(world.student, world.room.id, aid)

    assert isinstance(view, StudentSolveView)
    assert view.counted.score == 0
    assert view.counted.results[0].verdict is Verdict.TIME_LIMIT
    standing = world.assignments.problem(world.student, world.room.id, aid).standing
    assert (standing.state, standing.score) == (StudentState.PARTIAL, 0)


def test_after_the_deadline_late_work_is_flagged_not_penalised(world: World) -> None:
    aid = world.publish(allow_late=True)
    world.clock.advance(timedelta(days=8))

    submission = world.submit(aid)

    assert submission.is_late and submission.score == 10
    standing = world.assignments.problem(world.student, world.room.id, aid).standing
    assert standing.state is StudentState.LATE


def test_late_is_decided_at_receive_when_judging_ends_after_the_deadline(
    world: World,
) -> None:
    aid = world.publish(allow_late=True)
    received_at = DEADLINE - timedelta(minutes=1)
    world.clock.current = received_at
    received = world.receive(aid)
    world.clock.advance(timedelta(minutes=5))

    world.service.judge(received.id)

    done = world.stored(received)
    assert world.clock.now() > DEADLINE
    assert done.status is SubmissionStatus.DONE and done.score == 10
    assert done.submitted_at == received_at and done.is_late is False


def test_past_the_deadline_without_late_work_the_posting_is_closed(
    world: World,
) -> None:
    aid = world.publish()
    world.submit(aid)
    world.clock.advance(timedelta(days=8))

    with pytest.raises(PostingClosedError):
        world.submit(aid)
    with pytest.raises(PostingClosedError):
        world.service.run(world.student, world.room.id, aid, "print(1)")
    view = world.service.page(world.student, world.room.id, aid)
    assert view.closed and not view.can_submit


def test_instructor_close_stops_submissions(world: World) -> None:
    aid = world.publish()
    world.assignments.close(world.teacher, world.room.id, aid)

    with pytest.raises(PostingClosedError):
        world.submit(aid)


def test_resubmission_off_allows_one_submit(world: World) -> None:
    aid = world.publish(allow_resubmission=False)
    world.submit(aid)

    with pytest.raises(ResubmissionNotAllowedError):
        world.submit(aid)
    assert not world.service.page(world.student, world.room.id, aid).can_submit


def test_an_outage_releases_the_submission_and_recovery_finishes_it_unmarked(
    world: World,
) -> None:
    aid = world.publish(allow_resubmission=False)
    world.runner.backend.down()

    received = world.submit(aid)
    world.service.recover()

    assert received.status is SubmissionStatus.RECEIVED
    assert world.stored(received).status is SubmissionStatus.RECEIVED
    world.runner.backend.resume()
    world.service.recover()
    done = world.stored(received)
    assert done.status is SubmissionStatus.DONE and done.score == 10
    assert done.recovery is Recovery.NONE and done.attempt == 1


def test_a_run_the_runner_cannot_reach_is_unavailable_and_keeps_nothing(
    world: World,
) -> None:
    aid = world.publish()

    with pytest.raises(RunnerUnavailableError):
        world.service.run(world.student, world.room.id, aid, "# judge down")

    assert world.service.submissions(world.student, world.room.id, aid) == ()


def test_empty_code_is_refused(world: World) -> None:
    aid = world.publish()

    with pytest.raises(EmptyCodeError):
        world.submit(aid, "   ")


def test_an_edit_marks_the_counted_submission_resubmit_needed(world: World) -> None:
    aid = world.publish()
    world.submit(aid)
    world.assignments.edit(world.teacher, aid, reason="Fixed test 3", title="New")

    view = world.service.page(world.student, world.room.id, aid)

    assert view.update is not None and view.update.reason == "Fixed test 3"
    standing = world.assignments.problem(world.student, world.room.id, aid).standing
    assert standing.state is StudentState.RESUBMIT_NEEDED


def test_instructor_cannot_submit_and_outsiders_see_nothing(world: World) -> None:
    aid = world.publish()

    with pytest.raises(PermissionDeniedError):
        world.service.submit(world.teacher, world.room.id, aid, "print(1)")
    with pytest.raises(PostingNotFoundError):
        world.service.page(world.teacher, world.room.id, aid + 99)
    with pytest.raises(ClassroomNotVisibleError):
        world.service.page(world.outsider, world.room.id, aid)


def test_results_view_counts_submitted_late_and_failing_tests(world: World) -> None:
    aid = world.publish()
    world.submit(aid)
    world.runner.outputs = {"1": "one"}
    world.submit(aid, who=world.other)

    view = world.service.page(world.teacher, world.room.id, aid)

    assert isinstance(view, InstructorResultsView)
    assert len(view.submitted) == 2 and view.all_passed == 1
    assert view.average_score == (10 + 2) / 2
    assert [f.rate for f in view.failures] == [0.0, 0.5, 0.5, 0.5]
    summary = world.assignments.problem(world.teacher, world.room.id, aid).summary
    assert (summary.submitted, summary.avg_score) == (2, 6.0)


def test_results_search_matches_name_or_student_number(world: World) -> None:
    aid = world.publish()
    world.submit(aid)
    world.submit(aid, who=world.other)

    def shown(query: str) -> list[str]:
        view = world.service.page(world.teacher, world.room.id, aid, query=query)
        assert isinstance(view, InstructorResultsView)
        return [row.name for row in view.shown]

    assert shown("") == ["Nattapong Suwan", "Pimchanok Wong"]
    assert shown("pimchanok") == ["Pimchanok Wong"]
    assert shown("  SUWAN ") == ["Nattapong Suwan"]
    assert shown("66010002") == ["Pimchanok Wong"]
    assert shown("660100") == ["Nattapong Suwan", "Pimchanok Wong"]
    assert shown("nobody") == []


def test_results_search_filters_the_table_not_the_totals(world: World) -> None:
    aid = world.publish()
    world.submit(aid)
    world.submit(aid, who=world.other)

    view = world.service.page(world.teacher, world.room.id, aid, query="wong")

    assert isinstance(view, InstructorResultsView)
    assert view.query == "wong"
    assert len(view.rows) == 2 and len(view.submitted) == 2
    assert [row.student_number for row in view.shown] == ["66010002"]


def test_get_submission_is_for_its_student_or_the_instructor(world: World) -> None:
    aid = world.publish()
    submission = world.submit(aid)

    assert world.service.get(world.teacher, submission.id) == submission
    assert world.service.get(world.student, submission.id) == submission
    with pytest.raises(SubmissionNotFoundError):
        world.service.get(world.other, submission.id)


def test_archived_classroom_refuses_run_and_submit(world: World) -> None:
    aid = world.publish()
    world.classrooms.archive(world.teacher, world.room.id)

    with pytest.raises(ClassroomArchivedError):
        world.submit(aid)
    with pytest.raises(ClassroomArchivedError):
        world.service.run(world.student, world.room.id, aid, "print(1)")
    view = world.service.page(world.student, world.room.id, aid)
    assert view.archived and view.closed and not view.can_submit


# ---- Background judging and recovery --------------------------------------


class WorkerDied(Exception):
    """Stands for the process dying while the runner works."""


class DyingRunner(ScriptedCodeRunner):
    """Hands the batch over, then the worker dies before collecting it."""

    def collect(self, tokens: Sequence[str]) -> BatchResult:
        raise WorkerDied


class LosingRunner(ScriptedCodeRunner):
    """A runner that loses every batch it was given."""

    def collect(self, tokens: Sequence[str]) -> BatchResult:
        return BatchResult(state=BatchState.GONE)


def _start_then_die(world: World, aid: int) -> Submission:
    """Submit, and the worker dies after the runner took the batch."""
    world.service = world.restart(DyingRunner(CORRECT, world.runner.backend))
    received = world.receive(aid)
    with pytest.raises(WorkerDied):
        world.service.judge(received.id)
    assert world.stored(received).status is SubmissionStatus.JUDGING
    return received


def test_submit_receives_a_pending_submission_judged_in_the_background(
    world: World,
) -> None:
    aid = world.publish()

    received = world.receive(aid)

    assert received.status is SubmissionStatus.RECEIVED
    assert received.score is None and received.results == ()
    assert (received.attempt, received.max_score, received.version_number) == (1, 10, 1)
    assert world.runner.batches == []
    world.service.judge(received.id)
    world.service.judge(received.id)
    assert world.runner.batches == [("1", "2", "3", "")]
    done = world.stored(received)
    assert done.status is SubmissionStatus.DONE and done.score == 10


def test_a_pending_submission_blocks_submit_and_run(world: World) -> None:
    aid = world.publish(allow_resubmission=True)
    pending = world.receive(aid)

    with pytest.raises(SubmissionPendingError):
        world.receive(aid)
    with pytest.raises(SubmissionPendingError):
        world.service.run(world.student, world.room.id, aid, "print(1)")

    view = world.service.page(world.student, world.room.id, aid)
    assert isinstance(view, StudentSolveView)
    assert view.pending == pending and view.counted is None
    assert not view.can_submit
    world.service.judge(pending.id)
    assert world.service.page(world.student, world.room.id, aid).can_submit


def test_a_failed_submission_neither_counts_nor_uses_up_the_one_submit(
    world: World,
) -> None:
    aid = world.publish(allow_resubmission=False)
    world.service = world.restart(LosingRunner(CORRECT))

    lost = world.submit(aid)

    assert lost.status is SubmissionStatus.FAILED and lost.score is None
    assert lost.recovery is Recovery.REJUDGED
    view = world.service.page(world.student, world.room.id, aid)
    assert view.failed == lost and view.counted is None and view.can_submit
    summary = world.assignments.problem(world.teacher, world.room.id, aid).summary
    assert summary.submitted == 0
    world.service = world.restart(world.runner)
    again = world.submit(aid)
    assert (again.status, again.attempt, again.score) == (SubmissionStatus.DONE, 2, 10)
    view = world.service.page(world.student, world.room.id, aid)
    assert view.failed is None and not view.can_submit


def test_after_a_restart_recovery_reconnects_to_the_runners_batch(
    world: World,
) -> None:
    aid = world.publish()
    received = _start_then_die(world, aid)
    world.service = world.restart(ScriptedCodeRunner(CORRECT, world.runner.backend))

    world.service.recover()
    assert world.stored(received).status is SubmissionStatus.JUDGING
    world.clock.advance(LEASE + timedelta(seconds=1))
    world.service.recover()

    done = world.stored(received)
    assert done.status is SubmissionStatus.DONE and done.score == 10
    assert done.recovery is Recovery.RECONNECTED
    assert done.submitted_at == received.submitted_at == NOW


def test_a_runner_that_lost_the_batch_rejudges_on_the_received_version(
    world: World,
) -> None:
    aid = world.publish()
    received = _start_then_die(world, aid)
    world.assignments.edit(
        world.teacher,
        aid,
        reason="One test only",
        test_cases=[TestCase(input_data="9", expected_output="nine")],
    )
    fresh = ScriptedCodeRunner(CORRECT, RunnerBackend())
    world.service = world.restart(fresh)
    world.clock.advance(LEASE + timedelta(seconds=1))

    world.service.recover()

    done = world.stored(received)
    assert done.status is SubmissionStatus.DONE and done.recovery is Recovery.REJUDGED
    assert fresh.batches == [("1", "2", "3", "")]
    assert (done.version_number, len(done.results), done.score) == (1, 4, 10)


def test_a_submission_nobody_started_is_rejudged_once_its_lease_expires(
    world: World,
) -> None:
    aid = world.publish()
    received = world.receive(aid)
    world.clock.advance(LEASE + timedelta(seconds=1))

    world.service.recover()

    done = world.stored(received)
    assert done.status is SubmissionStatus.DONE and done.recovery is Recovery.REJUDGED


def test_a_submission_pending_past_the_give_up_window_fails(world: World) -> None:
    aid = world.publish()
    world.runner.backend.down()
    received = world.submit(aid)
    world.runner.backend.resume()
    world.clock.advance(GIVE_UP)

    world.service.recover()

    assert world.stored(received).status is SubmissionStatus.FAILED
    assert world.runner.batches == []


def test_a_batch_still_running_at_the_give_up_window_fails(world: World) -> None:
    aid = world.publish()

    class StuckRunner(ScriptedCodeRunner):
        def collect(self, tokens: Sequence[str]) -> BatchResult:
            world.clock.advance(timedelta(minutes=1))
            return super().collect(tokens)

    runner = StuckRunner(CORRECT)
    runner.backend.hold()
    world.service = world.restart(runner)

    failed = world.submit(aid)

    assert failed.status is SubmissionStatus.FAILED and failed.score is None
    assert world.clock.now() - failed.submitted_at == GIVE_UP


def test_unposting_mid_judging_stops_the_worker_quietly(world: World) -> None:
    aid = world.publish()
    vanished: list[int] = []

    class UnpostingRunner(ScriptedCodeRunner):
        """The Instructor unposts while the runner works; SQL cascades it."""

        def collect(self, tokens: Sequence[str]) -> BatchResult:
            world.assignments.unpost(world.teacher, world.room.id, aid)
            for submission_id in vanished:
                world.submissions.drop(submission_id)
            return BatchResult(state=BatchState.RUNNING)

    runner = UnpostingRunner(CORRECT)
    world.service = world.restart(runner)
    received = world.receive(aid)
    vanished.append(received.id)

    world.service.judge(received.id)
    world.service.recover()

    assert world.submissions.get(received.id) is None
    assert len(runner.batches) == 1


def test_counted_submissions_and_stats_ignore_a_pending_one(world: World) -> None:
    aid = world.publish()
    world.submit(aid)
    world.receive(aid, "# crash")
    world.receive(aid, who=world.other)

    view = world.service.page(world.teacher, world.room.id, aid)

    assert isinstance(view, InstructorResultsView)
    assert [(r.counted is not None, r.pending) for r in view.rows] == [
        (True, True),
        (False, True),
    ]
    assert len(view.submitted) == 1 and view.average_score == 10
    assert all(row.listed for row in view.rows)
    summary = world.assignments.problem(world.teacher, world.room.id, aid).summary
    assert (summary.submitted, summary.avg_score) == (1, 10.0)
    standing = world.assignments.problem(world.student, world.room.id, aid).standing
    assert standing.score == 10


# ---- PostingStats aggregation, independent of the demo seed ---------------


def _graded(
    student_id: int, passed: int, total: int, *, at: int, late: bool = False
) -> Submission:
    results = tuple(
        TestResult(
            position=n,
            ordinal=n,
            kind=TestCaseKind.SAMPLE,
            note="",
            verdict=Verdict.PASSED if n <= passed else Verdict.WRONG_ANSWER,
            time_seconds=0.0,
            expected_output="",
            actual_output="",
            error="",
        )
        for n in range(1, total + 1)
    )
    return Submission(
        posting_id=1,
        classroom_id=1,
        assignment_id=1,
        student_id=student_id,
        version_number=1,
        code="",
        language="python3",
        submitted_at=NOW + timedelta(minutes=at),
        is_late=late,
        score=score_for(results, 10),
        max_score=10,
        attempt=at,
        results=results,
    )


def _stats(*submissions: Submission) -> SubmissionPostingStats:
    repository = FakeSubmissionRepository()
    for submission in submissions:
        repository.add(submission)
    return SubmissionPostingStats(repository, FakeAssignmentRepository())


def test_stats_use_each_students_latest_submission_only() -> None:
    # Student 1 scored 10 first, then 3 later: the later 3 counts.
    stats = _stats(
        _graded(1, 3, 3, at=1), _graded(2, 1, 3, at=2), _graded(1, 1, 3, at=3)
    )

    assert stats.score(1, 1) == 3
    assert stats.posting_summary(1).submitted == 2
    assert stats.standing(1, 1).state is StudentState.PARTIAL


def test_stats_order_by_submitted_at_not_insertion() -> None:
    stats = _stats(_graded(1, 1, 3, at=5), _graded(1, 3, 3, at=1))

    assert stats.score(1, 1) == 3


def test_scores_round_down_and_the_average_rounds_to_one_decimal() -> None:
    # 2/3 of 10 -> 6 (floor), 1/3 -> 3, 3/3 -> 10; average 19/3 = 6.33 -> 6.3.
    stats = _stats(
        _graded(1, 2, 3, at=1), _graded(2, 1, 3, at=2), _graded(3, 3, 3, at=3)
    )

    assert [stats.score(1, s) for s in (1, 2, 3)] == [6, 3, 10]
    assert stats.posting_summary(1).avg_score == 6.3


def test_fail_rate_is_students_failing_any_test_over_students_who_submitted() -> None:
    stats = _stats(
        _graded(1, 3, 3, at=1),
        _graded(2, 2, 3, at=2),
        _graded(3, 0, 3, at=3),
        _graded(4, 3, 3, at=4, late=True),
    )

    assert stats.fail_rate(1) == 2 / 4
    assert stats.standing(1, 4).state is StudentState.LATE
    assert stats.fail_rate(99) is None
    assert stats.posting_summary(99).avg_score is None


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ([6, 6, 6, 7], 6.3),  # 6.25: round() and "%.1f" would say 6.2
        ([8] * 11 + [9] * 9, 8.5),  # 8.45: "%.1f" would say 8.4
    ],
)
def test_average_rounds_half_up_not_to_even(scores: list[int], expected: float) -> None:
    # With 10 tests and max score 10, a score equals the tests passed.
    stats = _stats(
        *(
            _graded(student, score, 10, at=student)
            for student, score in enumerate(scores, start=1)
        )
    )

    assert stats.posting_summary(1).avg_score == expected


def test_export_submissions_by_owner_lists_student_and_verdict(world: World) -> None:
    aid = world.publish()
    submission = world.submit(aid)

    rows = world.service.export_submissions(world.teacher, world.room.id, aid)

    assert rows == (
        SubmissionExportRow(
            id=submission.id,
            student_number="66010001",
            student_name="Nattapong Suwan",
            attempt=1,
            version_number=submission.version_number,
            submitted_at=submission.submitted_at,
            status=SubmissionStatus.DONE,
            outcome=Judged(score=10, passed_count=4, total_tests=4),
            max_score=10,
            is_late=False,
        ),
    )


def test_export_submissions_has_no_verdict_until_a_submission_is_done(
    world: World,
) -> None:
    aid = world.publish()
    world.runner.backend.down()
    lost = world.submit(aid, who=world.other)
    world.runner.backend.resume()
    world.clock.advance(GIVE_UP)
    world.service.recover()
    assert world.stored(lost).status is SubmissionStatus.FAILED
    pending = world.receive(aid)
    world.classrooms.join(world.outsider, world.room.join_code)
    done = world.submit(aid, who=world.outsider)

    rows = {
        row.id: row
        for row in world.service.export_submissions(world.teacher, world.room.id, aid)
    }

    assert rows[lost.id].status is SubmissionStatus.FAILED
    assert rows[lost.id].outcome == NotJudged()
    assert rows[pending.id].status is SubmissionStatus.RECEIVED
    assert rows[pending.id].outcome == NotJudged()
    assert rows[done.id].outcome == Judged(score=10, passed_count=4, total_tests=4)


def test_export_submissions_leaves_number_blank_for_an_email_without_digits(
    world: World,
) -> None:
    aid = world.publish()
    named = seed_user(
        world.users,
        email="visiting.lecturer@kmitl.ac.th",
        full_name="Visiting Student",
        role=Role.STUDENT,
    )
    visitor = Actor(named.id, Role.STUDENT, named.full_name, named.email)
    world.classrooms.join(visitor, world.room.join_code)
    world.submit(aid, who=visitor)

    (row,) = world.service.export_submissions(world.teacher, world.room.id, aid)

    assert (row.student_number, row.student_name) == ("", "Visiting Student")


def test_export_submissions_by_student_raises_permission_denied(world: World) -> None:
    aid = world.publish()
    with pytest.raises(PermissionDeniedError):
        world.service.export_submissions(world.student, world.room.id, aid)


def test_export_submissions_by_outsider_raises_classroom_not_visible(
    world: World,
) -> None:
    aid = world.publish()
    with pytest.raises(ClassroomNotVisibleError):
        world.service.export_submissions(world.outsider, world.room.id, aid)
