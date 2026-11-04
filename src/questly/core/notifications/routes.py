"""HTTP routes for Notifications."""

from fastapi import APIRouter, Request, status

from questly.core.auth.current import current_actor
from questly.core.http_errors import ErrorRule, ErrorTable
from questly.core.notifications.models import Notification
from questly.core.notifications.schemas import (
    DueSoonResponse,
    MarkAllReadResponse,
    NotificationListResponse,
    NotificationResponse,
)
from questly.core.notifications.service import (
    DueSoonService,
    NotificationNotFoundError,
    NotificationService,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

NOTIFICATION_ERRORS = ErrorTable(
    {
        NotificationNotFoundError: ErrorRule(
            status.HTTP_404_NOT_FOUND,
            "notification_not_found",
            "Notification not found",
        ),
    }
)


def notification_service(request: Request) -> NotificationService:
    """Resolve NotificationService from application state."""
    return request.app.state.notification_service


def due_soon_service(request: Request) -> DueSoonService:
    """Resolve DueSoonService from application state."""
    return request.app.state.due_soon_service


def notification_response(
    notification: Notification,
) -> NotificationResponse:
    """Convert the domain model into an API response."""
    if notification.id is None:
        raise RuntimeError("persisted notification has no id")

    return NotificationResponse(
        id=notification.id,
        kind=notification.kind,
        payload=notification.payload,
        created_at=notification.created_at,
        read_at=notification.read_at,
    )


@router.get("", response_model=NotificationListResponse)
def list_notifications(request: Request) -> NotificationListResponse:
    """List the logged-in user's notifications and what is due within 24 h."""
    actor = current_actor(request)
    items = notification_service(request).list_for_user(actor.user_id)
    due = due_soon_service(request).due_soon(actor)

    return NotificationListResponse(
        items=[notification_response(item) for item in items],
        due_soon=[
            DueSoonResponse(
                classroom_id=item.classroom_id,
                assignment_id=item.assignment_id,
                title=item.title,
                deadline=item.deadline,
            )
            for item in due
        ],
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    request: Request,
    notification_id: int,
) -> NotificationResponse:
    """Mark one of the current user's notifications as read."""
    actor = current_actor(request)

    with NOTIFICATION_ERRORS.translate():
        notification = notification_service(request).mark_read(
            actor.user_id,
            notification_id,
        )

    return notification_response(notification)


@router.post("/read-all", response_model=MarkAllReadResponse)
def mark_all_notifications_read(request: Request) -> MarkAllReadResponse:
    """Mark all notifications belonging to the current user as read."""
    actor = current_actor(request)
    count = notification_service(request).mark_all_read(actor.user_id)

    return MarkAllReadResponse(updated_count=count)
