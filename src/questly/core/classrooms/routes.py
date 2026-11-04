"""FastAPI adapter for /api/v1/classrooms."""

from fastapi import APIRouter, Request, Response, status

from questly.core.auth.current import current_actor
from questly.core.classrooms.models import (
    Classroom,
    ClassroomFilter,
    InstructorClassroomView,
    InstructorPicker,
    MemberView,
)
from questly.core.classrooms.schemas import (
    ClassroomCreate,
    ClassroomPatch,
    ClassroomResponse,
    InstructorCardResponse,
    JoinRequest,
    MemberResponse,
    PickerResponse,
    StudentCardResponse,
    StudentProgressResponse,
)
from questly.core.classrooms.service import (
    UNSET,
    ClassroomNotFoundError,
    ClassroomService,
    InvalidJoinCodeError,
    MemberNotFoundError,
)
from questly.core.http_errors import ErrorRule, ErrorTable

router = APIRouter(prefix="/api/v1/classrooms", tags=["classrooms"])

CLASSROOM_ERRORS = ErrorTable(
    {
        ClassroomNotFoundError: ErrorRule(
            status.HTTP_404_NOT_FOUND, "classroom_not_found", "Classroom not found"
        ),
        InvalidJoinCodeError: ErrorRule(
            422, "invalid_join_code", "No classroom has this code"
        ),
        MemberNotFoundError: ErrorRule(
            status.HTTP_404_NOT_FOUND, "member_not_found", "Not a member"
        ),
    }
)


def classroom_service(request: Request) -> ClassroomService:
    return request.app.state.classroom_service


def _response(classroom: Classroom, *, show_join_code: bool) -> ClassroomResponse:
    return ClassroomResponse(
        id=classroom.id,
        course_code=classroom.course_code,
        course_name=classroom.course_name,
        section=classroom.section,
        semester=classroom.semester,
        archived=classroom.archived,
        join_code=classroom.join_code if show_join_code else None,
    )


def _member(view: MemberView) -> MemberResponse:
    return MemberResponse(
        user_id=view.user_id,
        full_name=view.full_name,
        email=view.email,
        student_number=view.student_number,
        joined_at=view.joined_at,
    )


@router.get("", response_model=PickerResponse)
def picker(
    request: Request, filter: ClassroomFilter = ClassroomFilter.ALL
) -> PickerResponse:
    """C-01: the caller's classroom cards."""
    result = classroom_service(request).picker(current_actor(request), filter)
    if isinstance(result, InstructorPicker):
        return PickerResponse(
            variant="instructor",
            instructor_cards=[
                InstructorCardResponse(
                    classroom=_response(card.classroom, show_join_code=True),
                    student_count=card.student_count,
                    problem_count=card.stats.problem_count,
                    draft_count=card.stats.draft_count,
                    avg_pass_rate=card.stats.avg_pass_rate,
                )
                for card in result.cards
            ],
            student_cards=[],
            progress=None,
        )
    return PickerResponse(
        variant="student",
        instructor_cards=[],
        student_cards=[
            StudentCardResponse(
                classroom=_response(card.classroom, show_join_code=False),
                instructor_name=card.instructor_name,
                pending_count=card.stats.pending_count,
                next_deadline=card.stats.next_deadline,
            )
            for card in result.cards
        ],
        progress=StudentProgressResponse(
            solved=result.progress.solved,
            total=result.progress.total,
            passed=result.progress.passed,
        ),
    )


@router.post("", response_model=ClassroomResponse, status_code=status.HTTP_201_CREATED)
def create(request: Request, payload: ClassroomCreate) -> ClassroomResponse:
    """C-03: create a Classroom; the response carries its Join code."""
    classroom = classroom_service(request).create(
        current_actor(request),
        course_code=payload.course_code,
        course_name=payload.course_name,
        section=payload.section,
        semester=payload.semester,
    )
    return _response(classroom, show_join_code=True)


@router.post("/join", response_model=ClassroomResponse)
def join(request: Request, payload: JoinRequest) -> ClassroomResponse:
    """C-02: join with a 6-character code."""
    with CLASSROOM_ERRORS.translate():
        classroom = classroom_service(request).join(
            current_actor(request), payload.join_code
        )
    return _response(classroom, show_join_code=False)


@router.get("/{classroom_id}", response_model=ClassroomResponse)
def get(request: Request, classroom_id: int) -> ClassroomResponse:
    """One Classroom; the Join code is shown to its Instructor only."""
    with CLASSROOM_ERRORS.translate():
        view = classroom_service(request).view(current_actor(request), classroom_id)
    return _response(
        view.classroom, show_join_code=isinstance(view, InstructorClassroomView)
    )


@router.patch("/{classroom_id}", response_model=ClassroomResponse)
def update(
    request: Request, classroom_id: int, payload: ClassroomPatch
) -> ClassroomResponse:
    """T-01 Classroom settings."""
    fields_set = payload.model_fields_set
    with CLASSROOM_ERRORS.translate():
        classroom = classroom_service(request).update(
            current_actor(request),
            classroom_id,
            course_code=payload.course_code if "course_code" in fields_set else UNSET,
            course_name=payload.course_name if "course_name" in fields_set else UNSET,
            section=payload.section if "section" in fields_set else UNSET,
            semester=payload.semester if "semester" in fields_set else UNSET,
        )
    return _response(classroom, show_join_code=True)


@router.post("/{classroom_id}/archive", response_model=ClassroomResponse)
def archive(request: Request, classroom_id: int) -> ClassroomResponse:
    with CLASSROOM_ERRORS.translate():
        classroom = classroom_service(request).archive(
            current_actor(request), classroom_id
        )
    return _response(classroom, show_join_code=True)


@router.post("/{classroom_id}/unarchive", response_model=ClassroomResponse)
def unarchive(request: Request, classroom_id: int) -> ClassroomResponse:
    with CLASSROOM_ERRORS.translate():
        classroom = classroom_service(request).unarchive(
            current_actor(request), classroom_id
        )
    return _response(classroom, show_join_code=True)


@router.post("/{classroom_id}/join-code", response_model=ClassroomResponse)
def regenerate_join_code(request: Request, classroom_id: int) -> ClassroomResponse:
    """Replace the Join code; the old one stops working."""
    with CLASSROOM_ERRORS.translate():
        classroom = classroom_service(request).regenerate_join_code(
            current_actor(request), classroom_id
        )
    return _response(classroom, show_join_code=True)


@router.delete("/{classroom_id}/join-code", response_model=ClassroomResponse)
def disable_join_code(request: Request, classroom_id: int) -> ClassroomResponse:
    with CLASSROOM_ERRORS.translate():
        classroom = classroom_service(request).disable_join_code(
            current_actor(request), classroom_id
        )
    return _response(classroom, show_join_code=True)


@router.get("/{classroom_id}/members", response_model=list[MemberResponse])
def members(request: Request, classroom_id: int) -> list[MemberResponse]:
    """T-01 Members."""
    with CLASSROOM_ERRORS.translate():
        views = classroom_service(request).members(current_actor(request), classroom_id)
    return [_member(view) for view in views]


@router.delete(
    "/{classroom_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_member(request: Request, classroom_id: int, user_id: int) -> Response:
    with CLASSROOM_ERRORS.translate():
        classroom_service(request).remove_member(
            current_actor(request), classroom_id, user_id
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
