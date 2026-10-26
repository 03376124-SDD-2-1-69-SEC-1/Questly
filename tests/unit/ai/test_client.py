"""Tests for generation client adapters.

StubGenerationClient tests remain as-is (regression guard).
OpenRouterGenerationClient tests use monkeypatch to avoid real HTTP calls.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import openrouter.utils.retries
import pytest
from openrouter.errors import NoResponseError, OpenRouterError

import questly.ai.client as client_module
from questly.ai.app.models import ChunkSearchResult
from questly.ai.client import (
    _RESPONSE_FORMAT,
    CitationRetriever,
    OpenRouterGenerationClient,
    StubGenerationClient,
    VectorCitationRetriever,
    _flattened_sample_strategies,
    _grounding_failure,
    _parse_response,
    _sample_pairs,
    _SampleRunBudget,
    _too_similar,
)
from questly.ai.retrieval.models import RetrievalResult
from questly.core.generation.models import GenerationStep
from questly.core.generation.schemas import (
    Citation,
    GenerationFilters,
    GenerationRequest,
    GenerationResponse,
)
from questly.core.generation.service import GenerationFailedError, GenerationService
from questly.core.submissions.models import Execution, ExecutionStatus
from questly.core.submissions.ports import CodeRunner
from questly.integrations.judge0 import StubCodeRunner

# ---------------------------------------------------------------------------
# StubGenerationClient — kept as regression guard
# ---------------------------------------------------------------------------


def test_stub_client_returns_draft_with_citations() -> None:
    client = StubGenerationClient()
    request = GenerationRequest(prompt="two-sum problem")

    response = client.generate(request, _ignore_step)

    assert response.draft.title
    assert response.draft.test_cases
    assert response.citations


def test_stub_client_echoes_prompt_into_statement() -> None:
    client = StubGenerationClient()
    request = GenerationRequest(prompt="reverse a linked list")

    response = client.generate(request, _ignore_step)

    assert "reverse a linked list" in response.draft.statement


# ---------------------------------------------------------------------------
# _parse_response helper
# ---------------------------------------------------------------------------


def test_parse_response_valid_json() -> None:
    raw = """{
        "title": "Two Sum",
        "statement": "Given an array, find two numbers that add up to target.",
        "test_cases": [
            {"input_data": "4 2\\n1 3"},
            {"input_data": "3 1\\n0 1"}
        ],
        "reference_solution": "print(1)"
    }"""
    parsed = _parse_response(raw)
    response = parsed.response

    assert response.draft.title == "Two Sum"
    assert "array" in response.draft.statement
    assert len(response.draft.test_cases) == 2
    # the parser never trusts the LLM for outputs; the CodeRunner fills them
    assert all(tc.expected_output == "" for tc in response.draft.test_cases)
    # citations empty until AI-05
    assert response.citations == []


def test_parse_response_carries_reference_solution() -> None:
    raw = (
        '{"title": "T", "statement": "S", "test_cases": '
        '[{"input_data": "1"}, {"input_data": "2"}],'
        ' "reference_solution": "print(int(input()) + 1)"}'
    )
    assert _parse_response(raw).reference_solution == "print(int(input()) + 1)"


def test_parse_response_strips_markdown_fence() -> None:
    raw = (
        "```json\n"
        '{"title": "X", "statement": "Y", "test_cases": '
        '[{"input_data": "1"}, {"input_data": "2"}], '
        '"reference_solution": "print(input())"}\n'
        "```"
    )
    assert _parse_response(raw).response.draft.title == "X"


def test_parse_response_invalid_json_raises() -> None:
    with pytest.raises(ValueError, match="does not match assignment draft schema"):
        _parse_response("not json at all")


def test_parse_response_missing_title_raises() -> None:
    raw = (
        '{"statement": "S", "test_cases": '
        '[{"input_data": "1"}, {"input_data": "2"}], '
        '"reference_solution": "print(input())"}'
    )
    with pytest.raises(ValueError, match="does not match assignment draft schema"):
        _parse_response(raw)


def test_parse_response_missing_statement_raises() -> None:
    raw = (
        '{"title": "T", "test_cases": '
        '[{"input_data": "1"}, {"input_data": "2"}], '
        '"reference_solution": "print(input())"}'
    )
    with pytest.raises(ValueError, match="does not match assignment draft schema"):
        _parse_response(raw)


def test_parse_response_empty_test_cases_raises() -> None:
    raw = (
        '{"title": "T", "statement": "S", "test_cases": [], '
        '"reference_solution": "print(input())"}'
    )
    with pytest.raises(ValueError, match="does not match assignment draft schema"):
        _parse_response(raw)


def test_parse_response_rejects_fields_outside_strict_schema() -> None:
    raw = json.dumps(
        {
            "title": "T",
            "statement": "S",
            "test_cases": [{"input_data": "1"}, {"input_data": "2"}],
            "reference_solution": "print(input())",
            "unexpected": True,
        }
    )

    with pytest.raises(ValueError, match="does not match assignment draft schema"):
        _parse_response(raw)


# ---------------------------------------------------------------------------
# OpenRouterGenerationClient — monkeypatched, no real HTTP
# ---------------------------------------------------------------------------


class FakeCodeRunner:
    """Satisfies CodeRunner: records each call and replays scripted Executions."""

    def __init__(self, executions: list[Execution]) -> None:
        self._executions = list(executions)
        self.calls: list[tuple[str, str, str]] = []

    def run(
        self, *, code: str, language: str, stdin: str, time_limit_seconds: float
    ) -> Execution:
        self.calls.append((code, language, stdin))
        return self._executions.pop(0)


def _ok(stdout: str) -> Execution:
    return Execution(
        status=ExecutionStatus.OK, stdout=stdout, stderr="", time_seconds=0.01
    )


def _failed(status: ExecutionStatus) -> Execution:
    return Execution(status=status, stdout="partial", stderr="boom", time_seconds=5.0)


def _make_client(
    model: str = "test/model",
    code_runner: CodeRunner | None = None,
    retriever: CitationRetriever | None = None,
) -> OpenRouterGenerationClient:
    """Build a client with a dummy key; SDK won't validate until a call is made."""
    if code_runner is None:
        code_runner = FakeCodeRunner([_ok("5"), _ok("42")])
    return OpenRouterGenerationClient(
        api_key="dummy-key",
        model=lambda: model,
        code_runner=code_runner,
        retriever=retriever,
    )


