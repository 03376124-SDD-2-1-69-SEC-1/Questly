"""One shared World for GenerationService tests."""

from datetime import UTC, datetime

from questly.core.assignments.models import Difficulty
from questly.core.assignments.service import AssignmentService
from questly.core.auth.models import Actor, Role
from questly.core.auth.service import AuthService
from questly.core.classrooms.service import ClassroomService
from questly.core.generation.models import DocumentSummary
from questly.core.generation.service import GenerationService
from questly.integrations.email import StubEmailSender
from tests.fakes.assignments import (
    FakeAssignmentRepository,
    FakePostingRepository,
    FakePostingStats,
    FakeVersionRepository,
)
from tests.fakes.auth import FakeAuthRepository, FakeClock, seed_user
from tests.fakes.classrooms import FakeClassroomRepository, FakeClassroomStats
from tests.fakes.generation import (
    FakeDocumentCatalog,
    FakeDraftRepository,
    FakeGenerationClient,
    FakeGenerationJobStore,
    FakeProviderQuota,
    binary_search_response,
)
from tests.fakes.notifications import RecordingMemberNotifier

DEADLINE = datetime(2026, 10, 15, 16, 59, tzinfo=UTC)


class World:
    def __init__(self, quota: int = 20) -> None:
        self.clock = FakeClock()
        users = FakeAuthRepository()
        auth = AuthService(
            users, StubEmailSender(), self.clock, base_url="http://localhost:8000"
        )
        self.classrooms = ClassroomService(
            FakeClassroomRepository(), auth, FakeClassroomStats(), self.clock
        )
        self.postings = FakePostingRepository()
        assignments = AssignmentService(
            FakeAssignmentRepository(),
            FakeVersionRepository(),
            self.postings,
            self.classrooms,
            FakePostingStats(),
            self.clock,
            RecordingMemberNotifier(),
        )
        self.drafts = FakeDraftRepository()
        self.client = FakeGenerationClient(binary_search_response())
        self.catalog = FakeDocumentCatalog()
        self.jobs = FakeGenerationJobStore()
        self.provider_quota = FakeProviderQuota()
        self.service = GenerationService(
            self.drafts,
            self.client,
            self.catalog,
            self.classrooms,
            assignments,
            self.clock,
            daily_quota=lambda: quota,
            jobs=self.jobs,
            provider_quota=self.provider_quota,
        )

        def actor(email: str, role: Role) -> Actor:
            user = seed_user(users, email=email, full_name=email, role=role)
            return Actor(user_id=user.id, role=role, full_name=email, email=email)

        self.teacher = actor("somchai.p@kmitl.ac.th", Role.INSTRUCTOR)
        self.other = actor("warunee.k@kmitl.ac.th", Role.INSTRUCTOR)
        self.student = actor("66010001@kmitl.ac.th", Role.STUDENT)
        self.catalog.by_owner[self.teacher.user_id] = [
            DocumentSummary(id=1, filename="lecture-06-searching.pdf", pages=24)
        ]
        self.sec1 = self._classroom("1")
        self.sec2 = self._classroom("2")
        self.classrooms.join(self.student, self.sec1.join_code)

    def _classroom(self, section: str):
        return self.classrooms.create(
            self.teacher,
            course_code="01076001",
            course_name="Programming I",
            section=section,
            semester="1/2569",
        )

    def generate(self):
        return self.service.generate(
            self.teacher,
            self.sec1.id,
            prompt="Binary search",
            difficulty=Difficulty.MEDIUM,
            document_ids=[1],
        )
