"""Bind the vector deletion contract to PostgreSQL on a throwaway Neon branch.

The contract uses fixed `core_document_id`s, so every source it creates (all
carry `MODEL`) is removed, chunks by cascade, before and after every test.
Sources of other embedding models are left alone.
"""

from collections.abc import Generator

import pytest
from sqlalchemy import select, text

from questly.ai.app.repository import VectorRepository
from questly.database.rag.tables import KnowledgeChunk
from questly.database.rag.vector_repository import PostgresVectorRepository
from questly.database.session import SessionFactory
from tests.contracts.vector_repository import (
    MODEL,
    ChunkIdsForSource,
    VectorRepositoryContract,
)

pytestmark = pytest.mark.postgres


class TestPostgresVectorRepository(VectorRepositoryContract):
    @pytest.fixture(autouse=True)
    def remove_contract_sources(
        self, session_factory: SessionFactory
    ) -> Generator[None, None, None]:
        def empty() -> None:
            with session_factory() as session:
                session.execute(
                    text(
                        "DELETE FROM rag.knowledge_sources WHERE embedding_model = :m"
                    ),
                    {"m": MODEL},
                )
                session.commit()

        empty()
        yield
        empty()

    @pytest.fixture()
    def repository(self, session_factory: SessionFactory) -> VectorRepository:
        return PostgresVectorRepository(session_factory)

    @pytest.fixture()
    def chunk_ids_for_source(
        self, session_factory: SessionFactory
    ) -> ChunkIdsForSource:
        def chunk_ids(source_id: int) -> set[int]:
            with session_factory() as session:
                rows = session.execute(
                    select(KnowledgeChunk.id).where(
                        KnowledgeChunk.source_id == source_id
                    )
                )
                return set(rows.scalars())

        return chunk_ids
