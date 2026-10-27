"""Repository, email and lookup ports for Notifications.

main.py fills ClassroomMembers with the classrooms slice's repository and
UserAccounts with the auth slice's repository. Both are repositories rather
than services because AuthService sends its mail through NotificationService,
so NotificationService is built before either service exists.

DueSoonService is built after them, so main.py fills JoinedClassrooms with
ClassroomService and ClassroomProblems with AssignmentService.
"""

from datetime import datetime
from typing import Protocol

from questly.core.assignments.models import (
    InstructorProblemList,
    ProblemFilter,
    StudentProblemList,
)
from questly.core.auth.models import Actor, User
from questly.core.classrooms.models import (
    ClassroomFilter,
    InstructorPicker,
    Membership,
    StudentPicker,
)
from questly.core.notifications.models import Notification


class NotificationRepository(Protocol):
    """Persistence operations required by NotificationService."""

    def list_for_user(self, user_id: int) -> list[Notification]:
        """Return notifications belonging to one user."""
        ...

    def get(self, notification_id: int) -> Notification | None:
        """Return one notification if it exists."""
        ...

    def create(self, notification: Notification) -> Notification:
        """Store a notification and assign its id."""
        ...

    def mark_read(
        self,
        notification_id: int,
        *,
        read_at: datetime,
    ) -> Notification:
        """Mark an existing notification as read."""
        ...

    def mark_all_read(
        self,
        user_id: int,
        *,
        read_at: datetime,
    ) -> int:
        """Mark all unread notifications for a user as read."""
        ...


class EmailDeliveryError(Exception):
    """An email could not be handed to the mail provider."""


class EmailSender(Protocol):
    """Port for sending email without coupling Core to an email provider."""

    def send(self, *, to: str, subject: str, body: str) -> None:
        """Send an email message.

        Adapters raise only EmailDeliveryError when delivery fails, never an
        exception of the library they wrap.
        """
        ...


class ClassroomMembers(Protocol):
    """Who the Members of a Classroom are."""

    def list_memberships(self, classroom_id: int) -> list[Membership]: ...


class UserAccounts(Protocol):
    """The account a notification email goes to."""

    def get_user(self, user_id: int) -> User | None: ...


class JoinedClassrooms(Protocol):
    """The Classrooms an Actor sees on C-01."""

    def picker(
        self, actor: Actor, classroom_filter: ClassroomFilter
    ) -> InstructorPicker | StudentPicker: ...


class ClassroomProblems(Protocol):
    """A Classroom's Postings as the Actor sees them on S-01."""

    def problems(
        self, actor: Actor, classroom_id: int, problem_filter: ProblemFilter
    ) -> InstructorProblemList | StudentProblemList: ...
