"""ClassroomService use cases, including the 404/403 visibility rule."""

from datetime import timedelta

import pytest

from questly.core.auth.models import Actor, PermissionDeniedError, Role
from questly.core.auth.service import AuthService
from questly.core.classrooms.models import (
    ClassroomFilter,
    InstructorCardStats,
    InstructorClassroomView,
    InstructorPicker,
    StudentClassroomView,
    StudentPicker,
    StudentProgress,
)
from questly.core.classrooms.service import (
    JOIN_CODE_ALPHABET,
    ClassroomNotFoundError,
    ClassroomService,
    InvalidJoinCodeError,
    MemberNotFoundError,
)
from questly.integrations.email import StubEmailSender
from tests.fakes.auth import FakeAuthRepository, FakeClock, seed_user
from tests.fakes.classrooms import FakeClassroomRepository, FakeClassroomStats


class World:
    """Two Instructors, two Students, an Admin, and the service under test."""

    def __init__(self) -> None:
        self.users = FakeAuthRepository()
        self.clock = FakeClock()
        self.stats = FakeClassroomStats()
        self.repository = FakeClassroomRepository()
        directory = AuthService(
            self.users, StubEmailSender(), self.clock, base_url="http://localhost:8000"
        )
        self.service = ClassroomService(
            self.repository, directory, self.stats, self.clock
        )
        self.somchai = self._actor(
            "somchai.p@kmitl.ac.th", "Somchai Prasert", Role.INSTRUCTOR
        )
        self.warunee = self._actor(
            "warunee.k@kmitl.ac.th", "Warunee Kaew", Role.INSTRUCTOR
        )
        self.nattapong = self._actor(
            "66010001@kmitl.ac.th", "Nattapong Suwan", Role.STUDENT
        )
        self.pimchanok = self._actor(
            "66010002@kmitl.ac.th", "Pimchanok Wong", Role.STUDENT
        )
        self.admin = self._actor("admin@kmitl.ac.th", "Admin", Role.ADMIN)

    def _actor(self, email: str, name: str, role: Role) -> Actor:
        user = seed_user(self.users, email=email, full_name=name, role=role)
        return Actor(user_id=user.id, role=role, full_name=name, email=email)

    def classroom(self, owner: Actor | None = None, section: str = "1"):
        return self.service.create(
            owner or self.somchai,
            course_code="01076001",
            course_name="Programming I",
            section=section,
            semester="1/2569",
        )


@pytest.fixture()
def world() -> World:
    return World()


def test_create_gives_the_classroom_a_readable_join_code(world: World) -> None:
    classroom = world.classroom()

    assert classroom.instructor_id == world.somchai.user_id
    assert len(classroom.join_code) == 6
    assert set(classroom.join_code) <= set(JOIN_CODE_ALPHABET)
    assert classroom.label == "01076001 · Sec 1"


def test_only_instructors_create_classrooms(world: World) -> None:
    for outsider in (world.nattapong, world.admin):
        with pytest.raises(PermissionDeniedError):
            world.classroom(owner=outsider)


def test_create_rejects_blank_fields(world: World) -> None:
    with pytest.raises(ValueError):
        world.service.create(
            world.somchai, course_code=" ", course_name="P", section="1", semester="1"
        )


def test_join_normalizes_the_code_and_is_idempotent(world: World) -> None:
    classroom = world.classroom()
    typed = " ".join(classroom.join_code.lower())

    world.service.join(world.nattapong, typed)
    world.service.join(world.nattapong, classroom.join_code)

    assert len(world.repository.list_memberships(classroom.id)) == 1


@pytest.mark.parametrize("code", ["ABC", "ZZZZZZ"])
def test_join_rejects_a_wrong_or_unknown_code(world: World, code: str) -> None:
    world.classroom()

    with pytest.raises(InvalidJoinCodeError):
        world.service.join(world.nattapong, code)


def test_join_rejects_an_archived_classroom(world: World) -> None:
    classroom = world.classroom()
    world.service.archive(world.somchai, classroom.id)

    with pytest.raises(InvalidJoinCodeError):
        world.service.join(world.nattapong, classroom.join_code)


def test_instructors_do_not_join(world: World) -> None:
    classroom = world.classroom()

    with pytest.raises(PermissionDeniedError):
        world.service.join(world.warunee, classroom.join_code)


def test_regenerating_the_code_kills_the_old_one_but_keeps_members(
    world: World,
) -> None:
    classroom = world.classroom()
    world.service.join(world.nattapong, classroom.join_code)

    renewed = world.service.regenerate_join_code(world.somchai, classroom.id)

    assert renewed.join_code != classroom.join_code
    with pytest.raises(InvalidJoinCodeError):
        world.service.join(world.pimchanok, classroom.join_code)
    assert isinstance(
        world.service.view(world.nattapong, classroom.id), StudentClassroomView
    )


