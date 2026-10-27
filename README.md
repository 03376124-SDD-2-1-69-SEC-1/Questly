<p align="center">
  <img src="docs/Questly-logo.png" alt="Questly" width="480">
</p>

# Questly

<p align="center">
  <img src="https://img.shields.io/badge/PYTHON-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FASTAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/JINJA2-B41717?style=for-the-badge&logo=jinja&logoColor=white" alt="Jinja2">
  <img src="https://img.shields.io/badge/UVICORN-499848?style=for-the-badge&logo=uvicorn&logoColor=white" alt="Uvicorn">
  <img src="https://img.shields.io/badge/UV-DE5FE9?style=for-the-badge&logo=uv&logoColor=white" alt="uv"><br>
  <img src="https://img.shields.io/badge/SQLMODEL-7E56C2?style=for-the-badge" alt="SQLModel">
  <img src="https://img.shields.io/badge/SQLALCHEMY-2.x-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white" alt="SQLAlchemy">
  <img src="https://img.shields.io/badge/PSYCOPG-3-336791?style=for-the-badge&logo=postgresql&logoColor=white" alt="psycopg">
  <img src="https://img.shields.io/badge/POSTGRESQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/NEON-00E599?style=for-the-badge&logo=neon&logoColor=black" alt="Neon">
  <img src="https://img.shields.io/badge/PGVECTOR-768%20dims-336791?style=for-the-badge&logo=postgresql&logoColor=white" alt="pgvector"><br>
  <img src="https://img.shields.io/badge/ALEMBIC-6BA81E?style=for-the-badge&logo=sqlalchemy&logoColor=white" alt="Alembic">
  <img src="https://img.shields.io/badge/PYMUPDF-0A66C2?style=for-the-badge" alt="PyMuPDF">
  <img src="https://img.shields.io/badge/OPENROUTER-6467F2?style=for-the-badge&logo=openrouter&logoColor=white" alt="OpenRouter">
  <img src="https://img.shields.io/badge/CLOUDFLARE%20R2-F38020?style=for-the-badge&logo=cloudflare&logoColor=white" alt="Cloudflare R2"><br>
  <img src="https://img.shields.io/badge/TAILWIND%20CSS-4-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white" alt="Tailwind CSS">
  <img src="https://img.shields.io/badge/PYTEST-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white" alt="Pytest">
  <img src="https://img.shields.io/badge/RUFF-D7FF64?style=for-the-badge&logo=ruff&logoColor=black" alt="Ruff">
  <img src="https://img.shields.io/badge/GITHUB%20ACTIONS-2088FF?style=for-the-badge&logo=githubactions&logoColor=white" alt="GitHub Actions">
</p>

**Language:** English | [ภาษาไทย](README.th.md)

Questly is a FastAPI + Jinja2 application for programming courses.
Instructors draft Assignments and their Test Cases with AI help from their own
lecture PDFs, publish them to Classrooms, and Students submit code that is
judged.

**Architecture.** One FastAPI app in one repo — a modular monolith. `core/` and
`ai/` are modules of that app and call each other through Python interfaces
(`typing.Protocol`); there is no network call between them and `ai/` has no
routes of its own. Data lives in one Neon Postgres project with two schemas,
`core` and `rag` (pgvector, no foreign key across them), and uploaded PDFs live
in Cloudflare R2. Embeddings and generation go through OpenRouter (ADR-0009).
The rules are in `AGENTS.md`, the flow and its decisions in
`docs/adr/0007-classroom-centric-flow.md`, and what each area can do today in
the Status table of `AGENTS.md`.

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/03376124-SDD-2-1-69-SEC-1/PyJudge.git
cd PyJudge
```

If `git` is not recognized, install it using the instructions in the next
section, then run the clone command again.

### 2. Check the Required Tools

The project requires Git, `uv`, and Python 3.12 or newer.

#### macOS

Open Terminal and run:

```bash
git --version
uv --version
python3 --version
```

Install any missing tools:

```bash
# Git (Apple Command Line Tools)
xcode-select --install

# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.12 managed by uv
uv python install 3.12
```

#### Windows

Open PowerShell and run:

```powershell
git --version
uv --version
py --version
```

Install any missing tools with WinGet:

```powershell
# Git
winget install --id Git.Git -e --source winget

# uv
winget install --id astral-sh.uv -e

