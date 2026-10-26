"""S-02 solve and T-02 results on the seeded demo, plus the submissions API."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from scripts.demo import BINARY_SEARCH, UPDATE_REASON, DemoSeed, build_demo_app

from questly.core.auth.models import Actor
from questly.core.submissions.models import BatchResult, BatchState, Recovery
from tests.fakes.auth import DEFAULT_PASSWORD, FakeClock
from tests.fakes.submissions import ScriptedCodeRunner
from tests.integration.forms import log_in, post_form

SAMPLES = {"5\n1 3 5 7 9\n7": "3", "4\n2 4 6 8\n5": "-1"}


class LosingRunner(ScriptedCodeRunner):
    """Loses every batch it was given, so judging ends `failed`."""

    def collect(self, tokens: Sequence[str]) -> BatchResult:
        return BatchResult(state=BatchState.GONE)


class Demo:
    def __init__(self, runner: ScriptedCodeRunner | None = None) -> None:
        self.seed = DemoSeed(clock=FakeClock())
        self.runner = ScriptedCodeRunner(SAMPLES) if runner is None else runner
        self.seed.code_runner = self.runner
        self.app = build_demo_app(self.seed)
        self.sec1 = self.seed.programming_1.id

    async def client(self, email: str) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver"
        )
        await log_in(client, email, DEFAULT_PASSWORD)
        return client

    def url(self, number: int) -> str:
        """Sec 1 problem `number` (1-based), as S-01/T-01 link to it."""
        teacher = self.seed.somchai
        actor = Actor(teacher.id, teacher.role, teacher.full_name, teacher.email)
        problems = self.app.state.assignment_service.problems(actor, self.sec1)
        row = next(p for p in problems.problems if p.number == number)
        return f"/classes/{self.sec1}/assignments/{row.assignment.id}"


@pytest.fixture()
def demo() -> Demo:
    return Demo()


BS = BINARY_SEARCH + 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("student", "number", "state"),
    [
        (1, BS, "S-02b"),
        (3, BS, "S-02c"),
        (2, BS, "S-02d"),
        (0, BS, "S-02e"),
        (0, 1, "S-02f"),
    ],
)
async def test_s02_renders_each_seeded_state(
    demo: Demo, student: int, number: int, state: str
) -> None:
    client = await demo.client(demo.seed.students[student].email)

    page = await client.get(demo.url(number))

    assert page.status_code == 200
    assert 'data-page="S-02"' in page.text
    assert f'data-state="{state}"' in page.text


@pytest.mark.anyio
async def test_s02e_shows_the_instructors_reason(demo: Demo) -> None:
    client = await demo.client(demo.seed.students[0].email)

    page = await client.get(demo.url(BS))

    assert UPDATE_REASON in page.text and "Resubmit needed" in page.text


@pytest.mark.anyio
async def test_s02f_is_read_only(demo: Demo) -> None:
    client = await demo.client(demo.seed.students[0].email)

    page = await client.get(demo.url(1))

    assert "readonly" in page.text and "Final score" in page.text


@pytest.mark.anyio
async def test_run_shows_sample_results_and_keeps_nothing(demo: Demo) -> None:
    client = await demo.client(demo.seed.students[0].email)
    url = demo.url(BS)
    before = await client.get(url + "?tab=history")

    page = await post_form(client, url, {"action": "run", "code": "print(3)"}, page=url)
    after = await client.get(url + "?tab=history")

    assert page.status_code == 200
    assert "Results · Run (sample tests only)" in page.text
    assert "print(3)" in page.text
    assert demo.runner.calls == list(SAMPLES)
    assert before.text.count("View code") == after.text.count("View code") == 3


@pytest.mark.anyio
async def test_submit_grades_every_test_and_becomes_the_counted_one(
    demo: Demo,
) -> None:
    client = await demo.client(demo.seed.students[0].email)
    url = demo.url(BS)

    response = await post_form(
        client, url, {"action": "submit", "code": "print(3)"}, page=url
    )
    page = await client.get(url)

    assert (
        response.status_code == 303
        and response.headers["location"] == f"{url}?submitted=1"
    )
    assert len(demo.runner.calls) == 6
    assert "attempt 4" in page.text
    assert 'data-state="S-02e"' not in page.text


@pytest.mark.anyio
async def test_s02g_history_lists_attempts_and_shows_code(demo: Demo) -> None:
    client = await demo.client(demo.seed.students[0].email)
    url = demo.url(BS)

    page = await client.get(url + "?tab=history&attempt=2")

    assert 'data-state="S-02g"' in page.text
    assert 'data-attempt="2"' in page.text and "def binary_search" in page.text
    assert (await client.get(url + "?tab=history&attempt=9")).status_code == 404


@pytest.mark.anyio
async def test_t02_results_counts_the_counted_submissions(demo: Demo) -> None:
    client = await demo.client(demo.seed.somchai.email)

    page = await client.get(demo.url(BS))

    assert page.status_code == 200 and 'data-page="T-02"' in page.text
    assert "4 / 6" in page.text
    assert "Hidden 1 · empty list" in page.text
    assert "Nattapong Suwan" in page.text and "Resubmit needed" in page.text


@pytest.mark.anyio
async def test_t02a_panel_shows_a_students_code(demo: Demo) -> None:
    client = await demo.client(demo.seed.somchai.email)
    student = demo.seed.students[3]

    page = await client.get(demo.url(BS) + f"?student={student.id}")

    assert 'data-page="T-02a"' in page.text and "def binary_search" in page.text
    assert (await client.get(demo.url(BS) + "?student=9999")).status_code == 404


@pytest.mark.anyio
async def test_t02b_lists_versions_with_reasons(demo: Demo) -> None:
    client = await demo.client(demo.seed.somchai.email)

    page = await client.get(demo.url(BS) + "?tab=versions")

    assert 'data-state="T-02b"' in page.text
    assert UPDATE_REASON in page.text and "v2 · current" in page.text


@pytest.mark.anyio
async def test_t02c_when_nobody_submitted(demo: Demo) -> None:
    client = await demo.client(demo.seed.somchai.email)

    page = await client.get(demo.url(6))

    assert 'data-state="T-02c"' in page.text and "No submissions yet" in page.text


@pytest.mark.anyio
async def test_close_submissions_closes_s02(demo: Demo) -> None:
    teacher = await demo.client(demo.seed.somchai.email)
    student = await demo.client(demo.seed.students[1].email)
    url = demo.url(BS)

    closed = await post_form(teacher, url + "/close", {}, page=url)
    refused = await post_form(
        student, url, {"action": "submit", "code": "print(1)"}, page=url
    )

    assert closed.status_code == 303
    assert refused.status_code == 409 and 'data-state="S-02f"' in refused.text


@pytest.mark.anyio
async def test_post_without_the_csrf_token_is_403(demo: Demo) -> None:
    client = await demo.client(demo.seed.students[0].email)

    response = await client.post(demo.url(BS), data={"action": "run", "code": "x"})

    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("who", "query", "status"),
    [
        ("student", "?tab=versions", 403),
        ("student", "?tab=nope", 404),
        ("teacher", "?tab=history", 404),
        ("warunee", "", 404),
        ("sec2_student", "", 404),
    ],
)
async def test_roles_and_outsiders(
    demo: Demo, who: str, query: str, status: int
) -> None:
    email = {
        "student": demo.seed.students[0].email,
        "teacher": demo.seed.somchai.email,
        "warunee": demo.seed.warunee.email,
        "sec2_student": demo.seed.students[7].email,
    }[who]
    client = await demo.client(email)

    response = await client.get(demo.url(BS) + query)

    assert response.status_code == status


@pytest.mark.anyio
async def test_instructor_cannot_submit(demo: Demo) -> None:
    client = await demo.client(demo.seed.somchai.email)
    url = demo.url(BS)

    response = await post_form(
        client, url, {"action": "submit", "code": "print(1)"}, page=url + "?student=0"
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_api_run_submit_list_and_get(demo: Demo) -> None:
    client = await demo.client(demo.seed.students[1].email)
    api = "/api/v1/classrooms" + demo.url(BS).removeprefix("/classes")

    run = await client.post(api + "/runs", json={"code": "print(3)"})
    created = await client.post(api + "/submissions", json={"code": "print(3)"})
    listed = await client.get(api + "/submissions")
    fetched = await client.get(f"/api/v1/submissions/{created.json()['id']}")

    assert run.status_code == 200 and run.json()["total"] == 2
    assert created.status_code == 202 and created.json()["attempt"] == 2
    assert [s["attempt"] for s in listed.json()] == [1, 2]
    assert fetched.status_code == 200 and fetched.json()["status"] == "done"
    hidden = [r for r in fetched.json()["results"] if r["kind"] != "sample"]
    assert hidden and all(r["actual_output"] is None for r in hidden)


@pytest.mark.anyio
async def test_api_submit_answers_202_with_the_status_url_and_no_score_yet(
    demo: Demo,
) -> None:
    client = await demo.client(demo.seed.students[1].email)
    api = "/api/v1/classrooms" + demo.url(BS).removeprefix("/classes")

    created = await client.post(api + "/submissions", json={"code": "print(3)"})
    status = await client.get(created.headers["location"])

    body = created.json()
    assert created.status_code == 202
    assert created.headers["location"] == f"/api/v1/submissions/{body['id']}"
    assert (body["status"], body["score"], body["results"]) == ("received", None, [])
    assert body["recovery"] == "none"
    judged = status.json()
    assert judged["status"] == "done" and judged["score"] is not None
    assert judged["total"] == len(judged["results"]) > 0


@pytest.mark.anyio
async def test_api_refuses_submit_and_run_while_a_submission_is_pending(
    demo: Demo,
) -> None:
    client = await demo.client(demo.seed.students[1].email)
    api = "/api/v1/classrooms" + demo.url(BS).removeprefix("/classes")

    kept = await client.post(api + "/submissions", json={"code": "# judge down"})
    again = await client.post(api + "/submissions", json={"code": "print(3)"})
    run = await client.post(api + "/runs", json={"code": "print(3)"})
    status = await client.get(kept.headers["location"])

    assert kept.status_code == 202
    assert status.json()["status"] == "received"
    for refused in (again, run):
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "submission_pending"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("who", "status"),
    [("owner", 200), ("teacher", 200), ("classmate", 404), ("sec2_student", 404)],
)
async def test_api_submission_status_is_for_its_student_and_instructor(
    demo: Demo, who: str, status: int
) -> None:
    owner = await demo.client(demo.seed.students[1].email)
    api = "/api/v1/classrooms" + demo.url(BS).removeprefix("/classes")
    created = await owner.post(api + "/submissions", json={"code": "# judge down"})
    email = {
        "owner": demo.seed.students[1].email,
        "teacher": demo.seed.somchai.email,
        "classmate": demo.seed.students[3].email,
        "sec2_student": demo.seed.students[7].email,
    }[who]
    client = await demo.client(email)

    response = await client.get(created.headers["location"])

    assert response.status_code == status
    if status == 200:
        assert response.json()["status"] == "received"
        assert response.json()["score"] is None


@pytest.mark.anyio
async def test_api_hides_other_students_submissions(demo: Demo) -> None:
    owner = await demo.client(demo.seed.students[1].email)
    other = await demo.client(demo.seed.students[3].email)
    api = "/api/v1/classrooms" + demo.url(BS).removeprefix("/classes")
    created = await owner.post(api + "/submissions", json={"code": "print(3)"})

    response = await other.get(f"/api/v1/submissions/{created.json()['id']}")

    assert response.status_code == 404


@pytest.mark.anyio
async def test_archived_classroom_shows_s02f_and_refuses_submit(demo: Demo) -> None:
    teacher = await demo.client(demo.seed.somchai.email)
    student = await demo.client(demo.seed.students[1].email)
    url = demo.url(BS)
    settings = f"/classes/{demo.sec1}?tab=settings"
    await post_form(teacher, f"/classes/{demo.sec1}/archive", {}, page=settings)

    page = await student.get(url)
    refused = await student.post(
        "/api/v1/classrooms" + url.removeprefix("/classes") + "/submissions",
        json={"code": "print(3)"},
    )

    assert "This classroom is archived" in page.text and "readonly" in page.text
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "classroom_archived"


@pytest.mark.anyio
async def test_toast_reports_pass_and_fail_after_submit(demo: Demo) -> None:
    url = demo.url(BS)
    passing = await demo.client(demo.seed.students[1].email)
    failing = await demo.client(demo.seed.students[3].email)

    passed = await passing.get(url + "?submitted=1")
    failed = await failing.get(url + "?submitted=1")
    plain = await passing.get(url)

    assert 'data-state="toast-pass"' in passed.text
    assert 'data-state="toast-fault"' in failed.text
    assert "toast-stack" not in plain.text


@pytest.mark.anyio
async def test_toast_reports_a_failing_run(demo: Demo) -> None:
    client = await demo.client(demo.seed.students[0].email)
    url = demo.url(BS)

    page = await post_form(client, url, {"action": "run", "code": "# crash"}, page=url)

    assert 'data-state="toast-fault"' in page.text
    assert 'data-state="toast-pass"' not in page.text


@pytest.mark.anyio
async def test_page_asks_to_try_again_when_the_runner_is_down_for_a_run(
    demo: Demo,
) -> None:
    client = await demo.client(demo.seed.students[1].email)
    url = demo.url(BS)

    page = await post_form(
        client, url, {"action": "run", "code": "# judge down"}, page=url
    )
    history = await client.get(url + "?tab=history")

    assert page.status_code == 503
    assert 'data-page="S-02"' in page.text
    assert "try again" in page.text and "# judge down" in page.text
    assert history.text.count("View code") == 1


@pytest.mark.anyio
async def test_api_answers_503_when_the_runner_is_down_for_a_run(demo: Demo) -> None:
    client = await demo.client(demo.seed.students[1].email)
    api = "/api/v1/classrooms" + demo.url(BS).removeprefix("/classes")

    response = await client.post(api + "/runs", json={"code": "# judge down"})
    listed = await client.get(api + "/submissions")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "runner_unavailable"
    assert [s["attempt"] for s in listed.json()] == [1]


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["/runs", "/submissions"])
@pytest.mark.parametrize(("who", "status"), [("teacher", 403), ("sec2_student", 404)])
async def test_api_authorizes_before_reaching_the_runner(
    demo: Demo, endpoint: str, who: str, status: int
) -> None:
    email = {
        "teacher": demo.seed.somchai.email,
        "sec2_student": demo.seed.students[7].email,
    }[who]
    client = await demo.client(email)
    api = "/api/v1/classrooms" + demo.url(BS).removeprefix("/classes")

    response = await client.post(api + endpoint, json={"code": "# judge down"})

    assert response.status_code == status


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["run", "submit"])
@pytest.mark.parametrize(("who", "status"), [("teacher", 403), ("sec2_student", 404)])
async def test_page_authorizes_before_reaching_the_runner(
    demo: Demo, action: str, who: str, status: int
) -> None:
    email = {
        "teacher": demo.seed.somchai.email,
        "sec2_student": demo.seed.students[7].email,
    }[who]
    client = await demo.client(email)
    url = demo.url(BS)
    form_page = url + "?student=0" if who == "teacher" else "/classes"

    response = await post_form(
        client, url, {"action": action, "code": "# judge down"}, page=form_page
    )

    assert response.status_code == status


# ---- S-02a and the async states on S-02, S-02g and T-02 --------------------


PENDING_CODE = "print(3)  # waiting for the judge"


async def _submit_during_an_outage(demo: Demo) -> AsyncClient:
    """Students[1] submits while the runner is down: the Submission stays
    pending until a sweep finds the runner back."""
    client = await demo.client(demo.seed.students[1].email)
    url = demo.url(BS)
    demo.runner.backend.down()
    response = await post_form(
        client, url, {"action": "submit", "code": PENDING_CODE}, page=url
    )
    assert response.status_code == 303
    return client


@pytest.mark.anyio
async def test_s02a_refreshes_read_only_while_the_submission_is_judged(
    demo: Demo,
) -> None:
    client = await _submit_during_an_outage(demo)
    url = demo.url(BS)

    page = await client.get(url + "?submitted=1")
    history = await client.get(url + "?tab=history")
    refused = await post_form(
        client, url, {"action": "run", "code": "print(3)"}, page=url
    )

    assert page.status_code == 200 and 'data-state="S-02a"' in page.text
    assert '<meta http-equiv="refresh" content="2">' in page.text
    assert "Judging attempt 2, 6 tests" in page.text
    assert "readonly" in page.text and PENDING_CODE in page.text
    assert 'value="submit"' not in page.text and 'value="run"' not in page.text
    assert "toast-stack" not in page.text
    assert "Judging" in history.text and history.text.count("View code") == 2
    assert refused.status_code == 409 and "still being judged" in refused.text


@pytest.mark.anyio
@pytest.mark.parametrize(("who", "status"), [("classmate", 200), ("sec2_student", 404)])
async def test_s02a_is_only_the_submitting_students_page(
    demo: Demo, who: str, status: int
) -> None:
    await _submit_during_an_outage(demo)
    email = {
        "classmate": demo.seed.students[3].email,
        "sec2_student": demo.seed.students[7].email,
    }[who]
    client = await demo.client(email)

    page = await client.get(demo.url(BS))

    assert page.status_code == status
    assert 'data-state="S-02a"' not in page.text
    assert "refresh" not in page.text


@pytest.mark.anyio
async def test_s02a_turns_into_the_result_toast_once_judged(demo: Demo) -> None:
    client = await _submit_during_an_outage(demo)
    url = demo.url(BS)
    demo.runner.backend.resume()

    demo.app.state.submission_service.recover()
    page = await client.get(url + "?submitted=1")

    assert 'data-state="S-02a"' not in page.text and "refresh" not in page.text
    assert "toast-stack" in page.text and "attempt 2" in page.text


@pytest.mark.anyio
async def test_a_failed_submission_shows_an_alert_and_submit_works_again() -> None:
    demo = Demo(LosingRunner(SAMPLES))
    client = await demo.client(demo.seed.students[1].email)
    url = demo.url(BS)

    await post_form(client, url, {"action": "submit", "code": "print(3)"}, page=url)
    page = await client.get(url + "?submitted=1")
    history = await client.get(url + "?tab=history")

    assert 'data-state="S-02-failed"' in page.text
    assert "Attempt 2 could not be judged" in page.text
    assert 'value="submit"' in page.text and "refresh" not in page.text
    assert "toast-stack" not in page.text
    assert "Not judged" in history.text


@pytest.mark.anyio
async def test_t02_shows_a_judging_badge_for_a_pending_submission(
    demo: Demo,
) -> None:
    await _submit_during_an_outage(demo)
    teacher = await demo.client(demo.seed.somchai.email)
    student = demo.seed.students[1]

    page = await teacher.get(demo.url(BS))
    panel = await teacher.get(demo.url(BS) + f"?student={student.id}")

    assert page.status_code == 200 and "Judging" in page.text
    assert "An attempt is being judged" in panel.text


@pytest.mark.anyio
@pytest.mark.parametrize("recovery", [Recovery.RECONNECTED, Recovery.REJUDGED])
async def test_the_recovered_banner_shows_to_the_instructor_only(
    demo: Demo, recovery: Recovery
) -> None:
    student = demo.seed.students[1]
    url = demo.url(BS)
    teacher_client = await demo.client(demo.seed.somchai.email)
    student_client = await demo.client(student.email)
    posting_id = demo.app.state.assignment_service.problem(
        Actor(student.id, student.role, student.full_name, student.email),
        demo.sec1,
        int(url.rsplit("/", 1)[1]),
    ).posting.id
    (last,) = demo.seed.submissions.list_for_student(posting_id, student.id)
    recovered = demo.seed.submissions.add(
        replace(
            last,
            id=None,
            attempt=last.attempt + 1,
            submitted_at=last.submitted_at + timedelta(hours=1),
            recovery=recovery,
        )
    )

    results = await teacher_client.get(url)
    panel = await teacher_client.get(url + f"?student={student.id}")
    own = await student_client.get(url)
    history = await student_client.get(url + "?tab=history")

    assert "Recovered" in results.text
    assert 'data-state="T-02a-recovered"' in panel.text
    assert "Late is decided from this time" in panel.text
    expected = {
        Recovery.RECONNECTED: "reconnected to the original run",
        Recovery.REJUDGED: "The code was judged again.",
    }[recovery]
    assert expected in panel.text
    assert recovered.attempt == 2
    for page in (own, history):
        assert "Recovered" not in page.text and "server restart" not in page.text
