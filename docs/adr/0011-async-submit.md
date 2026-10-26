---
status: accepted
date: 2026-10-13
task: CORE-17, OPS-30
---

# Submit is judged in the background and survives a restart

## Context

ADR-0007 §5.5 has Judge0 judge a Submission "synchronously with a timeout",
and §11 lists "no progress display" for Submit as a deliberate gap. ADR-0010
closed that gap for Generate only. A Submit holds the request open for as long
as Judge0 takes, and a Judge0 outage or a timeout answers 503 and keeps
nothing. A server restart mid-judging loses the work and the Student does not
know whether the Submit counted.

ADR-0008 left `submissions.status` out because judging was synchronous and
said "that task adds the column". This is that task: CORE-17 builds the
behaviour and OPS-30 builds the schema and the SQL adapter.

## Decision

This supersedes, **for Submit only**, ADR-0007 §5.5 ("synchronously with a
timeout"), the Submit half of the first bullet of ADR-0007 §11, and the
paragraph in ADR-0008 "A Submission keeps its own max score" that defers
`status`. Run stays synchronous and keeps its 503.

### A Submission moves through four statuses

| from | to | who moves it |
| --- | --- | --- |
| (none) | `received` | the request thread, in `SubmissionService.submit` |
| `received` | `judging` | the worker, once Judge0 accepted the batch and returned tokens |
| `judging` | `done` | the worker, once every token finished |
| `judging` | `received` | the worker, when the batch is `gone` (once per worker) |
| `received` or `judging` | `failed` | the worker, past the give-up window, after a second loss in its pass, or when the Version is gone |

`done` and `failed` are terminal. The transition table lives in
`core/submissions/models.py` (`SubmissionStatus.can_move`). The repository has
one method per transition, and each SQL write is a compare-and-set on
`status`. A write whose row has moved on, or was deleted by an unpost, changes
nothing and returns `False`, and the worker stops.

Only a `done` Submission has a score and test results. A `failed` Submission
never counts and does not use up the single Submit of a Posting that allows no
resubmission, so a Student loses no right to an outage. "Counted Submission"
now means the latest `done` one.

### The columns live on `core.submissions`

ADR-0007 §5.1 already lists `status` on `submissions`. A separate table of
pending rows was rejected because every reader would need a join and a second
write path.

- `status text NOT NULL`, CHECK in (`received`, `judging`, `done`, `failed`).
  Existing rows are backfilled as `done`, and the column has no default
  afterwards, so every insert names its status.
- `recovery text NOT NULL DEFAULT 'none'`, CHECK in (`none`, `reconnected`,
  `rejudged`).
- `runner_tokens text[] NOT NULL DEFAULT '{}'`: one Judge0 token per Test Case,
  in Test Case order.
- `claimed_until timestamptz NULL`: the lease. It exists only in the table and
  never in the domain model.
- `score` becomes nullable, with
  `CHECK ((status = 'done') = (score IS NOT NULL))`.
- A partial unique index `uq_submissions_one_unfinished` on
  `(posting_id, student_id) WHERE status IN ('received', 'judging')`.

`max_score`, `is_late`, `version_number` and `attempt` stay NOT NULL, because
the request thread knows all four at receive time. `uq_submissions_attempt`
now also refuses a double-clicked Submit.

`claim_due` gets no index of its own. It reads pending rows only, and the
partial unique index above holds exactly those rows, so Postgres scans that
small index and sorts the few rows it finds. A `done` row never enters it, so
the sweeper's cost does not grow with the history of Submissions.

### The request stores the Submission, a background task judges it

1. `submit` stamps the receive time first. Late is decided from that time,
   once, and never recomputed.
2. It stores the Submission as `received` with a lease of 90 seconds.
3. The handler schedules `SubmissionService.judge(id)` as a FastAPI
   background task, the synchronous pattern ADR-0010 uses for Generate.

`judge` runs without an Actor: the service authorized the Student at receive
time, and no route exposes `judge`. It starts the batch on Judge0, saves the
tokens (`started`), collects them and renews the lease while Judge0 reports
them running, then saves score and results (`finished`) in one transaction.
It judges against the Version the Submission was received on, not the current
one.

When Judge0 cannot be reached, the worker releases the lease and returns. No
thread sleeps through an outage. When the batch is `gone`, the worker sends
the row back to `received`, sets `recovery` to `rejudged` and judges the code
again at once. The Judge0 adapter reads a batch as `gone` when Judge0 no
longer knows a token, or reports a status that is neither finished nor still
running (Internal Error, for example). A second `gone` in the same worker's
pass ends as `failed`. When one worker sees a loss and the worker that takes
the row over sees another, each counts once, so neither fails the row.

Before each start and each poll the worker checks the receive time. A
Submission received 15 minutes ago or more ends as `failed` without a call to
Judge0. A Submission whose Version can no longer be read ends as `failed` at
once.

### The lease decides who may judge a row

A row is held while `claimed_until` is in the future. A pending row with
`claimed_until` unset or in the past is due. A worker sets the lease when it
receives or claims a row and moves it forward after every poll. `finished`
and `failed` clear it. `released` clears it on purpose so the row is due at
once.

The lease is 90 seconds. `receive`, `claim_due` and `started` set it to now
plus 90 seconds, and the worker renews it after every poll that finds the
batch still running. One poll of the Judge0 adapter waits at most 20 seconds,
so a live worker renews well inside its lease.

A sweeper thread calls `SubmissionService.recover()`, waits 15 seconds after
the pass ends, and calls it again. `production_app` starts that thread and
`create_app` never does. A pass claims one due row at a time, oldest
submitted first, with one statement:
`UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED) RETURNING …`. It
judges that row to `done` or `failed` before it claims the next. A pass ends
when no row is due, or as soon as Judge0 is unreachable. In that case the
pass releases the row it holds, and every other due row waits for the next
pass. Two processes never claim the same row, and a worker that stalled past
its lease loses its next compare-and-set to the worker that took the row
over. After a restart, a row is claimed by the first pass after its lease
expired: up to 90 seconds plus one 15-second wait, or later while the pass
is still judging an older row.

### `recovery` records that the first run did not finish normally

`recovery` says that the original run was lost or its worker vanished. Two
events set it:

1. A worker claims a row whose lease **expired**, meaning the previous worker
   died or stalled without releasing it. A `judging` row gets `reconnected`
   and its saved tokens are collected. A `received` row gets `rejudged`,
   because no tokens were saved and the code is sent to Judge0 again.
2. Any worker, the background task included, finds its batch `gone`. The row
   gets `rejudged` and the code is sent to Judge0 again. No restart has to
   happen for this.

A released row (Judge0 unreachable) is claimed again without a marker. The
marker only moves up (`none` < `reconnected` < `rejudged`), so a reconnected
row whose batch then turns out `gone` ends `rejudged`. The marker is written
with the next `started` or `finished`. A `failed` row keeps the marker it had
before. T-02 shows the Instructor a red "Recovered" badge on a `done` row with
a marker, and T-02a says whether the result was reconnected or the code
judged again. The T-02a copy says a server restart interrupted judging, which
is not true for the second event.

### Rejudging touches unfinished Submissions only

ADR-0007 §5.2 says the counted Submission is never re-judged. That still holds
for every `done` Submission. Rejudging here applies only to a Submission that
never finished judging, so no stored score changes.

### Resubmission and the edges

- A Submit while the Student has a pending Submission on the Posting answers
  409 `submission_pending`, whatever `allow_resubmission` says. The partial
  unique index enforces it.
- Closing the Posting, archiving the Classroom or passing the deadline after
  receive does not stop judging.
- Unposting mid-judging deletes the row. The worker's next compare-and-set
  changes nothing and it returns quietly. The Judge0 batch is orphaned and
  only costs CPU.

### The JSON Submit answers 202

`POST /api/v1/classrooms/{id}/assignments/{aid}/submissions` answers
**202 Accepted** instead of 201, with
`Location: /api/v1/submissions/{id}` and `score: null`.
`GET /api/v1/submissions/{id}` is the status endpoint. The Student page keeps
its CSRF check and its 303. S-02 becomes read-only S-02a while a Submission is
pending and refreshes itself every 2 seconds with `<meta http-equiv="refresh">`.

## Consequences

- `scripts/demo.py` and the in-memory fakes in `tests/fakes/` need no change:
  a Submission built without a status is `done`, and the fake repository holds
  tokens and leases beside its rows as the SQL table does.
- `SubmissionRepository` gains `receive`, `claim_due`, `started`, `renew`,
  `finished`, `requeued`, `released` and `failed`. `add` stays for seeding
  `done` rows. `get` and the `list_*` reads return every status, so each
  reader that counts Submissions filters `done` itself.
- The downgrade deletes every Submission that is not `done`, then restores
  `score NOT NULL`. A pending Submission cannot be expressed in the old
  schema.
- Open risk: a stuck Judge0 holds one thread-pool thread for up to 15
  minutes per Submission. The background task polls a batch that stays
  running until the give-up window ends, and that thread pool also serves the
  synchronous route handlers. The sweeper judges one row at a time on its own
  thread, so the same stuck batch delays every other due row by up to 15
  minutes.
- Open risk: a background task that starts after its 90-second lease expired
  can orphan one Judge0 batch. `judge` checks only that the row is still
  `received`, not that it still holds the lease. If a sweeper claimed the row
  in the meantime, both send the code to Judge0. The first `started` wins,
  the other worker stops, and its batch is never collected. When the sweeper
  wins, the row is marked `rejudged` although no worker vanished.
- A crash after Judge0 accepted a batch but before `started` saved its tokens
  judges the code a second time on recovery and marks it `rejudged`. The first
  batch is orphaned.
