"""Async Submit end to end on Postgres (ADR-0011, OPS-30).

The app is built as `main.py` builds it for production, over the throwaway
database, with only the runner and the clock swapped for fakes. A Student
submits over HTTP; every claim below is checked through HTTP and again by
reading the rows the SQL adapter wrote. A restart is a second `create_app`
over the same database.
"""

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import timedelta

import anyio.from_thread
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from questly.core.assignments.models import (
    AssignmentContent,
    Difficulty,
    Schedule,
    TestCase,
    TestCaseKind,
)
from questly.core.auth.models import Actor, Role, User
from questly.core.auth.service import hash_password
from questly.core.submissions.models import BatchResult
from questly.core.submissions.service import LEASE
from questly.database.core.auth_repository import SQLAuthRepository
from questly.database.session import SessionFactory
from questly.main import create_app
from tests.fakes.app import fake_settings
from tests.fakes.auth import DEFAULT_NOW, FakeClock
from tests.fakes.documents import FakeObjectStorage
from tests.fakes.submissions import RunnerBackend, ScriptedCodeRunner
from tests.fakes.vector import FakeVectorRepository
from tests.integration.forms import asgi_client, log_in

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery"
RECEIVED_AT = DEFAULT_NOW
# Judging outlives the deadline in every scenario; Late must not follow it.
DEADLINE = RECEIVED_AT + timedelta(seconds=30)
# Two of three Test Cases pass: 10 * 2 / 3 floors to 6.
OUTPUTS = {"1 2": "3", "2 2": "4", "5 5": "11"}
CODE = "print(sum(map(int, input().split())))"
ROW = (
    "SELECT status, score, submitted_at, is_late, recovery,"
    " cardinality(runner_tokens), claimed_until FROM core.submissions"
)


def _nothing() -> None:
    pass


class ObservedRunner(ScriptedCodeRunner):
    """Calls `on_start` after the runner took a batch and `on_collect` before
    each poll, so a test can read or change the database mid-judging."""

    def __init__(
        self,
        backend: RunnerBackend,
        on_start: Callable[[], None] = _nothing,
        on_collect: Callable[[], None] = _nothing,
    ) -> None:
        super().__init__(OUTPUTS, backend)
        self.on_start = on_start
        self.on_collect = on_collect

    def start(
        self,
        *,
        code: str,
        language: str,
        stdins: Sequence[str],
        time_limit_seconds: float,
    ) -> tuple[str, ...]:
        tokens = super().start(
            code=code,
            language=language,
            stdins=stdins,
            time_limit_seconds=time_limit_seconds,
        )
        self.on_start()
        return tokens

    def collect(self, tokens: Sequence[str]) -> BatchResult:
        self.on_collect()
        return super().collect(tokens)


class WorkerVanishedError(Exception):
    """The process judging a Submission died mid-poll."""


