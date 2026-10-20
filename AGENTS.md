# AGENTS.md — Questly

Modular monolith: one FastAPI + Jinja2 app in one repo, one Neon project.
`core/` and `ai/` are modules of that app, not separate services; they call
each other through Python interfaces (`typing.Protocol`), never over HTTP.
A classroom system for programming courses: Instructors publish Assignments
(drafted by the AI slice from their lecture notes) to Classrooms, and Students
submit code that is judged.
The flow and every decision behind it: `docs/adr/0007-classroom-centric-flow.md`.
Domain words: `CONTEXT.md`.

Do not write Attribution line: drop the Co-Authored-By claude header

This file holds the rules an agent needs to not break things. Human-facing
setup and background live in `README.md` and `docs/adr/`.

## Status

| Area                          | State                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Owner (task)                    |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| `core/topics`                 | Working. **The reference slice — copy its shape.**                                                                                                                                                                                                                                                                                                                                                                                                        | shared                          |
| `core/auth`, `classrooms`     | Working; persisted on Neon. Admins approve Instructor requests in `core/admin`                                                                                                                                                                                                                                                                                                                                                                            | done (CORE-12, 13)              |
| `core/assignments`            | Working; Versions and Postings persisted on Neon                                                                                                                                                                                                                                                                                                                                                                                                          | done (CORE-14)                  |
| `core/generation`             | Drafts, T-03/T-04, daily Quota persisted on Neon; production client is `OpenRouterGenerationClient` (AI-06). Retrieval is not wired in yet, so citations are `[]` (AI-09)                                                                                                                                                                                                                                                                                 | done (CORE-15)                  |
| `core/submissions`            | Run vs Submit, PostingStats persisted on Neon; production `CodeRunner` is a stub                                                                                                                                                                                                                                                                                                                                                                          | พาย (CORE-17)                   |
| `core/documents`              | Working; Instructor document library persisted on Neon                                                                                                                                                                                                                                                                                                                                                                                                    | CORE-16, OPS-20                 |
| `core/notifications`, `admin` | Working; persisted on Neon (CORE-18/OPS-21, CORE-19/OPS-22)                                                                                                                                                                                                                                                                                                                                                                                               | done (CORE-18, 19)              |
| `database/`                   | ADR-0007 + ADR-0008 schema live on Neon (head `a2ec7512fb2c` once OPS-23 is upgraded); a SQL adapter per port, each held to a contract in `tests/contracts/`                                                                                                                                                                                                                                                                                              | done (OPS-15)                   |
| `ai/`                         | Vector storage (AI-01), ingestion (AI-02 to 04) and retrieval (AI-05) work; embeddings and the real `GenerationClient` (AI-06) call OpenRouter through the `openrouter` SDK (ADR-0009). Retrieval is not yet wired into generation. AI-07 and AI-08 are open. The AI module has no routes; core reaches it through ports (OPS-16). Seed corpus: 100 PDFs on R2 under `sources/programming-in-th/` (OPS-17), registered in `core.knowledge_documents` (99 rows; one seed file was already in the instructor's library as document id 3) as `uploaded`, owned by `instructor@kmitl.ac.th` (OPS-18), not yet in `rag` (AI-08). | ฟิล์ม (AI-07, 08), พาย (OPS-16) |
| `web/templates/`              | Every page of ADR-0007 exists except T-06 (FE-08); every page is re-skinned to Figma Make (UI-02 done)                                                                                                                                                                                                                                                                                                                                                    | พาย (FE-08)                     |

Every slice also runs on the in-memory fakes in demo mode (`scripts/demo.py`)
and in tests. In production `main.py` wires the SQL adapters in
`database/core/`; a new SQL adapter is an OPS task's work, and a slice's task
never lists one.

Do not implement another area's placeholder unless the task says to.

**UI source of truth:** the Figma Make file "Questly Classroom Web App (Copy)"
(fileKey `ft2Fe3c5xrv3NO2zCq21wi`), through the local copy in
`docs/ui-redesign/` (`tokens/theme.css`, `reference/figma-make/`). Agents read that
folder and do not call the Figma MCP (the Starter plan quota is used up). A page
without a mock follows `docs/wireframes/Questly wireframes - Prototype.html`.
`docs/ui-audit.md` records which template matches which screen and every
deviation from the design.

## Stack

Python 3.12+ · FastAPI · Jinja2 · uv · Pytest · Ruff · Tailwind (standalone CLI)

```
SQLModel        <- models written here (wraps SQLAlchemy 2.x + Pydantic)
SQLAlchemy 2.x  <- the actual ORM
psycopg v3      <- driver
PostgreSQL      <- Neon, schemas `core` and `rag`, pgvector extension
```

- **Everything is synchronous.** No `async def`, no `AsyncSession`, no async
  drivers, anywhere.
- Domain services are pulled off `request.app.state`, never injected with
  `Depends()`. The only exceptions are the infrastructure health checks in
  `main.py` — do not copy that pattern into a slice.
- DSN must be `postgresql+psycopg://`. Anything else resolves to psycopg2,
  which is not installed, and fails with an unhelpful `NoSuchModuleError`.
- SQLModel does not re-export everything; `from sqlalchemy import Column,
BigInteger` is expected and correct.
- pgvector is not an ORM — it is an extension plus a `Vector` column type.

## Layering

```
routes.py → service.py → ports.py (Protocol) → adapter
```

| File         | Holds                                                           | Must not import                    |
| ------------ | --------------------------------------------------------------- | ---------------------------------- |
| `models.py`  | domain model (frozen slotted dataclass)                         | FastAPI, ORM, storage client       |
| `schemas.py` | request/response shapes for OpenAPI                             | business rules                     |
| `ports.py`   | `typing.Protocol`                                               | HTTP                               |
| `service.py` | use cases; takes the repo via constructor                       | FastAPI, ORM, SQL                  |
| `routes.py`  | JSON API under `/api/v1/`: HTTP in, service call, error mapping | business rules, SQL, templates     |
| `pages.py`   | HTML pages: HTTP in, service call, pick a template              | business rules, SQL, `/api` prefix |

The in-memory adapter is not part of the slice — it lives in
`tests/fakes/<slice>.py` (e.g. `tests/fakes/topics.py`), satisfying the same
Protocol as the SQL adapter under `database/`.

Database adapters live only in `database/core/<slice>_repository.py`.
Nothing under `core/` may import an ORM or a storage client.
`ai` may import `core` domain models; `core` must never import `ai`.

`main.py` is the composition root: build repos and services, put them on
`app.state`, include routers, mount static files. No business rules there.

**When adding a slice, read `core/topics/` first and follow it.** It is the
one executable reference; prose in this file does not override it.

## Schema rules (locked — do not "fix" these)

- Primary keys are `BIGSERIAL` (int), not UUID. (In-memory Topics still uses
  UUID; that is the demo, not the pattern to copy for persisted tables.)
- One Neon project, two schemas: `core` and `rag`.
- **No foreign keys across schemas.** `core` and `rag` share one database and
  one app; the modules reach each other through Python interfaces, and cleanup
  across the two schemas is done by the application, not by a cascade.
- `assignments.artifact_id` is nullable and UNIQUE.
- `test_cases` has no `title` column. Do not add one. The per-test label is
  `note`, and `kind` (sample, hidden, edge) replaced `is_hidden` (OPS-15).
- The classroom schema is ADR-0007 "Schema changes" as amended by ADR-0008
  (OPS-15, revision `c4e8a2f17b90`). A table or column beyond it is a new OPS
  task with its own migration and ADR entry.
- Embeddings are `VECTOR(768)`.
- Some columns look redundant on purpose — `knowledge_sources.r2_object_key`,
  `knowledge_sources.metadata`, `knowledge_chunks.embedding_model`,
  `generation_artifacts.citations.text_snapshot`. They exist because there is
  no cross-schema FK. Do not remove them.

## Do not do these without a task saying so

- Do not run `alembic upgrade`, `downgrade`, or `--autogenerate` against the
  shared database. It is shared across the team; a downgrade destroys other
  people's work. The one exception is `scripts/run_integration_tests.py`, which
  runs `alembic upgrade head` against a Neon branch it creates and deletes
  within the same run. CI does not run it.
- Do not create or edit migration files.
- Do not edit `.env`, `.env.example`, or anything holding credentials.
- Do not add `<script>`, `javascript:` URLs, or inline handlers (`onclick`,
  `onload`, …) to templates. An architecture test enforces this.
- Do not hand-edit `static/css/app.css` — edit `input.css` and rebuild.
- Do not add new files outside the agreed scope. `.gitignore` does not
  technically block this — it only ignores build/venv/cache noise — so this
  is a team rule to follow, not a tooling gate.

If a task looks like it needs a schema change, stop and say so.

## The old project name is gone

The project was renamed to Questly. The package is `src/questly/`, imports are
`questly.*`, and there is no other package under `src/`. A branch cut before the rename
(`chore/rename-questly`) brings the old name back, usually as a second package
tree next to `src/questly/`.

- Before you start work and before you open a PR: `git pull origin dev`.
  Exception: on a stacked branch (see "Agent workflow rules") never pull
  `dev`. Update with `git fetch origin && git rebase origin/<dependency-branch>`.
- New code goes under `src/questly/`. If your branch has `src/<old name>/`, move
  the files into the matching `src/questly/` folder, fix the imports, and delete
  the old folder.
- `tests/architecture/test_conventions.py::test_old_project_name_is_gone` fails
  CI when any tracked file path or line contains the old name. Two things are
  exempt: the CI R2 bucket `greader-ci` (a real resource name) and dated
  snapshots under `docs/handoff/`.

## Who may change database and locked files

All of `src/questly/database/` and `alembic/` belong to พาย (GitHub
`Doonminus2`), in every case. A task whose description seems to need a
`database/` or `alembic/` edit is not an exception — stop and ask พาย instead
of editing it. `docs/task-scope.md` enforces this at the row level: it never
grants a `database/` or `alembic/` path to a row owned by anyone else.

A subset of that — the paths under "Off-limits regardless of task" in
`docs/task-scope.md` (`alembic/`, credentials, `database/core/tables.py`,
`database/rag/tables.py`, `pyproject.toml` dependencies, CI config, `AGENTS.md`
itself) — is locked further still: those change only through an OPS task, and
anyone else who needs one changed asks; they do not edit it and explain
afterwards.

`.github/CODEOWNERS` is what enforces that stricter subset: a pull request
touching one of those paths cannot merge without a review from the owner. That
file is the mechanism, `docs/task-scope.md`'s off-limits list is the reason —
if the two ever disagree, CODEOWNERS wins and the list is what's out of date.

## Known trap: alembic autogenerate

Autogenerate only sees models imported into `SQLModel.metadata`. A missing
import produces a migration with missing tables **silently, with no error**,
and the next run may emit `drop_table` for tables that exist.
`database/__init__.py` must import both `core.tables` and `rag.tables`.

`CREATE SCHEMA`, `CREATE EXTENSION vector`, and the HNSW index cannot be
autogenerated. All three are already written by hand in the history.

## Task scope

Before touching code, read `docs/task-scope.md`. Find the row matching the
current branch's TASK-ID (`<type>/<TASK-ID>-<slug>`) and stay inside its "May
touch" column. This applies to every agent, not just Claude Code — `/grill-me`
(Claude Code only) reads the same file automatically; agents without
slash-command support must open `docs/task-scope.md` themselves at the start
of a session.

The row's Status is `open`, `in-progress` or `done`, and it is the only thing
that says whether a task is available. `/start-task` offers `open` rows owned by
you or by `TBD`, and your `in-progress` rows; it hides a row whose paths no
longer exist. A path the task must create is written `creates:` in its row.
`uv run python -m scripts.task_scope <owner>` prints the same list.

## Terminology

Instructor (not teacher/user) · Student (not learner) · Classroom (not
class/course/room) · Member · Join code · Assignment (not challenge/task/question)
· Posting · Version · Draft · Submission (not attempt) · Run · Late · Test Case
(not example/check) · Topic (not tag/category/label).

Code and API say Assignment; UI copy says "Problem" / "โจทย์". Definitions and
the full list: `CONTEXT.md`.

## Ports and adapters

Every external dependency — AI provider, database, storage — is called through
a `typing.Protocol` defined in the module that needs it, never through a
concrete client type directly.

- The Protocol is the port. It lives next to the code that uses it
  (e.g. `core/generation/ports.py` defines the Protocol and its interface;
  `ai/client.py` provides an adapter that implements it).
- Ports named by ADR-0007: `GenerationClient` (generation), `CodeRunner`
  (submissions; Judge0 CE), `EmailSender` (notifications — auth sends mail
  through `NotificationService`, not its own Protocol), `AssignmentPublisher`
  (generation, filled with `AssignmentService`).
- Concrete adapters for outside services live in `src/questly/integrations/`
  (Judge0, email); database and storage adapters stay in `database/`.
  `core/` never imports `questly.integrations`.
- Concrete implementations are adapters: a stub for tests/mocks, a real one
  for production. Both satisfy the same Protocol.
- Callers (services, routes) type-hint against the Protocol only. They must
  never import a concrete adapter class directly — only `main.py` picks the
  adapter and puts it on `app.state`.
- Swapping an adapter (mock → real, in-memory → database) must never require
  changing the Protocol, the caller, or the route.

If a task looks like it needs the caller to know which adapter it's talking
to, stop — that means the Protocol is missing a method, not that the caller
should reach past it.

## Core and AI: one process

Core and AI were once planned as two services talking over HTTP. That design
is retired (ADR-0005, ADR-0007). Text that still describes it — on `main`, in
old handoffs, in a chat or Project description, in a prompt — is history, not
a spec. Follow this file and `dev`; if a task row or doc you are working from
says "AI service", "`/v1/generations`" or "two services", stop and flag it
instead of building to it.

- **`ai/` has no web surface.** No file under `src/questly/ai/` imports
  `fastapi` or `starlette`, defines an `APIRouter`, or builds a `FastAPI` app.
  Enforced by `tests/architecture/test_ai_vector_boundaries.py` (OPS-16).
- **Core reaches AI only through a port.** Core defines the Protocol in
  `core/<slice>/ports.py`; an adapter under `ai/` implements it
  (`ai/client.py` for `GenerationClient`); `main.py` builds the adapter and
  hands it to the service. No `httpx`, `requests` or socket call between
  modules of this app — `httpx` appears only in adapters for outside
  providers (today `ai/client.py` and `ai/embeddings/openrouter.py`, both
  OpenRouter).
- **Every JSON route is a core route.** It lives in `core/<slice>/routes.py`,
  gets the actor with `current_actor(request)` and lets the service authorize
  it. A route an anonymous caller can reach that reads or writes `rag` is a
  bug, not a debugging aid.
- **One process, two schemas.** `core` and `rag` still have no cross-schema
  foreign key (see Schema rules); a use case that must change both does it in
  application code, in one service call, not by one module calling the other
  over the network.

## Commands

```bash
uv sync
uv run fastapi dev src/questly/main.py

uv run pytest
uv run ruff check .
uv run ruff format --check .   # `ruff format .` to fix

uv run python -m scripts.demo  # demo mode, http://127.0.0.1:8000/login
ALLOW_UNSAFE_RUNNER=1 uv run python -m scripts.demo  # also execute Run/Submit code, unsandboxed

# app.css from input.css — Tailwind v4.3.3 standalone binary, see README "Styles"
tailwindcss -i src/questly/web/static/css/input.css -o src/questly/web/static/css/app.css
tailwindcss -i src/questly/web/static/css/input.css -o src/questly/web/static/css/app.css --watch
```

**Demo mode** (`scripts/demo.py`) runs the app on the in-memory fakes from
`tests/fakes/` with a seed mirroring `docs/wireframes/`, so pages are clickable
before OPS-15 creates the tables. G-01 lists every seeded account under "log in
as"; verification links are logged, not emailed; data is lost on restart. It
lives outside `src/` because fakes may not ship in the package. When a slice
lands, extend `DemoSeed` so its pages have data. `fastapi dev` wires the real
SQL adapters, so it reads and writes the database in `.env`.

`tests/unit/` domain + service · `tests/integration/` HTTP via ASGI transport ·
`tests/architecture/` import direction and the no-JavaScript rule ·
`tests/db/` database adapters against a real PostgreSQL · `tests/r2/` object
storage behaviour against a real R2 bucket.

Tests must never hit the shared database, the real R2 bucket, or a real AI
provider. `tests/db/` and `tests/r2/` are the exceptions, and only through
throwaway infrastructure:

- `tests/db/` — mark tests `postgres`, read the DSN from the `postgres_url`
  fixture, and never from `DATABASE_URL`. `scripts/run_integration_tests.py`
  supplies `POSTGRES_TEST_URL` by creating a Neon branch per run and deleting it
  afterwards. That is the only way to run them:
  `uv run --env-file .env python -m scripts.run_integration_tests --only postgres`.
  - No Docker in this project: no `docker run`, no `docker compose`, no
    `pgvector`/`postgres` image.
  - No local Postgres cluster for `tests/db/`: no `initdb`, `pg_ctl`, or
    `apt install postgresql`.
  - If you cannot reach Neon (no `.env`, or port 5432 is blocked), stop and ask
    พาย to run the script. Do not find another way, and never put results from
    a local database in a PR in place of the Neon result.
- `tests/r2/` — mark tests `r2`, read the client and bucket from the
  `r2_test_bucket` fixture and a unique key from `r2_test_prefix`, and never
  from `R2_BUCKET_NAME`/`R2_ENDPOINT_URL`/etc. Reserve this marker for
  behaviour the existing stub cannot faithfully reproduce (presigned URLs
  actually fetched, multipart upload, conditional-write conflicts) —
  everything else (upload-then-list) stays on the existing stub. `moto` is
  not a dev dependency today (not listed in `pyproject.toml`); adopting it
  would be a dependency change, so that's an OPS task, not something to add
  ad hoc. The script reads
  `R2_TEST_ENDPOINT_URL`, `R2_TEST_ACCESS_KEY_ID`, `R2_TEST_SECRET_ACCESS_KEY`
  and `R2_TEST_BUCKET_NAME` from the environment, pointing at a dedicated
  `greader-ci` bucket, sets a per-run `R2_TEST_PREFIX`, and cleans it up with
  `scripts/ci_r2_cleanup.py` afterwards.

**CI does not run `postgres` or `r2` tests.** They need real Neon and R2, and
from a hosted runner they took ten minutes, so CI runs
`pytest -m "not postgres and not r2"`. Run
`uv run --env-file .env python -m scripts.run_integration_tests` yourself before
a PR that touches `database/`, `alembic/`, `tests/db/` or `tests/r2/`, and quote
its result in the PR description. An agent that cannot reach Neon hands the run
to พาย (see `tests/db/` above). CI does not apply migrations either, so a new
migration is proven only by that script.

With those variables unset the marked tests skip themselves, so
`uv run pytest` stays green with no database or bucket. See `tests/conftest.py`.

## Seed corpus on R2

100 Thai problem-statement PDFs from programming.in.th live in the dev R2
bucket under `sources/programming-in-th/{task_id}.pdf` (OPS-17). Their topic
and difficulty are in `scripts/seed/pith-100-manifest.csv`; background, selection
rules and known limits are in `docs/handoff/2026-09-28-OPS-17-pith-seed.md`.

- Re-seed with `uv run --env-file .env python scripts/seed_pith_to_r2.py`
  (`--dry-run` first). It skips objects that exist and never deletes.
- Register the seed in `core.knowledge_documents` with
  `uv run --env-file .env python scripts/seed_pith_to_r2.py --register-db --owner-email instructor@kmitl.ac.th`
  (OPS-18). Rows start as `uploaded` with `metadata` keys `topic`, `difficulty`,
  `task_id` and `course: "seed-pith"`; a rerun skips rows the owner already has
  with the same `content_hash`. Only that owner sees the seed on T-03, and only
  once AI-08 marks the rows `ready`, so test AI-08 signed in as
  `instructor@kmitl.ac.th`.
- Do not delete or overwrite objects under that prefix; ingestion work reads them.
- Tests never read this prefix — the "never hit the real R2 bucket" rule above
  still applies. Use `tests/fixtures/pdfs/` or moto.
- Adding a PDF: check its extracted Thai text first. 53 of 190 candidates were
  rejected for floating vowels, broken ำ or Private-Use-Area glyphs.

## Definition of done

- endpoint under `/api/v1/`, visible in `/docs`
- `core/` free of FastAPI, ORM, and storage-client imports
- routes hold no business rules or SQL
- authorization lives in the service: every use case touching classroom data
  takes `actor: Actor` first and raises `PermissionDeniedError`; routes and
  pages never branch on a role (serializing one is fine). A handler gets the actor with
  `current_actor(request)`, never `Depends()`. Non-member → 404, wrong role
  in own Classroom → 403
- HTML pages live in `core/<slice>/pages.py` (no `/api` prefix); only
  `pages.py` renders templates. Templates live in
  `web/templates/<group>/<page-id>_<slug>.html`, group one of `shared`,
  `student`, `instructor`, `admin` (e.g. `student/s02_solve.html`)
- templates build buttons and inputs only through the macros in
  `web/templates/_components/forms.html`, and layout pieces (page header, tabs,
  badge, empty state, modal) through `_components/ui.html`; style with the
  tokens in `input.css`, then rebuild `app.css`
- every `<form method="post">` renders `{{ csrf_field() }}` (import the forms
  macros `with context`), and every POST page handler takes `csrf_token` and
  calls `require_csrf(request, csrf_token)` first (`core/auth/csrf.py`); the
  tests post forms through `tests/integration/forms.py`
- integration tests cover each page route for every role: 200 with the right
  template for the allowed role, 403/404 for the others
- unit test at the service/repository seam, integration test at the HTTP layer
- architecture tests pass
- `pytest`, `ruff check`, `ruff format --check` all pass
- if the PR touches anything under `src/`, it also adds or modifies something
  under `tests/` — a pre-existing suite staying green is not evidence the new
  code works, only that it wasn't exercised
- the PR description quotes the CI result for the PR's own head commit, not a
  number from a local run; a PR touching `database/`, `alembic/`, `tests/db/` or
  `tests/r2/` also quotes the `scripts/run_integration_tests.py` result

## Git

Branch from `dev`, named `<type>/<TASK-ID>-<slug>` where type is `feat`, `fix`,
`chore`, `docs`, or `refactor`. Pull requests target `dev`, not `main` — `main`
is only this repo's default branch, so GitHub pre-fills it as the PR base;
change the base to `dev` before opening, every time. `main` lags `dev` by
many merges, so read code and docs from `dev`, never from `main`. Pai reviews every PR by
hand first, then with AI. Pai reviews and merges his own PRs, and the
description quotes the integration-test results.
Never commit directly to `main` or `dev`.

## Typing rules

These rules apply to every file under `src/` and `tests/`. `ruff`'s `ANN401`
only flags `Any` in argument annotations — `-> dict[str, Any]` passes it clean.
The actual enforcement is `tests/architecture/test_conventions.py`. **A rule
below without a matching test in that file is advisory, not binding** — when
you add a rule here, add its test in the same PR.

**No `Any`**

- Never import or use `typing.Any`, including `dict[str, Any]`, `list[Any]`, `Callable[..., Any]`.
- If you think you need `Any`, the contract has not been decided yet. Stop and ask in the PR.
- A service or repository must never return a bare `dict` from a public method. Return a
  domain object (dataclass). Converting to dict/JSON is the schema layer's job, at `routes`.

**`X | None` is allowed in exactly three cases**

1. A `Repository.get()` whose Protocol declares `X | None` (see `core/topics/ports.py`).
2. A domain field that is genuinely nullable, e.g. `artifact_id: str | None`.
3. An optional argument defaulting to `None` where the `None` case is handled explicitly
   on the following lines.

Anywhere else, `| None` is not allowed.

**Never use None as control flow**

- A service must not return `None` to mean "not found". Raise a domain error
  (`TopicNotFoundError`, `AssignmentNotFoundError`) and let `routes` translate it to HTTP.
- Never write `value or []`, `value or {}`, `value or 0` to paper over a `None`.
  If the field must not be empty, give the dataclass a `field(default_factory=list)`.
  If it can genuinely be empty, write `if value is None:` so the case is visible.
- Never write `getattr(obj, "field", [])` to dodge an AttributeError. If the attribute
  should exist, access it directly and let the test fail loudly instead of hiding it at runtime.

**Partial updates must not drop fields**

- A use case that updates part of an entity must load the existing entity and merge.
  Never build a fresh object from the request payload alone and hand it to `repository.update()`.
- Every adapter implementing the same Protocol must behave identically. If the in-memory
  adapter clears a relationship and the SQL adapter does not, that is a Protocol violation.

## Agent workflow rules

Binding by team agreement; not covered by tests/architecture/test_conventions.py.

These rules apply to every agent on this repo, for every team member. The
subagent steps below are how Claude Code does them; another agent does the
equivalent by hand.

### Source of truth
- The ticket row is the spec. A ticket prompt only names the row and any deviation from the default recipe, which is the flow in `.claude/commands/start-task.md`.
- If the row conflicts with this file, stop and ask. The one exception is a row that tells you to edit another person's row or branch: see "Ownership and scope".

### Stacked branches
When a ticket depends on a branch that is not merged yet:
- Branch off it without asking: `git switch -c <type>/<ID>-<slug> origin/<dependency-branch>`.
- Never push to, rebase, or force-push another person's branch.
- Never `git pull origin dev` on a stacked branch, and never merge `origin/dev` into it. Update with `git fetch origin && git rebase origin/<dependency-branch>`.
- Open the PR as a draft against dev, with the title prefix `[stacked on <DEP-ID>]`. State the dependency in the body and record the dependency branch's tip SHA (`git rev-parse origin/<dependency-branch>`), because merged branches get deleted.
- The stacked PR merges only after the dependency's row is done and CI is green. Merge it with a merge commit, never squash, so the dependency author's commits stay theirs.
- If the dependency lands alone first: `git fetch origin && git rebase --onto origin/dev <recorded SHA>`, then `git push --force-with-lease` on your own branch only.

### Ownership and scope
Two cases, and only these:
- A file outside the row's "May touch" column, locked files included: stop and ask before editing. List the files and why.
- Another person's row or branch: never edit it, even if a row says to. List the change you need under "Needs owner action" in the PR description and continue. This is an exception to "If the row conflicts with this file, stop and ask".

### Autonomy
- Without asking: create your branch, run tests, commit, push your own branch, open or update a draft PR.
- Leave to a human: marking the PR ready for review, merging, and anything touching another person's branch.

### Subagents
- Before editing, use an Explore subagent to gather facts (the row, the dependency diff, contracts, existing patterns). Keep the main context for edits.
- Before pushing, run a fresh self-check subagent that has not seen the work. Give it the row and `git diff origin/<base>...HEAD`, and have it check the row's acceptance criteria and these rules. Fix what it finds, or report it. The self-check does not replace พาย's review (see "Git").
- Never let two subagents edit the same files in parallel.