def _mock_result(content: str) -> MagicMock:
    """Return a mock that looks like openrouter.components.ChatResult."""
    choice = MagicMock()
    choice.message.content = content
    result = MagicMock()
    result.choices = [choice]
    return result


_SOLUTION = "n = int(input())\nprint(max(map(int, input().split())))"
_STRUCTURED_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

_VALID_LLM_RESPONSE = json.dumps(
    {
        "title": "Find Maximum",
        "statement": "Given N integers, find the maximum.",
        "test_cases": [{"input_data": "3\n1 5 2"}, {"input_data": "1\n42"}],
        "reference_solution": _SOLUTION,
    }
)

_NO_SOLUTION_LLM_RESPONSE = """{
    "title": "Find Maximum",
    "statement": "Given N integers, find the maximum.",
    "test_cases": [
        {"input_data": "3\\n1 5 2"},
        {"input_data": "1\\n42"}
    ]
}"""


def _ignore_step(step: GenerationStep) -> None:
    pass


def _generate(client: OpenRouterGenerationClient, llm_reply: str) -> GenerationResponse:
    with patch.object(
        client._client.chat,
        "send",
        return_value=_mock_result(llm_reply),
    ):
        return client.generate(GenerationRequest(prompt="find max"), _ignore_step)


def test_openrouter_client_reads_the_model_on_every_call() -> None:
    """An Admin's A-01 model change reaches the next generation."""
    chosen = ["first/model"]
    client = OpenRouterGenerationClient(
        api_key="dummy-key",
        model=lambda: chosen[0],
        code_runner=FakeCodeRunner([_ok("5"), _ok("42"), _ok("5"), _ok("42")]),
    )

    with patch.object(
        client._client.chat,
        "send",
        return_value=_mock_result(_VALID_LLM_RESPONSE),
    ) as create:
        client.generate(GenerationRequest(prompt="find max"), _ignore_step)
        chosen[0] = "second/model"
        client.generate(GenerationRequest(prompt="find max"), _ignore_step)

    assert [call.kwargs["model"] for call in create.call_args_list] == [
        "first/model",
        "second/model",
    ]


