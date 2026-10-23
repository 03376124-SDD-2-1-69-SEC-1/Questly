"""Contract shared by document repository adapters."""

from collections.abc import Callable

from questly.core.documents.models import (
    DocumentLibraryItem,
    DocumentStatus,
    KnowledgeDocument,
)
from questly.core.documents.ports import KnowledgeDocumentRepository

SeedDocument = Callable[[int, str], KnowledgeDocument]
SeedUsage = Callable[[int, int], None]


class DocumentRepositoryContract:
    """Owner isolation, usage aggregation and deletion rules."""

    def test_list_is_owner_scoped_and_counts_each_saved_draft(
        self,
        repository: KnowledgeDocumentRepository,
        seed_document: SeedDocument,
        seed_usage: SeedUsage,
    ) -> None:
        mine = seed_document(10, "mine.pdf")
        theirs = seed_document(20, "theirs.pdf")
        seed_usage(mine.id, 1)
        seed_usage(mine.id, 2)

        assert repository.list_for_owner(10) == [
            DocumentLibraryItem(document=mine, used_in=3)
        ]
        assert repository.list_for_owner(20) == [
            DocumentLibraryItem(document=theirs, used_in=0)
        ]

    def test_get_for_owner_does_not_disclose_foreign_documents(
        self,
        repository: KnowledgeDocumentRepository,
        seed_document: SeedDocument,
    ) -> None:
        document = seed_document(10, "private.pdf")

        assert repository.get_for_owner(document.id, 10) == DocumentLibraryItem(
            document=document, used_in=0
        )
        assert repository.get_for_owner(document.id, 20) is None

    def test_delete_requires_ownership_and_zero_usage(
        self,
        repository: KnowledgeDocumentRepository,
        seed_document: SeedDocument,
        seed_usage: SeedUsage,
    ) -> None:
        unused = seed_document(10, "unused.pdf")
        used = seed_document(10, "used.pdf")
        seed_usage(used.id, 1)

        assert not repository.delete_for_owner(unused.id, 20)
        assert not repository.delete_for_owner(used.id, 10)
        assert repository.delete_for_owner(unused.id, 10)
        assert repository.get_for_owner(unused.id, 10) is None

    def test_delete_refuses_a_document_that_left_the_uploaded_state(
        self,
        repository: KnowledgeDocumentRepository,
        seed_document: SeedDocument,
    ) -> None:
        document = seed_document(10, "ingesting.pdf")
        assert repository.update_status_for_owner(
            document.id,
            10,
            expected_status=DocumentStatus.UPLOADED,
            new_status=DocumentStatus.INGESTING,
        )

        assert not repository.delete_for_owner(document.id, 10)

        kept = repository.get_for_owner(document.id, 10)
        assert kept is not None
        assert kept.document.status is DocumentStatus.INGESTING

    def test_update_status_requires_ownership_and_expected_status(
        self,
        repository: KnowledgeDocumentRepository,
        seed_document: SeedDocument,
    ) -> None:
        document = seed_document(10, "status.pdf")

        assert repository.update_status_for_owner(
            document.id,
            10,
            expected_status=document.status,
            new_status=DocumentStatus.READY,
        )

        updated = repository.get_for_owner(document.id, 10)
        assert updated is not None
        assert updated.document.status is DocumentStatus.READY

        assert not repository.update_status_for_owner(
            document.id,
            20,
            expected_status=DocumentStatus.READY,
            new_status=DocumentStatus.FAILED,
        )

        assert not repository.update_status_for_owner(
            document.id,
            10,
            expected_status=DocumentStatus.UPLOADED,
            new_status=DocumentStatus.FAILED,
        )

        unchanged = repository.get_for_owner(document.id, 10)
        assert unchanged is not None
        assert unchanged.document.status is DocumentStatus.READY

    def test_update_status_moves_a_failed_document_back_to_uploaded(
        self,
        repository: KnowledgeDocumentRepository,
        seed_document: SeedDocument,
    ) -> None:
        document = seed_document(10, "retry.pdf")
        assert repository.update_status_for_owner(
            document.id,
            10,
            expected_status=DocumentStatus.UPLOADED,
            new_status=DocumentStatus.FAILED,
        )

        assert repository.update_status_for_owner(
            document.id,
            10,
            expected_status=DocumentStatus.FAILED,
            new_status=DocumentStatus.UPLOADED,
        )
        assert not repository.update_status_for_owner(
            document.id,
            10,
            expected_status=DocumentStatus.FAILED,
            new_status=DocumentStatus.UPLOADED,
        )

        retried = repository.get_for_owner(document.id, 10)
        assert retried is not None
        assert retried.document.status is DocumentStatus.UPLOADED

    def test_update_status_returns_false_for_missing_document(
        self,
        repository: KnowledgeDocumentRepository,
    ) -> None:
        assert not repository.update_status_for_owner(
            999,
            10,
            expected_status=DocumentStatus.UPLOADED,
            new_status=DocumentStatus.READY,
        )
