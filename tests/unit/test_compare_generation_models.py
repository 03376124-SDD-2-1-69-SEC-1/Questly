"""run_case sandboxes model-written code: no secrets in, no repo cwd.

The child runs `python -I` with a PATH-only env and a fresh temporary cwd;
these tests prove both guarantees without touching the network. They
monkeypatch the real environment's OPENROUTER_API_KEY to make sure the
--env-file-loaded key never reaches the model's stdout. Later tests cover
the judge path: empty `choices` replies and the 1-5 score validation.
"""

import csv
import io
from types import SimpleNamespace

import pytest
import scripts.compare_generation_models as comparison
from scripts.compare_generation_models import (
    ROOT,
    parse_judge_row,
    run_case,
)


def _probe(exits_on_secret: str = "3", exits_on_repo: str = "4") -> str:
    """Code that exits 0 only when both sandbox guarantees hold."""
    return "\n".join(
        (
            "import os, sys",
            f"repo = {str(ROOT)!r}",
            f"if os.environ.get('OPENROUTER_API_KEY'): sys.exit({exits_on_secret})",
            f"if os.getcwd() == repo: sys.exit({exits_on_repo})",
            "sys.exit(0)",
        )
    )


def test_child_process_cannot_see_the_api_key_and_cannot_run_in_the_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent exposes the key and sits in the repo; the child still exits 0."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "loaded-by---env-file")
    assert run_case(_probe(), stdin="")


def test_exit_code_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """True on exit 0, False on any other exit or on the timeout."""
    monkeypatch.setattr(comparison, "SOLUTION_RUN_TIMEOUT", 2)
    assert run_case("import sys; sys.exit(0)", stdin="")
    assert not run_case("import sys; sys.exit(2)", stdin="")
    # the 5 s production default is patched away, so this one is the timeout
    assert not run_case("while True: pass", stdin="")


def test_child_env_is_path_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second secret beyond the API key never reaches the child either."""
    monkeypatch.setenv("QUESTLY_REVIEW_SECRET", "also-not-for-the-child")
    probe = (
        "import os, sys\n"
        "if os.environ.get('QUESTLY_REVIEW_SECRET'): sys.exit(3)\n"
        "sys.exit(0)\n"
    )
    assert run_case(probe, stdin="")


# ---------------------------------------------------------------------------
# Replies with an empty choices list guard every [0] access (no network)
# ---------------------------------------------------------------------------


def _reply_with_choices(choices: list) -> SimpleNamespace:
    return SimpleNamespace(choices=choices)


class _FakeJudgeClient:
    """chat.send() returning the given reply, and nothing else."""

    def __init__(self, reply: SimpleNamespace) -> None:
        self.chat = SimpleNamespace(send=lambda model, messages: reply)


def test_reply_raw_is_text_of_the_first_choice() -> None:
    message = SimpleNamespace(content="hello")
    reply = _reply_with_choices([SimpleNamespace(message=message)])

    assert comparison._reply_raw(reply) == "hello"


def test_reply_raw_is_none_on_empty_choices() -> None:
    assert comparison._reply_raw(_reply_with_choices([])) is None


def test_judge_statement_records_empty_choices_without_raising(
    tmp_path: object,
) -> None:
    """choices=[] lands in judge_error, not an IndexError; no raw saved."""
    score = comparison._Score("fake/model", "0000")
    comparison.judge_statement(
        client=_FakeJudgeClient(_reply_with_choices([])),
        judge_model="fake/judge",
        problem_id="pith_0000",
        label="A",
        source="แหล่งโจทย์",
        candidate="โจทย์ที่ตัดสิน",
        score=score,
        raw_path=tmp_path / "0000.raw",
    )

    assert score.judge_error == "empty choices"
    assert score.usable == ""
    assert not (tmp_path / "0000.raw").exists()


# ---------------------------------------------------------------------------
# parse_judge_row validates every non-empty score as an integer 1-5
# ---------------------------------------------------------------------------


def _judge_raw(
    thai: str = "4",
    clarity: str = "4",
    originality: str = "3",
    usable: str = "yes",
) -> str:
    buffer = io.StringIO()
    csv.writer(buffer).writerow(
        ["llm", "pith_0000", "A", thai, clarity, originality, usable, "โน้ต"]
    )
    return buffer.getvalue()


def test_parse_judge_row_accepts_in_range_scores() -> None:
    assert parse_judge_row(_judge_raw("5", "3", "4")) == (5.0, 3.0, 4.0, "yes", "โน้ต")


def test_parse_judge_row_empty_score_stays_none() -> None:
    thai, clarity, originality, usable, _ = parse_judge_row(_judge_raw("", "3", "4"))

    assert (thai, clarity, originality, usable) == (None, 3.0, 4.0, "yes")


@pytest.mark.parametrize("bad", ["0", "6", "3.5", "N/A"])
def test_parse_judge_row_rejects_out_of_range_and_non_integer_scores(
    bad: str,
) -> None:
    with pytest.raises(ValueError, match="integer 1-5|not a number"):
        parse_judge_row(_judge_raw(bad))
