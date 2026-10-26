"""Bind the document repository contract to the in-memory fake."""

import pytest

from questly.core.documents.models import DocumentStatus, KnowledgeDocument
from questly.core.documents.ports import KnowledgeDocumentRepository
from tests.contracts.document_repository import (
    DocumentRepositoryContract,
    SeedDocument,
    SeedUsage,
)
from tests.fakes.documents import FakeKnowledgeDocumentRepository


class TestFakeDocumentRepository(DocumentRepositoryContract):
    @pytest.fixture()
    def repository(self) -> KnowledgeDocumentRepository:
        return FakeKnowledgeDocumentRepository()

    @pytest.fixture()
    def seed_document(self, repository: KnowledgeDocumentRepository) -> SeedDocument:
        def seed(owner_id: int, filename: str) -> KnowledgeDocument:
            return repository.create(
                KnowledgeDocument(
                    filename=filename,
                    object_key=f"uploads/{filename}",
                    content_hash=f"hash-{filename}",
                    status=DocumentStatus.UPLOADED,
                ),
                owner_id,
            )

        return seed

    @pytest.fixture()
    def seed_usage(self, repository: KnowledgeDocumentRepository) -> SeedUsage:
        assert isinstance(repository, FakeKnowledgeDocumentRepository)

        def seed(document_id: int, count: int) -> None:
            repository.seed_usage(document_id, count)

        return seed