def test_openrouter_client_returns_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()

    with patch.object(
        client._client.chat,
        "send",
        return_value=_mock_result(_VALID_LLM_RESPONSE),
    ):
        response = client.generate(
            GenerationRequest(prompt="find max in array"), _ignore_step
        )

    assert response.draft.title == "Find Maximum"
    assert len(response.draft.test_cases) == 2


def test_openrouter_client_runs_reference_solution_on_each_input() -> None:
    runner = FakeCodeRunner([_ok("5\n"), _ok("42\n")])

    _generate(_make_client(code_runner=runner), _VALID_LLM_RESPONSE)

    assert runner.calls == [
        (_SOLUTION, "python", "3\n1 5 2"),
        (_SOLUTION, "python", "1\n42"),
    ]


def test_openrouter_client_expected_output_is_runner_stdout() -> None:
    runner = FakeCodeRunner([_ok("5  \nx\n\n"), _ok("42\n")])

    response = _generate(_make_client(code_runner=runner), _VALID_LLM_RESPONSE)

    # stored the way SubmissionService.outputs_match normalises a run's stdout
    assert [tc.expected_output for tc in response.draft.test_cases] == ["5\nx", "42"]


@pytest.mark.parametrize(
    "status", [ExecutionStatus.RUNTIME_ERROR, ExecutionStatus.TIME_LIMIT]
)
def test_openrouter_client_failed_run_leaves_that_output_blank(
    status: ExecutionStatus, caplog: pytest.LogCaptureFixture
) -> None:
    runner = FakeCodeRunner([_failed(status), _ok("42\n")])

    with caplog.at_level(logging.WARNING, logger="questly.ai.client"):
        response = _generate(
            _make_client(model="some/model", code_runner=runner), _VALID_LLM_RESPONSE
        )

    assert [tc.expected_output for tc in response.draft.test_cases] == ["", "42"]
    assert "some/model" in caplog.text
    assert status.value in caplog.text
    assert _SOLUTION not in caplog.text


@pytest.mark.parametrize(
    "solution_field", ["", ', "reference_solution": ""', ', "reference_solution": 7']
)
def test_openrouter_client_rejects_reply_without_valid_solution(
    solution_field: str,
) -> None:
    runner = FakeCodeRunner([_ok("5"), _ok("42")])
    reply = _NO_SOLUTION_LLM_RESPONSE.rstrip().removesuffix("}") + solution_field + "}"

    with pytest.raises(ValueError, match="does not match assignment draft schema"):
        _generate(_make_client(code_runner=runner), reply)

    assert runner.calls == []


def test_openrouter_client_sends_strict_assignment_schema() -> None:
    client = _make_client(model=_STRUCTURED_MODEL)

    with patch.object(
        client._client.chat,
        "send",
        return_value=_mock_result(_VALID_LLM_RESPONSE),
    ) as send:
        client.generate(GenerationRequest(prompt="find max"), _ignore_step)

    response_format = send.call_args.kwargs["response_format"]
    assert response_format is _RESPONSE_FORMAT
    payload = response_format.model_dump(by_alias=True, exclude_none=True)
    assert payload["type"] == "json_schema"
    assert payload["json_schema"]["name"] == "assignment_draft"
    assert payload["json_schema"]["strict"] is True
    schema = payload["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "title",
        "statement",
        "test_cases",
        "reference_solution",
    }


@pytest.mark.parametrize(
    "model",
    ["poolside/laguna-s-2.1:free", "poolside/laguna-xs-2.1:free"],
)
def test_poolside_models_omit_unsupported_response_format(model: str) -> None:
    client = _make_client(model=model)

    with patch.object(
        client._client.chat,
        "send",
        return_value=_mock_result(_VALID_LLM_RESPONSE),
    ) as send:
        client.generate(GenerationRequest(prompt="find max"), _ignore_step)

    assert send.call_args.kwargs["response_format"] is None


def test_invalid_structured_reply_becomes_generation_failed_error() -> None:
    client = _make_client()
    service = object.__new__(GenerationService)
    service._client = client

    with (
        patch.object(
            client._client.chat,
            "send",
            return_value=_mock_result('{"title": 7}'),
        ),
        pytest.raises(GenerationFailedError),
    ):
        service._call(GenerationRequest(prompt="find max"), _ignore_step)


