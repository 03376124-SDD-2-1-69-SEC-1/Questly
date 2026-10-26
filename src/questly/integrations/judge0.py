"""CodeRunner and BatchRunner adapters: Judge0 CE over its batch API, and a stub
that runs nothing."""

import base64
import logging
import time
from collections.abc import Callable, Iterator, Sequence

import httpx
from pydantic import BaseModel, ValidationError

from questly.core.assignments.models import Language
from questly.core.submissions.models import (
    RUNNER_NOT_CONFIGURED_STDERR,
    BatchResult,
    BatchState,
    Execution,
    ExecutionStatus,
    RunnerUnavailableError,
    UnsupportedLanguageError,
)

logger = logging.getLogger("questly.judge0")

JUDGE0_LANGUAGE_IDS: dict[Language, int] = {
    Language.PYTHON3: 71,  # "Python (3.8.1)" on Judge0 CE
}

# Judge0's MAX_SUBMISSION_BATCH_SIZE default; one request may not hold more.
_BATCH_LIMIT = 20
_PENDING = {1, 2}  # In Queue, Processing
# Judge0's own Wrong Answer (4) is never trusted: no expected_output is sent,
# and the service judges stdout with outputs_match. 13 (Internal Error) and
# any id not listed here mean the code was not judged at all.
_FINISHED: dict[int, ExecutionStatus] = {
    3: ExecutionStatus.OK,
    4: ExecutionStatus.OK,
    5: ExecutionStatus.TIME_LIMIT,
    6: ExecutionStatus.RUNTIME_ERROR,
    **dict.fromkeys(range(7, 13), ExecutionStatus.RUNTIME_ERROR),
    14: ExecutionStatus.RUNTIME_ERROR,
}
_FIELDS = "token,stdout,stderr,compile_output,status_id,time"

# A Run holds its request open while Judge0 works, so the whole batch gets a
# bound; each HTTP call gets a shorter one. A collect for a background Submit
# polls for at most _COLLECT_SECONDS, well inside the 90 s judging lease.
_POLL_INTERVAL_SECONDS = 0.5
_BATCH_TIMEOUT_SECONDS = 30.0
_COLLECT_SECONDS = 20.0
_HTTP_TIMEOUT_SECONDS = 10.0


class _Created(BaseModel):
    token: str


class _Judged(BaseModel):
    token: str
    status_id: int
    stdout: str | None = None
    stderr: str | None = None
    compile_output: str | None = None
    time: str | None = None


class _Polled(BaseModel):
    # Judge0 answers null for a token it does not know.
    submissions: list[_Judged | None]


class Judge0CodeRunner:
    """Runs code on Judge0 CE as a CodeRunner and as a BatchRunner.

    `run_many` waits until every input is judged, until `timeout_seconds`
    pass, or until Judge0 fails, whichever comes first; anything short of a
    finished verdict for every input raises RunnerUnavailableError, so nothing
    half-judged is graded. `start` and `collect` split that wait so a
    background Submit can save the tokens in between.
    """

    def __init__(
        self,
        *,
        base_url: str,
        auth_token: str,
        client: httpx.Client,
        poll_interval_seconds: float,
        timeout_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Auth-Token": auth_token} if auth_token else {}
        self._client = client
        self._poll_interval = poll_interval_seconds
        self._timeout = timeout_seconds
        self._monotonic = monotonic
        self._sleep = sleep

    def run(
        self, *, code: str, language: str, stdin: str, time_limit_seconds: float
    ) -> Execution:
        (execution,) = self.run_many(
            code=code,
            language=language,
            stdins=[stdin],
            time_limit_seconds=time_limit_seconds,
        )
        return execution

    def run_many(
        self,
        *,
        code: str,
        language: str,
        stdins: Sequence[str],
        time_limit_seconds: float,
    ) -> tuple[Execution, ...]:
        _language_id(language)
        if not stdins:
            return ()
        deadline = self._monotonic() + self._timeout
        tokens = self.start(
            code=code,
            language=language,
            stdins=stdins,
            time_limit_seconds=time_limit_seconds,
        )
        batch = self._collect(tokens, deadline)
        if batch.state is BatchState.RUNNING:
            raise RunnerUnavailableError(
                f"Judge0 did not finish within {self._timeout} seconds"
            )
        if batch.state is BatchState.GONE:
            raise RunnerUnavailableError("Judge0 could not judge the code")
        return batch.executions

    def start(
        self,
        *,
        code: str,
        language: str,
        stdins: Sequence[str],
        time_limit_seconds: float,
    ) -> tuple[str, ...]:
        language_id = _language_id(language)
        return tuple(
            token
            for chunk in _chunks(stdins)
            for token in self._create(code, language_id, chunk, time_limit_seconds)
        )

    def collect(self, tokens: Sequence[str]) -> BatchResult:
        return self._collect(tokens, self._monotonic() + _COLLECT_SECONDS)

    def _create(
        self,
        code: str,
        language_id: int,
        stdins: Sequence[str],
        time_limit_seconds: float,
    ) -> list[str]:
        body = {
            "submissions": [
                {
                    "source_code": _encode(code),
                    "language_id": language_id,
                    "stdin": _encode(stdin),
                    "cpu_time_limit": time_limit_seconds,
                }
                for stdin in stdins
            ]
        }
        reply = self._request("POST", {"base64_encoded": "true"}, body)
        try:
            created = [_Created.model_validate(item) for item in reply.json()]
        except (ValueError, TypeError) as error:
            raise RunnerUnavailableError("Judge0 rejected the batch") from error
        if len(created) != len(stdins):
            raise RunnerUnavailableError("Judge0 answered for a different batch")
        return [item.token for item in created]

    def _collect(self, tokens: Sequence[str], deadline: float) -> BatchResult:
        judged: dict[str, Execution] = {}
        while True:
            pending = [token for token in tokens if token not in judged]
            for chunk in _chunks(pending):
                for token, item in zip(chunk, self._poll(chunk), strict=True):
                    if item is None:
                        logger.warning("Judge0 does not know token %s", token)
                        return BatchResult(state=BatchState.GONE)
                    if item.status_id in _PENDING:
                        continue
                    if item.status_id not in _FINISHED:
                        logger.warning("Judge0 status %d for %s", item.status_id, token)
                        return BatchResult(state=BatchState.GONE)
                    judged[token] = _execution(item)
            if len(judged) == len(tokens):
                return BatchResult(
                    state=BatchState.FINISHED,
                    executions=tuple(judged[token] for token in tokens),
                )
            if self._monotonic() >= deadline:
                return BatchResult(state=BatchState.RUNNING)
            self._sleep(self._poll_interval)

    def _poll(self, tokens: Sequence[str]) -> list[_Judged | None]:
        params = {
            "tokens": ",".join(tokens),
            "base64_encoded": "true",
            "fields": _FIELDS,
        }
        reply = self._request("GET", params, None)
        try:
            polled = _Polled.model_validate_json(reply.content).submissions
        except ValidationError as error:
            raise RunnerUnavailableError("Judge0 sent an unreadable reply") from error
        if len(polled) != len(tokens):
            raise RunnerUnavailableError("Judge0 answered for different tokens")
        return polled

    def _request(
        self,
        method: str,
        params: dict[str, str],
        body: dict[str, list[dict[str, str | int | float]]] | None,
    ) -> httpx.Response:
        try:
            reply = self._client.request(
                method,
                f"{self._base_url}/submissions/batch",
                params=params,
                json=body,
                headers=self._headers,
            )
            reply.raise_for_status()
        except httpx.HTTPError as error:
            logger.warning("Judge0 %s failed: %s", method, error)
            raise RunnerUnavailableError("Judge0 is unreachable") from error
        return reply


def connect_judge0(base_url: str, auth_token: str) -> Judge0CodeRunner:
    """The production runner: a pooled HTTP client and the default bounds."""
    return Judge0CodeRunner(
        base_url=base_url,
        auth_token=auth_token,
        client=httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS),
        poll_interval_seconds=_POLL_INTERVAL_SECONDS,
        timeout_seconds=_BATCH_TIMEOUT_SECONDS,
    )


