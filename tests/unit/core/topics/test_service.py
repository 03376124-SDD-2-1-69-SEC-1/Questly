"""Tests for Topic application rules."""

import pytest

from questly.core.auth.models import Actor, PermissionDeniedError, Role
from questly.core.topics.service import (
    TopicNameConflictError,
    TopicNotFoundError,
    TopicService,
)
from tests.fakes.topics import FakeTopicRepository

TEACHER = Actor(
    user_id=1, role=Role.INSTRUCTOR, full_name="Somchai", email="t@kmitl.ac.th"
)
STUDENT = Actor(
    user_id=2, role=Role.STUDENT, full_name="Nattapong", email="s@kmitl.ac.th"
)


def test_create_normalizes_topic_values() -> None:
    service = TopicService(FakeTopicRepository())

    topic = service.create(
        TEACHER, name="  Graphs ", description="  Network problems  "
    )

    assert topic.name == "Graphs"
    assert topic.description == "Network problems"


def test_create_assigns_an_int_id() -> None:
    service = TopicService(FakeTopicRepository())

    topic = service.create(TEACHER, name="Graphs", description=None)

    assert isinstance(topic.id, int)


def test_create_rejects_case_insensitive_duplicate_name() -> None:
    service = TopicService(FakeTopicRepository())
    service.create(TEACHER, name="Graphs", description=None)

    with pytest.raises(TopicNameConflictError):
        service.create(TEACHER, name="graphs", description=None)


def test_list_is_ordered_by_normalized_name() -> None:
    service = TopicService(FakeTopicRepository())
    service.create(TEACHER, name="Trees", description=None)
    service.create(TEACHER, name="arrays", description=None)

    assert [topic.name for topic in service.list()] == ["arrays", "Trees"]


def test_replace_overwrites_the_description() -> None:
    service = TopicService(FakeTopicRepository())
    topic = service.create(TEACHER, name="Graphs", description="Network problems")

    replaced = service.replace(
        TEACHER, topic_id=topic.id, name="Graphs", description=None
    )

    assert replaced.description is None


def test_patch_updates_only_the_given_field() -> None:
    service = TopicService(FakeTopicRepository())
    topic = service.create(TEACHER, name="Graphs", description="Network problems")

    patched = service.patch(TEACHER, topic_id=topic.id, name="Trees")

    assert patched.name == "Trees"
    assert patched.description == "Network problems"


def test_patch_with_no_fields_is_a_no_op() -> None:
    service = TopicService(FakeTopicRepository())
    topic = service.create(TEACHER, name="Graphs", description="Network problems")

    patched = service.patch(TEACHER, topic_id=topic.id)

    assert patched.name == "Graphs"
    assert patched.description == "Network problems"


def test_patch_unknown_topic_raises_not_found() -> None:
    service = TopicService(FakeTopicRepository())

    with pytest.raises(TopicNotFoundError):
        service.patch(TEACHER, topic_id=999, name="Trees")


def test_patch_rejects_case_insensitive_duplicate_name() -> None:
    service = TopicService(FakeTopicRepository())
    service.create(TEACHER, name="Graphs", description=None)
    topic = service.create(TEACHER, name="Trees", description=None)

    with pytest.raises(TopicNameConflictError):
        service.patch(TEACHER, topic_id=topic.id, name="graphs")


def test_delete_unknown_topic_raises_not_found() -> None:
    service = TopicService(FakeTopicRepository())

    with pytest.raises(TopicNotFoundError):
        service.delete(TEACHER, 999)


@pytest.mark.parametrize("actor", [STUDENT])
def test_only_instructors_may_change_topics(actor: Actor) -> None:
    service = TopicService(FakeTopicRepository())
    topic = service.create(TEACHER, name="Graphs", description=None)

    with pytest.raises(PermissionDeniedError):
        service.create(actor, name="Trees", description=None)
    with pytest.raises(PermissionDeniedError):
        service.replace(actor, topic_id=topic.id, name="Trees", description=None)
    with pytest.raises(PermissionDeniedError):
        service.patch(actor, topic_id=topic.id, name="Trees")
    with pytest.raises(PermissionDeniedError):
        service.delete(actor, topic.id)

    assert [t.name for t in service.list()] == ["Graphs"]
