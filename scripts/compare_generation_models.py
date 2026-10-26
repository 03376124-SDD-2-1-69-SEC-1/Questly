"""OPS-26: เทียบโมเดล generation บน OpenRouter กับโจทย์ seed ภาษาไทย 3 ข้อ

สองขั้น ใช้ system prompt และตัว parse ตัวเดียวกับ `OpenRouterGenerationClient`
(`questly.ai.client`) เพื่อให้ผลเทียบสะท้อนสิ่งที่ production เจอจริง:

1. สร้าง draft — โมเดลผู้สมัครเขียนโจทย์จาก statement ใน PDF ของ seed ต้อง
   parse เป็น draft ได้ และ reference solution ต้องรันผ่านทุกเคสที่มันแต่ง
   ขึ้นเอง (python3 ในเครื่อง, timeout 5 วินาที/เคส — เทียบเท่าสิ่งที่
   CodeRunner ทำกับ expected_output ใน production ตาม PR #46)
2. ตัดสิน statement — โมเดลตัวตัดสิน (JUDGE_MODEL ไม่อยู่ในรายการผู้สมัคร)
   ให้คะแนนตาม rubric: thai_fluency / clarity_correctness /
   originality_not_copied / usable + note ภาษาไทย

ผลลัพธ์:
- draft ดิบของแต่ละ (โมเดล × โจทย์) เก็บที่ data/model-comp/<model>/<task>.raw
  และ reply ดิบของตัวตัดสินที่ data/model-comp/<model>/judge/<task>.raw
- คะแนนรายเกม + คะแนนตัดสิน รวมที่ data/model-comp/results.json เขียนใหม่
  หลังทุก (โมเดล × โจทย์) เพื่อให้รันค้างเก็บแถวที่เสร็จแล้วไว้ (data/
  อยู่นอก git — สำเนาผลรันใน repo อยู่ที่ docs/adr/assets/0012-results.json
  ผูกไว้ใน docs/adr/0012-generation-model-selection.md)
- พิมพ์ตารางสรุปต่อโมเดลท้ายรัน

รัน (จาก root ของ repo):
    uv run --env-file .env python scripts/compare_generation_models.py

อ่าน OPENROUTER_API_KEY จาก env (`--env-file .env`), sleep ระหว่าง call
กัน rate limit ของแพ็กเกจ free, retry จัดการ 429 ที่เหลือ

ตัวเลือก:
    --models "a/b,c/d"    รายการโมเดลผู้สมัคร (คั่น comma)
    --tasks "0029,1073"   task_id จาก pith-100-manifest.csv
    --judge "g/..."       โมเดลตัวตัดสิน (default JUDGE_MODEL)
    --no-judge            ข้ามขั้นตัดสิน
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pymupdf
from openrouter import OpenRouter
from openrouter.errors import NoResponseError, OpenRouterError
from openrouter.utils.retries import BackoffStrategy, RetryConfig

from questly.ai.client import _SYSTEM_PROMPT, _parse_response

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "seed" / "pith-100-manifest.csv"
CACHE_DIR = ROOT / "data" / "pith"
OUT_DIR = ROOT / "data" / "model-comp"

TIMEOUT_MS = 120_000
RETRY = RetryConfig(
    "backoff",
    BackoffStrategy(
        initial_interval=1_000,
        max_interval=10_000,
        exponent=2,
        max_elapsed_time=30_000,
    ),
    retry_connection_errors=True,
    status_codes_override=["429", "5XX"],
)
# ให้แพ็กเกจ free คลาย rate limit ระหว่าง call
SLEEP_BETWEEN_CALLS = 5.0
SLEEP_BETWEEN_JUDGE_CALLS = 2.0
SOLUTION_RUN_TIMEOUT = 5.0

# โมเดลผู้สมัคร — ตัวแรกคือ default เดิม (baseline)
DEFAULT_MODELS = (
    "poolside/laguna-s-2.1:free",
    "poolside/laguna-xs-2.1:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "thinkingmachines/inkling:free",
)
DEFAULT_TASKS = ("0029", "1073", "1083")
# ตัวตัดสินต้องไม่เป็นโมเดลผู้สมัคร เพื่อไม่ให้ตัดสินตัวเอง
# (gemma-4-26b อยู่ในตระกูลเดียวกับ gemma-4-31b ที่เป็นผู้สมัคร — เบี่ยงเบนระดับ
# sibling บันทึกไว้ใน docs/adr/0012-generation-model-selection.md โมเดลอื่นที่
# ลองแล้ว: api key free ของโครงการเรียกจ่ายหรือ inkling-small/nemotron-lightning
# ไม่ได้ Forbidden/BadRequest)
JUDGE_MODEL = "google/gemma-4-26b-a4b-it:free"

# The team's reviewer-round rubric, adapted (ADR-0012 §Judge): the example CSV
# row is dropped, en dashes become hyphens, and long lines are re-wrapped —
# the anchor sentences themselves are unchanged. Assembled from a line list
# because long prompt lines would trip E501 inside a string literal.
_JUDGE_RUBRIC_LINES = (
    "You are an expert reviewer of competitive-programming problems written in Thai.",
    'You will evaluate ONE AI-generated problem statement (the "candidate")',
    "against its original source problem, and score it on a fixed rubric.",
    "",
    "## Inputs",
    "- problem_id: @@PROBLEM_ID@@",
    "- label: @@LABEL@@",
    "- source_problem: @@SOURCE_PROBLEM@@",
    "- candidate: @@CANDIDATE@@",
    "",
    "Evaluate the candidate on its own merits. Do not compare it with other variants.",
    "",
    "## Rubric (integer 1-5 for each score)",
    "",
    "### 1. thai_fluency - How natural and readable is the Thai?",
    "- 5: Natural, fluent Thai. Standard English technical terms "
    "(array, string, input) are fine.",
    "- 4: Minor awkward phrasing, OR a small leftover fragment in another script "
    "(e.g., a few Chinese characters) that does not block understanding.",
    "- 3: Noticeable awkwardness, missing words, or broken sentences "
    "that force the reader to re-read.",
    "- 2: Mixed languages (Thai + Chinese/other) or run-on text "
    "that makes parts hard to understand.",
    "- 1: Largely unreadable; heavy language mixing throughout.",
    "- Leave EMPTY if the candidate is written entirely in English "
    "(fluency in Thai does not apply).",
    "",
    "### 2. clarity_correctness - Is the problem complete, unambiguous, and correct?",
    "Check: task statement, input format, output format, constraints, and examples "
    "are all present and consistent with each other. Every variable and index "
    "is defined once and used consistently (e.g., if v_ij is used, it must be "
    "clear what i and j refer to).",
    "- 5: Complete, unambiguous, examples match the statement. "
    "A student could solve it without asking questions.",
    "- 4: One minor gap or inconsistency that a careful reader can resolve.",
    "- 3: An ambiguity a student would need to ask about "
    "(e.g., unclear variable meaning, missing constraint).",
    "- 2: Multiple ambiguities, or an example that contradicts the statement.",
    "- 1: Unsolvable as written, or the core task is wrong.",
    "",
    "### 3. originality_not_copied - How far is the candidate from a copy",
    "of the source?",
    "- 5: New scenario and new wording, while testing the same underlying concept.",
    "- 4: Same scenario, but substantially rewritten in its own words.",
    "- 3: Same scenario and structure; partially reworded.",
    "- 2: Mostly the source's wording with light edits.",
    "- 1: Near-verbatim copy or direct sentence-by-sentence translation of the source.",
    "",
    "### 4. usable - Could an instructor publish this with only light edits?",
    '- "yes" only if ALL of the following hold:',
    "  - every score is 3 or higher (ignore an empty thai_fluency),",
    "  - no fragments in scripts other than Thai or English remain,",
    "  - no ambiguity that requires rewriting a sentence.",
    '- Otherwise "no".',
    "",
    "### 5. note",
    '- Required when usable = "no" or any score is 3 or lower. Optional otherwise.',
    "- Write in Thai, 1-2 sentences, naming the specific problem and where it appears.",
    '- No generic comments like "ควรปรับปรุง".',
    "",
    "## Procedure",
    "1. Read the source problem, then the candidate.",
    "2. Identify concrete issues (language, ambiguity, copying) "
    "before assigning any score.",
    "3. Assign each score using the anchors above; when between two levels, "
    "choose the lower one.",
    "4. Apply the usable rule mechanically.",
    "",
    "## Output",
    "Return exactly one CSV row and nothing else, in this column order, "
    "with every field quoted:",
    '"scorer","problem","label","thai_fluency","clarity_correctness",'
    '"originality_not_copied","usable","note"',
    'Use "llm" as the scorer.',
)

JUDGE_RUBRIC_TEMPLATE = "\n".join(_JUDGE_RUBRIC_LINES)

DRAFT = "draft"
JUDGE = "judge"


class _Score:
    """One (model, task) scorecard, draft stage and judge stage."""

    def __init__(self, model: str, task_id: str) -> None:
        self.model = model
        self.task_id = task_id
        self.elapsed_seconds = 0.0
        self.parse_ok = False
        self.test_cases = 0
        self.runs_passed = 0
        self.runs_total = 0
        self.error = ""
        # judge stage
        self.thai_fluency: float | None = None
        self.clarity: float | None = None
        self.originality: float | None = None
        self.usable = ""
        self.note = ""
        self.judge_error = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "task_id": self.task_id,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "parse_ok": self.parse_ok,
            "test_cases": self.test_cases,
            "runs_passed": self.runs_passed,
            "runs_total": self.runs_total,
            "error": self.error,
            "judge_thai_fluency": self.thai_fluency,
            "judge_clarity_correctness": self.clarity,
            "judge_originality_not_copied": self.originality,
            "judge_usable": self.usable,
            "judge_note": self.note,
            "judge_error": self.judge_error,
        }


def load_statement(task_id: str) -> str:
    """Statement text from the cached PDF, plus the manifest's difficulty."""
    row = next(
        r
        for r in csv.DictReader(MANIFEST.open(encoding="utf-8-sig"))
        if r["task_id"] == task_id
    )
    pdf = CACHE_DIR / f"{task_id}.pdf"
    document = pymupdf.open(pdf)
    text = "\n".join(page.get_text() for page in document)
    document.close()
    if not text.strip():
        sys.exit(f"PDF {pdf.name} extracted no text")
    return f"{text}\n\nDifficulty level: {row['difficulty']}"