def _language_id(language: str) -> int:
    # Language is a StrEnum, so its members match their plain-string values.
    if language not in JUDGE0_LANGUAGE_IDS:
        raise UnsupportedLanguageError(f"Judge0 has no runtime for {language!r}")
    return JUDGE0_LANGUAGE_IDS[Language(language)]


def _chunks(items: Sequence[str]) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), _BATCH_LIMIT):
        yield items[start : start + _BATCH_LIMIT]


def _execution(judged: _Judged) -> Execution:
    error = "\n".join(
        text
        for text in (_decode(judged.compile_output), _decode(judged.stderr))
        if text
    )
    return Execution(
        status=_FINISHED[judged.status_id],
        stdout=_decode(judged.stdout),
        stderr=error,
        time_seconds=0.0 if judged.time is None else float(judged.time),
    )


def _encode(text: str) -> str:
    return base64.b64encode(text.encode()).decode("ascii")


def _decode(text: str | None) -> str:
    if text is None:
        return ""
    return base64.b64decode(text).decode(errors="replace")


class StubCodeRunner:
    """Satisfies CodeRunner and BatchRunner without Judge0: every run is a
    runtime error.

    Records each call so a deployment without Judge0 fails visibly instead
    of grading code it never ran.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def run(
        self, *, code: str, language: str, stdin: str, time_limit_seconds: float
    ) -> Execution:
        self.calls.append((language, stdin))
        logger.warning("code runner not configured; %s run refused", language)
        return _NOT_CONFIGURED

    def run_many(
        self,
        *,
        code: str,
        language: str,
        stdins: Sequence[str],
        time_limit_seconds: float,
    ) -> tuple[Execution, ...]:
        return tuple(
            self.run(
                code=code,
                language=language,
                stdin=stdin,
                time_limit_seconds=time_limit_seconds,
            )
            for stdin in stdins
        )

    def start(
        self,
        *,
        code: str,
        language: str,
        stdins: Sequence[str],
        time_limit_seconds: float,
    ) -> tuple[str, ...]:
        for stdin in stdins:
            self.calls.append((language, stdin))
        logger.warning("code runner not configured; %s batch refused", language)
        return tuple(f"stub-{n}" for n in range(1, len(stdins) + 1))

    def collect(self, tokens: Sequence[str]) -> BatchResult:
        return BatchResult(
            state=BatchState.FINISHED,
            executions=tuple(_NOT_CONFIGURED for _ in tokens),
        )


_NOT_CONFIGURED = Execution(
    status=ExecutionStatus.RUNTIME_ERROR,
    stdout="",
    stderr=RUNNER_NOT_CONFIGURED_STDERR,
    time_seconds=0.0,
)
