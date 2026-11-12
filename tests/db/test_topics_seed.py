"""After `alembic upgrade head`, core.topics holds the starter Topic list (OPS-56)."""

import pytest
from sqlalchemy import text

from questly.database.session import SessionFactory

pytestmark = pytest.mark.postgres

STARTER_TOPICS = {
    "2D Arrays",
    "Arrays",
    "Binary Search",
    "Bit Manipulation",
    "Conditionals",
    "Greedy",
    "I/O & Arithmetic",
    "Loops",
    "Math",
    "Pattern Printing",
    "Queue",
    "Recursion",
    "Simulation",
    "Sorting",
    "Stack",
    "Strings",
    "Two Pointers",
}


def test_starter_topics_are_seeded(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        names = set(session.execute(text("SELECT name FROM core.topics")).scalars())

    assert names >= STARTER_TOPICS
