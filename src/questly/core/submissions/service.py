"""Submission and Run use cases (ADR-0007 §5) and the T-02 numbers (§7).

Submit only receives a Submission; `judge` (a background task) and `recover`
(the sweeper) carry it through `received` -> `judging` -> `done` or `failed`
(ADR-0011).
"""

from datetime import datetime, timedelta

from questly.core.assignments.models import (
    InstructorProblem,
    StudentProblem,
    TestCase,
    TestCaseKind,
)
from questly.core.assignments.ports import Clock
from questly.core.assignments.service import (
    AssignmentNotFoundError,
    ClassroomNotVisibleError,
    PostingNotFoundError,
)
from questly.core.auth.models import Actor, PermissionDeniedError
from questly.core.classrooms.models import MemberView
from questly.core.submissions.models import (
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
    RunReport,
    StudentResult,
    StudentSolveView,
    Submission,
    SubmissionExportRow,
    SubmissionNotFoundError,
    SubmissionPendingError,
    SubmissionStatus,
    TestFailure,
    TestResult,
    Verdict,
)
from questly.core.submissions.ports import (
    BatchRunner,
    CodeRunner,
    Problems,
    Roster,
    SubmissionRepository,
    VersionLookup,
)

# A judging worker holds a pending Submission for LEASE and renews it while the
# runner works; once the lease lapses the sweeper may take the Submission over.
# A Submission still pending GIVE_UP after it was received is failed.
LEASE = timedelta(seconds=90)
GIVE_UP = timedelta(minutes=15)


def outputs_match(expected: str, actual: str) -> bool:
    """Equal after dropping trailing spaces on each line and trailing blank lines."""

    def normalise(text: str) -> list[str]:
        return [line.rstrip() for line in text.rstrip().splitlines()]

    return normalise(expected) == normalise(actual)


def judge(test_case: TestCase, execution: Execution) -> Verdict:
    if execution.status is ExecutionStatus.TIME_LIMIT:
        return Verdict.TIME_LIMIT
    if execution.status is ExecutionStatus.RUNTIME_ERROR:
        return Verdict.RUNTIME_ERROR
    if outputs_match(test_case.expected_output, execution.stdout):
        return Verdict.PASSED
    return Verdict.WRONG_ANSWER


def _export_row(
    submission: Submission, roster: dict[int, MemberView]
) -> SubmissionExportRow:
    """One submissions.csv row; only a `done` Submission has a verdict."""
    if submission.id is None:
        raise ValueError("a stored Submission has an id")
    outcome: Judged | NotJudged = NotJudged()
    if submission.score is not None:
        outcome = Judged(
            score=submission.score,
            passed_count=submission.passed_count,
            total_tests=len(submission.results),
        )
    student_number = ""
    student_name = ""
    member = roster.get(submission.student_id)
    if member is not None:
        student_name = member.full_name
        if member.student_number is not None:
            student_number = member.student_number
    return SubmissionExportRow(
        id=submission.id,
        student_number=student_number,
        student_name=student_name,
        attempt=submission.attempt,
        version_number=submission.version_number,
        submitted_at=submission.submitted_at,
        status=submission.status,
        outcome=outcome,
        max_score=submission.max_score,
        is_late=submission.is_late,
    )


def score_for(results: tuple[TestResult, ...], max_score: int) -> int:
    """Share of passed tests times max score, rounded down; no tests scores 0."""
    if not results:
        return 0
    passed = sum(1 for result in results if result.passed)
    return max_score * passed // len(results)


def ordinals(test_cases: list[TestCase]) -> list[int]:
    """1-based place of each Test Case among those of its kind ("Hidden 2")."""
    seen: dict[TestCaseKind, int] = {}
    places = []
    for test_case in test_cases:
        seen[test_case.kind] = seen.get(test_case.kind, 0) + 1
        places.append(seen[test_case.kind])
    return places


def grade(
    test_cases: list[TestCase], executions: tuple[Execution, ...]
) -> tuple[TestResult, ...]:
    """One TestResult per Test Case, from the Execution run on its input."""
    return tuple(
        TestResult(
            position=position,
            ordinal=ordinal,
            kind=test_case.kind,
            note=test_case.note,
            verdict=judge(test_case, execution),
            time_seconds=execution.time_seconds,
            expected_output=test_case.expected_output,
            actual_output=execution.stdout,
            error=execution.stderr,
        )
        for position, (test_case, ordinal, execution) in enumerate(
            zip(test_cases, ordinals(test_cases), executions, strict=True),
            start=1,
        )
    )


