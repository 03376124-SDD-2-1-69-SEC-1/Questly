"""Bind the vector deletion contract to the in-memory adapter."""

import pytest

from questly.ai.app.repository import VectorRepository
from tests.contracts.vector_repository import (
    ChunkIdsForSource,
    VectorRepositoryContract,
)
from tests.fakes.vector import FakeVectorRepository


class TestFakeVectorRepository(VectorRepositoryContract):
    @pytest.fixture()
    def repository(self) -> VectorRepository:
        return FakeVectorRepository()

    @pytest.fixture()
    def chunk_ids_for_source(self, repository: VectorRepository) -> ChunkIdsForSource:
        assert isinstance(repository, FakeVectorRepository)
        return repository.chunk_ids_for_source
