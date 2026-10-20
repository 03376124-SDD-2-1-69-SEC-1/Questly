"""register_seed() puts the seed PDFs in core.knowledge_documents exactly once."""

import pytest
from scripts.seed_pith_to_r2 import register_seed
from sqlmodel import select

from questly.database.core.knowledge_document_repository import (
    SQLKnowledgeDocumentRepository,
)
from questly.database.core.tables import KnowledgeDocument as DocumentRow
from questly.database.core.tables import User as UserRow
from questly.database.session import SessionFactory

pytestmark = pytest.mark.postgres

OWNER_ID = 10
ROWS = [
    {
        "task_id": f"codecube_00{n}",
        "title": f"Problem {n}",
        "topic": topic,
        "difficulty": difficulty,
        "r2_key": f"sources/programming-in-th/codecube_00{n}.pdf",
        "sha256": str(n) * 64,
    }
    for n, topic, difficulty in (
        (1, "io-arithmetic", "easy"),
        (2, "math", "easy"),
        (3, "arrays", "medium"),
    )
]


def _ensure_user(session_factory: SessionFactory, user_id: int) -> None:
    with session_factory() as session:
        if session.get(UserRow, user_id) is None:
            session.add(
                UserRow(
                    id=user_id,
                    email=f"owner{user_id}@kmitl.ac.th",
                    full_name=f"Owner {user_id}",
                    password_hash="scrypt$x",
                    role="instructor",
                )
            )
            session.commit()


def test_rerun_registers_each_seed_document_once(
    session_factory: SessionFactory, empty_core_tables: None
) -> None:
    _ensure_user(session_factory, OWNER_ID)
    repository = SQLKnowledgeDocumentRepository(session_factory)

    assert register_seed(ROWS, repository, OWNER_ID) == (3, 0)
    assert register_seed(ROWS, repository, OWNER_ID) == (0, 3)

    with session_factory() as session:
        stored = session.exec(
            select(DocumentRow)
            .where(DocumentRow.uploaded_by == OWNER_ID)
            .order_by(DocumentRow.id)
        ).all()
    assert [
        (row.r2_object_key, row.filename, row.content_hash, row.status, row.metadata_)
        for row in stored
    ] == [
        (
            row["r2_key"],
            row["title"],
            row["sha256"],
            "uploaded",
            {
                "topic": row["topic"],
                "difficulty": row["difficulty"],
                "course": "seed-pith",
                "task_id": row["task_id"],
            },
        )
        for row in ROWS
    ]