# What a page or route answers with 404: the Posting is not there for the Actor.
NOT_VISIBLE = (
    AssignmentNotFoundError,
    ClassroomNotVisibleError,
    PostingNotFoundError,
    SubmissionNotFoundError,
)


class SubmissionService:
    def __init__(
        self,
        repository: SubmissionRepository,
        runner: CodeRunner,
        batch_runner: BatchRunner,
        problems: Problems,
        roster: Roster,
        versions: VersionLookup,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._batch_runner = batch_runner
        self._problems = problems
        self._roster = roster
        self._versions = versions
        self._clock = clock

    # ---- Pages --------------------------------------------------------------

    def page(
        self, actor: Actor, classroom_id: int, assignment_id: int, query: str = ""
    ) -> StudentSolveView | InstructorResultsView:
        """S-02 for a Member, T-02 for the owning Instructor.

        `query` is the T-02 search; a Member's page ignores it.
        """
        problem = self._problems.problem(actor, classroom_id, assignment_id)
        if isinstance(problem, InstructorProblem):
            return self._results(actor, classroom_id, problem, query.strip())
        return self._solve(actor, classroom_id, problem)

    def submissions(
        self, actor: Actor, classroom_id: int, assignment_id: int
    ) -> tuple[Submission, ...]:
        """Every Student's for the owner; a Member's own (S-02g). Oldest first."""
        problem = self._problems.problem(actor, classroom_id, assignment_id)
        if isinstance(problem, InstructorProblem):
            return self._repository.list_for_posting(problem.posting.id)
        return self._repository.list_for_student(problem.posting.id, actor.user_id)

    def export_submissions(
        self, actor: Actor, classroom_id: int, assignment_id: int
    ) -> tuple[SubmissionExportRow, ...]:
        """Export all submissions on a Posting; restricted to the Classroom owner."""
        problem = self._problems.problem(actor, classroom_id, assignment_id)
        if not isinstance(problem, InstructorProblem):
            raise PermissionDeniedError
        roster = {m.user_id: m for m in self._roster.members(actor, classroom_id)}
        submissions = self._repository.list_for_posting(problem.posting.id)
        return tuple(_export_row(s, roster) for s in submissions)

    def get(self, actor: Actor, submission_id: int) -> Submission:
        """A Student's own Submission, or any on the Instructor's Posting."""
        submission = self._repository.get(submission_id)
        if submission is None:
            raise SubmissionNotFoundError
        if submission.student_id == actor.user_id:
            return submission
        try:
            problem = self._problems.problem(
                actor, submission.classroom_id, submission.assignment_id
            )
        except NOT_VISIBLE:
            raise SubmissionNotFoundError from None
        if not isinstance(problem, InstructorProblem):
            raise SubmissionNotFoundError
        return submission

    # ---- Run and Submit -----------------------------------------------------

    def run(
        self, actor: Actor, classroom_id: int, assignment_id: int, code: str
    ) -> RunReport:
        """Sample Test Cases only; not graded, not stored (ADR-0007 §5.3).

        Refused while a Submission is being judged: S-02 is read-only then.
        """
        problem = self._student_problem(actor, classroom_id, assignment_id)
        self._require_open(actor, problem, self._clock.now())
        self._require_code(code)
        self._require_none_pending(
            self._repository.list_for_student(problem.posting.id, actor.user_id)
        )
        assignment = problem.assignment
        samples = [
            test_case
            for test_case in assignment.test_cases
            if test_case.kind is TestCaseKind.SAMPLE
        ]
        settings = assignment.settings
        executions = self._runner.run_many(
            code=code,
            language=settings.language.value,
            stdins=[test_case.input_data for test_case in samples],
            time_limit_seconds=settings.time_limit_ms / 1000,
        )
        return RunReport(code=code, results=grade(samples, executions))

    def submit(
        self, actor: Actor, classroom_id: int, assignment_id: int, code: str
    ) -> Submission:
        """Keep the code as a `received` Submission; `judge` grades it later.

        Late, the max score and the Version are fixed here, at receive time,
        so a slow judge cannot make an on-time Submit Late. A `failed`
        Submission does not use up a one-Submit Posting.
        """
        received_at = self._clock.now()
        problem = self._student_problem(actor, classroom_id, assignment_id)
        self._require_open(actor, problem, received_at)
        self._require_code(code)
        posting = problem.posting
        previous = self._repository.list_for_student(posting.id, actor.user_id)
        self._require_none_pending(previous)
        used = [s for s in previous if s.status is not SubmissionStatus.FAILED]
        if used and not posting.schedule.allow_resubmission:
            raise ResubmissionNotAllowedError
        assignment = problem.assignment
        return self._repository.receive(
            Submission(
                posting_id=posting.id,
                classroom_id=classroom_id,
                assignment_id=assignment.id,
                student_id=actor.user_id,
                version_number=assignment.current_version,
                code=code,
                language=assignment.settings.language.value,
                submitted_at=received_at,
                is_late=received_at > posting.schedule.deadline,
                score=None,
                max_score=posting.schedule.max_score,
                attempt=max((s.attempt for s in previous), default=0) + 1,
                status=SubmissionStatus.RECEIVED,
            ),
            lease_until=received_at + LEASE,
        )

    # ---- Judging: no Actor, `submit` authorized it. Only the BackgroundTask a
    # Submit schedules and the sweeper call these. ---------------------------

    def judge(self, submission_id: int) -> None:
        """Grade a Submission `submit` just received, holding its lease."""
        submission = self._repository.get(submission_id)
        if submission is None or submission.status is not SubmissionStatus.RECEIVED:
            return
        self._carry(submission, (), Recovery.NONE)

    def recover(self) -> None:
        """Take over every due pending Submission, oldest first, and grade it.

        Due means released (the runner was unreachable) or with an expired
        lease (its worker vanished); only the latter sets the Recovery marker.
        Stops early while the runner is unreachable.
        """
        while True:
            now = self._clock.now()
            claims = self._repository.claim_due(now, now + LEASE, limit=1)
            if not claims:
                return
            (claim,) = claims
            submission = claim.submission
            recovery = submission.recovery
            if claim.interrupted:
                recovery = recovery.after(
                    Recovery.RECONNECTED
                    if submission.status is SubmissionStatus.JUDGING
                    else Recovery.REJUDGED
                )
            if not self._carry(submission, claim.tokens, recovery):
                return

    # ---- Internals ----------------------------------------------------------

    def _student_problem(
        self, actor: Actor, classroom_id: int, assignment_id: int
    ) -> StudentProblem:
        problem = self._problems.problem(actor, classroom_id, assignment_id)
        if not isinstance(problem, StudentProblem):
            raise PermissionDeniedError
        return problem

    def _require_open(
        self, actor: Actor, problem: StudentProblem, now: datetime
    ) -> None:
        if self._roster.is_archived(actor, problem.posting.classroom_id):
            raise ClassroomArchivedError
        if problem.posting.is_closed(now):
            raise PostingClosedError

    @staticmethod
    def _require_code(code: str) -> None:
        if not code.strip():
            raise EmptyCodeError

    @staticmethod
    def _require_none_pending(submissions: tuple[Submission, ...]) -> None:
        if any(submission.status.pending for submission in submissions):
            raise SubmissionPendingError

    def _carry(
        self, submission: Submission, tokens: tuple[str, ...], recovery: Recovery
    ) -> bool:
        """Move a pending Submission this worker holds the lease on to `done`
        or `failed`, against the Version it was received on.

        Returns False when the runner is unreachable: the lease is released so
        a later sweep retries. Any repository write answering False means the
        row was unposted or another worker moved it on, so this one stops.
        """
        submission_id = submission.id
        if submission_id is None:
            raise ValueError("only a stored Submission is judged")
        version = self._versions.get(
            submission.assignment_id, submission.version_number
        )
        if version is None:
            self._repository.failed(submission_id)
            return True
        judging = submission.status is SubmissionStatus.JUDGING
        lost_once = False
        while True:
            now = self._clock.now()
            if now - submission.submitted_at >= GIVE_UP:
                self._repository.failed(submission_id)
                return True
            try:
                if not judging:
                    tokens = self._batch_runner.start(
                        code=submission.code,
                        language=submission.language,
                        stdins=[
                            test_case.input_data for test_case in version.test_cases
                        ],
                        time_limit_seconds=version.settings.time_limit_ms / 1000,
                    )
                    if not self._repository.started(
                        submission_id,
                        tokens=tokens,
                        lease_until=self._clock.now() + LEASE,
                        recovery=recovery,
                    ):
                        return True
                    judging = True
                batch = self._batch_runner.collect(tokens)
            except RunnerUnavailableError:
                self._repository.released(submission_id)
                return False
            if batch.state is BatchState.RUNNING:
                if not self._repository.renew(
                    submission_id, lease_until=self._clock.now() + LEASE
                ):
                    return True
            elif batch.state is BatchState.GONE:
                if lost_once:
                    self._repository.failed(submission_id)
                    return True
                if not self._repository.requeued(submission_id):
                    return True
                lost_once, judging = True, False
                recovery = recovery.after(Recovery.REJUDGED)
            else:
                results = grade(version.test_cases, batch.executions)
                self._repository.finished(
                    submission_id,
                    tokens=tokens,
                    score=score_for(results, submission.max_score),
                    results=results,
                    recovery=recovery,
                )
                return True

    def _solve(
        self, actor: Actor, classroom_id: int, problem: StudentProblem
    ) -> StudentSolveView:
        posting = problem.posting
        assignment = problem.assignment
        submissions = self._repository.list_for_student(posting.id, actor.user_id)
        counted = _latest_done(submissions)
        pending = next((s for s in submissions if s.status.pending), None)
        latest = submissions[-1] if submissions else None
        used = [s for s in submissions if s.status is not SubmissionStatus.FAILED]
        update = None
        if counted is not None and counted.version_number < assignment.current_version:
            update = self._problems.current_version(actor, classroom_id, assignment.id)
        archived = self._roster.is_archived(actor, classroom_id)
        closed = archived or posting.is_closed(self._clock.now())
        samples = tuple(
            test_case
            for test_case in assignment.test_cases
            if test_case.kind is TestCaseKind.SAMPLE
        )
        return StudentSolveView(
            problem=problem,
            samples=samples,
            hidden_count=len(assignment.test_cases) - len(samples),
            submissions=submissions,
            counted=counted,
            pending=pending,
            failed=latest
            if latest is not None and latest.status is SubmissionStatus.FAILED
            else None,
            update=update,
            closed=closed,
            archived=archived,
            can_submit=not closed
            and pending is None
            and (posting.schedule.allow_resubmission or not used),
        )

    def _results(
        self,
        actor: Actor,
        classroom_id: int,
        problem: InstructorProblem,
        query: str,
    ) -> InstructorResultsView:
        by_student: dict[int, list[Submission]] = {}
        for submission in self._repository.list_for_posting(problem.posting.id):
            by_student.setdefault(submission.student_id, []).append(submission)
        rows = tuple(
            StudentResult(
                student_id=member.user_id,
                name=member.full_name,
                student_number=member.student_number,
                counted=_latest_done(tuple(by_student.get(member.user_id, ()))),
                pending=any(
                    s.status.pending for s in by_student.get(member.user_id, ())
                ),
                attempts=len(by_student.get(member.user_id, [])),
            )
            for member in self._roster.members(actor, classroom_id)
        )
        counted = [row.counted for row in rows if row.counted is not None]
        test_cases = problem.assignment.test_cases
        failures = tuple(
            TestFailure(
                position=position,
                ordinal=ordinal,
                kind=test_case.kind,
                note=test_case.note,
                rate=_failing_share(counted, position),
            )
            for position, (test_case, ordinal) in enumerate(
                zip(test_cases, ordinals(test_cases), strict=True), start=1
            )
        )
        return InstructorResultsView(
            problem=problem,
            rows=rows,
            failures=failures,
            versions=tuple(self._problems.versions(actor, problem.assignment.id)),
            closed=problem.posting.is_closed(self._clock.now()),
            shown=tuple(row for row in rows if _matches_query(row, query)),
            query=query,
        )


def _matches_query(row: StudentResult, query: str) -> bool:
    """T-02 search: part of the name (any case) or of the student number."""
    if not query:
        return True
    if query.casefold() in row.name.casefold():
        return True
    return row.student_number is not None and query in row.student_number


def _failing_share(counted: list[Submission], position: int) -> float | None:
    """Share of Counted Submissions that ran this test and failed it."""
    ran = [
        result
        for submission in counted
        for result in submission.results
        if result.position == position
    ]
    if not ran:
        return None
    return sum(1 for result in ran if not result.passed) / len(ran)


def counted_submissions(
    submissions: tuple[Submission, ...],
) -> tuple[Submission, ...]:
    """Each Student's Counted Submission: their latest `done` one.

    A pending or `failed` Submission has no score and never counts.
    """
    latest: dict[int, Submission] = {}
    for submission in sorted(submissions, key=_submitted_order):
        if submission.status is SubmissionStatus.DONE:
            latest[submission.student_id] = submission
    return tuple(latest.values())


def _latest_done(submissions: tuple[Submission, ...]) -> Submission | None:
    """One Student's Counted Submission, or None before their first `done` one."""
    counted = counted_submissions(submissions)
    return counted[-1] if counted else None


def _submitted_order(submission: Submission) -> tuple[datetime, int]:
    return (submission.submitted_at, submission.attempt)
