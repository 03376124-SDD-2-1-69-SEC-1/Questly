"""FastAPI adapter for Assignments, Versions and Postings (ADR-0007 API contract).

Two routers: `router` under /api/v1/assignments for content and Versions,
`classroom_router` under /api/v1/classrooms/{id}/... for Postings and the
Summary tab.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request, Response, status

from questly.core.assignments.models import (
    Assignment,
    AssignmentVersion,
    InstructorProblem,
    InstructorProblemList,
    InstructorSummary,
    JudgingSettings,
    Posting,
    StudentProblem,
    TestCase,
)
from questly.core.assignments.schemas import (
    AssignmentPatch,
    AssignmentResponse,
    FailRateResponse,
    PostingPatch,
    PostingResponse,
    ProblemListResponse,
    ProblemResponse,
    ScoreRowResponse,
    SummaryResponse,
    TestCaseResponse,
    VersionResponse,
)
from questly.core.assignments.service import (
    UNSET,
    AssignmentNotFoundError,
    AssignmentService,
    ClassroomNotVisibleError,
    PostingNotFoundError,
    TestCaseNotFoundError,
    VersionNotFoundError,
)
from questly.core.auth.current import current_actor
from questly.core.classrooms.csv_export import csv_response

router = APIRouter(prefix="/api/v1/assignments", tags=["assignments"])
classroom_router = APIRouter(prefix="/api/v1/classrooms", tags=["assignments"])

_NOT_FOUND = {
    AssignmentNotFoundError: ("assignment_not_found", "Assignment not found"),
    ClassroomNotVisibleError: ("classroom_not_found", "Classroom not found"),
    PostingNotFoundError: ("posting_not_found", "Not published to this classroom"),
    VersionNotFoundError: ("version_not_found", "Version not found"),
    TestCaseNotFoundError: ("test_case_not_found", "Test case not found"),
}


def assignment_service(request: Request) -> AssignmentService:
    return request.app.state.assignment_service


@contextmanager
def domain_errors() -> Iterator[None]:
    """Map the slice's errors to 404 and rule violations to 422."""
    try:
        yield
    except tuple(_NOT_FOUND) as error:
        code, message = _NOT_FOUND[type(error)]
        _fail(status.HTTP_404_NOT_FOUND, code, message)
    except ValueError as error:
        _fail(422, "invalid_assignment", str(error))


def _fail(status_code: int, code: str, message: str) -> NoReturn:
    raise HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


def test_case_response(test_case: TestCase) -> TestCaseResponse:
    return TestCaseResponse(
        id=test_case.id,
        input_data=test_case.input_data,
        expected_output=test_case.expected_output,
        kind=test_case.kind,
        note=test_case.note,
        order_index=test_case.order_index,
    )


def assignment_response(assignment: Assignment) -> AssignmentResponse:
    return AssignmentResponse(
        id=assignment.id,
        title=assignment.title,
        problem_statement=assignment.problem_statement,
        difficulty=assignment.difficulty,
        topic_id=assignment.topic_id,
        current_version=assignment.current_version,
        time_limit_ms=assignment.settings.time_limit_ms,
        language=assignment.settings.language,
        show_hidden_names=assignment.settings.show_hidden_names,
        test_cases=[test_case_response(tc) for tc in assignment.test_cases],
    )


def _version(version: AssignmentVersion) -> VersionResponse:
    return VersionResponse(
        number=version.number,
        title=version.title,
        reason=version.reason,
        changed_at=version.changed_at,
        test_case_count=len(version.test_cases),
    )


def posting_response(posting: Posting) -> PostingResponse:
    return PostingResponse(
        id=posting.id,
        classroom_id=posting.classroom_id,
        assignment_id=posting.assignment_id,
        deadline=posting.schedule.deadline,
        max_score=posting.schedule.max_score,
        allow_late=posting.schedule.allow_late,
        allow_resubmission=posting.schedule.allow_resubmission,
        published_at=posting.published_at,
        closed_at=posting.closed_at,
    )


