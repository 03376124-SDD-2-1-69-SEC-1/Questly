"""FastAPI adapter for /api/v1/assignments/{id}/test-cases.

Every write publishes a new Version (ADR-0007 §3.7), so each one carries the
reason Students will see.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status

from questly.core.assignments.models import TestCase
from questly.core.assignments.routes import (
    ASSIGNMENT_ERRORS,
    assignment_service,
    test_case_response,
)
from questly.core.assignments.schemas import TestCaseResponse, TestCaseWrite
from questly.core.auth.current import current_actor

router = APIRouter(
    prefix="/api/v1/assignments/{assignment_id}/test-cases", tags=["assignments"]
)


def _domain(payload: TestCaseWrite) -> TestCase:
    return TestCase(
        input_data=payload.input_data,
        expected_output=payload.expected_output,
        kind=payload.kind,
        note=payload.note,
    )


@router.get("", response_model=list[TestCaseResponse])
def list_test_cases(request: Request, assignment_id: int) -> list[TestCaseResponse]:
    with ASSIGNMENT_ERRORS.translate():
        test_cases = assignment_service(request).test_cases(
            current_actor(request), assignment_id
        )
    return [test_case_response(tc) for tc in test_cases]


@router.get("/{test_case_id}", response_model=TestCaseResponse)
def get_test_case(
    request: Request, assignment_id: int, test_case_id: int
) -> TestCaseResponse:
    with ASSIGNMENT_ERRORS.translate():
        test_case = assignment_service(request).test_case(
            current_actor(request), assignment_id, test_case_id
        )
    return test_case_response(test_case)


@router.post("", response_model=TestCaseResponse, status_code=status.HTTP_201_CREATED)
def add_test_case(
    request: Request, assignment_id: int, payload: TestCaseWrite
) -> TestCaseResponse:
    with ASSIGNMENT_ERRORS.translate():
        test_case = assignment_service(request).add_test_case(
            current_actor(request),
            assignment_id,
            _domain(payload),
            reason=payload.reason,
        )
    return test_case_response(test_case)


@router.put("/{test_case_id}", response_model=TestCaseResponse)
def replace_test_case(
    request: Request, assignment_id: int, test_case_id: int, payload: TestCaseWrite
) -> TestCaseResponse:
    with ASSIGNMENT_ERRORS.translate():
        test_case = assignment_service(request).replace_test_case(
            current_actor(request),
            assignment_id,
            test_case_id,
            _domain(payload),
            reason=payload.reason,
        )
    return test_case_response(test_case)


@router.delete("/{test_case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_test_case(
    request: Request,
    assignment_id: int,
    test_case_id: int,
    reason: Annotated[str, Query(min_length=1, max_length=500)],
) -> Response:
    with ASSIGNMENT_ERRORS.translate():
        assignment_service(request).delete_test_case(
            current_actor(request), assignment_id, test_case_id, reason=reason
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
