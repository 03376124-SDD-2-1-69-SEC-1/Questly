"""Assignment use cases: publish, Postings, Versions (ADR-0007 §3).

Every public method takes the Actor first. Visibility follows ADR-0007 §9.6:
a Classroom the Actor neither owns nor belongs to is ClassroomNotVisibleError
(404); a Member asking for an Instructor use case is PermissionDeniedError (403).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from questly.core.assignments.models import (
    Assignment,
    AssignmentContent,
    AssignmentVersion,
    Difficulty,
    FailRate,
    InstructorProblem,
    InstructorProblemList,
    InstructorSummary,
    JudgingSettings,
    Posting,
    ProblemFilter,
    PublishedAssignment,
    Schedule,
    ScoreRow,
    StudentProblem,
    StudentProblemList,
    StudentStanding,
    StudentState,
    StudentSummary,
    TestCase,
)
from questly.core.assignments.ports import (
    AssignmentRepository,
    ClassroomAccess,
    Clock,
    MemberNotifier,
    PostingRepository,
    PostingStats,
    VersionRepository,
)
from questly.core.auth.models import Actor, PermissionDeniedError, Role
from questly.core.classrooms.models import ClassroomRole
from questly.core.notifications.models import NotificationKind, NotificationPayload
from questly.core.rounding import average

FIRST_VERSION_REASON = "Published"
MOST_FAILED_LIMIT = 5


class AssignmentNotFoundError(Exception):
    """Missing, or not visible to this Actor."""


class ClassroomNotVisibleError(Exception):
    """The Classroom is missing or the Actor is not in it (404)."""


class PostingNotFoundError(Exception):
    """The Assignment is not published to this Classroom."""


class VersionNotFoundError(Exception):
    pass


class TestCaseNotFoundError(Exception):
    pass


class _Unset:
    """Marks a PATCH field left out of the request body."""


UNSET = _Unset()


class AssignmentService:
    def __init__(
        self,
        assignments: AssignmentRepository,
        versions: VersionRepository,
        postings: PostingRepository,
        classrooms: ClassroomAccess,
        stats: PostingStats,
        clock: Clock,
        notifier: MemberNotifier,
    ) -> None:
        self._assignments = assignments
        self._versions = versions
        self._postings = postings
        self._classrooms = classrooms
        self._stats = stats
        self._clock = clock
        self._notifier = notifier

    # ---- Publishing ------------------------------------------------------

    def publish(
        self,
        actor: Actor,
        content: AssignmentContent,
        schedule: Schedule,
        classroom_ids: list[int],
        artifact_id: int | None = None,
    ) -> PublishedAssignment:
        """T-04 step 4: create the Assignment, Version 1, one Posting per Classroom."""
        if actor.role is not Role.INSTRUCTOR:
            raise PermissionDeniedError
        _validate_content(content)
        _validate_schedule(schedule)
        if not classroom_ids:
            raise ValueError("choose at least one classroom")
        for classroom_id in classroom_ids:
            self._require_owner(actor, classroom_id)

        now = self._clock.now()
        assignment = self._assignments.create(
            Assignment(
                title=content.title.strip(),
                problem_statement=content.problem_statement.strip(),
                difficulty=content.difficulty,
                test_cases=_ordered(content.test_cases),
                topic_id=content.topic_id,
                settings=content.settings,
                owner_id=actor.user_id,
                artifact_id=artifact_id,
                current_version=1,
            )
        )
        self._versions.append(_snapshot(assignment, FIRST_VERSION_REASON, now))
        postings = [
            self._postings.create(
                Posting(
                    classroom_id=classroom_id,
                    assignment_id=assignment.id,
                    schedule=schedule,
                    published_at=now,
                )
            )
            for classroom_id in sorted(set(classroom_ids))
        ]
        for posting in postings:
            self._notifier.notify_members(
                posting.classroom_id,
                NotificationKind.ASSIGNMENT_PUBLISHED,
                _payload(assignment, posting.classroom_id),
            )
        return PublishedAssignment(assignment=assignment, postings=postings)

    # ---- Classroom views (T-01, S-01) -----------------------------------

    def problems(
        self,
        actor: Actor,
        classroom_id: int,
        problem_filter: ProblemFilter = ProblemFilter.ALL,
    ) -> InstructorProblemList | StudentProblemList:
        """T-01 Problems for the owner, S-01 Problems (filtered) for a Member."""
        role = self._visible_role(actor, classroom_id)
        numbered = self._numbered_postings(classroom_id)
        if role is ClassroomRole.OWNER:
            return InstructorProblemList(
                student_count=len(self._classrooms.member_ids(actor, classroom_id)),
                problems=[
                    InstructorProblem(
                        number=number,
                        posting=posting,
                        assignment=assignment,
                        summary=self._stats.posting_summary(posting.id),
                    )
                    for number, posting, assignment in numbered
                ],
            )
        now = self._clock.now()
        rows = [
            StudentProblem(
                number=number,
                posting=posting,
                assignment=assignment,
                standing=self._student_standing(posting, actor.user_id, now),
            )
            for number, posting, assignment in numbered
        ]
        return StudentProblemList(
            problems=[row for row in rows if _matches(row, problem_filter)],
            total=len(rows),
        )

    def summary(
        self, actor: Actor, classroom_id: int
    ) -> InstructorSummary | StudentSummary:
        """T-01 Summary for the owner, S-01 Summary for a Member."""
        role = self._visible_role(actor, classroom_id)
        numbered = self._numbered_postings(classroom_id)
        postings = [posting for _, posting, _ in numbered]
        if role is ClassroomRole.OWNER:
            return self._instructor_summary(actor, classroom_id, numbered)
        standings = [self._stats.standing(p.id, actor.user_id) for p in postings]
        scores = [s.score for s in standings if s.score is not None]
        return StudentSummary(
            solved=sum(1 for s in standings if s.state is not StudentState.NOT_STARTED),
            passed=sum(1 for s in standings if s.state is StudentState.PASSED),
            total=len(postings),
            avg_score=average(scores),
        )

    def export_summary(self, actor: Actor, classroom_id: int) -> InstructorSummary:
        """Export the classroom score table as CSV; restricted to the owner."""
        self._require_owner(actor, classroom_id)
        numbered = self._numbered_postings(classroom_id)
        return self._instructor_summary(actor, classroom_id, numbered)

    def problem(
        self, actor: Actor, classroom_id: int, assignment_id: int
    ) -> InstructorProblem | StudentProblem:
        """One Posting in a Classroom, shaped for the Actor's role."""
        role = self._visible_role(actor, classroom_id)
        posting = self._posting(classroom_id, assignment_id)
        number = next(
            n for n, p, _ in self._numbered_postings(classroom_id) if p.id == posting.id
        )
        assignment = self._assignment(assignment_id)
        if role is ClassroomRole.OWNER:
            return InstructorProblem(
                number=number,
                posting=posting,
                assignment=assignment,
                summary=self._stats.posting_summary(posting.id),
            )
        return StudentProblem(
            number=number,
            posting=posting,
            assignment=assignment,
            standing=self._student_standing(posting, actor.user_id, self._clock.now()),
        )

    # ---- Posting settings ------------------------------------------------

    def update_posting(
        self,
        actor: Actor,
        classroom_id: int,
        assignment_id: int,
        *,
        deadline: datetime | _Unset = UNSET,
        max_score: int | _Unset = UNSET,
        allow_late: bool | _Unset = UNSET,
        allow_resubmission: bool | _Unset = UNSET,
    ) -> Posting:
        self._require_owner(actor, classroom_id)
        posting = self._posting(classroom_id, assignment_id)
        current = posting.schedule
        schedule = Schedule(
            deadline=current.deadline if isinstance(deadline, _Unset) else deadline,
            max_score=current.max_score if isinstance(max_score, _Unset) else max_score,
            allow_late=current.allow_late
            if isinstance(allow_late, _Unset)
            else allow_late,
            allow_resubmission=current.allow_resubmission
            if isinstance(allow_resubmission, _Unset)
            else allow_resubmission,
        )
        _validate_schedule(schedule)
        return self._postings.update(replace(posting, schedule=schedule))

    def close(self, actor: Actor, classroom_id: int, assignment_id: int) -> Posting:
        """T-02 "Close submissions"."""
        self._require_owner(actor, classroom_id)
        posting = self._posting(classroom_id, assignment_id)
        if posting.closed_at is not None:
            return posting
        return self._postings.update(replace(posting, closed_at=self._clock.now()))

    def unpost(self, actor: Actor, classroom_id: int, assignment_id: int) -> None:
        self._require_owner(actor, classroom_id)
        self._postings.delete(self._posting(classroom_id, assignment_id).id)

    def notify_students(
        self, actor: Actor, classroom_id: int, assignment_id: int
    ) -> None:
        """T-02c "Notify students": every Member of this Classroom hears of it."""
        self._require_owner(actor, classroom_id)
        self._posting(classroom_id, assignment_id)
        self._notifier.notify_members(
            classroom_id,
            NotificationKind.ASSIGNMENT_NOTIFIED,
            _payload(self._assignment(assignment_id), classroom_id),
        )

    # ---- Content and Versions ----------------------------------------------

    def get(self, actor: Actor, assignment_id: int) -> Assignment:
        """The owner, or a Member of a Classroom it is posted to."""
        assignment = self._assignment(assignment_id)
        if assignment.owner_id == actor.user_id:
            return assignment
        for posting in self._postings.list_for_assignment(assignment_id):
            if self._classrooms.role_of(actor, posting.classroom_id) is not (
                ClassroomRole.OUTSIDER
            ):
                return assignment
        raise AssignmentNotFoundError

    def edit(
        self,
        actor: Actor,
        assignment_id: int,
        *,
        reason: str,
        title: str | _Unset = UNSET,
        problem_statement: str | _Unset = UNSET,
        difficulty: Difficulty | _Unset = UNSET,
        topic_id: int | None | _Unset = UNSET,
        test_cases: list[TestCase] | _Unset = UNSET,
        settings: JudgingSettings | _Unset = UNSET,
        extend_deadlines: dict[int, datetime] | None = None,
    ) -> Assignment:
        """T-05: a new Version for every Posting; deadlines move only where chosen."""
        current = self._owned_assignment(actor, assignment_id)
        if not reason.strip():
            raise ValueError("a reason for the change is required")
        merged = replace(
            current,
            title=current.title if isinstance(title, _Unset) else title.strip(),
            problem_statement=current.problem_statement
            if isinstance(problem_statement, _Unset)
            else problem_statement.strip(),
            difficulty=current.difficulty
            if isinstance(difficulty, _Unset)
            else difficulty,
            topic_id=current.topic_id if isinstance(topic_id, _Unset) else topic_id,
            test_cases=current.test_cases
            if isinstance(test_cases, _Unset)
            else _ordered(test_cases),
            settings=current.settings if isinstance(settings, _Unset) else settings,
            current_version=current.current_version + 1,
        )
        _validate_content(
            AssignmentContent(
                title=merged.title,
                problem_statement=merged.problem_statement,
                difficulty=merged.difficulty,
                test_cases=merged.test_cases,
            )
        )
        if extend_deadlines is None:
            extend_deadlines = {}
        own_postings = {
            p.id: p for p in self._postings.list_for_assignment(assignment_id)
        }
        unknown = set(extend_deadlines) - set(own_postings)
        if unknown:
            raise PostingNotFoundError
        updated = self._assignments.update(merged)
        self._versions.append(_snapshot(updated, reason.strip(), self._clock.now()))
        for posting_id, deadline in extend_deadlines.items():
            posting = own_postings[posting_id]
            self._postings.update(
                replace(posting, schedule=replace(posting.schedule, deadline=deadline))
            )
        for posting in own_postings.values():
            self._notifier.notify_members(
                posting.classroom_id,
                NotificationKind.ASSIGNMENT_UPDATED,
                {**_payload(updated, posting.classroom_id), "reason": reason.strip()},
            )
        return updated

    def versions(self, actor: Actor, assignment_id: int) -> list[AssignmentVersion]:
        """T-02b Version history, newest first."""
        self._owned_assignment(actor, assignment_id)
        return sorted(
            self._versions.list_for(assignment_id),
            key=lambda version: version.number,
            reverse=True,
        )

    def current_version(
        self, actor: Actor, classroom_id: int, assignment_id: int
    ) -> AssignmentVersion:
        """The Version a Posting shows now; S-02e reads its reason."""
        self._visible_role(actor, classroom_id)
        posting = self._posting(classroom_id, assignment_id)
        assignment = self._assignment(posting.assignment_id)
        found = self._versions.get(assignment.id, assignment.current_version)
        if found is None:
            raise VersionNotFoundError
        return found

    def version(
        self, actor: Actor, assignment_id: int, number: int
    ) -> AssignmentVersion:
        self._owned_assignment(actor, assignment_id)
        found = self._versions.get(assignment_id, number)
        if found is None:
            raise VersionNotFoundError
        return found

    def postings_of(self, actor: Actor, assignment_id: int) -> list[Posting]:
        """Every Posting of an owned Assignment (T-05a lists them)."""
        self._owned_assignment(actor, assignment_id)
        return self._postings.list_for_assignment(assignment_id)

    # ---- Test cases (each write is a new Version) --------------------------

    def test_cases(self, actor: Actor, assignment_id: int) -> list[TestCase]:
        return self._owned_assignment(actor, assignment_id).test_cases

    def test_case(
        self, actor: Actor, assignment_id: int, test_case_id: int
    ) -> TestCase:
        return _find(self.test_cases(actor, assignment_id), test_case_id)

    def add_test_case(
        self, actor: Actor, assignment_id: int, test_case: TestCase, *, reason: str
    ) -> TestCase:
        current = self._owned_assignment(actor, assignment_id)
        known = {tc.id for tc in current.test_cases}
        updated = self.edit(
            actor,
            assignment_id,
            reason=reason,
            test_cases=[*current.test_cases, replace(test_case, id=None)],
        )
        (added,) = [tc for tc in updated.test_cases if tc.id not in known]
        return added

    def replace_test_case(
        self,
        actor: Actor,
        assignment_id: int,
        test_case_id: int,
        test_case: TestCase,
        *,
        reason: str,
    ) -> TestCase:
        current = self._owned_assignment(actor, assignment_id)
        _find(current.test_cases, test_case_id)
        updated = self.edit(
            actor,
            assignment_id,
            reason=reason,
            test_cases=[
                replace(test_case, id=test_case_id) if tc.id == test_case_id else tc
                for tc in current.test_cases
            ],
        )
        return _find(updated.test_cases, test_case_id)

    def delete_test_case(
        self, actor: Actor, assignment_id: int, test_case_id: int, *, reason: str
    ) -> None:
        current = self._owned_assignment(actor, assignment_id)
        _find(current.test_cases, test_case_id)
        self.edit(
            actor,
            assignment_id,
            reason=reason,
            test_cases=[tc for tc in current.test_cases if tc.id != test_case_id],
        )

    # ---- Internals ----------------------------------------------------------

    def _visible_role(self, actor: Actor, classroom_id: int) -> ClassroomRole:
        role = self._classrooms.role_of(actor, classroom_id)
        if role is ClassroomRole.OUTSIDER:
            raise ClassroomNotVisibleError
        return role

    def _require_owner(self, actor: Actor, classroom_id: int) -> None:
        if self._visible_role(actor, classroom_id) is not ClassroomRole.OWNER:
            raise PermissionDeniedError

    def _assignment(self, assignment_id: int) -> Assignment:
        assignment = self._assignments.get(assignment_id)
        if assignment is None:
            raise AssignmentNotFoundError
        return assignment

    def _owned_assignment(self, actor: Actor, assignment_id: int) -> Assignment:
        assignment = self.get(actor, assignment_id)
        if assignment.owner_id != actor.user_id:
            raise PermissionDeniedError
        return assignment

    def _posting(self, classroom_id: int, assignment_id: int) -> Posting:
        posting = self._postings.find(classroom_id, assignment_id)
        if posting is None:
            raise PostingNotFoundError
        return posting

    def _numbered_postings(
        self, classroom_id: int
    ) -> list[tuple[int, Posting, Assignment]]:
        postings = sorted(
            self._postings.list_for_classroom(classroom_id),
            key=lambda posting: (posting.published_at, posting.id),
        )
        return [
            (number, posting, self._assignment(posting.assignment_id))
            for number, posting in enumerate(postings, start=1)
        ]

    def _student_standing(
        self, posting: Posting, student_id: int, now: datetime
    ) -> StudentStanding:
        standing = self._stats.standing(posting.id, student_id)
        if standing.state is StudentState.NOT_STARTED and posting.is_closed(now):
            return replace(standing, state=StudentState.CLOSED)
        return standing

    def _instructor_summary(
        self,
        actor: Actor,
        classroom_id: int,
        numbered: list[tuple[int, Posting, Assignment]],
    ) -> InstructorSummary:
        members = self._classrooms.members(actor, classroom_id)
        postings = [posting for _, posting, _ in numbered]
        passed = 0
        never_submitted = 0
        rows = []
        for member in members:
            standings = [self._stats.standing(p.id, member.user_id) for p in postings]
            passed += sum(1 for s in standings if s.state is StudentState.PASSED)
            if postings and all(s.state is StudentState.NOT_STARTED for s in standings):
                never_submitted += 1
            rows.append(
                ScoreRow(
                    student_name=member.full_name,
                    student_number=member.student_number,
                    scores=[self._stats.score(p.id, member.user_id) for p in postings],
                )
            )
        slots = len(members) * len(postings)
        rates = []
        for _, posting, assignment in numbered:
            rate = self._stats.fail_rate(posting.id)
            if rate is not None:
                rates.append(FailRate(title=assignment.title, rate=rate))
        rates.sort(key=lambda item: item.rate, reverse=True)
        return InstructorSummary(
            student_count=len(members),
            problem_count=len(postings),
            avg_pass_rate=passed / slots if slots else None,
            never_submitted=never_submitted,
            most_failed=rates[:MOST_FAILED_LIMIT],
            problem_titles=[a.title for _, _, a in numbered],
            score_table=rows,
        )


