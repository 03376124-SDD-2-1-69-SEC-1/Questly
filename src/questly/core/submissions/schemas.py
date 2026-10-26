"""Request and response shapes for the submissions API."""

from datetime import datetime

from pydantic import BaseModel, Field

from questly.core.assignments.models import TestCaseKind
from questly.core.submissions.models import Recovery, SubmissionStatus, Verdict


class CodeRequest(BaseModel):
    code: str = Field(min_length=1)


class TestResultResponse(BaseModel):
    position: int
    kind: TestCaseKind
    verdict: Verdict
    time_seconds: float
    note: str | None = Field(
        description="Hidden and edge tests show a note only to the Instructor "
        "or when the Assignment shows hidden test names."
    )
    expected_output: str | None = Field(description="Sample tests only.")
    actual_output: str | None = Field(description="Sample tests only.")


class SubmissionResponse(BaseModel):
    id: int
    student_id: int
    attempt: int
    version_number: int
    submitted_at: datetime
    is_late: bool
    status: SubmissionStatus = Field(
        description="received and judging are pending; done and failed are final."
    )
    recovery: Recovery = Field(
        description="reconnected or rejudged when judging was interrupted "
        "(a server restart, or the runner lost the batch)."
    )
    score: int | None = Field(description="Set once the status is done.")
    max_score: int
    passed: int
    total: int
    code: str
    results: list[TestResultResponse] = Field(description="Empty until done.")


class RunResponse(BaseModel):
    passed: int
    total: int
    results: list[TestResultResponse]
