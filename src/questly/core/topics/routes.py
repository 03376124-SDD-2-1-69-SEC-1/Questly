"""FastAPI adapter for the Topic reference API."""

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request, Response, status

from questly.core.auth.current import current_actor
from questly.core.topics.models import Topic
from questly.core.topics.schemas import (
    TopicCreate,
    TopicPatch,
    TopicReplace,
    TopicResponse,
)
from questly.core.topics.service import (
    UNSET,
    TopicNameConflictError,
    TopicNotFoundError,
    TopicService,
)

router = APIRouter(prefix="/api/v1/topics", tags=["topics"])


def _service(request: Request) -> TopicService:
    return request.app.state.topic_service


def _response(topic: Topic) -> TopicResponse:
    return TopicResponse(id=topic.id, name=topic.name, description=topic.description)


def _raise_not_found() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "topic_not_found", "message": "Topic not found"},
    )


def _raise_name_conflict() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "topic_name_conflict", "message": "Topic name already in use"},
    )


@router.post("", response_model=TopicResponse, status_code=status.HTTP_201_CREATED)
def create_topic(request: Request, payload: TopicCreate) -> TopicResponse:
    """Create a reusable Topic."""
    try:
        return _response(
            _service(request).create(
                current_actor(request),
                name=payload.name,
                description=payload.description,
            )
        )
    except TopicNameConflictError:
        _raise_name_conflict()


@router.get("", response_model=list[TopicResponse])
def list_topics(request: Request) -> list[TopicResponse]:
    """List every Topic in deterministic name order."""
    return [_response(topic) for topic in _service(request).list()]


@router.get("/{topic_id}", response_model=TopicResponse)
def get_topic(request: Request, topic_id: int) -> TopicResponse:
    """Get one Topic."""
    try:
        return _response(_service(request).get(topic_id))
    except TopicNotFoundError:
        _raise_not_found()


@router.put("/{topic_id}", response_model=TopicResponse)
def replace_topic(
    request: Request,
    topic_id: int,
    payload: TopicReplace,
) -> TopicResponse:
    """Replace one Topic."""
    try:
        return _response(
            _service(request).replace(
                current_actor(request),
                topic_id=topic_id,
                name=payload.name,
                description=payload.description,
            )
        )
    except TopicNotFoundError:
        _raise_not_found()
    except TopicNameConflictError:
        _raise_name_conflict()


@router.patch("/{topic_id}", response_model=TopicResponse)
def patch_topic(
    request: Request,
    topic_id: int,
    payload: TopicPatch,
) -> TopicResponse:
    """Update only the fields present in the request body."""
    fields_set = payload.model_fields_set
    try:
        return _response(
            _service(request).patch(
                current_actor(request),
                topic_id=topic_id,
                name=payload.name if "name" in fields_set else UNSET,
                description=(
                    payload.description if "description" in fields_set else UNSET
                ),
            )
        )
    except TopicNotFoundError:
        _raise_not_found()
    except TopicNameConflictError:
        _raise_name_conflict()


@router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(request: Request, topic_id: int) -> Response:
    """Delete one Topic."""
    try:
        _service(request).delete(current_actor(request), topic_id)
    except TopicNotFoundError:
        _raise_not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