def run_case(code: str, stdin: str) -> bool:
    """True when the model's solution exits 0 in time on one input.

    The child is sandboxed: the model wrote the code, and it must not get
    anything worth leaking. `python -I` leaves the import system and user
    site-packages alone, the cwd is a fresh temporary directory (so no
    repo file is in reach), and the env carries only PATH — every secret
    the parent had, including OPENROUTER_API_KEY, stays home.
    """
    try:
        with tempfile.TemporaryDirectory() as scratch:
            process = subprocess.run(
                [sys.executable, "-I", "-c", code],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=SOLUTION_RUN_TIMEOUT,
                cwd=scratch,
                env={"PATH": os.environ.get("PATH", "")},
            )
    except subprocess.TimeoutExpired:
        return False
    return process.returncode == 0


def evaluate_draft(raw: str, elapsed: float, score: _Score) -> str:
    """Draft-stage checks; returns the draft statement, "" when unusable."""
    score.elapsed_seconds = elapsed
    try:
        parsed = _parse_response(raw)
    except ValueError as error:
        score.error = str(error)[:300].replace("\n", " ")
        return ""
    draft = parsed.response.draft
    score.parse_ok = True
    score.test_cases = len(draft.test_cases)
    if not parsed.reference_solution:
        score.error = "no reference_solution in reply"
        return draft.statement
    score.runs_total = len(draft.test_cases)
    score.runs_passed = sum(
        run_case(parsed.reference_solution, case.input_data)
        for case in draft.test_cases
    )
    return draft.statement


