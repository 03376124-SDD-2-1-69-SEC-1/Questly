"""Classroom use cases. Every public method takes the Actor first (ADR-0007 §9.5).

Visibility rule (§9.6): a Classroom the Actor neither owns nor belongs to is
reported as ClassroomNotFoundError (404), never as forbidden; a Member asking
for an Instructor-only use case gets PermissionDeniedError (403).
"""

import secrets
from dataclasses import replace

from questly.core.auth.models import Actor, PermissionDeniedError, Role
from questly.core.classrooms.models import (
    Classroom,
    ClassroomFilter,
    ClassroomRole,
    InstructorClassroomCard,
    InstructorClassroomView,
    InstructorPicker,
    Membership,
    MemberView,
    StudentClassroomCard,
    StudentClassroomView,
    StudentPicker,
)
from questly.core.classrooms.ports import (
    ClassroomRepository,
    ClassroomStats,
    Clock,
    UserDirectory,
)

JOIN_CODE_LENGTH = 6
# No 0/O, 1/I/L: the code is read aloud in class and typed by hand.
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_JOIN_CODE_ATTEMPTS = 20


class ClassroomNotFoundError(Exception):
    """Missing, or not visible to this Actor."""


class InvalidJoinCodeError(Exception):
    """C-02a: no joinable Classroom has this code."""


class MemberNotFoundError(Exception):
    """The Student is not a Member of this Classroom."""


class _Unset:
    """Marks a PATCH field left out of the request body."""


UNSET = _Unset()