def test_openrouter_client_citations_are_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()

    with patch.object(
        client._client.chat,
        "send",
        return_value=_mock_result(_VALID_LLM_RESPONSE),
    ):
        response = client.generate(GenerationRequest(prompt="find max"), _ignore_step)

    assert response.citations == []


def _chat_result_json(content: str) -> dict[str, object]:
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 1,
        "model": "test/model",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


def test_openrouter_client_retries_a_rate_limit_then_parses_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Free models return transient 429s; the SDK alone retries only 5XX."""
    monkeypatch.setattr(openrouter.utils.retries.time, "sleep", lambda _s: None)
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, json={"error": {"code": 429, "message": "x"}})
        return httpx.Response(200, json=_chat_result_json(_VALID_LLM_RESPONSE))

    client = _make_client(model=_STRUCTURED_MODEL)
    client._client.sdk_configuration.client = httpx.Client(
        transport=httpx.MockTransport(reply)
    )

    response = client.generate(GenerationRequest(prompt="find max"), _ignore_step)

    assert response.draft.title == "Find Maximum"
    assert len(requests) == 2
    assert requests[-1].url == "https://openrouter.ai/api/v1/chat/completions"
    body = json.loads(requests[-1].content)
    assert body["model"] == _STRUCTURED_MODEL
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "assignment_draft"
    assert "schema" in body["response_format"]["json_schema"]
    assert "schema_" not in body["response_format"]["json_schema"]


@pytest.mark.parametrize(
    "error",
    [
        OpenRouterError(
            "429 rate limited",
            httpx.Response(429, request=httpx.Request("POST", "https://x")),
        ),
        NoResponseError("429 rate limited"),
        httpx.ReadTimeout("429 rate limited"),
    ],
    ids=["openrouter-error", "no-response", "timeout"],
)
def test_openrouter_client_logs_and_reraises_api_failure(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """The service hides the cause, so the client must log it."""
    client = _make_client(model="some/model:free")

    with (
        patch.object(client._client.chat, "send", side_effect=error),
        caplog.at_level(logging.ERROR, logger="questly.ai.client"),
        pytest.raises(type(error)),
    ):
        client.generate(GenerationRequest(prompt="anything"), _ignore_step)

    assert "some/model:free" in caplog.text
    assert "429 rate limited" in caplog.text


def test_openrouter_client_bad_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM returning garbage → ValueError → GenerationService catches it."""
    client = _make_client()

    with (
        patch.object(
            client._client.chat,
            "send",
            return_value=_mock_result("oops not json"),
        ),
        pytest.raises(ValueError),
    ):
        client.generate(GenerationRequest(prompt="anything"), _ignore_step)


def _steps_of(
    client: OpenRouterGenerationClient, llm_reply: str
) -> list[GenerationStep]:
    steps: list[GenerationStep] = []
    with patch.object(
        client._client.chat, "send", return_value=_mock_result(llm_reply)
    ):
        client.generate(GenerationRequest(prompt="find max"), steps.append)
    return steps


def test_openrouter_client_reports_the_stages_it_really_runs() -> None:
    steps = _steps_of(_make_client(), _VALID_LLM_RESPONSE)

    assert steps == [
        GenerationStep.CALLING_MODEL,
        GenerationStep.VALIDATING,
        GenerationStep.RUNNING_SOLUTION,
    ]


def test_openrouter_client_skips_running_solution_when_there_is_none() -> None:
    steps = _steps_of(_make_client(), _NO_SOLUTION_LLM_RESPONSE)

    assert steps == [GenerationStep.CALLING_MODEL, GenerationStep.VALIDATING]


def test_a_step_callback_that_raises_stops_before_the_next_stage() -> None:
    class Stop(Exception):
        pass

    def stop_at_validating(step: GenerationStep) -> None:
        if step is GenerationStep.VALIDATING:
            raise Stop

    runner = FakeCodeRunner([_ok("5"), _ok("42")])
    client = _make_client(code_runner=runner)
    with (
        patch.object(
            client._client.chat, "send", return_value=_mock_result(_VALID_LLM_RESPONSE)
        ),
        pytest.raises(Stop),
    ):
        client.generate(GenerationRequest(prompt="find max"), stop_at_validating)

    assert runner.calls == []


def test_stub_client_reports_the_stages_it_runs() -> None:
    steps: list[GenerationStep] = []

    StubGenerationClient().generate(GenerationRequest(prompt="x"), steps.append)

    assert steps == [GenerationStep.CALLING_MODEL, GenerationStep.VALIDATING]