# Python 3.12 managed by uv
uv python install 3.12
```

Restart the terminal after installing a tool, then run the version checks
again. Alternative installers are available from the official
[Git](https://git-scm.com/install/) and
[`uv`](https://docs.astral.sh/uv/getting-started/installation/) pages.

### 3. Install the Project Dependencies

Run this command from the project directory:

```bash
uv sync
```

`uv` creates a local virtual environment and installs the locked dependencies.
You do not need to activate the environment when using `uv run`.

### 4. Configure the Environment

Create a local `.env` file from the template.

On macOS:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Open `.env` and provide the credentials supplied by your team. The application
currently needs a Neon PostgreSQL connection in `DATABASE_URL` and these
Cloudflare R2 values:

```dotenv
DATABASE_URL=postgresql+psycopg://user:password@host/database?sslmode=require
R2_ENDPOINT_URL=
R2_BUCKET_NAME=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
BASE_URL=https://questly.example.com
```

Set `BASE_URL` to the app's canonical public origin (without a trailing slash).
It must start with `http://` or `https://` and name a host; anything else is
rejected when the app reads its configuration. Emails use this value to build
full links: `{BASE_URL}/verify?token=...` in verification emails and
`{BASE_URL}/classes/...` in notification emails. A link is never derived from
the incoming request's `Host` header. In production set `APP_ENV=production`
and `BASE_URL`; with `APP_ENV=production` and no `BASE_URL` the app does not
start (`MissingConfigurationError`). Any other `APP_ENV` falls back to
`http://localhost:8000`.

Set `OPENROUTER_API_KEY` to call OpenRouter for generation and embeddings
(ADR-0009); leave it empty to run on the stub generation client.
`GENERATION_MODEL` and `EMBEDDING_MODEL` pick the models and default to the
values in `src/questly/config.py`. Never commit `.env` or place real
credentials in `.env.example`.

#### Email

`EMAIL_BACKEND` picks how verification and notification emails leave the app:

