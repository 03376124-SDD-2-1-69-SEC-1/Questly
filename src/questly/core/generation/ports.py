"""Ports the generation slice needs.

ADR-0007 Drafts use DraftRepository, DocumentCatalog, ClassroomLookup and
AssignmentPublisher; production wires the SQL adapters in `database/core/`
for the first two (OPS-15), ClassroomService for the lookup, and
AssignmentService as the publisher. GenerationRepository (requests and
artifacts) is the pre-ADR shape: no service uses it, and since OPS-15 it has
no SQL adapter, only the in-memory one in `tests/fakes/generation.py`
(ADR-0008).

`ai/client.py` satisfies `GenerationClient`, with a stub today and an HTTP
adapter later (OPS-04). Nothing in `core/` may import an adapter; only
`main.py` wires them in.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from questly.core.assignments.models import (
    AssignmentContent,
    PublishedAssignment,
    Schedule,
)
from questly.core.auth.models import Actor
from questly.core.classrooms.models import Classroom, ClassroomRole
from questly.core.generation.models import (
    AssignmentDraft,
    Citation,
    DocumentSummary,
    Draft,
    GenerationArtifact,
    GenerationJob,
    GenerationStep,
    ProviderQuotaSnapshot,
    StartedJob,
)
from questly.core.generation.schemas import GenerationRequest, GenerationResponse


class GenerationClient(Protocol):
    """Boundary to whatever produces an Assignment draft from a prompt."""

    def is_available(self) -> bool:
        """Whether this adapter is configured to generate; makes no network call."""
        ...

    def generate(
        self, request: GenerationRequest, on_step: Callable[[GenerationStep], None]
    ) -> GenerationResponse:
        """Return a draft and its supporting citations.

        Calls `on_step` as each real stage starts. `on_step` may raise to stop
        the work (the job was cancelled); the client lets that propagate.
        """
        ...


class GenerationRepository(Protocol):
    """Persistence operations required by GenerationService.

    A request and its artifact are tracked as separate rows because a request
    can fail before ever producing one. `create_request` is the only
    operation allowed to assign a request its id, and `create_artifact` is the
    only one allowed to assign an artifact its id -- callers must always use
    the returned value, never a guess at what it will be.
    """

    def create_request(self, prompt: str, filters: dict[str, object]) -> int:
        """Insert a pending request row and return its generated id."""
        ...

    def mark_request_completed(self, request_id: int) -> None:
        """Mark a request as completed."""
        ...

    def mark_request_failed(self, request_id: int, error_code: str) -> None:
        """Mark a request as failed, recording why."""
        ...

    def create_artifact(
        self, request_id: int, draft: AssignmentDraft, citations: list[Citation]
    ) -> GenerationArtifact:
        """Insert an artifact linked to `request_id` and return it with an id."""
        ...

    def get(self, artifact_id: int) -> GenerationArtifact | None:
        """Return an artifact by id when present."""
        ...


class DraftRepository(Protocol):
    """Drafts and the log of successful generations the daily Quota counts.

    `create` is the only operation that assigns a Draft its id.
    """

    def create(self, draft: Draft) -> Draft: ...

    def get(self, draft_id: int) -> Draft | None: ...

    def update(self, draft: Draft) -> Draft: ...

    def delete(self, draft_id: int) -> bool: ...

    def list_for_classroom(self, classroom_id: int) -> list[Draft]: ...

    def record_generation(self, user_id: int, at: datetime) -> None: ...

    def count_generations(self, user_id: int, since: datetime) -> int: ...

    def count_all_generations(self, since: datetime) -> int:
        """Every user's generations at or after `since` (A-01 usage)."""
        ...


class DocumentCatalog(Protocol):
    """The Instructor's Document library, owned by the documents slice."""

    def documents_of(self, owner_id: int) -> list[DocumentSummary]: ...


class ClassroomLookup(Protocol):
    """What generation needs from the classrooms slice."""

    def role_of(self, actor: Actor, classroom_id: int) -> ClassroomRole: ...

    def owned_classrooms(self, actor: Actor) -> list[Classroom]: ...

    def member_ids(self, actor: Actor, classroom_id: int) -> list[int]: ...


class AssignmentPublisher(Protocol):
    """T-04 step 4 hands the reviewed content to the assignments slice."""

    def publish(
        self,
        actor: Actor,
        content: AssignmentContent,
        schedule: Schedule,
        classroom_ids: list[int],
        artifact_id: int | None = None,
    ) -> PublishedAssignment: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class ProviderQuota(Protocol):
    """What the model provider still allows today (T-03, A-01).

    `snapshot` never raises: when the provider cannot be asked, the snapshot
    says so (`QuotaKind.UNKNOWN`). `invalidate` drops any cached answer; the
    service calls it after a generation succeeds so the number drops at once.
    """

    def snapshot(self) -> ProviderQuotaSnapshot: ...

    def invalidate(self) -> None: ...


class GenerationJobStore(Protocol):
    """Running and recent Generate jobs (ADR-0010).

    `create_unless_running` is the only operation that assigns a job its id.
    It returns the user's job already RUNNING in that Classroom instead of
    making another, and does so atomically: two clicks at once make one job.
    `update`
    applies `change` to the stored job as one step no other writer can
    interleave with, stores the result and returns it; if `change` raises,
    the stored job is left as it was and the error propagates. An unknown id
    raises KeyError.
    """

    def create_unless_running(self, job: GenerationJob) -> StartedJob: ...

    def get(self, job_id: int) -> GenerationJob | None: ...

    def update(
        self, job_id: int, change: Callable[[GenerationJob], GenerationJob]
    ) -> GenerationJob: ...

    def running_for(self, user_id: int, classroom_id: int) -> GenerationJob | None:
        """This user's job still RUNNING in this Classroom, if any."""
        ...
