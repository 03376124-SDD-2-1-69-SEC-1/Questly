---
status: accepted
date: 2026-10-01
task: OPS-15
---

# OPS-15 schema: where it departs from ADR-0007

ADR-0007 "Schema changes for the Phase 3 OPS task" is the list OPS-15
implements as one hand-written migration. Each change below departs from that
list or adds something it did not cover, because the ports and fakes already
built on top of ADR-0007 need a shape the list did not foresee. Every other
item in that list is applied as written. Neon dev held 0 rows in every
`core` and `rag` table when this was decided (read-only check, 2026-10-01), so
no NOT NULL or backfill below risks existing data.

## Drafts and the daily Quota get their own tables

ADR-0007 has no `drafts` table and counts the Quota "from successful
requests". The ports built since (CORE-15) disagree:
`DraftRepository.record_generation(user_id, at)` and
`count_generations(user_id, since)` take no Classroom, and a successful
"Regenerate this part" costs one generation without creating a request or a
Draft. So:

- new `drafts`: one row per `Draft` (classroom, requested_by, prompt,
  content, citations, settings, target classrooms, document ids, status,
  resulting assignment, generated_at). Content, citations and settings are
  JSONB, because the Draft is edited and read whole by the T-04 stepper and
  never queried by field.
- new `generation_events` (user, occurred_at; indexed on both): one row per
  successful generation or regeneration. The Quota is a count of these since
  local midnight Asia/Bangkok.

`generation_requests` still receives ADR-0007's columns (`classroom_id` and
`requested_by` NOT NULL, `document_ids`), but nothing writes to it today.
The pre-ADR `SQLGenerationRepository` cannot fill those columns, so it is
deleted along with its database test; the JSON codecs in that module stay
because `tests/fakes/generation.py` uses them. `generation_artifacts` and
`assignments.artifact_id` are unchanged.

## Per-Submission score is floored (closes ADR-0007 open question 2)

A Submission's score is `max_score × passed ÷ total`, rounded down, as
`score_for` already computes; 2 of 3 tests at max 10 scores 6. It is stored
as an integer, so changing the rule later means re-scoring stored rows.
Averages keep rounding half up to one decimal (`core/rounding.py`).

## A Submission keeps its own max score

`submissions` stores `max_score` next to `score`, because the Posting's max
score can be edited after grading and a stored score must stay explainable.
Classroom and Assignment are not copied; they come from the Posting by join.
`submission_test_results` holds one row per Test Case, as ADR-0007 §5 says.
ADR-0007 §5.1 also lists a `status`; it is left out, because judging is
synchronous and a Submission is stored only once judged. Asynchronous Submit
waits on ADR-0007 open question 1, and that task adds the column.
Superseded by ADR-0011, which adds `status` for asynchronous Submit.

## Unposting deletes the Posting's Submissions

`submissions.posting_id` is `ON DELETE CASCADE`. `AssignmentService.unpost`
removes a Posting whether or not Students have submitted, and the
alternative, `RESTRICT`, would turn that into an unhandled integrity error
until `core/assignments` gains a domain error for it. Unpost is API-only (no
page offers it); closing a Posting is the way to stop Submissions and keep
grades. `CONTEXT.md` now says a Submission is kept for as long as its Posting
exists.

## Judging settings live on the Assignment as well as each Version

ADR-0007 puts `time_limit_ms`, `language` and `show_hidden_names` only on
`assignment_versions`. The domain `Assignment` carries them too, and an
Assignment exists before its first Version (`current_version = 0`), so
`assignments` gets the same three columns with defaults (1000, `python3`,
false). Versions keep their own copy as part of the snapshot.

## A Version's Test Cases are a JSONB snapshot

`assignment_versions.test_cases` is JSONB (input, expected output, kind,
note, order). A Version is immutable and always read whole; a child table
would add joins with no query that needs them.

## `users.display_name` becomes `full_name`

The column is renamed and made `VARCHAR(120) NOT NULL`, matching the domain
`User.full_name` and the sign-up limit. Downgrade renames it back.

## Test Case kind replaces `is_hidden`

`test_cases` gains `kind` (CHECK sample, hidden, edge; default sample) and a
nullable `note`; `kind` is backfilled as `hidden` where `is_hidden` was true,
then `is_hidden` is dropped. Downgrade restores `is_hidden = kind <> 'sample'`,
so an `edge` Test Case comes back as hidden.

## Instructor requests record when they were filed

ADR-0007 lists `instructor_requests` as (user, faculty, status, reviewed_by,
reviewed_at, note). The domain `InstructorRequest` also carries the moment it
was filed, which A-01 sorts and shows, so the table gets `requested_at`
(NOT NULL, default `now()`).

## Sessions store the CSRF token

ADR-0007 §9.7 keeps the logged-in synchronizer token on the Session, but its
`sessions` column list omits it. `sessions` gets `csrf_token` NOT NULL.

## `knowledge_documents.uploaded_by` stays nullable for now

The domain `KnowledgeDocument` has no uploader yet and the upload endpoint,
already wired to the database, inserts without one; NOT NULL would break
uploads today. The column stays nullable until CORE-16 adds the uploader to
the domain; `page_count`, `progress` and `error_code` are added now. The
Document catalog lists an Instructor's Documents by `uploaded_by`, so a row
without one belongs to no library. The OPS-18 seed rows must name an owner
before the constraint lands.

## `ai_settings` is created now, with a provider

The single-row table (`id` CHECK = 1) is created and seeded in this
migration, because CORE-19, which reads it, is not an OPS task and cannot add
a table. Seed: provider `gemini`, model `gemini-3.5-flash`, daily quota 20,
max pages 50, citations required. `provider` is new: it lets the Admin pick
another AI provider later. Each provider's base URL and API key stay in the
environment; the database stores only which provider and model, and has no
CHECK on `provider`, because the allowlist lives in configuration. Storing a
URL in the table was rejected: an Admin could then send the API key to any
host.

Superseded in part by ADR-0009: the seed's provider is now `openrouter`, with
model `DEFAULT_GENERATION_MODEL` from `config.py`.

## Card numbers (C-01), as ADR-0007 §7 left them open

- A Student's "pending" Postings are the ones not Closed whose Counted
  Submission has not passed every test, never-submitted included; the card's
  deadline is the nearest among them.
- The Instructor card's pass rate counts current Members only: a Student who
  left counts in neither the numerator nor the denominator. With no Members
  or no Postings it is unknown (shown as —).
- The numbers are computed on read from the repository ports
  (`ComputedClassroomStats`), reusing the domain's Counted Submission, Closed
  and Passed rules rather than restating them in SQL.

## The T-03 Document picker lists only ready Documents

Generation reads ingested chunks, so the catalog lists an Instructor's
Documents in status `ready` only; a page count not yet known reads as 0. Until
ingestion (AI-02 to AI-07) marks Documents ready, the picker is empty and T-03
shows T-03b.

## Mapping that needs no decision

Where ADR-0007 has a timestamp and the domain a flag (`revoked_at`,
`email_verified_at`, `used_at`, `archived_at`), the adapter reads the flag as
"timestamp is set" and writes `now()` when the flag turns true.
`notifications.kind` is plain text until CORE-18 defines the kinds.