class World:
    """An Instructor's Classroom with a Student, a non-member, and one Posting
    of a three-Test-Case Assignment, all in Postgres."""

    def __init__(self, postgres_url: str, session_factory: SessionFactory) -> None:
        self.postgres_url = postgres_url
        self.session_factory = session_factory
        self.clock = FakeClock(RECEIVED_AT)
        self.backend = RunnerBackend()
        self.runner = ObservedRunner(self.backend)
        self.app = self.restart(self.runner)
        users = SQLAuthRepository(session_factory)

        def account(email: str, role: Role) -> Actor:
            user = users.create_user(
                User(
                    email=email,
                    full_name=email,
                    password_hash=hash_password(PASSWORD),
                    role=role,
                    email_verified=True,
                )
            )
            return Actor(user.id, role, email, email)

        self.teacher = account("somchai.p@kmitl.ac.th", Role.INSTRUCTOR)
        self.student = account("66010001@kmitl.ac.th", Role.STUDENT)
        self.outsider = account("66010002@kmitl.ac.th", Role.STUDENT)
        classrooms = self.app.state.classroom_service
        self.classroom = classrooms.create(
            self.teacher,
            course_code="01076001",
            course_name="Programming I",
            section="1",
            semester="1/2569",
        )
        classrooms.join(self.student, self.classroom.join_code)
        published = self.app.state.assignment_service.publish(
            self.teacher,
            AssignmentContent(
                title="Add two numbers",
                problem_statement="Print a + b.",
                difficulty=Difficulty.EASY,
                test_cases=[
                    TestCase(input_data="1 2", expected_output="3"),
                    TestCase(
                        input_data="2 2", expected_output="4", kind=TestCaseKind.HIDDEN
                    ),
                    TestCase(
                        input_data="5 5", expected_output="10", kind=TestCaseKind.EDGE
                    ),
                ],
            ),
            Schedule(deadline=DEADLINE),
            [self.classroom.id],
        )
        self.assignment = published.assignment
        self.posting = published.postings[0]
        self.page = f"/classes/{self.classroom.id}/assignments/{self.assignment.id}"
        self.api = "/api/v1/classrooms" + self.page.removeprefix("/classes")

    def restart(self, runner: ScriptedCodeRunner) -> FastAPI:
        """A fresh process over the same database, clock and runner."""
        return create_app(
            settings=replace(fake_settings(), database_url=self.postgres_url),
            object_storage=FakeObjectStorage(),
            vector_repository=FakeVectorRepository(),
            clock=self.clock,
            code_runner=runner,
            secure_cookies=False,
        )

    async def client(self, actor: Actor, app: FastAPI | None = None):
        client = asgi_client(self.app if app is None else app)
        await log_in(client, actor.email, PASSWORD)
        return client

    def rows(self, sql: str) -> list[tuple]:
        with self.session_factory() as session:
            return [tuple(row) for row in session.execute(text(sql)).all()]

    def submission_id(self) -> int:
        ((submission_id,),) = self.rows("SELECT id FROM core.submissions")
        return submission_id


@pytest.fixture()
def world(
    postgres_url: str, session_factory: SessionFactory, empty_core_tables: None
) -> World:
    return World(postgres_url, session_factory)


@pytest.mark.anyio
async def test_submit_is_stored_at_once_then_judged_in_the_background(
    world: World,
) -> None:
    client = await world.client(world.student)
    seen: dict[str, list[tuple]] = {}

    def at_start() -> None:
        seen["received"] = world.rows(ROW)

    def at_collect() -> None:
        seen["judging"] = world.rows(ROW)
        body = anyio.from_thread.run(
            client.get, f"/api/v1/submissions/{world.submission_id()}"
        ).json()
        seen["polled"] = [(body["status"], body["score"])]
        world.clock.advance(timedelta(minutes=1))

    world.runner.on_start, world.runner.on_collect = at_start, at_collect

    created = await client.post(world.api + "/submissions", json={"code": CODE})
    polled = await client.get(created.headers["location"])

    submission_id = world.submission_id()
    assert created.status_code == 202
    assert created.headers["location"] == f"/api/v1/submissions/{submission_id}"
    assert (created.json()["status"], created.json()["score"]) == ("received", None)
    assert seen["received"] == [
        ("received", None, RECEIVED_AT, False, "none", 0, RECEIVED_AT + LEASE)
    ]
    assert seen["judging"] == [
        ("judging", None, RECEIVED_AT, False, "none", 3, RECEIVED_AT + LEASE)
    ]
    assert seen["polled"] == [("judging", None)]
    assert world.rows(ROW) == [("done", 6, RECEIVED_AT, False, "none", 3, None)]
    assert world.rows(
        "SELECT position, kind, verdict FROM core.submission_test_results"
        " ORDER BY position"
    ) == [
        (1, "sample", "passed"),
        (2, "hidden", "passed"),
        (3, "edge", "wrong_answer"),
    ]
    assert polled.json()["status"] == "done"
    assert (polled.json()["score"], polled.json()["passed"]) == (6, 2)


