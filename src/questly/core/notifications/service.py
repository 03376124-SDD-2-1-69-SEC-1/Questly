"""Business logic for user notifications (ADR-0007 §6)."""

import logging
from datetime import timedelta

from questly.core.assignments.models import ProblemFilter
from questly.core.auth.models import Actor, Role
from questly.core.auth.ports import Clock
from questly.core.classrooms.models import ClassroomFilter
from questly.core.notifications.models import (
    DueSoon,
    Notification,
    NotificationKind,
    NotificationPayload,
)
from questly.core.notifications.ports import (
    ClassroomMembers,
    ClassroomProblems,
    EmailDeliveryError,
    EmailSender,
    JoinedClassrooms,
    NotificationRepository,
    UserAccounts,
)

logger = logging.getLogger(__name__)

DUE_SOON_WINDOW = timedelta(hours=24)

_SUBJECTS = {
    NotificationKind.ASSIGNMENT_PUBLISHED: "New problem: {title}",
    NotificationKind.ASSIGNMENT_UPDATED: "Problem updated: {title}",
    NotificationKind.ASSIGNMENT_NOTIFIED: "Reminder: {title}",
}


class NotificationNotFoundError(Exception):
    """Raised when a notification does not exist for the requested user."""


class NotificationService:
    """Coordinate notification storage, read state, and email delivery."""

    def __init__(
        self,
        repository: NotificationRepository,
        email_sender: EmailSender,
        clock: Clock,
        members: ClassroomMembers,
        accounts: UserAccounts,
        *,
        base_url: str,
    ) -> None:
        self._repository = repository
        self._email_sender = email_sender
        self._clock = clock
        self._members = members
        self._accounts = accounts
        self._base_url = base_url.rstrip("/")

    def list_for_user(self, user_id: int) -> list[Notification]:
        """List a user's notifications, newest first."""
        return self._repository.list_for_user(user_id)

    def mark_read(
        self,
        user_id: int,
        notification_id: int,
    ) -> Notification:
        """Mark a notification as read only if it belongs to the user."""
        notification = self._repository.get(notification_id)

        if notification is None or notification.user_id != user_id:
            raise NotificationNotFoundError

        if notification.read_at is not None:
            return notification

        return self._repository.mark_read(
            notification_id,
            read_at=self._clock.now(),
        )

    def mark_all_read(self, user_id: int) -> int:
        """Mark every unread notification belonging to the user as read."""
        return self._repository.mark_all_read(
            user_id,
            read_at=self._clock.now(),
        )

    def create(
        self,
        *,
        user_id: int,
        kind: NotificationKind,
        payload: NotificationPayload,
    ) -> Notification:
        """Persist a notification for one user."""
        return self._repository.create(
            Notification(
                user_id=user_id,
                kind=kind,
                payload=payload,
                created_at=self._clock.now(),
            )
        )

    def notify_members(
        self,
        classroom_id: int,
        kind: NotificationKind,
        payload: NotificationPayload,
    ) -> None:
        """Store one notification per Member and email each of them.

        A Member whose email fails is logged and skipped: the others still get
        theirs and the notification stays stored. The caller has already decided
        the Actor may notify this Classroom.
        `payload` carries `classroom_id`, `assignment_id`, `title` and, for an
        update, `reason`.
        """
        subject = _SUBJECTS[kind].format(title=payload["title"])
        for membership in self._members.list_memberships(classroom_id):
            self.create(user_id=membership.student_id, kind=kind, payload=payload)
            user = self._accounts.get_user(membership.student_id)
            if user is None:
                continue
            try:
                self.send_email(
                    to=user.email,
                    subject=subject,
                    body=_member_email(
                        self._base_url, user.full_name, subject, payload
                    ),
                )
            except EmailDeliveryError:
                logger.warning(
                    "member email for user %s not sent (subject %r)",
                    user.id,
                    subject,
                    exc_info=True,
                )

    def send_email(
        self,
        *,
        to: str,
        subject: str,
        body: str,
    ) -> None:
        """Send an email through the notification-owned email port."""
        self._email_sender.send(to=to, subject=subject, body=body)


class DueSoonService:
    """ "Deadline tomorrow" for the G-02a dropdown (ADR-0007 §6.2)."""

    def __init__(
        self,
        classrooms: JoinedClassrooms,
        problems: ClassroomProblems,
        clock: Clock,
    ) -> None:
        self._classrooms = classrooms
        self._problems = problems
        self._clock = clock

    def due_soon(self, actor: Actor) -> list[DueSoon]:
        """Open Postings the Student has not submitted, due within 24 h."""
        if actor.role is not Role.STUDENT:
            return []
        now = self._clock.now()
        due = []
        for card in self._classrooms.picker(actor, ClassroomFilter.ACTIVE).cards:
            listing = self._problems.problems(
                actor, card.classroom.id, ProblemFilter.NOT_STARTED
            )
            for problem in listing.problems:
                posting = problem.posting
                deadline = posting.schedule.deadline
                if (
                    posting.is_closed(now)
                    or not now < deadline <= now + DUE_SOON_WINDOW
                ):
                    continue
                due.append(
                    DueSoon(
                        classroom_id=posting.classroom_id,
                        assignment_id=posting.assignment_id,
                        title=problem.assignment.title,
                        deadline=deadline,
                    )
                )
        return sorted(due, key=lambda item: item.deadline)


def _member_email(
    base_url: str, full_name: str, subject: str, payload: NotificationPayload
) -> str:
    lines = [f"Hi {full_name},", "", subject]
    if "reason" in payload:
        lines.append(f"Reason from your instructor: {payload['reason']}")
    lines += [
        "",
        f"{base_url}/classes/{payload['classroom_id']}"
        f"/assignments/{payload['assignment_id']}",
    ]
    return "\n".join(lines)
