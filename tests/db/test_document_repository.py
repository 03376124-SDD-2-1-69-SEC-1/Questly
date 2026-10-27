"""SQLKnowledgeDocumentRepository against the shared contract, on real Postgres."""

from datetime import UTC, datetime
from itertools import count

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from questly.core.documents.models import DocumentStatus, KnowledgeDocument
from questly.core.documents.ports import DocumentRepositoryError
from questly.database.core.knowledge_document_repository import (
    SQLKnowledgeDocumentRepository,
)
from questly.database.core.tables import Classroom as ClassroomRow
from questly.database.core.tables import DraftRow
from questly.database.core.tables import KnowledgeDocument as DocumentRow
from questly.database.core.tables import User as UserRow
from questly.database.session import SessionFactory
from tests.contracts.document_repository import (
    DocumentRepositoryContract,
    SeedDocument,
    SeedUsage,
)

pytestmark = pytest.mark.postgres


def _ensure_user(session_factory: SessionFactory, user_id: int) -> None:
    """The contract names owners by fixed id, so the rows must carry those ids."""
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


class _LostConnection:
    """A session whose first statement fails the way a dropped connection does."""

    def __enter__(self) -> "_LostConnection":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, *args: object, **kwargs: object) -> None:
        raise OperationalError("DELETE", {}, Exception("connection lost"))


def _document(
    filename: str = "lecture.pdf",
    object_key: str = "uploads/abc-lecture.pdf",
    content_hash: str = "a" * 64,
) -> KnowledgeDocument:
    return KnowledgeDocument(
        filename=filename, object_key=object_key, content_hash=content_hash
    )


class TestSQLDocumentRepository(DocumentRepositoryContract):
    """Run the contract against the SQL adapter."""

    @pytest.fixture()
    def repository(
        self, session_factory: SessionFactory, empty_core_tables: None
    ) -> SQLKnowledgeDocumentRepository:
        return SQLKnowledgeDocumentRepository(session_factory)

    @pytest.fixture()
    def seed_document(
        self,
        session_factory: SessionFactory,
        repository: SQLKnowledgeDocumentRepository,
    ) -> SeedDocument:
        def seed(owner_id: int, filename: str) -> KnowledgeDocument:
            _ensure_user(session_factory, owner_id)
            return repository.create(
                _document(
                    filename=filename,
                    object_key=f"uploads/{owner_id}-{filename}",
                    content_hash=f"hash-{owner_id}-{filename}",
                ),
                owner_id,
            )

        return seed

    @pytest.fixture()
    def seed_usage(self, session_factory: SessionFactory) -> SeedUsage:
        ids = count(1)

        def seed(document_id: int, uses: int) -> None:
            with session_factory() as session:
                document = session.get(DocumentRow, document_id)
                assert document is not None
                owner = session.get(UserRow, document.uploaded_by)
                assert owner is not None
                classroom = ClassroomRow(
                    instructor_id=owner.id,
                    course_code="01076001",
                    course_name="Programming",
                    section=str(next(ids)),
                    semester="1/2569",
                )
                session.add(classroom)
                session.commit()
                for _ in range(uses):
                    session.add(
                        DraftRow(
                            classroom_id=classroom.id,
                            requested_by=owner.id,
                            prompt="Make a problem.",
                            document_ids=[document_id],
                            generated_at=datetime.now(UTC),
                        )
                    )
                session.commit()

        return seed

    def test_get_for_owner_counts_saved_drafts(
        self,
        repository: SQLKnowledgeDocumentRepository,
        seed_document: SeedDocument,
        seed_usage: SeedUsage,
    ) -> None:
        """The service refuses to delete a document on this number."""
        document = seed_document(10, "used.pdf")
        seed_usage(document.id, 2)

        item = repository.get_for_owner(document.id, 10)

        assert item is not None
        assert item.used_in == 2

    def test_create_records_the_uploader_and_status(
        self,
        session_factory: SessionFactory,
        repository: SQLKnowledgeDocumentRepository,
    ) -> None:
        _ensure_user(session_factory, 10)

        created = repository.create(_document(), 10)

        assert isinstance(created.id, int)
        assert created.status is DocumentStatus.UPLOADED
        assert repository.get_for_owner(created.id, 10) is not None
        with session_factory() as session:
            stored = session.execute(
                text("SELECT uploaded_by FROM core.knowledge_documents")
            ).all()
        assert [tuple(row) for row in stored] == [(10,)]

    def test_create_writes_metadata_and_reads_it_back(
        self,
        session_factory: SessionFactory,
        repository: SQLKnowledgeDocumentRepository,
    ) -> None:
        """Seed documents carry topic and difficulty for ingestion to copy."""
        _ensure_user(session_factory, 10)
        metadata = {"topic": "math", "difficulty": "easy", "course": "seed-pith"}

        created = repository.create(
            KnowledgeDocument(
                filename="gcd.pdf",
                object_key="sources/programming-in-th/codecube_002.pdf",
                content_hash="e" * 64,
                metadata=metadata,
            ),
            10,
        )

        assert created.metadata == metadata
        assert repository.get_by_content_hash("e" * 64, 10) == created
        with session_factory() as session:
            stored = session.get(DocumentRow, created.id)
        assert stored is not None
        assert stored.metadata_ == metadata

    def test_content_hash_lookup_is_scoped_to_the_uploader(
        self,
        session_factory: SessionFactory,
        repository: SQLKnowledgeDocumentRepository,
    ) -> None:
        _ensure_user(session_factory, 10)
        _ensure_user(session_factory, 20)
        created = repository.create(_document(content_hash="b" * 64), 10)

        assert repository.get_by_content_hash("b" * 64, 10) == created
        assert repository.get_by_content_hash("b" * 64, 20) is None
        assert repository.get_by_content_hash("c" * 64, 10) is None

    def test_duplicate_object_key_is_rejected(
        self,
        session_factory: SessionFactory,
        repository: SQLKnowledgeDocumentRepository,
    ) -> None:
        """`r2_object_key` is UNIQUE: one row per stored object."""
        _ensure_user(session_factory, 10)
        repository.create(_document(object_key="uploads/same-key.pdf"), 10)

        with pytest.raises(IntegrityError):
            repository.create(
                _document(object_key="uploads/same-key.pdf", content_hash="d" * 64),
                10,
            )

    def test_database_failure_on_delete_surfaces_as_repository_error(self) -> None:
        """The service restores the stored object only on this error type."""
        broken = SQLKnowledgeDocumentRepository(_LostConnection)

        with pytest.raises(DocumentRepositoryError):
            broken.delete_for_owner(1, 10)

    def test_database_failure_on_status_update_surfaces_as_repository_error(
        self,
    ) -> None:
        """A lost connection is a repository error, not a silent False."""
        broken = SQLKnowledgeDocumentRepository(_LostConnection)

        with pytest.raises(DocumentRepositoryError):
            broken.update_status_for_owner(
                1,
                10,
                expected_status=DocumentStatus.UPLOADED,
                new_status=DocumentStatus.INGESTING,
            )

    def test_database_failure_on_fail_ingesting_surfaces_as_repository_error(
        self,
    ) -> None:
        """A lost connection is a repository error, not a count of zero."""
        broken = SQLKnowledgeDocumentRepository(_LostConnection)

        with pytest.raises(DocumentRepositoryError):
            broken.fail_ingesting()