class ClassroomService:
    def __init__(
        self,
        repository: ClassroomRepository,
        directory: UserDirectory,
        stats: ClassroomStats,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._directory = directory
        self._stats = stats
        self._clock = clock

    def picker(
        self, actor: Actor, classroom_filter: ClassroomFilter = ClassroomFilter.ALL
    ) -> InstructorPicker | StudentPicker:
        """C-01: the Instructor's or the Student's classroom cards."""
        if actor.role is Role.INSTRUCTOR:
            owned = _filtered(
                self._repository.list_owned_by(actor.user_id), classroom_filter
            )
            return InstructorPicker(
                cards=[
                    InstructorClassroomCard(
                        classroom=classroom,
                        student_count=len(
                            self._repository.list_memberships(classroom.id)
                        ),
                        stats=self._stats.instructor_card(classroom.id),
                    )
                    for classroom in owned
                ]
            )
        if actor.role is Role.STUDENT:
            joined = _filtered(
                self._repository.list_joined_by(actor.user_id), classroom_filter
            )
            return StudentPicker(
                progress=self._stats.student_progress(
                    actor.user_id, [classroom.id for classroom in joined]
                ),
                cards=[
                    StudentClassroomCard(
                        classroom=classroom,
                        instructor_name=self._name_of(classroom.instructor_id),
                        stats=self._stats.student_card(classroom.id, actor.user_id),
                    )
                    for classroom in joined
                ],
            )
        raise PermissionDeniedError

    def create(
        self,
        actor: Actor,
        *,
        course_code: str,
        course_name: str,
        section: str,
        semester: str,
    ) -> Classroom:
        """C-03: the new Classroom carries a fresh Join code (C-03a)."""
        if actor.role is not Role.INSTRUCTOR:
            raise PermissionDeniedError
        return self._repository.create(
            Classroom(
                instructor_id=actor.user_id,
                course_code=_required(course_code, "course_code"),
                course_name=_required(course_name, "course_name"),
                section=_required(section, "section"),
                semester=_required(semester, "semester"),
                join_code=self._new_join_code(),
            )
        )

    def join(self, actor: Actor, join_code: str) -> Classroom:
        """C-02: joining is instant; joining twice is harmless."""
        if actor.role is not Role.STUDENT:
            raise PermissionDeniedError
        normalized = "".join(join_code.split()).upper()
        if len(normalized) != JOIN_CODE_LENGTH:
            raise InvalidJoinCodeError
        classroom = self._repository.find_by_join_code(normalized)
        if classroom is None or classroom.archived:
            raise InvalidJoinCodeError
        if self._repository.get_membership(classroom.id, actor.user_id) is None:
            self._repository.add_membership(
                Membership(
                    classroom_id=classroom.id,
                    student_id=actor.user_id,
                    joined_at=self._clock.now(),
                )
            )
        return classroom

    def view(
        self, actor: Actor, classroom_id: int
    ) -> InstructorClassroomView | StudentClassroomView:
        """/classes/{id}: T-01 for the owner, S-01 for a Member."""
        classroom = self._visible(actor, classroom_id)
        if classroom.instructor_id == actor.user_id:
            return InstructorClassroomView(
                classroom=classroom,
                student_count=len(self._repository.list_memberships(classroom.id)),
            )
        return StudentClassroomView(
            classroom=classroom, instructor_name=self._name_of(classroom.instructor_id)
        )

    def update(
        self,
        actor: Actor,
        classroom_id: int,
        *,
        course_code: str | _Unset = UNSET,
        course_name: str | _Unset = UNSET,
        section: str | _Unset = UNSET,
        semester: str | _Unset = UNSET,
    ) -> Classroom:
        """T-01 Classroom settings. Fields left out keep their value."""
        current = self._owned(actor, classroom_id)
        merged = replace(
            current,
            course_code=_merge(current.course_code, course_code, "course_code"),
            course_name=_merge(current.course_name, course_name, "course_name"),
            section=_merge(current.section, section, "section"),
            semester=_merge(current.semester, semester, "semester"),
        )
        return self._repository.update(merged)

    def archive(self, actor: Actor, classroom_id: int) -> Classroom:
        return self._repository.update(
            replace(self._owned(actor, classroom_id), archived=True)
        )

    def unarchive(self, actor: Actor, classroom_id: int) -> Classroom:
        return self._repository.update(
            replace(self._owned(actor, classroom_id), archived=False)
        )

    def regenerate_join_code(self, actor: Actor, classroom_id: int) -> Classroom:
        """The old code stops working; existing Members keep access."""
        classroom = self._owned(actor, classroom_id)
        return self._repository.update(
            replace(classroom, join_code=self._new_join_code())
        )

    def disable_join_code(self, actor: Actor, classroom_id: int) -> Classroom:
        return self._repository.update(
            replace(self._owned(actor, classroom_id), join_code=None)
        )

    def members(self, actor: Actor, classroom_id: int) -> list[MemberView]:
        """T-01 Members, in join order."""
        classroom = self._owned(actor, classroom_id)
        memberships = self._repository.list_memberships(classroom.id)
        summaries = {
            summary.user_id: summary
            for summary in self._directory.summaries(
                [membership.student_id for membership in memberships]
            )
        }
        views = []
        for membership in sorted(memberships, key=lambda item: item.joined_at):
            summary = summaries[membership.student_id]
            views.append(
                MemberView(
                    user_id=summary.user_id,
                    full_name=summary.full_name,
                    email=summary.email,
                    student_number=summary.student_number,
                    joined_at=membership.joined_at,
                )
            )
        return views

    def role_of(self, actor: Actor, classroom_id: int) -> ClassroomRole:
        """Owner, Member or outsider; a missing Classroom counts as outsider."""
        try:
            classroom = self._visible(actor, classroom_id)
        except ClassroomNotFoundError:
            return ClassroomRole.OUTSIDER
        if classroom.instructor_id == actor.user_id:
            return ClassroomRole.OWNER
        return ClassroomRole.MEMBER

    def is_archived(self, actor: Actor, classroom_id: int) -> bool:
        """Archived Classrooms take no Submissions (ADR-0007 §2); owner or Member."""
        return self._visible(actor, classroom_id).archived

    def member_ids(self, actor: Actor, classroom_id: int) -> list[int]:
        """Every Member's user id; the owning Instructor only."""
        classroom = self._owned(actor, classroom_id)
        return [m.student_id for m in self._repository.list_memberships(classroom.id)]

    def owned_classrooms(self, actor: Actor) -> list[Classroom]:
        """The Instructor's active Classrooms (T-04 step 4 lists them)."""
        if actor.role is not Role.INSTRUCTOR:
            raise PermissionDeniedError
        return [
            classroom
            for classroom in self._repository.list_owned_by(actor.user_id)
            if not classroom.archived
        ]

    def remove_member(self, actor: Actor, classroom_id: int, student_id: int) -> None:
        classroom = self._owned(actor, classroom_id)
        if not self._repository.remove_membership(classroom.id, student_id):
            raise MemberNotFoundError

    def _visible(self, actor: Actor, classroom_id: int) -> Classroom:
        classroom = self._repository.get(classroom_id)
        if classroom is None:
            raise ClassroomNotFoundError
        if classroom.instructor_id == actor.user_id:
            return classroom
        if self._repository.get_membership(classroom.id, actor.user_id) is not None:
            return classroom
        raise ClassroomNotFoundError

    def _owned(self, actor: Actor, classroom_id: int) -> Classroom:
        classroom = self._visible(actor, classroom_id)
        if classroom.instructor_id != actor.user_id:
            raise PermissionDeniedError
        return classroom

    def _name_of(self, user_id: int) -> str:
        summaries = self._directory.summaries([user_id])
        if not summaries:
            return "—"
        return summaries[0].full_name

    def _new_join_code(self) -> str:
        for _ in range(_JOIN_CODE_ATTEMPTS):
            code = "".join(
                secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH)
            )
            if self._repository.find_by_join_code(code) is None:
                return code
        raise RuntimeError("could not find an unused join code")


def _filtered(
    classrooms: list[Classroom], classroom_filter: ClassroomFilter
) -> list[Classroom]:
    if classroom_filter is ClassroomFilter.ACTIVE:
        return [classroom for classroom in classrooms if not classroom.archived]
    if classroom_filter is ClassroomFilter.ARCHIVED:
        return [classroom for classroom in classrooms if classroom.archived]
    return classrooms


def _required(value: str, name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must not be blank")
    return value


def _merge(current: str, value: str | _Unset, name: str) -> str:
    if isinstance(value, _Unset):
        return current
    return _required(value, name)
