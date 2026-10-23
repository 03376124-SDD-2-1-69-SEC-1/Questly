"""AssignmentService: publish, Postings, Versions, views and visibility."""

from datetime import UTC, datetime, timedelta

import pytest

from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    InstructorProblemList,
    InstructorSummary,
    JudgingSettings,
    PostingSummary,
    Schedule,
    ScoreRow,
    StudentProblemList,
    StudentStanding,
    StudentState,
    StudentSummary,
    TestCase,
    TestCaseKind,
)
from questly.core.assignments.service import (
    AssignmentNotFoundError,
    AssignmentService,
    ClassroomNotVisibleError,
    PostingNotFoundError,
    TestCaseNotFoundError,
)
from questly.core.auth.models import Actor, PermissionDeniedError, Role
from questly.core.auth.service import AuthService
from questly.core.classrooms.service import ClassroomService
from questly.core.notifications.models import NotificationKind
from questly.integrations.email import StubEmailSender
from tests.fakes.assignments import (
    FakeAssignmentRepository,
    FakePostingRepository,
    FakePostingStats,
    FakeVersionRepository,
)
from tests.fakes.auth import FakeAuthRepository, FakeClock, seed_user
from tests.fakes.classrooms import FakeClassroomRepository, FakeClassroomStats
from tests.fakes.notifications import RecordingMemberNotifier

DEADLINE = datetime(2026, 10, 15, 16, 59, tzinfo=UTC)
TITLE = "Binary search on sorted input"


def content(**overrides) -> AssignmentContent:
    fields = {
        "title": TITLE,
        "problem_statement": "Print the index of x, or -1.",
        "difficulty": Difficulty.MEDIUM,
        "test_cases": [
            TestCase(input_data="5\n1 3 5 7 9\n7", expected_output="3"),
            TestCase(
                input_data="0\n\n3",
                expected_output="-1",
                kind=TestCaseKind.EDGE,
                note="empty list",
            ),
        ],
        "settings": JudgingSettings(time_limit_ms=1000),
    }
    return AssignmentContent(**{**fields, **overrides})


class World:
    def __init__(self) -> None:
        self.clock = FakeClock()
        users = FakeAuthRepository()
        auth = AuthService(users, StubEmailSender(), self.clock)
        self.classrooms = ClassroomService(
            FakeClassroomRepository(), auth, FakeClassroomStats(), self.clock
        )
        self.stats = FakePostingStats()
        self.versions = FakeVersionRepository()
        self.postings = FakePostingRepository()
        self.notifier = RecordingMemberNotifier()
        self.service = AssignmentService(
            FakeAssignmentRepository(),
            self.versions,
            self.postings,
            self.classrooms,
            self.stats,
            self.clock,
            self.notifier,
        )

        def actor(email: str, name: str, role: Role) -> Actor:
            user = seed_user(users, email=email, full_name=name, role=role)
            return Actor(user_id=user.id, role=role, full_name=name, email=email)

        self.somchai = actor(
            "somchai.p@kmitl.ac.th", "Somchai Prasert", Role.INSTRUCTOR
        )
        self.warunee = actor("warunee.k@kmitl.ac.th", "Warunee Kaew", Role.INSTRUCTOR)
        self.nattapong = actor("66010001@kmitl.ac.th", "Nattapong Suwan", Role.STUDENT)
        self.pimchanok = actor("66010002@kmitl.ac.th", "Pimchanok Wong", Role.STUDENT)
        self.sec1 = self._classroom("1")
        self.sec2 = self._classroom("2")
        self.classrooms.join(self.nattapong, self.sec1.join_code)

    def _classroom(self, section: str):
        return self.classrooms.create(
            self.somchai,
            course_code="01076001",
            course_name="Programming I",
            section=section,
            semester="1/2569",
        )

    def publish(self, classroom_ids: list[int] | None = None, **overrides):
        if classroom_ids is None:
            classroom_ids = [self.sec1.id]
        return self.service.publish(
            self.somchai,
            content(**overrides),
            Schedule(deadline=DEADLINE),
            classroom_ids,
        )