def test_disabled_joining_refuses_every_code(world: World) -> None:
    classroom = world.classroom()

    disabled = world.service.disable_join_code(world.somchai, classroom.id)

    assert disabled.join_code is None
    with pytest.raises(InvalidJoinCodeError):
        world.service.join(world.nattapong, classroom.join_code)


def test_instructor_picker_counts_students_and_filters(world: World) -> None:
    first = world.classroom(section="1")
    second = world.classroom(section="2")
    world.service.join(world.nattapong, first.join_code)
    world.service.archive(world.somchai, second.id)
    world.stats.instructor_cards[first.id] = InstructorCardStats(
        problem_count=6, draft_count=1, avg_pass_rate=0.78
    )

    everything = world.service.picker(world.somchai)
    active = world.service.picker(world.somchai, ClassroomFilter.ACTIVE)
    archived = world.service.picker(world.somchai, ClassroomFilter.ARCHIVED)

    assert isinstance(everything, InstructorPicker)
    assert [(c.classroom.id, c.student_count) for c in everything.cards] == [
        (first.id, 1),
        (second.id, 0),
    ]
    assert everything.cards[0].stats.problem_count == 6
    assert [c.classroom.id for c in active.cards] == [first.id]
    assert [c.classroom.id for c in archived.cards] == [second.id]


def test_student_picker_names_instructors_and_carries_progress(world: World) -> None:
    classroom = world.classroom()
    world.service.join(world.nattapong, classroom.join_code)
    world.stats.progress[world.nattapong.user_id] = StudentProgress(
        solved=12, total=15, passed=10
    )

    picker = world.service.picker(world.nattapong)

    assert isinstance(picker, StudentPicker)
    assert picker.cards[0].instructor_name == "Somchai Prasert"
    assert picker.progress.passed == 10


def test_student_picker_is_empty_before_joining(world: World) -> None:
    picker = world.service.picker(world.pimchanok)

    assert isinstance(picker, StudentPicker)
    assert picker.cards == []


def test_admins_have_no_classroom_picker(world: World) -> None:
    with pytest.raises(PermissionDeniedError):
        world.service.picker(world.admin)


def test_view_is_role_specific_and_hidden_from_outsiders(world: World) -> None:
    classroom = world.classroom()
    world.service.join(world.nattapong, classroom.join_code)

    assert isinstance(
        world.service.view(world.somchai, classroom.id), InstructorClassroomView
    )
    assert isinstance(
        world.service.view(world.nattapong, classroom.id), StudentClassroomView
    )
    for outsider in (world.pimchanok, world.warunee, world.admin):
        with pytest.raises(ClassroomNotFoundError):
            world.service.view(outsider, classroom.id)
    with pytest.raises(ClassroomNotFoundError):
        world.service.view(world.somchai, 999)


def test_update_merges_fields_left_out(world: World) -> None:
    classroom = world.classroom()

    updated = world.service.update(world.somchai, classroom.id, course_name="Prog I")

    assert (updated.course_name, updated.course_code, updated.section) == (
        "Prog I",
        "01076001",
        "1",
    )


def test_members_may_not_use_instructor_use_cases(world: World) -> None:
    classroom = world.classroom()
    world.service.join(world.nattapong, classroom.join_code)

    for use_case in (
        lambda: world.service.update(world.nattapong, classroom.id, section="9"),
        lambda: world.service.archive(world.nattapong, classroom.id),
        lambda: world.service.regenerate_join_code(world.nattapong, classroom.id),
        lambda: world.service.members(world.nattapong, classroom.id),
    ):
        with pytest.raises(PermissionDeniedError):
            use_case()


def test_outsiders_get_not_found_for_instructor_use_cases(world: World) -> None:
    classroom = world.classroom()

    with pytest.raises(ClassroomNotFoundError):
        world.service.archive(world.warunee, classroom.id)


def test_archive_then_unarchive(world: World) -> None:
    classroom = world.classroom()

    assert world.service.archive(world.somchai, classroom.id).archived
    assert not world.service.unarchive(world.somchai, classroom.id).archived


def test_members_are_listed_in_join_order_with_student_numbers(world: World) -> None:
    classroom = world.classroom()
    world.service.join(world.pimchanok, classroom.join_code)
    world.clock.advance(timedelta(days=2))
    world.service.join(world.nattapong, classroom.join_code)

    members = world.service.members(world.somchai, classroom.id)

    assert [(m.full_name, m.student_number) for m in members] == [
        ("Pimchanok Wong", "66010002"),
        ("Nattapong Suwan", "66010001"),
    ]


def test_remove_member_then_the_classroom_disappears_for_them(world: World) -> None:
    classroom = world.classroom()
    world.service.join(world.nattapong, classroom.join_code)

    world.service.remove_member(world.somchai, classroom.id, world.nattapong.user_id)

    with pytest.raises(ClassroomNotFoundError):
        world.service.view(world.nattapong, classroom.id)
    with pytest.raises(MemberNotFoundError):
        world.service.remove_member(
            world.somchai, classroom.id, world.nattapong.user_id
        )
