"""In-memory SubmissionRepository and two runners for tests and the demo.

Both runners fill CodeRunner and BatchRunner; the batch half keeps its
results in a RunnerBackend.
"""

import contextlib
import math
import os
import warnings

try:
    import resource
except ImportError:
    resource = None
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime

from questly.core.submissions.models import (
    BatchResult,
    BatchState,
    Claim,
    Execution,
    ExecutionStatus,
    Recovery,
    RunnerUnavailableError,
    Submission,
    SubmissionPendingError,
    SubmissionStatus,
    TestResult,
)


class FakeSubmissionRepository:
    """Holds the runner tokens and the lease beside each row, as SQL does."""

    def __init__(self) -> None:
        self._rows: dict[int, Submission] = {}
        self._tokens: dict[int, tuple[str, ...]] = {}
        self._leases: dict[int, datetime] = {}

    def add(self, submission: Submission) -> Submission:
        return self._rows[self._insert(submission)]

    def receive(self, submission: Submission, lease_until: datetime) -> Submission:
        for row in self._rows.values():
            same_student = (row.posting_id, row.student_id) == (
                submission.posting_id,
                submission.student_id,
            )
            if same_student and (
                row.status.pending or row.attempt == submission.attempt
            ):
                raise SubmissionPendingError
        submission_id = self._insert(submission)
        self._leases[submission_id] = lease_until
        return self._rows[submission_id]

    def claim_due(
        self, now: datetime, lease_until: datetime, limit: int
    ) -> tuple[Claim, ...]:
        due = [
            (submission_id, row)
            for submission_id, row in self._ordered_items()
            if row.status.pending
            and (
                submission_id not in self._leases or self._leases[submission_id] <= now
            )
        ][:limit]
        claims = tuple(
            Claim(
                submission=row,
                tokens=self._tokens[submission_id],
                interrupted=submission_id in self._leases,
            )
            for submission_id, row in due
        )
        for submission_id, _ in due:
            self._leases[submission_id] = lease_until
        return claims

    def started(
        self,
        submission_id: int,
        *,
        tokens: Sequence[str],
        lease_until: datetime,
        recovery: Recovery,
    ) -> bool:
        if not self._can_move(submission_id, SubmissionStatus.JUDGING):
            return False
        self._set(submission_id, status=SubmissionStatus.JUDGING, recovery=recovery)
        self._tokens[submission_id] = tuple(tokens)
        self._leases[submission_id] = lease_until
        return True

    def renew(self, submission_id: int, *, lease_until: datetime) -> bool:
        row = self._rows.get(submission_id)
        if row is None or row.status is not SubmissionStatus.JUDGING:
            return False
        self._leases[submission_id] = lease_until
        return True

    def finished(
        self,
        submission_id: int,
        *,
        tokens: Sequence[str],
        score: int,
        results: Sequence[TestResult],
        recovery: Recovery,
    ) -> bool:
        if not self._can_move(submission_id, SubmissionStatus.DONE):
            return False
        if self._tokens[submission_id] != tuple(tokens):
            return False
        self._set(
            submission_id,
            status=SubmissionStatus.DONE,
            score=score,
            results=tuple(results),
            recovery=recovery,
        )
        self._leases.pop(submission_id, None)
        return True

    def requeued(self, submission_id: int) -> bool:
        if not self._can_move(submission_id, SubmissionStatus.RECEIVED):
            return False
        self._set(submission_id, status=SubmissionStatus.RECEIVED)
        self._tokens[submission_id] = ()
        return True

    def released(self, submission_id: int) -> bool:
        row = self._rows.get(submission_id)
        if row is None or not row.status.pending or submission_id not in self._leases:
            return False
        del self._leases[submission_id]
        return True

    def failed(self, submission_id: int) -> bool:
        if not self._can_move(submission_id, SubmissionStatus.FAILED):
            return False
        self._set(submission_id, status=SubmissionStatus.FAILED)
        self._leases.pop(submission_id, None)
        return True

    def get(self, submission_id: int) -> Submission | None:
        return self._rows.get(submission_id)

    def list_for_student(
        self, posting_id: int, student_id: int
    ) -> tuple[Submission, ...]:
        return tuple(
            row
            for _, row in self._ordered_items()
            if row.posting_id == posting_id and row.student_id == student_id
        )

    def list_for_posting(self, posting_id: int) -> tuple[Submission, ...]:
        return tuple(
            row for _, row in self._ordered_items() if row.posting_id == posting_id
        )

    def _insert(self, submission: Submission) -> int:
        submission_id = len(self._rows) + 1
        self._rows[submission_id] = replace(submission, id=submission_id)
        self._tokens[submission_id] = ()
        return submission_id

    def _can_move(self, submission_id: int, target: SubmissionStatus) -> bool:
        row = self._rows.get(submission_id)
        return row is not None and row.status.can_move(target)

    def _set(
        self,
        submission_id: int,
        *,
        status: SubmissionStatus,
        score: int | None = None,
        results: tuple[TestResult, ...] = (),
        recovery: Recovery | None = None,
    ) -> None:
        row = self._rows[submission_id]
        self._rows[submission_id] = replace(
            row,
            status=status,
            score=score,
            results=results,
            recovery=row.recovery if recovery is None else recovery,
        )

    def _ordered_items(self) -> list[tuple[int, Submission]]:
        return sorted(
            self._rows.items(), key=lambda item: (item[1].submitted_at, item[0])
        )


