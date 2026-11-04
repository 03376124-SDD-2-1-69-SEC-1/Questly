"""FastAPI adapter for Drafts (ADR-0007 API contract).

`classroom_router`: POST/GET /api/v1/classrooms/{id}/drafts.
`router`: /api/v1/drafts/{id} (GET, PATCH, DELETE, /regenerate, /publish).
"""

from fastapi import APIRouter, Request, Response, status

from questly.core.assignments.models import JudgingSettings, TestCase
from questly.core.auth.current import current_actor
from questly.core.generation.models import Draft, DraftPart, DraftSettings
from questly.core.generation.schemas import (
    DraftCitation,
    DraftCreate,
    DraftPatch,
    DraftResponse,
    DraftTestCase,
    PublishRequest,
    PublishResponse,
    RegenerateRequest,
)
from questly.core.generation.service import (
    UNSET,
    ClassroomNotVisibleError,
    DocumentNotAllowedError,
    DraftNotFoundError,
    GenerationFailedError,
    GenerationService,
    MissingFieldsError,
    QuotaExceededError,
)
from questly.core.http_errors import ErrorRule, ErrorTable

router = APIRouter(prefix="/api/v1/drafts", tags=["drafts"])
classroom_router = APIRouter(prefix="/api/v1/classrooms", tags=["drafts"])


def generation_service(request: Request) -> GenerationService:
    return request.app.state.generation_service


DRAFT_ERRORS = ErrorTable(
    {
        DraftNotFoundError: ErrorRule(404, "draft_not_found", "Draft not found"),
        ClassroomNotVisibleError: ErrorRule(
            404, "classroom_not_found", "Classroom not found"
        ),
        DocumentNotAllowedError: ErrorRule(
            422, "document_not_allowed", "Choose documents from your library"
        ),
        QuotaExceededError: ErrorRule(
            429, "quota_exceeded", "No generations left today"
        ),
        GenerationFailedError: ErrorRule(
            502, "generation_failed", "The model did not respond; no quota used"
        ),
        MissingFieldsError: ErrorRule(
            422, "missing_fields", lambda error: f"Fill in: {error}"
        ),
        ValueError: ErrorRule(422, "invalid_draft", str),
    }
)


def draft_response(draft: Draft) -> DraftResponse:
    content = draft.content
    return DraftResponse(
        id=draft.id,
        classroom_id=draft.classroom_id,
        status=draft.status.value,
        prompt=draft.prompt,
        generated_at=draft.generated_at,
        title=content.title,
        problem_statement=content.problem_statement,
        difficulty=content.difficulty,
        topic_id=content.topic_id,
        test_cases=[
            DraftTestCase(
                input_data=tc.input_data,
                expected_output=tc.expected_output,
                kind=tc.kind,
                note=tc.note,
            )
            for tc in content.test_cases
        ],
        time_limit_ms=content.settings.time_limit_ms,
        language=content.settings.language,
        show_hidden_names=content.settings.show_hidden_names,
        deadline=draft.settings.deadline,
        max_score=draft.settings.max_score,
        allow_late=draft.settings.allow_late,
        allow_resubmission=draft.settings.allow_resubmission,
        classroom_ids=draft.classroom_ids,
        document_ids=draft.document_ids,
        citations=[
            DraftCitation(
                source_id=c.source_id, page=c.page, text_snapshot=c.text_snapshot
            )
            for c in draft.citations
        ],
        assignment_id=draft.assignment_id,
    )


