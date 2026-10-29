"""Topic use cases independent from HTTP and database technology."""

from questly.core.auth.models import Actor, PermissionDeniedError, Role
from questly.core.topics.models import Topic
from questly.core.topics.ports import TopicRepository


class TopicNameConflictError(Exception):
    """Raised when a normalized Topic name is already in use."""


class TopicNotFoundError(Exception):
    """Raised when a requested Topic does not exist."""


class _Unset:
    """Marks a PATCH field left out of the request body."""


UNSET = _Unset()


class TopicService:
    """Coordinates Topic rules through a repository seam."""

    def __init__(self, repository: TopicRepository) -> None:
        self._repository = repository

    def create(self, actor: Actor, *, name: str, description: str | None) -> Topic:
        _require_instructor(actor)
        name, description = _normalize_values(name, description)
        self._ensure_name_available(name)
        return self._repository.create(Topic(name=name, description=description))

    def list(self) -> list[Topic]:
        return sorted(self._repository.list(), key=lambda topic: topic.name.casefold())

    def get(self, topic_id: int) -> Topic:
        topic = self._repository.get(topic_id)
        if topic is None:
            raise TopicNotFoundError
        return topic

    def replace(
        self,
        actor: Actor,
        *,
        topic_id: int,
        name: str,
        description: str | None,
    ) -> Topic:
        _require_instructor(actor)
        self.get(topic_id)
        name, description = _normalize_values(name, description)
        self._ensure_name_available(name, excluding_topic_id=topic_id)
        return self._repository.update(
            Topic(id=topic_id, name=name, description=description)
        )

    def patch(
        self,
        actor: Actor,
        *,
        topic_id: int,
        name: str | _Unset = UNSET,
        description: str | None | _Unset = UNSET,
    ) -> Topic:
        _require_instructor(actor)
        current = self.get(topic_id)
        new_name = current.name if isinstance(name, _Unset) else name
        new_description = (
            current.description if isinstance(description, _Unset) else description
        )
        new_name, new_description = _normalize_values(new_name, new_description)
        self._ensure_name_available(new_name, excluding_topic_id=topic_id)
        return self._repository.update(
            Topic(id=topic_id, name=new_name, description=new_description)
        )

    def delete(self, actor: Actor, topic_id: int) -> None:
        _require_instructor(actor)
        if not self._repository.delete(topic_id):
            raise TopicNotFoundError

    def _ensure_name_available(
        self,
        name: str,
        *,
        excluding_topic_id: int | None = None,
    ) -> None:
        """Reject a name another Topic already holds.

        Asking the repository is what lets the SQL adapter answer with one
        indexed query instead of loading every row, and keeps the rule identical
        in both adapters.
        """
        owner = self._repository.find_by_name(name)
        if owner is None:
            return
        if excluding_topic_id is not None and owner.id == excluding_topic_id:
            return
        raise TopicNameConflictError


def _require_instructor(actor: Actor) -> None:
    """Topics are shared reference data: only Instructors change them."""
    if actor.role is not Role.INSTRUCTOR:
        raise PermissionDeniedError


def _normalize_values(name: str, description: str | None) -> tuple[str, str | None]:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("name must not be blank")
    if description is None:
        return normalized_name, None
    normalized_description = description.strip()
    if not normalized_description:
        return normalized_name, None
    return normalized_name, normalized_description