class FakeCitationRetriever:
    def __init__(self, citations: list[Citation]) -> None:
        self.citations = citations
        self.calls: list[GenerationRequest] = []

    def retrieve(self, request: GenerationRequest) -> list[Citation]:
        self.calls.append(request)
        return self.citations


def _citation(text: str) -> Citation:
    return Citation(chunk_id=12, source_id=7, page=2, score=0.9, text_snapshot=text)


def test_vector_retriever_passes_selected_ids_and_returns_citations() -> None:
    embedder = MagicMock()
    embedder.model = "test-embedding"
    embedder.embed.return_value = (1.0,) + (0.0,) * 767
    retrieval = MagicMock()
    retrieval.retrieve.return_value = RetrievalResult(
        matches=(ChunkSearchResult(12, 7, 2, "excerpt", 0.9),),
        difficulty_relaxed=False,
    )
    request = GenerationRequest(
        prompt="array",
        document_ids=[9, 2, 9],
        filters=GenerationFilters(topic="arrays", difficulty="easy"),
    )

    citations = VectorCitationRetriever(embedder, retrieval).retrieve(request)

    assert citations == [_citation("excerpt")]
    assert retrieval.retrieve.call_args.kwargs["filters"].core_document_ids == (2, 9)


def test_generation_uses_cited_sample_to_retry_wrong_solution() -> None:
    retriever = FakeCitationRetriever(
        [_citation("โจทย์เรื่องค่าสูงสุด\nSample Input:\n3\n1 5 2\nSample Output:\n5")]
    )
    runner = FakeCodeRunner([_ok("4"), _ok("5"), _ok("5"), _ok("42")])
    client = _make_client(
        model=_STRUCTURED_MODEL, code_runner=runner, retriever=retriever
    )
    steps: list[GenerationStep] = []
    with patch.object(
        client._client.chat,
        "send",
        side_effect=[
            _mock_result(_VALID_LLM_RESPONSE),
            _mock_result(_VALID_LLM_RESPONSE),
        ],
    ) as send:
        response = client.generate(
            GenerationRequest(prompt="find max", document_ids=[7]), steps.append
        )

    assert send.call_count == 2
    assert all(
        call.kwargs["response_format"] is _RESPONSE_FORMAT
        for call in send.call_args_list
    )
    assert [call[2] for call in runner.calls] == [
        "3\n1 5 2",
        "3\n1 5 2",
        "3\n1 5 2",
        "1\n42",
    ]
    assert response.citations == retriever.citations
    assert [case.expected_output for case in response.draft.test_cases] == ["5", "42"]
    assert steps == [
        GenerationStep.RETRIEVING,
        GenerationStep.CALLING_MODEL,
        GenerationStep.VALIDATING,
        GenerationStep.RUNNING_SOLUTION,
    ]


def test_generation_retries_near_copy_of_cited_chunk() -> None:
    excerpt = "โจทย์ให้อ่านจำนวนเต็มหลายค่าแล้วหาค่ามากที่สุดจากรายการที่ได้รับ " * 3
    copied = json.dumps(
        {
            "title": "Copy",
            "statement": excerpt,
            "test_cases": [
                {"input_data": "3\n1 5 2"},
                {"input_data": "1\n42"},
            ],
            "reference_solution": _SOLUTION,
        }
    )
    retriever = FakeCitationRetriever([_citation(excerpt)])
    client = _make_client(retriever=retriever)
    with patch.object(
        client._client.chat,
        "send",
        side_effect=[_mock_result(copied), _mock_result(_VALID_LLM_RESPONSE)],
    ) as send:
        response = client.generate(
            GenerationRequest(prompt="find max", document_ids=[7]), _ignore_step
        )

    assert send.call_count == 2
    assert response.draft.statement == "Given N integers, find the maximum."
    assert response.citations == retriever.citations


def test_generation_rejects_two_near_copies() -> None:
    excerpt = "คำอธิบายโจทย์ที่ยาวมากและมีรายละเอียดหลายประโยคเพื่อใช้เปรียบเทียบ " * 3
    copied = json.dumps(
        {
            "title": "Copy",
            "statement": excerpt,
            "test_cases": [{"input_data": "1"}, {"input_data": "2"}],
            "reference_solution": "print(input())",
        }
    )
    client = _make_client(retriever=FakeCitationRetriever([_citation(excerpt)]))
    with (
        patch.object(client._client.chat, "send", return_value=_mock_result(copied)),
        pytest.raises(ValueError, match="too similar"),
    ):
        client.generate(
            GenerationRequest(prompt="copy", document_ids=[7]), _ignore_step
        )