def _validate_content(content: AssignmentContent) -> None:
    if not content.title.strip():
        raise ValueError("a title is required")
    if not content.problem_statement.strip():
        raise ValueError("a description is required")
    if not content.test_cases:
        raise ValueError("add at least one test case")
    if content.settings.time_limit_ms <= 0:
        raise ValueError("the time limit must be positive")


def _validate_schedule(schedule: Schedule) -> None:
    if schedule.max_score <= 0:
        raise ValueError("the max score must be positive")


def _ordered(test_cases: list[TestCase]) -> list[TestCase]:
    return [
        replace(test_case, order_index=index)
        for index, test_case in enumerate(test_cases)
    ]


def _payload(assignment: Assignment, classroom_id: int) -> NotificationPayload:
    return {
        "classroom_id": classroom_id,
        "assignment_id": assignment.id,
        "title": assignment.title,
    }


def _snapshot(assignment: Assignment, reason: str, now: datetime) -> AssignmentVersion:
    return AssignmentVersion(
        assignment_id=assignment.id,
        number=assignment.current_version,
        title=assignment.title,
        problem_statement=assignment.problem_statement,
        difficulty=assignment.difficulty,
        settings=assignment.settings,
        reason=reason,
        changed_at=now,
        test_cases=list(assignment.test_cases),
    )


_UNSTARTED = {StudentState.NOT_STARTED, StudentState.CLOSED}


def _matches(row: StudentProblem, problem_filter: ProblemFilter) -> bool:
    if problem_filter is ProblemFilter.NOT_STARTED:
        return row.standing.state in _UNSTARTED
    if problem_filter is ProblemFilter.SUBMITTED:
        return row.standing.state not in _UNSTARTED
    return True


def _find(test_cases: list[TestCase], test_case_id: int) -> TestCase:
    for test_case in test_cases:
        if test_case.id == test_case_id:
            return test_case
    raise TestCaseNotFoundError