@classroom_router.post(
    "/{classroom_id}/drafts",
    response_model=DraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate(
    request: Request, classroom_id: int, payload: DraftCreate
) -> DraftResponse:
    """T-03: generate a Draft in this Classroom (synchronous; counts one quota)."""
    with DRAFT_ERRORS.translate():
        draft = generation_service(request).generate(
            current_actor(request),
            classroom_id,
            prompt=payload.prompt,
            difficulty=payload.difficulty,
            topic_id=payload.topic_id,
            document_ids=payload.document_ids,
        )
    return draft_response(draft)


@classroom_router.get("/{classroom_id}/drafts", response_model=list[DraftResponse])
def list_drafts(request: Request, classroom_id: int) -> list[DraftResponse]:
    """T-01 Drafts: the caller's unpublished Drafts in this Classroom."""
    with DRAFT_ERRORS.translate():
        drafts = generation_service(request).drafts(
            current_actor(request), classroom_id
        )
    return [draft_response(draft) for draft in drafts]


@router.get("/{draft_id}", response_model=DraftResponse)
def get_draft(request: Request, draft_id: int) -> DraftResponse:
    with DRAFT_ERRORS.translate():
        draft = generation_service(request).draft(current_actor(request), draft_id)
    return draft_response(draft)


@router.patch("/{draft_id}", response_model=DraftResponse)
def save_draft(request: Request, draft_id: int, payload: DraftPatch) -> DraftResponse:
    """Save one T-04 step."""
    actor = current_actor(request)
    service = generation_service(request)
    with DRAFT_ERRORS.translate():
        current = service.draft(actor, draft_id)
        judging = current.content.settings
        settings = current.settings
        draft = service.save(
            actor,
            draft_id,
            title=UNSET if payload.title is None else payload.title,
            problem_statement=UNSET
            if payload.problem_statement is None
            else payload.problem_statement,
            difficulty=UNSET if payload.difficulty is None else payload.difficulty,
            topic_id=payload.topic_id
            if "topic_id" in payload.model_fields_set
            else UNSET,
            test_cases=UNSET
            if payload.test_cases is None
            else [
                TestCase(
                    input_data=tc.input_data,
                    expected_output=tc.expected_output,
                    kind=tc.kind,
                    note=tc.note,
                )
                for tc in payload.test_cases
            ],
            judging=JudgingSettings(
                time_limit_ms=judging.time_limit_ms
                if payload.time_limit_ms is None
                else payload.time_limit_ms,
                language=judging.language
                if payload.language is None
                else payload.language,
                show_hidden_names=judging.show_hidden_names
                if payload.show_hidden_names is None
                else payload.show_hidden_names,
            ),
            settings=DraftSettings(
                deadline=settings.deadline
                if payload.deadline is None
                else payload.deadline,
                max_score=settings.max_score
                if payload.max_score is None
                else payload.max_score,
                allow_late=settings.allow_late
                if payload.allow_late is None
                else payload.allow_late,
                allow_resubmission=settings.allow_resubmission
                if payload.allow_resubmission is None
                else payload.allow_resubmission,
            ),
            classroom_ids=UNSET
            if payload.classroom_ids is None
            else payload.classroom_ids,
        )
    return draft_response(draft)


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_draft(request: Request, draft_id: int) -> Response:
    with DRAFT_ERRORS.translate():
        generation_service(request).discard(current_actor(request), draft_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{draft_id}/regenerate", response_model=DraftResponse)
def regenerate(
    request: Request, draft_id: int, payload: RegenerateRequest
) -> DraftResponse:
    """T-03a "Regenerate this part"; a success counts one quota."""
    with DRAFT_ERRORS.translate():
        draft = generation_service(request).regenerate(
            current_actor(request), draft_id, DraftPart(payload.part)
        )
    return draft_response(draft)


@router.post("/{draft_id}/publish", response_model=PublishResponse)
def publish(
    request: Request, draft_id: int, payload: PublishRequest
) -> PublishResponse:
    """T-04 step 4 "Approve and publish"."""
    actor = current_actor(request)
    service = generation_service(request)
    with DRAFT_ERRORS.translate():
        if payload.classroom_ids is not None:
            service.save(actor, draft_id, classroom_ids=payload.classroom_ids)
        published = service.publish(actor, draft_id)
    return PublishResponse(
        assignment_id=published.assignment.id,
        posting_ids=[posting.id for posting in published.postings],
    )
