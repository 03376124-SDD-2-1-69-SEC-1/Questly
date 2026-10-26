---
status: accepted
date: 2026-10-17
task: OPS-26
---

# The generation models on A-01 come from a measured free-tier comparison

## Context

ADR-0009 made OpenRouter the only provider and left the model open. The
default has been `poolside/laguna-s-2.1:free` since then, chosen ad hoc, and
`GENERATION_MODEL_ALLOWLIST` still falls back to just that one model, so
A-01 has nothing else an Admin may switch to. OPS-25 (structured output
with `response_format`) also waits for this choice, because each model
supports it differently.

OpenRouter's free tier changes weekly. In October 2026 the account can see
sixteen `:free` models, several of which look plausible for Thai problem
drafts, so the pick was measured, not guessed.

## Method

`scripts/compare_generation_models.py` (this branch, rerunnable) does the
comparison. It reuses `OpenRouterGenerationClient`'s `_SYSTEM_PROMPT` and
`_parse_response`, so every draft has to survive exactly the parsing
production does, and every `expected_output` is computed the way the app
computes it (PR #46): the draft's `reference_solution` is executed locally
against each input the draft itself produced (5 s per case, the same
contract the CodeRunner fills in production).

Three seed statements from `scripts/seed/pith-100-manifest.csv` are the
inputs, all Thai with no extraction defects (the registry rejects Private-
Use-Area glyphs and broken ำ; PyMuPDF found none in these three):

- `0029` กบ (frog), loops, easy
- `1073` มด (mravi), simulation, easy
- `1083` ลงทุนซื้อหุ้น (stock), arrays, medium

### Candidates

Six `:free` models, picked from OpenRouter's current list as the cheap
families most likely to draft Thai: the incumbent
`poolside/laguna-s-2.1:free` and its sibling `laguna-xs-2.1:free`,
`google/gemma-4-31b-it:free`, `nvidia/nemotron-3-ultra-550b-a55b:free`,
`nvidia/nemotron-3-super-120b-a12b:free` and
`thinkingmachines/inkling:free`.

### Judge

The statement quality of each draft (thai_fluency, clarity_correctness,
originality_not_copied, usable, Thai note) is scored by an LLM judge
prompted with the team's reviewer-round rubric, **adapted from** it: the
example CSV row is dropped, en dashes are typed as hyphens, and long lines
are re-wrapped — the anchor sentences themselves are unchanged. The prompt
text lives in `scripts/compare_generation_models.py`
(`_JUDGE_RUBRIC_LINES`). The script now saves every judge reply under
`data/model-comp/<model>/judge/<task>.raw`; the run this ADR records
pre-dates that, so its judge output survives only through the
`judge_note` fields in `docs/adr/assets/0012-results.json`.
`google/gemini-3.8-flash` was tried first and the project key cannot pay
for it (Forbidden), likewise `thinkingmachines/inkling-small:free` and
`nvidia/nemotron-3-lightning:free` (Forbidden/BadRequest), so the judge on
this run was `google/gemma-4-26b-a4b-it:free`. It is a sibling of the
candidate `gemma-4-31b` — a residual bias to keep in mind; both were smoke
tested for parse compliance before the run.

Each (candidate × task) raw reply is kept on this machine under
`data/model-comp/` (git-ignored), one `.raw` file per reply, and judge
rows land in `results.json` next to them. The machine-readable copy of
that `results.json` — the run's own output, numbers unedited — is checked
into the repo at `docs/adr/assets/0012-results.json`; the printed table
below is a reading of it.

## Result

One row per (model × task); the summary columns come from
`docs/adr/assets/0012-results.json` after the run of 2026-10-17:

| model | parse | ran (exit 0) | thai | clarity | originality | usable |
|---|---|---|---|---|---|---|
| poolside/laguna-s-2.1:free | 3/3 | 10/10 | 3.0 (empty once) | 4.0 | 2.67 | 0/3 |
| poolside/laguna-xs-2.1:free | 2/3 | 5/5 | 2.0 | 3.0 | 1.5 | 0/3 |
| google/gemma-4-31b-it:free | 0/3 | — | — | — | — | 0/3 (gateway down) |
| nvidia/nemotron-3-ultra-550b-a55b:free | 1/3 | 5/5 | 3.0 | 3.0 | 1.0 | 0/3 (2 calls dead) |
| nvidia/nemotron-3-super-120b-a12b:free | 2/3 | 3/7 | 5.0 | 3.5 | 1.0 | 0/3 |
| thinkingmachines/inkling:free | 0/3 | — | — | — | — | 0/3 (key forbidden) |

"Ran" means the draft's `reference_solution` exited 0 within the 5 s
timeout on each of its own inputs. In line with PR #46 the expected
output is whatever that run printed, so this column says nothing about
whether the solution answers the source problem correctly. Keep two
readings of it apart: laguna-s's `10/10` is its `0029` draft
(5/5) plus its `1073` (2/2) and `1083` (3/3) drafts, while
nemotron-3-super's `3/7` is its `1073` draft running on all three of its
inputs against a `1083` draft whose solution exited non-zero on all four
of its own inputs — those four `expected_output`s would reach the
Instructor blank, not wrong answers. No output checking exists in this
comparison, and that limitation was not papered over.

What the per-task rows add, quoting the judge's Thai notes:

- **laguna-s** (the incumbent) is the only candidate that parsed all three
  and whose reference solution exited 0 on all ten of its own inputs.
  Its weaknesses: on `1073` it answered in English (thai_fluency empty),
  and the `1083` draft left a stray Japanese fragment «すべて» — a failure
  mode the draft stage cannot catch and only the statement review round
  would.
- **laguna-xs** drafts Thai full of wrong-letter typos ("ด้วนช้า"
  instead of "ด้วยช้า", "เต็นบรรทัด"), and its `1083` reply was broken
  JSON the production parser rejects.
- **nemotron-3-super** writes the most natural Thai of the set (thai 5.0
  on both drafts it completed) but recopies the source statement almost
  word for word (originality 1.0), and its `1083` solution exited 0 on 0
  of 4 of its own test inputs — the expected outputs would all be blanks
  for the Instructor to fill at T-04. It also broke JSON on `0029`.
- **nemotron-3-ultra** replied to one call in three; the other two died
  as `ResponseValidationError` before any draft existed.
- **gemma-4-31b** hit gateway errors on all three calls during this run,
  so it remains unmeasured rather than measured-bad.
- **inkling** is forbidden under this project key.

(No draft was judged `usable`; the rubric is deliberately strict — every
candidate failed either on missing examples, on leftover fragments, or on
recopying the source. For Questly's own flow this is less damaging than
it reads: drafting from a source statement that stays close on the
scenario is the normal case, which is why originality is reported but not
driving this decision.)

## Decision

- `GENERATION_MODEL` / `DEFAULT_GENERATION_MODEL` stays
  **`poolside/laguna-s-2.1:free`** — the only candidate that parsed every
  reply (3/3) and whose solutions exited 0 on all ten of its own inputs,
  and production-proven since OPS-23.
- `GENERATION_MODEL_ALLOWLIST` / `DEFAULT_GENERATION_ALLOWLIST` becomes
  the three measured models an Admin may pick on A-01:
  `poolside/laguna-s-2.1:free`, `poolside/laguna-xs-2.1:free`,
  `nvidia/nemotron-3-super-120b-a12b:free`. Unset `GENERATION_MODEL`
  plus an unset allowlist resolves to that triple; a custom
  `GENERATION_MODEL` outside it stands alone as before.
- Excluded on evidence, not taste: `nemotron-3-ultra` (two-thirds of its
  calls die), `gemma-4-31b` (gateway unreachable from this account —
  re-measure if wanted), `inkling` (forbidden for this key), everything
  paid (ADR-0009 keeps generation).
- No schema change and no migration: the runtime model is
  `core.ai_settings.model` — a database row, read in `main.py` when the
  composition root builds `OpenRouterGenerationClient`
  (`model=lambda: admin_repository.get_ai_settings().model`). The Admin
  switches it on A-01 and the next generation call picks the new value
  up. `Settings` governs only what A-01 may switch to: the allowlist —
  and `DEFAULT_GENERATION_MODEL` feeds that allowlist fallback (plus the
  demo seed), never the runtime model by itself.

## Open question: allowlist membership

The Decision above ships all three measured candidates, but the evidence
only supports the first one. Against each addition:

- `poolside/laguna-xs-2.1:free` — parsed 2 of 3 replies (broken JSON on
  `1083`), thai_fluency 2.0 with wrong-letter typos ("ด้วนช้า" instead of
  "ด้วยช้า", "เต็นบรรทัด"), and one of the weakest originality scores
  (1.5). It also shares the `poolside` provider with laguna-s, so the two
  together buy no outage redundancy: one provider-side incident takes
  both off the air at once.
- `nvidia/nemotron-3-super-120b-a12b:free` — parsed 2 of 3 (broken JSON
  on `0029`), and its solutions exited 0 on 3 of 7 attempts; its `1083`
  draft's solution never ran, leaving all four of that draft's
  `expected_output`s blank. Its Thai wording is the most natural of the
  set (thai 5.0), but the wording itself is a near-verbatim recopy of the
  source (originality 1.0).

The options the team may pick from when this ADR is revised:

1. Keep all three — what this ADR records, on the argument that nothing
   beats the runtime default anywhere and the other two live behind the
   allowlist as documented trade-offs.
2. Keep `laguna-s` + `nemotron-3-super` — drops the weakest measured
   sibling and leaves one model per provider family.
3. Keep `laguna-s` only — matches what the numbers alone support, at the
   cost of A-01 having exactly one legible option until the next
   measurement.

Nothing in the repo enforces one of these; whichever way it goes is one
line in `config.py`'s `DEFAULT_GENERATION_ALLOWLIST`, the value in
`.env.example`, and the same numbers re-quoted from
`docs/adr/assets/0012-results.json`.

## Consequences

- The judge has never been calibrated against the team's own human
  scores: two reviewers independently scored `pith_0001`, `pith_0030` and
  `pith_1087` from this same seed, and disagreed widely. The rubric this
  ADR's numbers come from has not been compared with either reviewer, so
  the statement columns (thai/clarity/originality/usable) carry the
  judge's view alone. It is also a sibling of a candidate
  (`gemma-4-26b` vs `gemma-4-31b`). And `usable` reads 0 yes out of 3 for
  every model — not for a single reason: of the eight judged rows
  carrying a `no`, six fell under the rubric's mechanical rule (some
  score of 2 or lower — originality 2 or lower in all six), while two said `no`
  with every score at 3 or higher (laguna-s's `1073` reply was English
  and lacked the input/output format, and its `1083` reply carried the
  stray Japanese fragment «すべて» alongside missing examples). A
  calibration run on the human-scored
  rows (same rubric, those three problems, both verdicts side by side)
  is the follow-up task before the statement columns are treated as
  ground truth; this ADR does not implement it.
- OPS-25 can start: structured-output support is verified per model in
  its tests, and the allowlist it must deal with is exactly these three
  `:free` slugs.
- The free tier changes weekly; this comparison is a snapshot, and
  OpenRouter may retire any of these slugs. Re-run
  `scripts/compare_generation_models.py` before touching the allowlist
  again, and expect a re-run to re-judge the same three seed statements.
- `nemotron-3-super` on the allowlist means an Instructor can pick a
  model whose `expected_output` fills blank on hard problems; T-04
  already treats blanks as "fill it yourself", so the failure is visible
  in the UI, not silent.
- The judge picked (`gemma-4-26b`) shares a family with one candidate;
  a re-run should try upgrading the judge to a paid model first (account
  credits permitting), then fall back to this one so scores stay
  comparable to this ADR.
- `.env.example` ships the allowlist as a real value now, not empty, so
  a fresh environment gets the measured set without reading this ADR.