def parse_judge_row(
    raw: str,
) -> tuple[
    float | None,
    float | None,
    float | None,
    str,
    str,
]:
    """(thai, clarity, originality, usable, note) from the judge's CSV row."""
    rows = list(csv.reader(io.StringIO(raw.strip())))
    fields = next((f for f in rows if len(f) >= 8 and f[0].strip() == "llm"), None)
    if fields is None:
        raise ValueError("no CSV row from llm scorer")
    usable = fields[6].strip().lower()
    if usable not in ("yes", "no"):
        raise ValueError(f"usable is not yes/no: {usable!r}")

    def _star(value: str) -> float | None:
        """The judge's score, None when empty, error when not an integer 1-5."""
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError as error:
            raise ValueError(f"score is not a number: {value!r}") from error
        if number < 1 or number > 5 or number != int(number):
            raise ValueError(f"score must be an integer 1-5: {value!r}")
        return number

    return (
        _star(fields[3]),
        _star(fields[4]),
        _star(fields[5]),
        usable,
        fields[7].strip(),
    )


def _reply_raw(reply: object) -> str | None:
    """The reply's text, or None when OpenRouter returned no choices.

    An empty choices list happens on top-level errors the SDK turns into
    200s; treating it as text would crash later, so both call sites guard
    with this helper instead of indexing [0] blind.
    """
    if not reply.choices:
        return None
    content = reply.choices[0].message.content
    return content if isinstance(content, str) else ""


