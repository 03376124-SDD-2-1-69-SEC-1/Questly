"""T-03 quota lines and the T-03c progress page, for every role."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from questly.core.assignments.models import Difficulty
from questly.core.generation.models import (
    GenerationStep,
    ProviderQuotaSnapshot,
    QuotaKind,
)
from tests.integration.forms import post_form
from tests.integration.markup import loading_icons
from tests.integration.test_drafts import (
    OTHER_TEACHER,
    OUTSIDER,
    STUDENT,
    TEACHER,
    Campus,
)

MIDNIGHT_UTC = datetime(2026, 10, 13, 0, 0, tzinfo=UTC)


def spent() -> ProviderQuotaSnapshot:
    return ProviderQuotaSnapshot(
        kind=QuotaKind.EXACT,
        used=50,
        limit=50,
        remaining=0,
        resets_at=MIDNIGHT_UTC,
        note="",
    )


async def running_job(campus: Campus, client: AsyncClient) -> int:
    """A job that was started but not run, as a browser sees it mid-flight."""
    service = campus.app.state.generation_service
    owner = campus.app.state.auth_service.resolve_session(
        client.cookies["questly_session"]
    )
    started = service.start_job(
        owner,
        campus.classroom.id,
        prompt="Binary search",
        difficulty=Difficulty.MEDIUM,
        document_ids=[1],
    )
    return started.job.id


def job_url(campus: Campus, job_id: int) -> str:
    return f"/classes/{campus.classroom.id}/generate/jobs/{job_id}"


# ---- T-03: the two quota lines ------------------------------------------------


@pytest.mark.anyio
async def test_t03_shows_both_quota_lines() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)

    page = await teacher.get(f"/classes/{campus.classroom.id}/generate")

    assert "Uses 1 of your 20 daily generations · 20 left today." in page.text
    assert "OpenRouter free quota: 37 of 50 left today · resets 07:00" in page.text


@pytest.mark.anyio
async def test_t03_says_when_the_provider_quota_is_not_measured() -> None:
    campus = Campus()
    campus.provider_quota.current = ProviderQuotaSnapshot(
        kind=QuotaKind.UNKNOWN,
        resets_at=MIDNIGHT_UTC,
        note="anthropic/claude is not a free model, so OpenRouter reports no quota.",
    )
    teacher = await campus.client_for(TEACHER)

    page = await teacher.get(f"/classes/{campus.classroom.id}/generate")

    assert "is not a free model" in page.text
    assert "disabled" not in page.text


@pytest.mark.anyio
async def test_t03_disables_generate_when_the_provider_quota_is_spent() -> None:
    campus = Campus()
    campus.provider_quota.current = spent()
    teacher = await campus.client_for(TEACHER)

    page = await teacher.get(f"/classes/{campus.classroom.id}/generate")

    assert "disabled" in page.text
    assert "free-model quota is spent" in page.text
    assert "07:00" in page.text


@pytest.mark.anyio
async def test_posting_while_the_provider_quota_is_spent_is_refused() -> None:
    campus = Campus()
    campus.provider_quota.current = spent()
    teacher = await campus.client_for(TEACHER)
    cid = campus.classroom.id

    response = await post_form(
        teacher,
        f"/classes/{cid}/generate",
        {"prompt": "x"},
        page=f"/classes/{cid}/generate",
    )

    assert response.status_code == 429
    assert "free-model quota is spent" in response.text
    assert campus.client_stub.requests == []


# ---- POST /generate --------------------------------------------------------------


@pytest.mark.anyio
async def test_post_redirects_to_the_job_and_runs_it_in_the_background() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    cid = campus.classroom.id

    submitted = await post_form(
        teacher,
        f"/classes/{cid}/generate",
        {"prompt": "Binary search", "document_ids": "1"},
        page=f"/classes/{cid}/generate",
    )

    assert submitted.status_code == 303
    assert submitted.headers["location"].startswith(f"/classes/{cid}/generate/jobs/")
    assert len(campus.client_stub.requests) == 1
    assert campus.provider_quota.invalidations == 1


@pytest.mark.anyio
async def test_a_second_post_while_running_redirects_to_the_same_job() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    cid = campus.classroom.id
    job_id = await running_job(campus, teacher)

    submitted = await post_form(
        teacher,
        f"/classes/{cid}/generate",
        {"prompt": "again"},
        page=f"/classes/{cid}/generate",
    )

    assert submitted.headers["location"] == job_url(campus, job_id)
    assert campus.client_stub.requests == []


@pytest.mark.anyio
async def test_posting_an_empty_prompt_stays_on_t03() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    cid = campus.classroom.id

    response = await post_form(
        teacher,
        f"/classes/{cid}/generate",
        {"prompt": "  "},
        page=f"/classes/{cid}/generate",
    )

    assert response.status_code == 422
    assert 'data-page="T-03"' in response.text


@pytest.mark.anyio
async def test_posting_without_a_csrf_token_is_refused() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)

    response = await teacher.post(
        f"/classes/{campus.classroom.id}/generate", data={"prompt": "x"}
    )

    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("who", "status"), [(STUDENT, 403), (OTHER_TEACHER, 404), (OUTSIDER, 404)]
)
async def test_only_the_owner_can_post(who: str, status: int) -> None:
    campus = Campus()
    client = await campus.client_for(who)
    cid = campus.classroom.id

    response = await post_form(
        client, f"/classes/{cid}/generate", {"prompt": "x"}, page="/classes"
    )

    assert response.status_code == status
    assert campus.client_stub.requests == []


# ---- T-03c ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_t03c_shows_a_running_job_with_progress_and_both_quotas() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    job_id = await running_job(campus, teacher)
    campus.job_store.update(
        job_id,
        lambda job: job.advance(GenerationStep.CALLING_MODEL, job.started_at).advance(
            GenerationStep.VALIDATING, job.started_at + timedelta(seconds=7)
        ),
    )

    page = await teacher.get(job_url(campus, job_id))

    assert page.status_code == 200
    assert 'data-page="T-03c"' in page.text
    assert '<meta http-equiv="refresh" content="2">' in page.text
    assert "✓" in page.text and "7 s" in page.text
    assert "In progress" in page.text
    assert "OpenRouter free quota: 37 of 50 left today" in page.text
    assert "Uses 1 of your 20 daily generations" in page.text
    assert "Cancel" in page.text
    assert "<script" not in page.text


@pytest.mark.anyio
async def test_t03c_shows_the_generating_icon_beside_a_cancel_control() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    job_id = await running_job(campus, teacher)
    url = job_url(campus, job_id)

    page = await teacher.get(url)

    assert page.status_code == 200
    assert 'data-page="T-03c"' in page.text
    [icon] = loading_icons(page.text)
    assert 'data-loading="generating"' in icon
    assert 'aria-hidden="true"' in icon
    assert f'<form method="post" action="{url}/cancel"' in page.text


@pytest.mark.anyio
async def test_t03c_redirects_to_the_draft_when_the_job_is_done() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    cid = campus.classroom.id
    submitted = await post_form(
        teacher,
        f"/classes/{cid}/generate",
        {"prompt": "x"},
        page=f"/classes/{cid}/generate",
    )

    finished = await teacher.get(submitted.headers["location"])

    assert finished.status_code == 303
    assert finished.headers["location"].startswith(f"/classes/{cid}/generate?draft=")


@pytest.mark.anyio
async def test_t03c_shows_t03d_with_the_form_refilled_when_the_job_failed() -> None:
    campus = Campus()
    campus.client_stub.fail = True
    teacher = await campus.client_for(TEACHER)
    cid = campus.classroom.id
    submitted = await post_form(
        teacher,
        f"/classes/{cid}/generate",
        {"prompt": "Refill me", "document_ids": "1"},
        page=f"/classes/{cid}/generate",
    )

    page = await teacher.get(submitted.headers["location"])

    assert page.status_code == 200
    assert 'data-state="T-03d"' in page.text
    assert "Refill me" in page.text
    assert "no quota was used" in page.text
    assert loading_icons(page.text) == []


@pytest.mark.anyio
async def test_a_stale_job_reads_as_failed() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    job_id = await running_job(campus, teacher)
    campus.clock.advance(timedelta(minutes=3, seconds=1))

    page = await teacher.get(job_url(campus, job_id))

    assert 'data-state="T-03d"' in page.text
    assert "took too long" in page.text


@pytest.mark.anyio
async def test_t03c_says_so_when_the_job_is_gone() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)

    page = await teacher.get(job_url(campus, 424242))

    assert page.status_code == 200
    assert 'data-page="T-03c"' in page.text
    assert "This generation is no longer available, try again" in page.text
    assert "http-equiv" not in page.text
    assert loading_icons(page.text) == []


@pytest.mark.anyio
async def test_cancelling_returns_to_t03_with_the_form_refilled() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    job_id = await running_job(campus, teacher)
    url = job_url(campus, job_id)

    cancelled = await post_form(teacher, f"{url}/cancel", {}, page=url)
    page = await teacher.get(cancelled.headers["location"])

    assert cancelled.status_code == 303
    assert cancelled.headers["location"] == url
    assert 'data-page="T-03"' in page.text
    assert "Binary search" in page.text


@pytest.mark.anyio
async def test_a_job_that_was_cancelled_does_not_run() -> None:
    campus = Campus()
    teacher = await campus.client_for(TEACHER)
    job_id = await running_job(campus, teacher)
    url = job_url(campus, job_id)

    await post_form(teacher, f"{url}/cancel", {}, page=url)

    assert campus.client_stub.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("who", "status"), [(STUDENT, 403), (OTHER_TEACHER, 404), (OUTSIDER, 404)]
)
async def test_only_the_instructor_who_started_the_job_can_see_it(
    who: str, status: int
) -> None:
    campus = Campus()
    owner = await campus.client_for(TEACHER)
    job_id = await running_job(campus, owner)
    client = await campus.client_for(who)
    url = job_url(campus, job_id)

    seen = await client.get(url)
    cancelled = await post_form(client, f"{url}/cancel", {}, page="/classes")

    assert seen.status_code == status
    assert loading_icons(seen.text) == []
    assert cancelled.status_code == status
    assert campus.job_store.get(job_id).status.value == "running"