def _problem(problem: InstructorProblem | StudentProblem) -> ProblemResponse:
    common = {
        "number": problem.number,
        "title": problem.assignment.title,
        "difficulty": problem.assignment.difficulty,
        "posting": posting_response(problem.posting),
    }
    if isinstance(problem, InstructorProblem):
        return ProblemResponse(
            **common,
            submitted=problem.summary.submitted,
            avg_score=problem.summary.avg_score,
        )
    return ProblemResponse(
        **common, state=problem.standing.state.value, score=problem.standing.score
    )


# ---- /api/v1/assignments --------------------------------------------------


@router.get("/{assignment_id}", response_model=AssignmentResponse)
def get_assignment(request: Request, assignment_id: int) -> AssignmentResponse:
    with domain_errors():
        assignment = assignment_service(request).get(
            current_actor(request), assignment_id
        )
    return assignment_response(assignment)


@router.patch("/{assignment_id}", response_model=AssignmentResponse)
def edit_assignment(
    request: Request, assignment_id: int, payload: AssignmentPatch
) -> AssignmentResponse:
    """T-05: save a new Version; extend deadlines only on the listed Postings."""
    fields = payload.model_fields_set
    with domain_errors():
        assignment = assignment_service(request).edit(
            current_actor(request),
            assignment_id,
            reason=payload.reason,
            title=payload.title if "title" in fields else UNSET,
            problem_statement=payload.problem_statement
            if "problem_statement" in fields
            else UNSET,
            difficulty=payload.difficulty if "difficulty" in fields else UNSET,
            topic_id=payload.topic_id if "topic_id" in fields else UNSET,
            test_cases=[
                TestCase(
                    input_data=body.input_data,
                    expected_output=body.expected_output,
                    kind=body.kind,
                    note=body.note,
                )
                for body in payload.test_cases
            ]
            if "test_cases" in fields and payload.test_cases is not None
            else UNSET,
            settings=JudgingSettings(
                time_limit_ms=payload.settings.time_limit_ms,
                language=payload.settings.language,
                show_hidden_names=payload.settings.show_hidden_names,
            )
            if "settings" in fields and payload.settings is not None
            else UNSET,
            extend_deadlines=payload.extend_deadline,
        )
    return assignment_response(assignment)


@router.get("/{assignment_id}/versions", response_model=list[VersionResponse])
def list_versions(request: Request, assignment_id: int) -> list[VersionResponse]:
    """T-02b Version history, newest first."""
    with domain_errors():
        versions = assignment_service(request).versions(
            current_actor(request), assignment_id
        )
    return [_version(version) for version in versions]


@router.get("/{assignment_id}/versions/{number}", response_model=VersionResponse)
def get_version(request: Request, assignment_id: int, number: int) -> VersionResponse:
    with domain_errors():
        version = assignment_service(request).version(
            current_actor(request), assignment_id, number
        )
    return _version(version)


# ---- /api/v1/classrooms/{id}/assignments and /summary -----------------------


@classroom_router.get("/{classroom_id}/assignments", response_model=ProblemListResponse)
def list_problems(request: Request, classroom_id: int) -> ProblemListResponse:
    """T-01 Problems for the Instructor, S-01 Problems for a Member."""
    with domain_errors():
        result = assignment_service(request).problems(
            current_actor(request), classroom_id
        )
    if isinstance(result, InstructorProblemList):
        return ProblemListResponse(
            variant="instructor",
            student_count=result.student_count,
            problems=[_problem(problem) for problem in result.problems],
        )
    return ProblemListResponse(
        variant="student",
        student_count=None,
        problems=[_problem(problem) for problem in result.problems],
    )


