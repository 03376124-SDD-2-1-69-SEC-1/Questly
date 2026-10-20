"""JSON API for Submissions and Runs under /api/v1 (ADR-0007 API contract)."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import NoReturn

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
    Response,
    status,
)

from questly.core.assignments.models import TestCaseKind
from questly.core.auth.current import current_actor
from questly.core.classrooms.csv_export import CsvCell, bangkok_time, csv_response
from questly.core.submissions.models import (
    ClassroomArchivedError,
    EmptyCodeError,
    Judged,
    PostingClosedError,
    ResubmissionNotAllowedError,
    RunnerUnavailableError,
    Submission,
    SubmissionExportRow,
    SubmissionPendingError,
    TestResult,
)
from questly.core.submissions.schemas import (
    CodeRequest,
    RunResponse,
    SubmissionResponse,
    TestResultResponse,
)
from questly.core.submissions.service import NOT_VISIBLE, SubmissionService

router = APIRouter(prefix="/api/v1/submissions", tags=["submissions"])
classroom_router = APIRouter(prefix="/api/v1/classrooms", tags=["submissions"])


def _service(request: Request) -> SubmissionService:
    return request.app.state.submission_service


def _fail(status_code: int, code: str, message: str) -> NoReturn:
    raise HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


@contextmanager
def domain_errors() -> Iterator[None]:
    try:
        yield
    except NOT_VISIBLE:
        _fail(status.HTTP_404_NOT_FOUND, "not_found", "Not found")
    except ClassroomArchivedError:
        _fail(status.HTTP_409_CONFLICT, "classroom_archived", "Classroom is archived")
    except PostingClosedError:
        _fail(status.HTTP_409_CONFLICT, "posting_closed", "Submissions are closed")
    except ResubmissionNotAllowedError:
        _fail(
            status.HTTP_409_CONFLICT,
            "resubmission_not_allowed",
            "This problem allows one submission only",
        )
    except SubmissionPendingError:
        _fail(
            status.HTTP_409_CONFLICT,
            "submission_pending",
            "Your last submission is still being judged",
        )
    except EmptyCodeError:
        _fail(422, "empty_code", "Code is empty")
    except RunnerUnavailableError:
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "runner_unavailable",
            "The code runner is unavailable; nothing was saved. Try again shortly.",
        )


def result_response(result: TestResult, *, full: bool) -> TestResultResponse:
    """`full` is set for sample tests; hidden ones report pass/fail only."""
    return TestResultResponse(
        position=result.position,
        kind=result.kind,
        verdict=result.verdict,
        time_seconds=result.time_seconds,
        note=result.note if full else None,
        expected_output=result.expected_output if full else None,
        actual_output=result.actual_output if full else None,
    )


def submission_response(submission: Submission) -> SubmissionResponse:
    return SubmissionResponse(
        id=submission.id,
        student_id=submission.student_id,
        attempt=submission.attempt,
        version_number=submission.version_number,
        submitted_at=submission.submitted_at,
        is_late=submission.is_late,
        status=submission.status,
        recovery=submission.recovery,
        score=submission.score,
        max_score=submission.max_score,
        passed=submission.passed_count,
        total=len(submission.results),
        code=submission.code,
        results=[
            result_response(result, full=result.kind is TestCaseKind.SAMPLE)
            for result in submission.results
        ],
    )


@classroom_router.post(
    "/{classroom_id}/assignments/{assignment_id}/submissions",
    status_code=status.HTTP_202_ACCEPTED,
)
def submit(
    request: Request,
    response: Response,
    classroom_id: int,
    assignment_id: int,
    body: CodeRequest,
    background_tasks: BackgroundTasks,
) -> SubmissionResponse:
    """Receives the code and judges it in the background; poll `Location`."""
    actor = current_actor(request)
    service = _service(request)
    with domain_errors():
        submission = service.submit(actor, classroom_id, assignment_id, body.code)
    background_tasks.add_task(service.judge, submission.id)
    response.headers["Location"] = f"{router.prefix}/{submission.id}"
    return submission_response(submission)


@classroom_router.get("/{classroom_id}/assignments/{assignment_id}/submissions")
def list_submissions(
    request: Request, classroom_id: int, assignment_id: int
) -> list[SubmissionResponse]:
    """The Instructor sees every Student's; a Student sees their own history."""
    actor = current_actor(request)
    with domain_errors():
        submissions = _service(request).submissions(actor, classroom_id, assignment_id)
    return [submission_response(submission) for submission in submissions]


@classroom_router.get("/{classroom_id}/assignments/{assignment_id}/submissions.csv")
def export_submissions_csv(
    request: Request,
    classroom_id: int,
    assignment_id: int,
) -> Response:
    """Export submission history for an Instructor."""
    with domain_errors():
        rows = _service(request).export_submissions(
            current_actor(request), classroom_id, assignment_id
        )

    return csv_response(
        f"classroom-{classroom_id}-assignment-{assignment_id}-submissions.csv",
        [
            "Submission ID",
            "Student Number",
            "Student Name",
            "Attempt",
            "Version",
            "Submitted At (Bangkok)",
            "Status",
            "Score",
            "Max Score",
            "Passed Tests",
            "Total Tests",
            "Late",
        ],
        (_export_cells(row) for row in rows),
    )


def _export_cells(row: SubmissionExportRow) -> list[CsvCell]:
    """Score and test counts stay blank until judging has a verdict."""
    score: CsvCell = ""
    passed: CsvCell = ""
    total: CsvCell = ""
    if isinstance(row.outcome, Judged):
        score = row.outcome.score
        passed = row.outcome.passed_count
        total = row.outcome.total_tests
    return [
        row.id,
        row.student_number,
        row.student_name,
        row.attempt,
        row.version_number,
        bangkok_time(row.submitted_at),
        row.status.value,
        score,
        row.max_score,
        passed,
        total,
        row.is_late,
    ]


@classroom_router.post("/{classroom_id}/assignments/{assignment_id}/runs")
def run(
    request: Request, classroom_id: int, assignment_id: int, body: CodeRequest
) -> RunResponse:
    """Sample tests only; not graded and not stored."""
    actor = current_actor(request)
    with domain_errors():
        report = _service(request).run(actor, classroom_id, assignment_id, body.code)
    return RunResponse(
        passed=sum(1 for result in report.results if result.passed),
        total=len(report.results),
        results=[result_response(result, full=True) for result in report.results],
    )


@router.get("/{submission_id}")
def get_submission(request: Request, submission_id: int) -> SubmissionResponse:
    """A Submission and how far judging it got; for its Student or Instructor."""
    actor = current_actor(request)
    with domain_errors():
        submission = _service(request).get(actor, submission_id)
    return submission_response(submission)