def test_generation_does_not_search_without_selected_documents() -> None:
    retriever = FakeCitationRetriever([_citation("private source")])
    client = _make_client(retriever=retriever)

    response = _generate(client, _VALID_LLM_RESPONSE)

    assert retriever.calls == []
    assert response.citations == []


def test_selected_documents_without_chunks_do_not_generate_ungrounded_draft() -> None:
    retriever = FakeCitationRetriever([])
    client = _make_client(retriever=retriever)

    with (
        patch.object(client._client.chat, "send") as send,
        pytest.raises(ValueError, match="no searchable chunks"),
    ):
        client.generate(
            GenerationRequest(prompt="find max", document_ids=[7]), _ignore_step
        )

    send.assert_not_called()


def test_thai_sample_headings_are_recognized() -> None:
    assert _sample_pairs("ตัวอย่างข้อมูลนำเข้า\n1 2\nตัวอย่างข้อมูลส่งออก\n3") == [("1 2", "3")]


def test_input_format_headings_are_not_mistaken_for_examples() -> None:
    assert _sample_pairs("Input:\nN integers\nOutput:\nmaximum integer") == []


def test_bundled_pdf_flattened_sample_is_checked_not_skipped() -> None:
    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures/ai/pith_0001.txt"
    ).read_text(encoding="utf-8")
    strategies = _flattened_sample_strategies(fixture)
    assert [("25\n25\n30", "A")] in strategies

    parsed = _parse_response(_VALID_LLM_RESPONSE)
    runner = FakeCodeRunner([_ok("wrong"), _ok("wrong"), _ok("A")])

    assert _grounding_failure(parsed, [_citation(fixture)], runner, _ignore_step) == ""
    assert [call[2] for call in runner.calls] == ["25", "25\n25", "25\n25\n30"]


def test_bundled_multirow_sample_table_ignores_page_number() -> None:
    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures/ai/pith_0004.txt"
    ).read_text(encoding="utf-8")

    assert [
        ("ABCDEFG", "All Capital Letter"),
        ("abcdefg", "All Small Letter"),
        ("AbCdEfG", "Mix"),
    ] in _flattened_sample_strategies(fixture)


def test_flattened_sample_with_wrong_solution_is_rejected() -> None:
    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures/ai/pith_0001.txt"
    ).read_text(encoding="utf-8")

    class WrongRunner:
        def run(
            self,
            *,
            code: str,
            language: str,
            stdin: str,
            time_limit_seconds: float,
        ) -> Execution:
            return _ok("wrong")

    assert "disagrees" in _grounding_failure(
        _parse_response(_VALID_LLM_RESPONSE),
        [_citation(fixture)],
        WrongRunner(),
        _ignore_step,
    )


def test_short_copied_statement_and_long_copied_title_are_detected() -> None:
    excerpt = "โจทย์ให้อ่านตัวเลขสามตัวและหาค่าสูงสุดจากข้อมูลที่ได้รับ"
    assert _too_similar(excerpt, f"รายละเอียดเพิ่มเติม {excerpt} และคำอธิบายอื่น")

    parsed = _parse_response(
        json.dumps(
            {
                "title": excerpt,
                "statement": "Different statement",
                "test_cases": [{"input_data": "1"}, {"input_data": "2"}],
                "reference_solution": "print(input())",
            }
        )
    )
    assert "too similar" in _grounding_failure(
        parsed, [_citation(excerpt)], FakeCodeRunner([]), _ignore_step
    )


_SAMPLE_EXCERPT = "โจทย์เรื่องค่าสูงสุด\nSample Input:\n3\n1 5 2\nSample Output:\n5"


def test_stub_runner_skips_sample_check_instead_of_failing_every_draft() -> None:
    runner = StubCodeRunner()
    client = _make_client(
        code_runner=runner,
        retriever=FakeCitationRetriever([_citation(_SAMPLE_EXCERPT)]),
    )
    with patch.object(
        client._client.chat, "send", return_value=_mock_result(_VALID_LLM_RESPONSE)
    ) as send:
        response = client.generate(
            GenerationRequest(prompt="find max", document_ids=[7]), _ignore_step
        )

    assert send.call_count == 1
    assert response.draft.title == "Find Maximum"
    assert [case.expected_output for case in response.draft.test_cases] == ["", ""]