@classroom_router.get(
    "/{classroom_id}/assignments/{assignment_id}", response_model=ProblemResponse
)
def get_problem(
    request: Request, classroom_id: int, assignment_id: int
) -> ProblemResponse:
    with domain_errors():
        problem = assignment_service(request).problem(
            current_actor(request), classroom_id, assignment_id
        )
    return _problem(problem)


@classroom_router.patch(
    "/{classroom_id}/assignments/{assignment_id}", response_model=PostingResponse
)
def update_posting(
    request: Request, classroom_id: int, assignment_id: int, payload: PostingPatch
) -> PostingResponse:
    """This Classroom's deadline, max score and late/resubmission policy."""
    with domain_errors():
        posting = assignment_service(request).update_posting(
            current_actor(request),
            classroom_id,
            assignment_id,
            deadline=UNSET if payload.deadline is None else payload.deadline,
            max_score=UNSET if payload.max_score is None else payload.max_score,
            allow_late=UNSET if payload.allow_late is None else payload.allow_late,
            allow_resubmission=UNSET
            if payload.allow_resubmission is None
            else payload.allow_resubmission,
        )
    return posting_response(posting)


@classroom_router.delete(
    "/{classroom_id}/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unpost(request: Request, classroom_id: int, assignment_id: int) -> Response:
    """Remove the Assignment from this Classroom only."""
    with domain_errors():
        assignment_service(request).unpost(
            current_actor(request), classroom_id, assignment_id
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@classroom_router.post(
    "/{classroom_id}/assignments/{assignment_id}/notify",
    status_code=status.HTTP_204_NO_CONTENT,
)
def notify_students(
    request: Request, classroom_id: int, assignment_id: int
) -> Response:
    """T-02c "Notify students": notify every Member of this Classroom."""
    with domain_errors():
        assignment_service(request).notify_students(
            current_actor(request), classroom_id, assignment_id
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@classroom_router.post(
    "/{classroom_id}/assignments/{assignment_id}/close",
    response_model=PostingResponse,
)
def close_posting(
    request: Request, classroom_id: int, assignment_id: int
) -> PostingResponse:
    """T-02 "Close submissions"."""
    with domain_errors():
        posting = assignment_service(request).close(
            current_actor(request), classroom_id, assignment_id
        )
    return posting_response(posting)


@classroom_router.get("/{classroom_id}/summary", response_model=SummaryResponse)
def summary(request: Request, classroom_id: int) -> SummaryResponse:
    """T-01 Summary for the Instructor, S-01 Summary for a Member."""
    with domain_errors():
        result = assignment_service(request).summary(
            current_actor(request), classroom_id
        )
    if isinstance(result, InstructorSummary):
        return SummaryResponse(
            variant="instructor",
            student_count=result.student_count,
            problem_count=result.problem_count,
            avg_pass_rate=result.avg_pass_rate,
            never_submitted=result.never_submitted,
            most_failed=[
                FailRateResponse(title=item.title, rate=item.rate)
                for item in result.most_failed
            ],
            problem_titles=result.problem_titles,
            score_table=[
                ScoreRowResponse(
                    student_name=row.student_name, scores=row.scores, total=row.total
                )
                for row in result.score_table
            ],
        )
    return SummaryResponse(
        variant="student",
        solved=result.solved,
        passed=result.passed,
        total=result.total,
        avg_score=result.avg_score,
    )


@classroom_router.get("/{classroom_id}/summary.csv")
def export_classroom_summary(request: Request, classroom_id: int) -> Response:
    """Export the Instructor's classroom score table as CSV."""
    with domain_errors():
        result = assignment_service(request).export_summary(
            current_actor(request), classroom_id
        )

    return csv_response(
        f"classroom-{classroom_id}-summary.csv",
        ["Student Number", "Student Name", *result.problem_titles, "Total"],
        (
            [
                "" if row.student_number is None else row.student_number,
                row.student_name,
                *["" if score is None else score for score in row.scores],
                row.total,
            ]
            for row in result.score_table
        ),
    )
