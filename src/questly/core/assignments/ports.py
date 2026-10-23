"""Ports the assignments slice needs.

`tests/fakes/assignments.py` provides in-memory adapters for all three
repositories and PostingStats; production wires the SQL adapters in
`database/core/` (OPS-15). main.py fills ClassroomAccess with ClassroomService,
PostingStats with the submissions slice's `SubmissionPostingStats`, and
MemberNotifier with NotificationService.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from questly.core.assignments.models import (
    Assignment,
    AssignmentVersion,
    Posting,
    PostingSummary,
    StudentStanding,
)
from questly.core.auth.models import Actor
from questly.core.classrooms.models import ClassroomRole, MemberView
from questly.core.notifications.models import NotificationKind, NotificationPayload


class AssignmentRepository(Protocol):
    """The current content of Assignments.

    `create` and `update` are the only operations allowed to assign an id, for
    the Assignment and its test cases alike. Callers use the returned object.
    """

    def list(self) -> list[Assignment]: ...

    def get(self, assignment_id: int) -> Assignment | None: ...

    def create(self, assignment: Assignment) -> Assignment: ...

    def update(self, assignment: Assignment) -> Assignment: ...

    def delete(self, assignment_id: int) -> bool: ...


class VersionRepository(Protocol):
    """Append-only history of Assignment content."""

    def append(self, version: AssignmentVersion) -> AssignmentVersion: ...

    def list_for(self, assignment_id: int) -> list[AssignmentVersion]: ...

    def get(self, assignment_id: int, number: int) -> AssignmentVersion | None: ...


class PostingRepository(Protocol):
    """Postings. `create` is the only operation that assigns an id."""

    def create(self, posting: Posting) -> Posting: ...

    def update(self, posting: Posting) -> Posting: ...

    def delete(self, posting_id: int) -> bool: ...

    def find(self, classroom_id: int, assignment_id: int) -> Posting | None: ...

    def list_for_classroom(self, classroom_id: int) -> list[Posting]: ...

    def list_for_assignment(self, assignment_id: int) -> list[Posting]: ...


class ClassroomAccess(Protocol):
    """What the classrooms slice knows: roles, member counts and members."""

    def role_of(self, actor: Actor, classroom_id: int) -> ClassroomRole: ...

    def member_ids(self, actor: Actor, classroom_id: int) -> list[int]: ...

    def members(self, actor: Actor, classroom_id: int) -> list[MemberView]: ...


class PostingStats(Protocol):
    """Numbers derived from Submissions, owned by the submissions slice."""

    def posting_summary(self, posting_id: int) -> PostingSummary: ...

    def standing(self, posting_id: int, student_id: int) -> StudentStanding: ...

    def score(self, posting_id: int, student_id: int) -> float | None: ...

    def fail_rate(self, posting_id: int) -> float | None: ...


class MemberNotifier(Protocol):
    """Notifies every Member of a Classroom about an Assignment (ADR-0007 §6)."""

    def notify_members(
        self,
        classroom_id: int,
        kind: NotificationKind,
        payload: NotificationPayload,
    ) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