def judge_statement(
    client: OpenRouter,
    judge_model: str,
    problem_id: str,
    label: str,
    source: str,
    candidate: str,
    score: _Score,
    raw_path: Path,
) -> None:
    """Record the judge's verdict on one draft statement.

    Every reply the judge sends is written to raw_path before parsing, so
    the evidence survives even when the reply is malformed or both of the
    two attempts fail.
    """
    message = (
        JUDGE_RUBRIC_TEMPLATE.replace("@@PROBLEM_ID@@", problem_id)
        .replace("@@LABEL@@", label)
        .replace("@@SOURCE_PROBLEM@@", source)
        .replace("@@CANDIDATE@@", candidate)
    )
    for attempt in (1, 2):
        try:
            reply = client.chat.send(
                model=judge_model, messages=[{"role": "user", "content": message}]
            )
        except (OpenRouterError, NoResponseError) as error:
            score.judge_error = f"judge call failed: {type(error).__name__}"
            return
        raw = _reply_raw(reply)
        if raw is None:
            score.judge_error = "empty choices"
            return
        raw_path.write_text(raw, encoding="utf-8")
        try:
            thai, clarity, originality, usable, note = parse_judge_row(raw)
        except ValueError as error:
            if attempt == 2:
                score.judge_error = str(error)
                return
            continue  # one retry on a malformed reply
        score.thai_fluency = thai
        score.clarity = clarity
        score.originality = originality
        score.usable = usable
        score.note = note
        return


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def summarise(model: str, scores: list[_Score]) -> dict[str, object]:
    """Per-model row for the closing table and the doc."""
    parse_ok = sum(s.parse_ok for s in scores)
    totals = []
    ran = []
    for s in scores:
        totals.append(s.runs_total)
        ran.append(s.runs_passed)
    thai = [s.thai_fluency for s in scores if s.thai_fluency is not None]
    clarity = [s.clarity for s in scores if s.clarity is not None]
    originality = [s.originality for s in scores if s.originality is not None]
    usable = sum(1 for s in scores if s.usable == "yes")
    return {
        "model": model,
        "parse_ok": f"{parse_ok}/{len(scores)}",
        "runs_passed": f"{sum(ran)}/{sum(totals)}",
        "judge_thai_fluency": _mean(thai),
        "judge_clarity_correctness": _mean(clarity),
        "judge_originality_not_copied": _mean(originality),
        "judge_usable": f"{usable}/{len(scores)}",
    }


def _for_table(value: object) -> str:
    """The table cell for a summary value, "—" when the column is empty."""
    return "—" if value is None else str(value)