def test_stub_runner_does_not_disable_the_copy_similarity_check() -> None:
    excerpt = "โจทย์ให้อ่านจำนวนเต็มหลายค่าแล้วหาค่ามากที่สุดจากรายการที่ได้รับ " * 3
    copied = json.dumps(
        {
            "title": "Copy",
            "statement": excerpt,
            "test_cases": [{"input_data": "1"}],
            "reference_solution": _SOLUTION,
        }
    )
    client = _make_client(
        code_runner=StubCodeRunner(),
        retriever=FakeCitationRetriever([_citation(excerpt + _SAMPLE_EXCERPT)]),
    )
    with (
        patch.object(client._client.chat, "send", return_value=_mock_result(copied)),
        pytest.raises(ValueError, match="too similar"),
    ):
        client.generate(
            GenerationRequest(prompt="copy", document_ids=[7]), _ignore_step
        )


def test_a_working_runner_that_runs_the_solution_wrong_still_fails_the_draft() -> None:
    runner = FakeCodeRunner([_failed(ExecutionStatus.RUNTIME_ERROR)] * 4)
    client = _make_client(
        code_runner=runner,
        retriever=FakeCitationRetriever([_citation(_SAMPLE_EXCERPT)]),
    )
    with (
        patch.object(
            client._client.chat, "send", return_value=_mock_result(_VALID_LLM_RESPONSE)
        ) as send,
        pytest.raises(ValueError, match="disagrees"),
    ):
        client.generate(
            GenerationRequest(prompt="find max", document_ids=[7]), _ignore_step
        )

    assert send.call_count == 2


def test_vector_retriever_filters_by_selected_documents_only() -> None:
    embedder = MagicMock()
    embedder.model = "test-embedding"
    embedder.embed.return_value = (1.0,) + (0.0,) * 767
    retrieval = MagicMock()
    retrieval.retrieve.return_value = RetrievalResult(
        matches=(ChunkSearchResult(12, 7, 2, "excerpt", 0.9),),
        difficulty_relaxed=False,
    )
    request = GenerationRequest(
        prompt="array",
        document_ids=[4],
        filters=GenerationFilters(topic="arrays", difficulty="easy"),
    )

    VectorCitationRetriever(embedder, retrieval).retrieve(request)

    sent = retrieval.retrieve.call_args.kwargs["filters"]
    assert (sent.topic, sent.difficulty, sent.core_document_ids) == (None, None, (4,))


class CountingRunner:
    """Runs every program to a fixed wrong answer and counts the runs."""

    def __init__(self) -> None:
        self.runs = 0

    def run(
        self, *, code: str, language: str, stdin: str, time_limit_seconds: float
    ) -> Execution:
        self.runs += 1
        return _ok("wrong")


def test_sample_runs_never_exceed_the_budget_and_the_rest_are_unverified() -> None:
    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures/ai/pith_0001.txt"
    ).read_text(encoding="utf-8")
    runner = CountingRunner()

    reason = _grounding_failure(
        _parse_response(_VALID_LLM_RESPONSE),
        [_citation(fixture)] * 5,
        runner,
        _ignore_step,
        _SampleRunBudget(remaining=3),
    )

    assert runner.runs == 3
    assert reason == ""


def test_generation_treats_samples_beyond_the_budget_as_unverified_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures/ai/pith_0001.txt"
    ).read_text(encoding="utf-8")
    monkeypatch.setattr(client_module, "_SAMPLE_RUN_BUDGET", 3)
    runner = CountingRunner()
    client = _make_client(
        code_runner=runner,
        retriever=FakeCitationRetriever([_citation(fixture)] * 5),
    )
    with patch.object(
        client._client.chat, "send", return_value=_mock_result(_VALID_LLM_RESPONSE)
    ) as send:
        response = client.generate(
            GenerationRequest(prompt="find max", document_ids=[7]), _ignore_step
        )

    test_case_runs = len(response.draft.test_cases)
    assert send.call_count == 1
    assert runner.runs == 3 + test_case_runs
