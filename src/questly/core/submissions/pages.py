"""S-02 solve and T-02 results: one URL, a template per role and tab."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from questly.core.auth.csrf import require_csrf
from questly.core.auth.current import current_actor
from questly.core.submissions.models import (
    ClassroomArchivedError,
    EmptyCodeError,
    InstructorResultsView,
    InstructorTab,
    PostingClosedError,
    ResubmissionNotAllowedError,
    RunnerUnavailableError,
    RunReport,
    StudentSolveView,
    StudentTab,
    SubmissionPendingError,
)
from questly.core.submissions.service import NOT_VISIBLE, SubmissionService

router = APIRouter(tags=["pages"], include_in_schema=False)

_REFUSALS: dict[type[Exception], tuple[str, int]] = {
    ClassroomArchivedError: (
        "This classroom is archived. Submissions are closed.",
        status.HTTP_409_CONFLICT,
    ),
    EmptyCodeError: ("Write some code first.", status.HTTP_422_UNPROCESSABLE_CONTENT),
    PostingClosedError: (
        "Submissions are closed for this problem.",
        status.HTTP_409_CONFLICT,
    ),
    ResubmissionNotAllowedError: (
        "This problem allows one submission only.",
        status.HTTP_409_CONFLICT,
    ),
    SubmissionPendingError: (
        "Your last submission is still being judged.",
        status.HTTP_409_CONFLICT,
    ),
    RunnerUnavailableError: (
        "The code runner is unavailable right now, so nothing was saved. "
        "Please try again in a moment.",
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
}


def _service(request: Request) -> SubmissionService:
    return request.app.state.submission_service


def _page_view(
    request: Request, classroom_id: int, assignment_id: int, query: str = ""
) -> StudentSolveView | InstructorResultsView:
    try:
        return _service(request).page(
            current_actor(request), classroom_id, assignment_id, query
        )
    except NOT_VISIBLE:
        raise HTTPException(status_code=404) from None


def _student_tab(tab: str) -> StudentTab:
    if tab in InstructorTab:
        raise HTTPException(status_code=403)
    if tab not in StudentTab:
        raise HTTPException(status_code=404)
    return StudentTab(tab)


def _instructor_tab(tab: str) -> InstructorTab:
    if tab not in InstructorTab:
        raise HTTPException(status_code=404)
    return InstructorTab(tab)


def _render(
    request: Request, template: str, context: dict[str, object], status_code: int = 200
) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request,
        template,
        {"actor": current_actor(request), **context},
        status_code=status_code,
    )


def _solve(
    request: Request,
    view: StudentSolveView,
    *,
    code: str,
    run: RunReport | None = None,
    error: str = "",
    submitted: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    return _render(
        request,
        "student/s02_solve.html",
        {
            "view": view,
            "code": code,
            "run": run,
            "error": error,
            "submitted": submitted,
        },
        status_code,
    )


@router.get("/classes/{classroom_id}/assignments/{assignment_id}")
def problem_page(
    request: Request,
    classroom_id: int,
    assignment_id: int,
    tab: str = "",
    attempt: int = 0,
    student: int = 0,
    q: str = "",
    notified: int = 0,
    submitted: int = 0,
) -> HTMLResponse:
    """S-02 (tabs problem, history) or T-02 (tabs results, versions).

    `q` is the T-02 student search; `notified` shows the "Notify students"
    confirmation after the redirect; `submitted` shows the pass/fail toast
    after a Submit.
    """
    view = _page_view(request, classroom_id, assignment_id, q)
    if isinstance(view, InstructorResultsView):
        chosen = _instructor_tab(tab or InstructorTab.RESULTS)
        if chosen is InstructorTab.VERSIONS:
            return _render(request, "instructor/t02b_versions.html", {"view": view})
        panel = next((row for row in view.rows if row.student_id == student), None)
        if student and panel is None:
            raise HTTPException(status_code=404)
        return _render(
            request,
            "instructor/t02_results.html",
            {"view": view, "panel": panel, "notified": bool(notified)},
        )
    if _student_tab(tab or StudentTab.PROBLEM) is StudentTab.HISTORY:
        shown = next((s for s in view.submissions if s.attempt == attempt), None)
        if attempt and shown is None:
            raise HTTPException(status_code=404)
        return _render(
            request, "student/s02g_history.html", {"view": view, "shown": shown}
        )
    code = view.counted.code if view.counted is not None else ""
    return _solve(request, view, code=code, submitted=bool(submitted))


@router.post("/classes/{classroom_id}/assignments/{assignment_id}", response_model=None)
def run_or_submit(
    request: Request,
    classroom_id: int,
    assignment_id: int,
    background_tasks: BackgroundTasks,
    csrf_token: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "run",
) -> HTMLResponse | RedirectResponse:
    """One form, two buttons (ADR-0007 §5.3): Run renders, Submit redirects
    to S-02, which shows S-02a until the background judging is done."""
    require_csrf(request, csrf_token)
    actor = current_actor(request)
    service = _service(request)
    try:
        if action == "submit":
            submission = service.submit(actor, classroom_id, assignment_id, code)
            background_tasks.add_task(service.judge, submission.id)
            return RedirectResponse(
                f"/classes/{classroom_id}/assignments/{assignment_id}?submitted=1",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        report = service.run(actor, classroom_id, assignment_id, code)
    except NOT_VISIBLE:
        raise HTTPException(status_code=404) from None
    except tuple(_REFUSALS) as refusal:
        view = _page_view(request, classroom_id, assignment_id)
        if not isinstance(view, StudentSolveView):
            raise HTTPException(status_code=403) from None
        message, status_code = _REFUSALS[type(refusal)]
        return _solve(request, view, code=code, error=message, status_code=status_code)
    view = _page_view(request, classroom_id, assignment_id)
    if not isinstance(view, StudentSolveView):
        raise HTTPException(status_code=403)
    return _solve(request, view, code=code, run=report)


@router.post("/classes/{classroom_id}/assignments/{assignment_id}/close")
def close_submissions(
    request: Request,
    classroom_id: int,
    assignment_id: int,
    csrf_token: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """T-02 "Close submissions"."""
    require_csrf(request, csrf_token)
    try:
        request.app.state.assignment_service.close(
            current_actor(request), classroom_id, assignment_id
        )
    except NOT_VISIBLE:
        raise HTTPException(status_code=404) from None
    return RedirectResponse(
        f"/classes/{classroom_id}/assignments/{assignment_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