def write_results(
    models: tuple[str, ...], scores: list[_Score]
) -> tuple[dict[str, object], ...]:
    """Results so far, after every (model, task) row — a crash keeps the done rows.

    Returns the summaries it built, so the closing table prints the same
    numbers without computing them twice.
    """
    summaries = tuple(summarise(m, [s for s in scores if s.model == m]) for m in models)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(
        json.dumps(
            {"rows": [s.as_dict() for s in scores], "summary": summaries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--tasks", default=",".join(DEFAULT_TASKS))
    parser.add_argument("--judge", default=JUDGE_MODEL)
    parser.add_argument("--no-judge", action="store_true")
    arguments = parser.parse_args()
    models = tuple(m.strip() for m in arguments.models.split(",") if m.strip())
    task_ids = tuple(t.strip() for t in arguments.tasks.split(",") if t.strip())

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set (ลืม --env-file .env หรือเปล่า)")

    statements = {task_id: load_statement(task_id) for task_id in task_ids}
    client = OpenRouter(api_key=api_key, timeout_ms=TIMEOUT_MS, retry_config=RETRY)
    scores: list[_Score] = []
    summaries: tuple[dict[str, object], ...] = ()

    for index, model in enumerate(models):
        print(f"\n=== {model} ===")
        model_dir = OUT_DIR / model.replace("/", "__")
        model_dir.mkdir(parents=True, exist_ok=True)
        for task_id in task_ids:
            score = _Score(model, task_id)
            scores.append(score)
            started = time.monotonic()
            try:
                reply = client.chat.send(
                    model=model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": statements[task_id]},
                    ],
                )
            except (OpenRouterError, NoResponseError) as error:
                score.error = f"call failed: {type(error).__name__}"
                print(f"  {task_id}: CALL FAILED {type(error).__name__}")
            else:
                raw = _reply_raw(reply)
                if raw is None:
                    score.error = "empty choices"
                    print(f"  {task_id}: EMPTY CHOICES")
                else:
                    (model_dir / f"{task_id}.raw").write_text(raw, encoding="utf-8")
                    statement = evaluate_draft(raw, time.monotonic() - started, score)
                    print(
                        f"  {task_id}: parse={'Y' if score.parse_ok else 'N'} "
                        f"cases={score.test_cases} "
                        f"ran={score.runs_passed}/{score.runs_total} "
                        f"{round(score.elapsed_seconds, 1)}s"
                    )
                    if statement and not arguments.no_judge:
                        score_dir = model_dir / "judge"
                        score_dir.mkdir(exist_ok=True)
                        problem_id = f"pith_{task_id}"
                        label = f"var-{index + 1:02d}"
                        judge_statement(
                            client=client,
                            judge_model=arguments.judge,
                            problem_id=problem_id,
                            label=label,
                            source=statements[task_id],
                            candidate=statement,
                            score=score,
                            raw_path=score_dir / f"{task_id}.raw",
                        )
                        verdict = score.usable or score.judge_error
                        print(
                            f"    judge({arguments.judge}): thai={score.thai_fluency} "
                            f"clarity={score.clarity} originality={score.originality} "
                            f"usable={score.usable or '-'} {verdict or 'no reply'}"
                        )
                        time.sleep(SLEEP_BETWEEN_JUDGE_CALLS)
            time.sleep(SLEEP_BETWEEN_CALLS)
            summaries = write_results(models, scores)

    header = ("model", "parse", "ran", "thai", "clarity", "originality", "usable")
    print("\n" + " | ".join(header))
    for model_score in summaries:
        print(
            f"{model_score['model']} | {model_score['parse_ok']} | "
            f"{model_score['runs_passed']} | "
            f"{_for_table(model_score['judge_thai_fluency'])} | "
            f"{_for_table(model_score['judge_clarity_correctness'])} | "
            f"{_for_table(model_score['judge_originality_not_copied'])} | "
            f"{model_score['judge_usable']}"
        )
    print(f"\nresults -> {OUT_DIR / 'results.json'}")


if __name__ == "__main__":
    main()
