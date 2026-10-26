"""Build an application wired entirely to fakes.

`create_app()` with no arguments builds SQL and R2 adapters, so every test that
drives HTTP goes through here instead: one place that decides what a test app is
made of, and no test that accidentally reaches real infrastructure.
"""

from __future__ import annotations

from fastapi import FastAPI

from questly.ai.app.repository import VectorRepository
from questly.config import DEFAULT_MAX_UPLOAD_SIZE_BYTES, Settings
from questly.core.admin.ports import AdminRepository, SystemHealthChecker
from questly.core.assignments.ports import (
    AssignmentRepository,
    PostingRepository,
    PostingStats,
    VersionRepository,
)
from questly.core.auth.pages import DemoAccount
from questly.core.auth.ports import AuthRepository, Clock
from questly.core.classrooms.ports import ClassroomRepository, ClassroomStats
from questly.core.documents.ports import KnowledgeDocumentRepository, ObjectStorage
from questly.core.generation.ports import (
    DocumentCatalog,
    DraftRepository,
    GenerationClient,
    GenerationJobStore,
    ProviderQuota,
)
from questly.core.submissions.ports import SubmissionRepository
from questly.core.topics.ports import TopicRepository
from questly.integrations.email import StubEmailSender
from questly.main import Runner, create_app
from tests.fakes.admin import FakeAdminRepository, FakeSystemHealthChecker
from tests.fakes.assignments import (
    FakeAssignmentRepository,
    FakePostingRepository,
    FakeVersionRepository,
)
from tests.fakes.auth import FakeAuthRepository, FakeClock
from tests.fakes.classrooms import FakeClassroomRepository, FakeClassroomStats
from tests.fakes.documents import FakeKnowledgeDocumentRepository, FakeObjectStorage
from tests.fakes.generation import (
    FakeDocumentCatalog,
    FakeDraftRepository,
    FakeGenerationJobStore,
    FakeProviderQuota,
)
from tests.fakes.notifications import FakeNotificationRepository
from tests.fakes.submissions import FakeSubmissionRepository, ScriptedCodeRunner
from tests.fakes.topics import FakeTopicRepository
from tests.fakes.vector import FakeVectorRepository


def fake_settings(
    max_upload_size_bytes: int = DEFAULT_MAX_UPLOAD_SIZE_BYTES,
) -> Settings:
    """Return settings that name no real database or bucket.

    Nothing dials these values: a test app is built from fakes, and the two
    health routes that would use them are not exercised here.
    """
    return Settings(
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        r2_endpoint_url="https://unused.r2.cloudflarestorage.com",
        r2_bucket_name="unused-bucket",
        r2_access_key_id="unused",
        r2_secret_access_key="unused",
        max_upload_size_bytes=max_upload_size_bytes,
    )


def build_app(
    *,
    settings: Settings | None = None,
    topic_repository: TopicRepository | None = None,
    assignment_repository: AssignmentRepository | None = None,
    version_repository: VersionRepository | None = None,
    posting_repository: PostingRepository | None = None,
    posting_stats: PostingStats | None = None,
    knowledge_document_repository: KnowledgeDocumentRepository | None = None,
    object_storage: ObjectStorage | None = None,
    generation_client: GenerationClient | None = None,
    provider_quota: ProviderQuota | None = None,
    generation_job_store: GenerationJobStore | None = None,
    draft_repository: DraftRepository | None = None,
    document_catalog: DocumentCatalog | None = None,
    vector_repository: VectorRepository | None = None,
    auth_repository: AuthRepository | None = None,
    notification_repository: FakeNotificationRepository | None = None,
    admin_repository: AdminRepository | None = None,
    system_health_checker: SystemHealthChecker | None = None,
    verification_mailer: StubEmailSender | None = None,
    clock: Clock | None = None,
    classroom_repository: ClassroomRepository | None = None,
    classroom_stats: ClassroomStats | None = None,
    submission_repository: SubmissionRepository | None = None,
    code_runner: Runner | None = None,
    demo_accounts: tuple[DemoAccount, ...] | None = None,
    max_upload_size_bytes: int = DEFAULT_MAX_UPLOAD_SIZE_BYTES,
) -> FastAPI:
    """Return an app whose every port is backed by a fake."""
    if topic_repository is None:
        topic_repository = FakeTopicRepository()
    if assignment_repository is None:
        assignment_repository = FakeAssignmentRepository()
    if version_repository is None:
        version_repository = FakeVersionRepository()
    if posting_repository is None:
        posting_repository = FakePostingRepository()
    if submission_repository is None:
        submission_repository = FakeSubmissionRepository()
    if code_runner is None:
        code_runner = ScriptedCodeRunner()
    if knowledge_document_repository is None:
        knowledge_document_repository = FakeKnowledgeDocumentRepository()
    if object_storage is None:
        object_storage = FakeObjectStorage()
    if provider_quota is None:
        provider_quota = FakeProviderQuota()
    if generation_job_store is None:
        generation_job_store = FakeGenerationJobStore()
    if draft_repository is None:
        draft_repository = FakeDraftRepository()
    if document_catalog is None:
        document_catalog = FakeDocumentCatalog()
    if vector_repository is None:
        vector_repository = FakeVectorRepository()
    if auth_repository is None:
        auth_repository = FakeAuthRepository()
    if admin_repository is None:
        admin_repository = FakeAdminRepository()
    if system_health_checker is None:
        system_health_checker = FakeSystemHealthChecker()
    if verification_mailer is None:
        verification_mailer = StubEmailSender()
    if clock is None:
        clock = FakeClock()
    if notification_repository is None:
        notification_repository = FakeNotificationRepository()
    if classroom_repository is None:
        classroom_repository = FakeClassroomRepository()
    if classroom_stats is None:
        classroom_stats = FakeClassroomStats()

    return create_app(
        settings=settings or fake_settings(max_upload_size_bytes=max_upload_size_bytes),
        topic_repository=topic_repository,
        assignment_repository=assignment_repository,
        version_repository=version_repository,
        posting_repository=posting_repository,
        posting_stats=posting_stats,
        knowledge_document_repository=knowledge_document_repository,
        object_storage=object_storage,
        generation_client=generation_client,
        provider_quota=provider_quota,
        generation_job_store=generation_job_store,
        draft_repository=draft_repository,
        document_catalog=document_catalog,
        vector_repository=vector_repository,
        auth_repository=auth_repository,
        email_sender=verification_mailer,
        notification_repository=notification_repository,
        admin_repository=admin_repository,
        system_health_checker=system_health_checker,
        clock=clock,
        classroom_repository=classroom_repository,
        classroom_stats=classroom_stats,
        submission_repository=submission_repository,
        code_runner=code_runner,
        demo_accounts=demo_accounts,
        # httpx talks plain HTTP to the ASGI app and drops Secure cookies.
        secure_cookies=False,
    )