async def _interrupted_then_recovered(
    world: World, *, runner_survives: bool
) -> tuple[FastAPI, ScriptedCodeRunner]:
    """Submit, lose the worker mid-judging, restart and run one sweep."""
    client = await world.client(world.student)

    def vanish() -> None:
        raise WorkerVanishedError

    world.runner.on_collect = vanish
    with pytest.raises(WorkerVanishedError):
        await client.post(world.api + "/submissions", json={"code": CODE})
    assert world.rows(ROW) == [
        ("judging", None, RECEIVED_AT, False, "none", 3, RECEIVED_AT + LEASE)
    ]

    backend = world.backend if runner_survives else RunnerBackend()
    runner = ScriptedCodeRunner(OUTPUTS, backend)
    restarted = world.restart(runner)
    restarted.state.submission_service.recover()
    assert world.rows("SELECT status FROM core.submissions") == [("judging",)]

    world.clock.advance(LEASE + timedelta(seconds=1))
    restarted.state.submission_service.recover()
    return restarted, runner


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("runner_survives", "recovery", "batches_sent_again"),
    [(True, "reconnected", 0), (False, "rejudged", 1)],
)
async def test_a_restart_mid_judging_is_recovered_after_the_lease(
    world: World, runner_survives: bool, recovery: str, batches_sent_again: int
) -> None:
    _, runner = await _interrupted_then_recovered(
        world, runner_survives=runner_survives
    )

    assert world.rows(ROW) == [("done", 6, RECEIVED_AT, False, recovery, 3, None)]
    assert world.rows("SELECT count(*) FROM core.submission_test_results") == [(3,)]
    assert len(runner.batches) == batches_sent_again


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("runner_survives", "says"),
    [(True, "reconnected to the original run"), (False, "judged again")],
)
async def test_the_recovered_banner_is_for_the_instructor_only(
    world: World, runner_survives: bool, says: str
) -> None:
    restarted, _ = await _interrupted_then_recovered(
        world, runner_survives=runner_survives
    )
    teacher = await world.client(world.teacher, restarted)
    student = await world.client(world.student, restarted)
    outsider = await world.client(world.outsider, restarted)
    panel_url = world.page + f"?student={world.student.user_id}"

    results = await teacher.get(world.page)
    panel = await teacher.get(panel_url)
    own = await student.get(world.page)
    history = await student.get(world.page + "?tab=history")

    assert results.status_code == 200 and "Recovered" in results.text
    assert panel.status_code == 200
    assert 'data-state="T-02a-recovered"' in panel.text and says in panel.text
    for page in (own, history):
        assert page.status_code == 200
        assert "Recovered" not in page.text and "server restart" not in page.text
    for url in (world.page, panel_url, f"/api/v1/submissions/{world.submission_id()}"):
        assert (await outsider.get(url)).status_code == 404


@pytest.mark.anyio
async def test_a_second_submit_while_one_is_pending_is_refused_and_stores_nothing(
    world: World,
) -> None:
    client = await world.client(world.student)
    world.backend.down()

    kept = await client.post(world.api + "/submissions", json={"code": CODE})
    again = await client.post(world.api + "/submissions", json={"code": "print(3)"})

    assert kept.status_code == 202
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "submission_pending"
    stored = world.rows("SELECT attempt, status, claimed_until FROM core.submissions")
    assert stored == [(1, "received", None)]
    with world.session_factory() as session, pytest.raises(IntegrityError) as refused:
        session.execute(
            text(
                "INSERT INTO core.submissions (posting_id, student_id,"
                " version_number, code, language, submitted_at, is_late,"
                " max_score, attempt, status)"
                " VALUES (:posting, :student, 1, 'x', 'python3', now(), false,"
                " 10, 2, 'received')"
            ),
            {"posting": world.posting.id, "student": world.student.user_id},
        )
    assert refused.value.orig.diag.constraint_name == "uq_submissions_one_unfinished"


@pytest.mark.anyio
@pytest.mark.parametrize("moment", ["before tokens are saved", "while polling"])
async def test_unposting_mid_judging_stops_the_worker_and_keeps_no_row(
    world: World, moment: str
) -> None:
    client = await world.client(world.student)
    unposted: list[bool] = []

    def unpost() -> None:
        assert not unposted, "the worker kept judging an unposted Submission"
        world.app.state.assignment_service.unpost(
            world.teacher, world.classroom.id, world.assignment.id
        )
        unposted.append(True)

    world.backend.hold()
    world.runner.on_collect = unpost
    if moment == "before tokens are saved":
        world.runner.on_start = unpost

    created = await client.post(world.api + "/submissions", json={"code": CODE})

    assert created.status_code == 202 and unposted == [True]
    assert world.rows("SELECT count(*) FROM core.submissions") == [(0,)]
    assert world.rows("SELECT count(*) FROM core.submission_test_results") == [(0,)]
    assert world.rows("SELECT count(*) FROM core.classroom_assignments") == [(0,)]