| `EMAIL_BACKEND` | Behaviour | Variables it needs |
| --- | --- | --- |
| `stub` (default outside production) | Sends nothing. The whole email, link included, is written to the log. | none |
| `smtp` | Sends through an SMTP server with the standard library. | `EMAIL_FROM`, `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`; optional `SMTP_SECURITY` (`starttls` by default, or `ssl`) and `SMTP_PORT` (587 for `starttls`, 465 for `ssl`) |
| `resend` | Sends through the [Resend](https://resend.com) REST API. | `EMAIL_FROM`, `RESEND_API_KEY` |

`EMAIL_FROM` may be a bare address or `Questly <noreply@your-domain>`. A backend
with missing variables stops the app at startup, and the error names every
missing variable. Messages are plain text in UTF-8, each send times out after
10 seconds, and nothing retries.

- **Production refuses `stub`.** With `APP_ENV=production`, an unset or `stub`
  `EMAIL_BACKEND` stops the app at startup. Use `smtp` or `resend`.
- **Cloud hosts.** The free tiers of Render and Railway block outbound SMTP
  ports, so use `resend` there.
- **Mailing `@kmitl.ac.th`.** Resend only delivers to arbitrary recipients from
  a verified sending domain, so verify your domain in Resend and send from it.
- **Development link.** With the `stub` backend and `APP_ENV` other than
  `production`, G-04 shows the verification link on the page so you can follow it
  without a mailbox. It never appears in production, with `smtp` or `resend`, or
  in any API response. Anyone who can reach the app can read the link for any
  unverified address, so never run the stub on a host other people can reach
  (a shared staging server, say) and set `APP_ENV=production` or a real backend there.
- **A failed send is not a failed sign-up.** The account and its link stay
  created. G-04 says the email could not be sent and offers "ส่งอีเมลอีกครั้ง",
  and `POST /api/v1/auth/verify/resend` answers 503 `email_delivery_failed`.

### 5. Apply Database Migrations

```bash
uv run --env-file .env alembic upgrade head
```

### 6. Start the Development Server

```bash
uv run --env-file .env uvicorn questly.main:app --reload
```

Open <http://127.0.0.1:8000> in your browser. Stop the server with `Ctrl+C`.

## Useful URLs

| URL | Description |
|---|---|
| <http://127.0.0.1:8000/> | Home page |
| <http://127.0.0.1:8000/docs> | Interactive API documentation |
| <http://127.0.0.1:8000/api/v1/topics> | Topics API |
| <http://127.0.0.1:8000/health> | Application health |
| <http://127.0.0.1:8000/health/db> | Neon PostgreSQL health |
| <http://127.0.0.1:8000/health/r2> | Cloudflare R2 health |

## Development Commands

Run the test suite:

```bash
uv run --env-file .env pytest
```

Check linting and formatting:

```bash
uv run ruff check .
uv run ruff format --check .
```

### Integration tests (Neon and R2)

CI runs everything except tests marked `postgres` or `r2`. Those talk to real
Neon and R2, and from a GitHub runner on another continent they took ten
minutes. Run them yourself with one script before you open a PR that touches
`database/`, `alembic/`, `tests/db/` or `tests/r2/`, and quote the result in the
PR description.

```bash
uv run --env-file .env python -m scripts.run_integration_tests
```

The script creates a throwaway Neon branch, applies the migrations to that
branch only, runs the `postgres` and `r2` tests, then deletes the branch and the
R2 objects it wrote. The branch also expires after two hours if the script is
killed. The shared database is never the target.

Options:

```bash
# only one group
uv run --env-file .env python -m scripts.run_integration_tests --only postgres
uv run --env-file .env python -m scripts.run_integration_tests --only r2

# extra pytest arguments go after `--`
uv run --env-file .env python -m scripts.run_integration_tests -- -k classroom

# keep the branch to poke at it (it still expires)
uv run --env-file .env python -m scripts.run_integration_tests --keep-branch
```

One-time setup:

1. Install the Neon CLI: `npm i -g neonctl`, then `neonctl auth` (or export
   `NEON_API_KEY`).
2. Export the project, and for R2 the dedicated `greader-ci` bucket's
   credentials. Ask พาย for the values:

   ```bash
   export NEON_PROJECT_ID=...
   export NEON_PARENT_BRANCH=production   # optional, this is the default
   export R2_TEST_ENDPOINT_URL=...
   export R2_TEST_ACCESS_KEY_ID=...
   export R2_TEST_SECRET_ACCESS_KEY=...
   export R2_TEST_BUCKET_NAME=greader-ci
   ```

Plain `uv run pytest` still skips the marked tests, so it needs no database or
bucket. Never point `POSTGRES_TEST_URL` at the shared database; `tests/conftest.py`
refuses when it matches `DATABASE_URL`.

### Styles (Tailwind CSS v4, standalone CLI)

`src/questly/web/static/css/app.css` is generated from `input.css` next to it
and is committed, so the app runs without the CLI. Rebuild it whenever you
change `input.css` or add Tailwind classes to a template (`@source` scans
`web/templates/`). Never edit `app.css` by hand.

The CLI is a single binary, not a Python or npm dependency. Download
`tailwindcss-<platform>` for **v4.3.3** from
https://github.com/tailwindlabs/tailwindcss/releases/tag/v4.3.3, then:

```bash
chmod +x tailwindcss-macos-arm64 && mv tailwindcss-macos-arm64 ~/.local/bin/tailwindcss

# build once
tailwindcss -i src/questly/web/static/css/input.css -o src/questly/web/static/css/app.css
# rebuild on every change while you work
tailwindcss -i src/questly/web/static/css/input.css -o src/questly/web/static/css/app.css --watch
```

Design tokens (colours, fonts) and components (`.btn`, `.input`, `.card`,
`.tabs`, `.badge`, `.modal`, …) live in `input.css` and come from
`docs/wireframes/`. Pages build forms and buttons only through the Jinja macros
in `src/questly/web/templates/_components/`.

Create a migration after changing database models:

```bash
uv run --env-file .env alembic revision --autogenerate -m "describe the change"
```

### UI check with Playwright (screenshots and video)

`playwright/ui_walk.py` opens pages of the demo app in a real Chromium at desktop
(1280×800) and mobile (390×844) width. It saves a full-page screenshot of every
page and a video of the whole walk. Use it to check a UI change without
clicking through by hand, and attach the screenshots to the PR.

Playwright is not a project dependency. `uv run --with playwright` installs it
for that one command, so `pyproject.toml` and `uv.lock` never change. Run both
commands from the repo root.

```bash
# terminal 1 — demo server (in-memory fakes, no database needed)
uv run python -m scripts.demo
```

```bash
# terminal 2 — first time only: download the browser (can take several minutes)
uv run --with playwright playwright install chromium

# walk the default pages (/classes, /documents)
uv run --with playwright python -I playwright/ui_walk.py

# walk the pages you changed
uv run --with playwright python -I playwright/ui_walk.py /classes /admin

# T-06: also upload a sample PDF and screenshot the result
uv run --with playwright python -I playwright/ui_walk.py --upload-pdf

# open the result on macOS
open playwright/out
```

| Option | Default | Meaning |
|---|---|---|
| `PATH ...` | `/classes /documents` | pages to open, in order |
| `--as EMAIL` | `somchai.p@kmitl.ac.th` (Instructor) | demo account from the "log in as" list on `/login`, e.g. `admin@kmitl.ac.th` |
| `--base-url URL` | `http://127.0.0.1:8000` | server to walk |
| `--out DIR` | `playwright/out` | where screenshots and videos go |
| `--upload-pdf` | off | upload a sample PDF on `/documents` (T-06) after the walk |

Output goes to `playwright/out/`, which is git-ignored:

- `desktop-<page>.png` and `mobile-<page>.png`: one full-page screenshot per page
- `desktop-documents-after-upload.png` and `mobile-…`: with `--upload-pdf`
- `video-desktop/*.webm` and `video-mobile/*.webm`: the whole walk

What to look at:

- Desktop: rows that should sit side by side are not stacked. If they are, the
  Tailwind classes are probably missing from `app.css`; rebuild it (see "Styles").
- Mobile: nothing overflows sideways and no button is cut off.
- Colours, cards, badges and the page header match the other pages.

`-I` runs Python isolated from the repo folder, so the script sees only the
installed `playwright` package. More detail: `playwright/README.md`.

## Project Structure

```text
src/questly/
├── main.py             # composition root: builds adapters and services, mounts routers
├── config.py           # settings read from the environment
├── api_errors.py       # the JSON error envelope shared by every route
├── core/               # slices: topics (reference), auth, classrooms, assignments,
│                       #   generation, submissions, uploads, notifications, admin
├── ai/                 # AI module, no routes of its own: vector storage (app/),
│                       #   ingestion/, embeddings/, retrieval/, client.py
├── integrations/       # adapters for outside services (Judge0, email, generation jobs)
├── database/           # SQL adapters (core/, rag/), R2 storage (storage/), health checks
└── web/                # Jinja templates and static assets

alembic/                # one migration history for both schemas
scripts/                # demo mode, seeding, integration-test runner, task scope
tests/                  # unit, integration, architecture, contracts, fakes, db, r2
```

## Data Model Reference

Reference documentation for the project's database layer — the *current
state* of the code. For who may change it, see `AGENTS.md` § "Who may change
database and locked files".

### 1. Actual stack

**One ORM, not two** — a common misconception.

```text
┌─────────────────────────────────────────┐
│  SQLModel                               │  ← where we write models
│  (Field, Relationship, SQLModel)        │
├─────────────────────────────────────────┤
│  SQLAlchemy Core + ORM                  │  ← what actually talks to the DB
│  (Column, BigInteger, ForeignKey, ...)  │
├─────────────────────────────────────────┤
│  psycopg v3                             │  ← driver
├─────────────────────────────────────────┤
│  PostgreSQL (Neon) + pgvector extension │
└─────────────────────────────────────────┘
```

| Piece | What it is | Why it's needed |
|---|---|---|
| **SQLModel** | Thin layer wrapping SQLAlchemy + Pydantic | Shorter models, free validation |
| **SQLAlchemy** | The real ORM/Core | SQLModel doesn't wrap everything; missing pieces are called directly |
| **pgvector** | **Not an ORM** — extension + column type | Lets SQLAlchemy recognize the `VECTOR(768)` type |
| **psycopg v3** | Database driver | Sends the actual SQL to Postgres |

Why code imports from `sqlalchemy` directly:

```python
from sqlmodel import Field, Relationship, SQLModel       # top layer
from sqlalchemy import Column, BigInteger, ForeignKey    # what SQLModel doesn't wrap
from sqlalchemy.dialects.postgresql import JSONB         # Postgres-specific type
from pgvector.sqlalchemy import Vector                   # type from the extension
```

Not two ORMs mixed together — one stack, different layers.

**Consequence for `.env`:** since SQLAlchemy (not SQLModel) reads the
connection string, the driver must be `postgresql+psycopg://`, not the plain
`postgresql://` Neon copies by default — otherwise it looks for `psycopg2`
(the old driver), which isn't installed.

### 2. Folder structure

```text
src/questly/
├── core/                        ← modules of the app; never import an ORM or `ai`
│   ├── topics/                  ✅ reference slice — copy its shape
│   │   ├── models.py            dataclass(frozen=True, slots=True)
│   │   ├── ports.py             typing.Protocol
│   │   ├── service.py           pure sync, receives the repo via constructor
│   │   └── routes.py            sync def, pulls the service from app.state
│   └── assignments/ …           same shape; its SQL adapter lives in database/core/
│
├── database/                    ← พาย only; the only place allowed to import an ORM
│   │                              (see AGENTS.md § "Who may change database and locked files")
│   ├── __init__.py              imports tables from both schemas (matters for alembic)
│   ├── session.py               engine + session factory (sync)
│   ├── core/
│   │   ├── tables.py            SQLModel — schema `core`
│   │   └── *_repository.py      one SQL adapter per core port
│   ├── rag/
│   │   ├── tables.py            SQLModel — schema `rag`
│   │   └── vector_repository.py SQL adapter for ai/app/repository.py
│   └── storage/r2.py            object storage adapter
│
├── ai/                          ← AI module of the same app; reached through ports, no routes
└── web/                         ← Jinja2 templates

alembic/                         ← at repo root, by convention
├── env.py                       target_metadata = SQLModel.metadata
│                                include_schemas=True
│                                uses DATABASE_URL_UNPOOLED
└── versions/
```

Hard rule — import direction:

```text
core/assignments/models.py      (dataclass)
core/assignments/ports.py       (Protocol)
            ↑ implemented by ↓
database/core/assignment_repository.py   ← tech lead's adapter lives here, and only here
            ↓ uses ↓
database/core/tables.py         (SQLModel)
```

The adapter must live under `database/` because it needs both SQLModel and the
dataclass — this seam must never live on the `core/` side.

The alembic mistake that keeps happening: `--autogenerate` only sees table
classes that have **already been imported**. Dropping a file in place isn't
enough — it must be imported in `database/__init__.py`:

```python
from questly.database.core import tables as core_tables  # noqa: F401
from questly.database.rag import tables as rag_tables    # noqa: F401
```

Forget this and it won't error — it silently generates a migration missing
that table, and the next autogenerate run may issue `drop_table` on a table
that genuinely exists in the DB. **Always open and review the migration file
before running it.**

### 3. ERD

> This diagram predates the classroom schema of ADR-0007 and ADR-0008.
> The source of truth is `database/core/tables.py` and `database/rag/tables.py`.

```mermaid
erDiagram
    direction TB

    %% ==================== schema: core ====================

    users {
        BIGSERIAL id PK
        TEXT email UK "indexed"
        TEXT display_name "nullable"
        TEXT role "CHECK: instructor | admin, default instructor"
        TIMESTAMPTZ created_at "default now()"
        TIMESTAMPTZ updated_at "default now(), onupdate"
    }

    knowledge_documents {
        BIGSERIAL id PK
        TEXT r2_object_key UK "natural key, cross-checked when copying"
        TEXT filename
        TEXT content_hash "sha256, prevents duplicate files"
        TEXT status "CHECK: uploaded | ingesting | ready | failed"
        JSONB metadata "topic, difficulty, course"
        BIGINT uploaded_by FK "nullable, ON DELETE SET NULL"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    generation_requests {
        BIGSERIAL id PK
        TEXT prompt
        JSONB filters "topic, difficulty — must match keys in metadata"
        TEXT status "CHECK: pending | generating | completed | failed"
        TEXT error_code "nullable"
        BIGINT requested_by FK "nullable, ON DELETE SET NULL"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    generation_artifacts {
        BIGSERIAL id PK
        BIGINT request_id FK "NOT NULL, ON DELETE CASCADE"
        JSONB draft "title, statement, test_cases — no default"
        JSONB citations "chunk_id, source_id, page, score, text_snapshot"
        TEXT model_provider "nullable"
        TEXT model_name "nullable, e.g. poolside/laguna-s-2.1:free"
        TEXT review_status "CHECK: pending | applied | discarded"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    assignments {
        BIGSERIAL id PK
        BIGINT artifact_id FK,UK "nullable when hand-created, ON DELETE SET NULL"
        TEXT title
        TEXT problem_statement
        TEXT difficulty "CHECK: easy | medium | hard"
        JSONB metadata "topic, language, course"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    test_cases {
        BIGSERIAL id PK
        BIGINT assignment_id FK "NOT NULL, ON DELETE CASCADE"
        TEXT input_data
        TEXT expected_output
        BOOLEAN is_hidden "default false"
        INT order_index "default 0"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    %% ==================== schema: rag ====================

    knowledge_sources {
        BIGSERIAL id PK
        BIGINT core_document_id UK "logical link to core, no real FK"
        TEXT r2_object_key "duplicated from core on purpose, used to verify the link"
        TEXT content_hash
        TEXT status "CHECK: pending | processing | ready | failed"
        TEXT embedding_model "nullable"
        INT embedding_dim "nullable"
        JSONB metadata "GIN index — copied for filtering"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    knowledge_chunks {
        BIGSERIAL id PK
        BIGINT source_id FK "NOT NULL, ON DELETE CASCADE"
        INT chunk_index "order within source, indexed with source_id"
        INT page "nullable"
        TEXT text
        INT token_count "nullable, bounds context budget"
        TEXT content_hash "UNIQUE together with source_id"
        TEXT embedding_model "duplicated from source on purpose, prevents mixed-version vectors"
        JSONB metadata "question_no, content_type"
        VECTOR embedding "768 dims, nullable — HNSW index hand-written in migration"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    %% ==================== relationships ====================

    users ||--o{ knowledge_documents : "uploads"
    users ||--o{ generation_requests : "creates"
    generation_requests ||--o{ generation_artifacts : "produces"
    generation_artifacts ||--o| assignments : "applied to"
    assignments ||--o{ test_cases : "has"
    knowledge_sources ||--o{ knowledge_chunks : "split into"
    knowledge_documents ||..o| knowledge_sources : "cross-schema copy (no real FK)"
```

### 4. What the ORM can't create — hand-write these in migrations

**4.1 Before the first migration:**

```sql
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS rag;
CREATE EXTENSION IF NOT EXISTS vector;
```

**4.2 HNSW index (the most important one)** — autogenerate does not create
this, write it by hand in `op.execute()`:

```sql
CREATE INDEX ON rag.knowledge_chunks
USING hnsw (embedding vector_cosine_ops);
```

Without it, every query is a sequential scan. Invisible at low row counts,
but slow immediately once chunks reach the tens of thousands.

**4.3 Cross-schema cleanup has no cascade.** Deleting
`core.knowledge_documents` does **not** cascade to `rag.knowledge_sources` +
`knowledge_chunks`, because there is no real FK between them. Cleanup must be
handled at the application level (Core calls the AI module's delete use case
through its Python interface) — otherwise orphaned chunks stay retrievable
even after the source file has been deleted.

### 5. Decisions already closed

| Topic | Decision | Effect on code |
|---|---|---|
| `assignments.artifact_id` UNIQUE | **Yes, keep it** | `unique=True` on the Column + `uselist=False` on the relationship |
| Alembic history | **Core and rag share one history** — not split into separate repos, single node | `database/__init__.py` must import tables from both sides |
| Teammate's DB | **Same database** | Requires an explicit migration agreement (see 5.1) |

**5.1 Sharing one database — what still needs agreeing.** Two teammates
sharing one database is fine for an MVP, but three cases will come up if they
aren't discussed beforehand:

1. **`alembic downgrade` affects the other person immediately.** `alembic/`
   and `database/` belong to the tech lead alone (AGENTS.md § "Who may change
   database and locked files"), so only one person runs downgrade — but they
   must announce it first, or the teammate's app breaks mid-session for no
   visible reason.
2. **Test data collides.** If the teammate writes CRUD and inserts/deletes
   test assignments, that data mixes with ours. Easiest fix: agree a
   convention (e.g. a title prefix for test data), or use a separate Neon
   branch just for running tests.
3. **If we decide to split later**, Neon's **branch** feature can copy the
   whole database in seconds (copy-on-write, no extra storage cost). If
   collisions become frequent, giving the teammate their own branch takes
   minutes — no need to decide now.

Both choices are recorded: one Alembic history in ADR-0004, one repo and one
app in ADR-0005.

### 6. Still open

| Topic | Status |
|---|---|
| Embedding model | Decided: `google/gemini-embedding-001` through OpenRouter at 768 dimensions (ADR-0009). `VECTOR(768)` is still a one-way door — changing dimensions means migrating the table and re-embedding every chunk |

## Technology Stack

- Python 3.12+
- FastAPI and Uvicorn
- SQLModel, SQLAlchemy, and Alembic
- Neon PostgreSQL with pgvector
- OpenRouter (`openrouter` SDK) for embeddings and generation
- Cloudflare R2 with Boto3
- Jinja2
- Pytest and Ruff
- `uv` for Python and dependency management