@pytest.fixture()
def world() -> World:
    return World()


def test_publish_creates_version_one_and_a_posting_per_classroom(world: World) -> None:
    published = world.publish([world.sec1.id, world.sec2.id])

    assert published.assignment.owner_id == world.somchai.user_id
    assert published.assignment.current_version == 1
    assert [p.classroom_id for p in published.postings] == [
        world.sec1.id,
        world.sec2.id,
    ]
    assert all(p.schedule.deadline == DEADLINE for p in published.postings)
    (version,) = world.versions.list_for(published.assignment.id)
    assert (version.number, version.reason) == (1, "Published")
    assert [tc.order_index for tc in published.assignment.test_cases] == [0, 1]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"title": "  "}, "title"),
        ({"problem_statement": ""}, "description"),
        ({"test_cases": []}, "test case"),
    ],
)
def test_publish_rejects_missing_required_fields(
    world: World, overrides: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        world.publish(**overrides)


def test_publish_needs_a_classroom_the_instructor_owns(world: World) -> None:
    other = world.classrooms.create(
        world.warunee,
        course_code="01076011",
        course_name="Data Structures",
        section="1",
        semester="1/2569",
    )

    with pytest.raises(ValueError):
        world.publish([])
    with pytest.raises(ClassroomNotVisibleError):
        world.publish([other.id])


def test_students_cannot_publish(world: World) -> None:
    with pytest.raises(PermissionDeniedError):
        world.service.publish(
            world.nattapong, content(), Schedule(deadline=DEADLINE), [world.sec1.id]
        )


def test_problems_are_numbered_by_publish_order_per_role(world: World) -> None:
    first = world.publish(title="Sum of a list")
    world.clock.advance(timedelta(days=1))
    world.publish(title="Reverse a string")
    world.stats.summaries[first.postings[0].id] = PostingSummary(
        submitted=1, avg_score=9.4
    )
    world.stats.standings[(first.postings[0].id, world.nattapong.user_id)] = (
        StudentStanding(state=StudentState.PASSED, score=10)
    )

    teacher_view = world.service.problems(world.somchai, world.sec1.id)
    student_view = world.service.problems(world.nattapong, world.sec1.id)

    assert isinstance(teacher_view, InstructorProblemList)
    assert teacher_view.student_count == 1
    assert [(p.number, p.assignment.title) for p in teacher_view.problems] == [
        (1, "Sum of a list"),
        (2, "Reverse a string"),
    ]
    assert teacher_view.problems[0].summary.avg_score == 9.4
    assert isinstance(student_view, StudentProblemList)
    assert [p.standing.state for p in student_view.problems] == [
        StudentState.PASSED,
        StudentState.NOT_STARTED,
    ]


def test_a_student_sees_closed_on_an_unstarted_closed_posting(world: World) -> None:
    published = world.publish()
    world.service.close(world.somchai, world.sec1.id, published.assignment.id)

    view = world.service.problems(world.nattapong, world.sec1.id)

    assert view.problems[0].standing.state is StudentState.CLOSED


def test_outsiders_get_not_visible_and_members_cannot_manage(world: World) -> None:
    published = world.publish()
    aid = published.assignment.id

    with pytest.raises(ClassroomNotVisibleError):
        world.service.problems(world.pimchanok, world.sec1.id)
    with pytest.raises(ClassroomNotVisibleError):
        world.service.problems(world.warunee, world.sec1.id)
    with pytest.raises(PermissionDeniedError):
        world.service.close(world.nattapong, world.sec1.id, aid)
    with pytest.raises(PermissionDeniedError):
        world.service.edit(world.nattapong, aid, reason="x", title="Hacked")
    with pytest.raises(AssignmentNotFoundError):
        world.service.get(world.pimchanok, aid)


def test_update_posting_merges_and_is_per_classroom(world: World) -> None:
    published = world.publish([world.sec1.id, world.sec2.id])
    aid = published.assignment.id
    later = DEADLINE + timedelta(days=3)

    updated = world.service.update_posting(
        world.somchai, world.sec1.id, aid, deadline=later
    )

    assert updated.schedule.deadline == later
    assert updated.schedule.max_score == 10
    other = world.postings.find(world.sec2.id, aid)
    assert other.schedule.deadline == DEADLINE


def test_unpost_removes_only_that_classroom(world: World) -> None:
    published = world.publish([world.sec1.id, world.sec2.id])
    aid = published.assignment.id

    world.service.unpost(world.somchai, world.sec2.id, aid)

    assert world.postings.find(world.sec2.id, aid) is None
    with pytest.raises(PostingNotFoundError):
        world.service.unpost(world.somchai, world.sec2.id, aid)


def test_edit_appends_a_version_and_extends_only_ticked_postings(world: World) -> None:
    published = world.publish([world.sec1.id, world.sec2.id])
    aid = published.assignment.id
    sec1_posting, sec2_posting = published.postings
    extended = DEADLINE + timedelta(days=3)

    edited = world.service.edit(
        world.somchai,
        aid,
        reason="Test case 3 had a wrong expected output.",
        title="Binary search (fixed)",
        extend_deadlines={sec1_posting.id: extended},
    )

    assert edited.current_version == 2
    assert edited.problem_statement == published.assignment.problem_statement
    assert edited.test_cases == published.assignment.test_cases
    versions = world.service.versions(world.somchai, aid)
    assert [(v.number, v.reason) for v in versions] == [
        (2, "Test case 3 had a wrong expected output."),
        (1, "Published"),
    ]
    assert world.postings.find(world.sec1.id, aid).schedule.deadline == extended
    assert world.postings.find(world.sec2.id, aid).schedule.deadline == DEADLINE


def test_edit_requires_a_reason_and_own_postings(world: World) -> None:
    aid = world.publish().assignment.id

    with pytest.raises(ValueError, match="reason"):
        world.service.edit(world.somchai, aid, reason=" ", title="New")
    with pytest.raises(PostingNotFoundError):
        world.service.edit(
            world.somchai, aid, reason="r", extend_deadlines={999: DEADLINE}
        )


def test_publish_notifies_the_members_of_each_classroom(world: World) -> None:
    published = world.publish([world.sec1.id, world.sec2.id])
    aid = published.assignment.id

    assert world.notifier.calls == [
        (
            world.sec1.id,
            NotificationKind.ASSIGNMENT_PUBLISHED,
            {"classroom_id": world.sec1.id, "assignment_id": aid, "title": TITLE},
        ),
        (
            world.sec2.id,
            NotificationKind.ASSIGNMENT_PUBLISHED,
            {"classroom_id": world.sec2.id, "assignment_id": aid, "title": TITLE},
        ),
    ]


def test_edit_notifies_every_posted_classroom_with_the_reason(world: World) -> None:
    aid = world.publish([world.sec1.id, world.sec2.id]).assignment.id
    world.notifier.calls.clear()

    world.service.edit(world.somchai, aid, reason="  Fixed test 3 ", title="Fixed")

    assert world.notifier.calls == [
        (
            classroom_id,
            NotificationKind.ASSIGNMENT_UPDATED,
            {
                "classroom_id": classroom_id,
                "assignment_id": aid,
                "title": "Fixed",
                "reason": "Fixed test 3",
            },
        )
        for classroom_id in (world.sec1.id, world.sec2.id)
    ]


def test_a_rejected_edit_notifies_nobody(world: World) -> None:
    aid = world.publish().assignment.id
    world.notifier.calls.clear()

    with pytest.raises(ValueError):
        world.service.edit(world.somchai, aid, reason=" ", title="New")

    assert world.notifier.calls == []


def test_notify_students_is_for_the_classrooms_instructor_only(world: World) -> None:
    aid = world.publish([world.sec1.id]).assignment.id
    world.notifier.calls.clear()

    world.service.notify_students(world.somchai, world.sec1.id, aid)

    assert world.notifier.calls == [
        (
            world.sec1.id,
            NotificationKind.ASSIGNMENT_NOTIFIED,
            {"classroom_id": world.sec1.id, "assignment_id": aid, "title": TITLE},
        )
    ]
    with pytest.raises(PermissionDeniedError):
        world.service.notify_students(world.nattapong, world.sec1.id, aid)
    with pytest.raises(ClassroomNotVisibleError):
        world.service.notify_students(world.warunee, world.sec1.id, aid)
    with pytest.raises(PostingNotFoundError):
        world.service.notify_students(world.somchai, world.sec2.id, aid)
    assert len(world.notifier.calls) == 1


def test_test_case_writes_are_versions(world: World) -> None:
    aid = world.publish().assignment.id

    added = world.service.add_test_case(
        world.somchai,
        aid,
        TestCase(input_data="1\n4\n4", expected_output="0", kind=TestCaseKind.HIDDEN),
        reason="Add a single-element test",
    )
    replaced = world.service.replace_test_case(
        world.somchai,
        aid,
        added.id,
        TestCase(input_data="1\n4\n4", expected_output="0", note="single element"),
        reason="Label it",
    )
    world.service.delete_test_case(world.somchai, aid, added.id, reason="Drop it")

    assert replaced.note == "single element"
    assert len(world.service.test_cases(world.somchai, aid)) == 2
    assert world.service.get(world.somchai, aid).current_version == 4
    with pytest.raises(TestCaseNotFoundError):
        world.service.test_case(world.somchai, aid, added.id)


def test_summaries_count_standings(world: World) -> None:
    world.classrooms.join(world.pimchanok, world.sec1.join_code)
    first = world.publish(title="Sum of a list").postings[0]
    second = world.publish(title="Reverse a string").postings[0]
    passed = StudentStanding(state=StudentState.PASSED, score=10)
    partial = StudentStanding(state=StudentState.PARTIAL, score=6)
    world.stats.standings[(first.id, world.nattapong.user_id)] = passed
    world.stats.standings[(second.id, world.nattapong.user_id)] = partial
    world.stats.fail_rates = {first.id: 0.06, second.id: 0.46}

    teacher = world.service.summary(world.somchai, world.sec1.id)
    student = world.service.summary(world.nattapong, world.sec1.id)

    assert isinstance(teacher, InstructorSummary)
    assert teacher.avg_pass_rate == pytest.approx(1 / 4)
    assert teacher.never_submitted == 1
    assert [f.title for f in teacher.most_failed] == [
        "Reverse a string",
        "Sum of a list",
    ]
    assert [row.total for row in teacher.score_table] == [16, 0]
    assert isinstance(student, StudentSummary)
    assert (student.solved, student.passed, student.total) == (2, 1, 2)
    assert student.avg_score == 8


def test_export_summary_by_owner_returns_score_table(world: World) -> None:
    world.classrooms.join(world.pimchanok, world.sec1.join_code)
    first = world.publish(title="Sum of a list").postings[0]
    passed = StudentStanding(state=StudentState.PASSED, score=10)
    world.stats.standings[(first.id, world.nattapong.user_id)] = passed

    result = world.service.export_summary(world.somchai, world.sec1.id)

    assert isinstance(result, InstructorSummary)
    assert result.problem_titles == ["Sum of a list"]
    assert result.score_table == [
        ScoreRow(
            student_name="Nattapong Suwan", student_number="66010001", scores=[10.0]
        ),
        ScoreRow(
            student_name="Pimchanok Wong", student_number="66010002", scores=[None]
        ),
    ]
    assert [row.total for row in result.score_table] == [10.0, 0]


def test_export_summary_by_student_raises_permission_denied(world: World) -> None:
    with pytest.raises(PermissionDeniedError):
        world.service.export_summary(world.nattapong, world.sec1.id)


def test_export_summary_by_outsider_raises_classroom_not_visible(world: World) -> None:
    with pytest.raises(ClassroomNotVisibleError):
        world.service.export_summary(world.pimchanok, world.sec1.id)