class RunnerBackend:
    """What a fake runner keeps between start and collect, as Judge0 keeps
    submissions. Share one between two runners to model Judge0 surviving our
    restart; a new one models Judge0 having lost everything."""

    def __init__(self) -> None:
        self._executions: dict[str, Execution] = {}
        self._held = False
        self._down = False

    def hold(self) -> None:
        """Every batch reads as running until `resume()`."""
        self._held = True

    def forget(self) -> None:
        """Drops every batch, so collecting one reads as gone."""
        self._executions.clear()

    def down(self) -> None:
        """start and collect raise RunnerUnavailableError until `resume()`."""
        self._down = True

    def resume(self) -> None:
        self._held = False
        self._down = False

    def start(self, judge: Callable[[], Sequence[Execution]]) -> tuple[str, ...]:
        self._require_up()
        tokens = []
        for execution in judge():
            token = str(uuid.uuid4())
            self._executions[token] = execution
            tokens.append(token)
        return tuple(tokens)

    def collect(self, tokens: Sequence[str]) -> BatchResult:
        self._require_up()
        if any(token not in self._executions for token in tokens):
            return BatchResult(state=BatchState.GONE)
        if self._held:
            return BatchResult(state=BatchState.RUNNING)
        return BatchResult(
            state=BatchState.FINISHED,
            executions=tuple(self._executions[token] for token in tokens),
        )

    def _require_up(self) -> None:
        if self._down:
            raise RunnerUnavailableError("runner backend is down")


class ScriptedCodeRunner:
    """Answers from `outputs` (stdin -> stdout); code markers force failures.

    A line `# tle` in the code times out and `# crash` raises a runtime
    error, so a test can reach S-02d without running anything. `# judge down`
    makes the runner unreachable.
    """

    def __init__(
        self,
        outputs: dict[str, str] | None = None,
        backend: RunnerBackend | None = None,
    ) -> None:
        self.outputs: dict[str, str] = {}
        if outputs is not None:
            self.outputs.update(outputs)
        self.backend = RunnerBackend() if backend is None else backend
        self.calls: list[str] = []
        self.batches: list[tuple[str, ...]] = []

    def start(
        self,
        *,
        code: str,
        language: str,
        stdins: Sequence[str],
        time_limit_seconds: float,
    ) -> tuple[str, ...]:
        return self.backend.start(
            lambda: self.run_many(
                code=code,
                language=language,
                stdins=stdins,
                time_limit_seconds=time_limit_seconds,
            )
        )

    def collect(self, tokens: Sequence[str]) -> BatchResult:
        return self.backend.collect(tokens)

    def run_many(
        self,
        *,
        code: str,
        language: str,
        stdins: Sequence[str],
        time_limit_seconds: float,
    ) -> tuple[Execution, ...]:
        self.batches.append(tuple(stdins))
        return tuple(
            self.run(
                code=code,
                language=language,
                stdin=stdin,
                time_limit_seconds=time_limit_seconds,
            )
            for stdin in stdins
        )

    def run(
        self, *, code: str, language: str, stdin: str, time_limit_seconds: float
    ) -> Execution:
        if "# judge down" in code:
            raise RunnerUnavailableError("scripted outage")
        self.calls.append(stdin)
        if "# tle" in code:
            return Execution(ExecutionStatus.TIME_LIMIT, "", "", time_limit_seconds)
        if "# crash" in code:
            return Execution(
                ExecutionStatus.RUNTIME_ERROR,
                "",
                "IndexError: list index out of range",
                0.01,
            )
        return Execution(ExecutionStatus.OK, self.outputs.get(stdin, ""), "", 0.02)


class UnsafeRunnerNotAllowedError(RuntimeError):
    """LocalUnsafeRunner was built without ALLOW_UNSAFE_RUNNER=1, or in production."""


class LocalUnsafeRunner:
    """Demo only: runs code with this machine's Python, NOT a sandbox.

    Opt-in: it refuses to exist unless ALLOW_UNSAFE_RUNNER=1, and always
    when ENV=production, so an unset ENV fails closed. Guards, none of which
    make it safe for untrusted code: each run gets a fresh temp dir as its working
    directory and a wall-clock timeout. CPU-time and memory rlimits are applied
    on POSIX only; Windows has no resource limits in this runner.
    `scripts/demo.py` binds 127.0.0.1, so the only code it runs is what the
    person at the keyboard typed. Production needs a sandboxed CodeRunner
    (ADR-0007 open question 1).
    """

    MEMORY_BYTES = 256 * 1024 * 1024

    def __init__(self, backend: RunnerBackend | None = None) -> None:
        self.backend = RunnerBackend() if backend is None else backend
        if os.environ.get("ENV", "").strip().lower() == "production":
            raise UnsafeRunnerNotAllowedError(
                "LocalUnsafeRunner runs code unsandboxed; refusing ENV=production"
            )
        if os.environ.get("ALLOW_UNSAFE_RUNNER", "").strip() != "1":
            raise UnsafeRunnerNotAllowedError(
                "LocalUnsafeRunner runs code unsandboxed; set ALLOW_UNSAFE_RUNNER=1 "
                "to use it in the local demo"
            )
        if os.name != "posix" or resource is None:
            warnings.warn(
                "LocalUnsafeRunner does not enforce CPU or memory limits on this "
                "platform; only the wall-clock timeout is applied.",
                RuntimeWarning,
                stacklevel=2,
            )

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
        return self.backend.start(
            lambda: self.run_many(
                code=code,
                language=language,
                stdins=stdins,
                time_limit_seconds=time_limit_seconds,
            )
        )

    def collect(self, tokens: Sequence[str]) -> BatchResult:
        return self.backend.collect(tokens)

    def run(
        self, *, code: str, language: str, stdin: str, time_limit_seconds: float
    ) -> Execution:
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="questly-run-") as workdir:
            try:
                command = [sys.executable, "-I", "-c", code]
                if os.name == "posix" and resource is not None:
                    done = subprocess.run(
                        command,
                        input=stdin,
                        capture_output=True,
                        text=True,
                        timeout=time_limit_seconds,
                        check=False,
                        cwd=workdir,
                        env={"PATH": os.environ.get("PATH", "")},
                        preexec_fn=self._limits(time_limit_seconds),
                    )
                else:
                    done = subprocess.run(
                        command,
                        input=stdin,
                        capture_output=True,
                        text=True,
                        timeout=time_limit_seconds,
                        check=False,
                        cwd=workdir,
                        env={"PATH": os.environ.get("PATH", "")},
                    )
            except subprocess.TimeoutExpired:
                return Execution(ExecutionStatus.TIME_LIMIT, "", "", time_limit_seconds)
        elapsed = time.perf_counter() - started
        if done.returncode != 0:
            return Execution(
                ExecutionStatus.RUNTIME_ERROR, done.stdout, done.stderr, elapsed
            )
        return Execution(ExecutionStatus.OK, done.stdout, done.stderr, elapsed)

    def _limits(self, time_limit_seconds: float) -> Callable[[], None]:
        if resource is None:
            raise RuntimeError("POSIX resource limits are unavailable")
        cpu_seconds = math.ceil(time_limit_seconds) + 1

        def apply() -> None:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            # macOS rejects RLIMIT_AS; the CPU limit and timeout still hold.
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(
                    resource.RLIMIT_AS, (self.MEMORY_BYTES, self.MEMORY_BYTES)
                )

        return apply
