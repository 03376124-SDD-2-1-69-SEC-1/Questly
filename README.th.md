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

**ภาษา:** [English](README.md) | ภาษาไทย

Questly คือแอป FastAPI + Jinja2 สำหรับวิชาเขียนโปรแกรม ผู้สอนร่างโจทย์และ
ชุดทดสอบโดยมี AI ช่วยจาก PDF เอกสารประกอบการสอนของตัวเอง แล้ว publish
ไปยัง Classroom และนักศึกษาส่งโค้ดเพื่อให้ระบบตรวจ

**สถาปัตยกรรม** แอป FastAPI ตัวเดียวใน repo เดียว (modular monolith) โดย
`core/` และ `ai/` เป็นโมดูลของแอปนี้ เรียกกันผ่าน Python interface
(`typing.Protocol`) ไม่มีการเรียกผ่านเครือข่ายระหว่างกัน และ `ai/` ไม่มี route
ของตัวเอง ข้อมูลอยู่ใน Neon Postgres project เดียว แยก schema `core` กับ `rag`
(pgvector, ไม่มี foreign key ข้าม schema) ส่วนไฟล์ PDF อยู่ใน Cloudflare R2
embedding และ generation เรียกผ่าน OpenRouter (ADR-0009) กฎอยู่ใน `AGENTS.md`
flow และการตัดสินใจอยู่ใน `docs/adr/0007-classroom-centric-flow.md` และสถานะ
ปัจจุบันของแต่ละส่วนอยู่ในตาราง Status ของ `AGENTS.md`

## เริ่มต้นใช้งาน

### 1. Clone Repository

```bash
git clone https://github.com/03376124-SDD-2-1-69-SEC-1/PyJudge.git
cd PyJudge
```

หากระบบไม่รู้จักคำสั่ง `git` ให้ติดตั้งตามหัวข้อถัดไป แล้วจึงรันคำสั่ง clone
อีกครั้ง

### 2. ตรวจสอบเครื่องมือที่จำเป็น

โปรเจกต์ต้องใช้ Git, `uv` และ Python 3.12 ขึ้นไป

#### macOS

เปิด Terminal แล้วรัน:

```bash
git --version
uv --version
python3 --version
```

ติดตั้งเครื่องมือที่ยังไม่มี:

```bash
# Git (Apple Command Line Tools)
xcode-select --install

# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.12 ที่จัดการโดย uv
uv python install 3.12
```

#### Windows

เปิด PowerShell แล้วรัน:

```powershell
git --version
uv --version
py --version
```

ติดตั้งเครื่องมือที่ยังไม่มีด้วย WinGet:

```powershell
# Git
winget install --id Git.Git -e --source winget

# uv
winget install --id astral-sh.uv -e

# Python 3.12 ที่จัดการโดย uv
uv python install 3.12
```

หลังติดตั้งเครื่องมือให้เปิด Terminal หรือ PowerShell ใหม่ แล้วตรวจสอบเวอร์ชัน
อีกครั้ง สามารถดูวิธีติดตั้งทางเลือกได้จากหน้าอย่างเป็นทางการของ
[Git](https://git-scm.com/install/) และ
[`uv`](https://docs.astral.sh/uv/getting-started/installation/)

### 3. ติดตั้ง Dependencies

รันคำสั่งนี้ภายในไดเรกทอรีของโปรเจกต์:

```bash
uv sync
```

`uv` จะสร้าง virtual environment และติดตั้ง dependencies ตาม lockfile
โดยไม่จำเป็นต้อง activate environment เมื่อใช้คำสั่ง `uv run`

### 4. ตั้งค่า Environment

สร้างไฟล์ `.env` จากไฟล์ตัวอย่าง

บน macOS:

```bash
cp .env.example .env
```

บน Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

เปิดไฟล์ `.env` แล้วกรอก credentials ที่ได้รับจากทีม ปัจจุบันแอปต้องใช้
การเชื่อมต่อ Neon PostgreSQL ใน `DATABASE_URL` และค่า Cloudflare R2 ต่อไปนี้:

```dotenv
DATABASE_URL=postgresql+psycopg://user:password@host/database?sslmode=require
R2_ENDPOINT_URL=
R2_BUCKET_NAME=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
BASE_URL=https://questly.example.com
```

ตั้ง `BASE_URL` เป็น origin สาธารณะหลักของแอป (ไม่ต้องมี `/` ปิดท้าย)
ลิงก์ยืนยันในอีเมลจะใช้ค่านี้สร้างเป็น `{BASE_URL}/verify?token=...`
โดยไม่อ่าน `Host` header จาก request; ค่าเริ่มต้นสำหรับ development คือ
`http://localhost:8000`

กำหนด `OPENROUTER_API_KEY` เพื่อเรียก OpenRouter สำหรับ generation และ embedding
(ADR-0009) ถ้าเว้นว่างแอปจะใช้ stub generation client แทน ส่วน `GENERATION_MODEL`
และ `EMBEDDING_MODEL` ใช้เลือกโมเดล ค่า default อยู่ใน `src/questly/config.py`
ห้าม commit ไฟล์ `.env` หรือใส่ credentials จริงลงใน `.env.example`

### 5. รัน Database Migrations

```bash
uv run --env-file .env alembic upgrade head
```

### 6. เริ่ม Development Server

```bash
uv run --env-file .env uvicorn questly.main:app --reload
```

เปิด <http://127.0.0.1:8000> ในเบราว์เซอร์ หยุดเซิร์ฟเวอร์ด้วย `Ctrl+C`

## URL ที่ใช้งานบ่อย

| URL | รายละเอียด |
|---|---|
| <http://127.0.0.1:8000/> | หน้าแรก |
| <http://127.0.0.1:8000/docs> | เอกสาร API แบบโต้ตอบ |
| <http://127.0.0.1:8000/api/v1/topics> | Topics API |
| <http://127.0.0.1:8000/health> | สถานะแอปพลิเคชัน |
| <http://127.0.0.1:8000/health/db> | สถานะ Neon PostgreSQL |
| <http://127.0.0.1:8000/health/r2> | สถานะ Cloudflare R2 |

## คำสั่งสำหรับพัฒนา

รันชุดทดสอบ:

```bash
uv run --env-file .env pytest
```

ตรวจสอบ lint และรูปแบบโค้ด:

```bash
uv run ruff check .
uv run ruff format --check .
```

สร้าง migration หลังแก้ไข database models:

```bash
uv run --env-file .env alembic revision --autogenerate -m "describe the change"
```

## โครงสร้างโปรเจกต์

```text
src/questly/
├── main.py             # composition root: สร้าง adapter กับ service แล้ว mount router
├── config.py           # ค่าตั้งที่อ่านจาก environment
├── api_errors.py       # รูปแบบ error JSON ที่ทุก route ใช้ร่วมกัน
├── core/               # slice: topics (ตัวอ้างอิง), auth, classrooms, assignments,
│                       #   generation, submissions, uploads, notifications, admin
├── ai/                 # โมดูล AI ไม่มี route ของตัวเอง: vector storage (app/),
│                       #   ingestion/, embeddings/, retrieval/, client.py
├── integrations/       # adapter ของบริการภายนอก (Judge0, email, generation jobs)
├── database/           # SQL adapter (core/, rag/), R2 (storage/), health check
└── web/                # Jinja templates และ static assets

alembic/                # migration history เดียวสำหรับทั้งสอง schema
scripts/                # demo mode, seed, ตัวรัน integration test, task scope
tests/                  # unit, integration, architecture, contracts, fakes, db, r2
```

## เอกสารอ้างอิงโครงสร้างข้อมูล

เอกสารอ้างอิงโครงสร้าง database layer ของโปรเจกต์ — เก็บ *สถานะปัจจุบัน*
ของโค้ด ส่วนใครแก้ไขได้บ้าง ดู `AGENTS.md` หัวข้อ "Who may change database
and locked files"

### 1. Stack ที่ใช้จริง

**ใช้ ORM ตัวเดียว ไม่ใช่สองตัว** — เป็นความเข้าใจผิดที่เจอบ่อย

```text
┌─────────────────────────────────────────┐
│  SQLModel                               │  ← ที่เราเขียน model
│  (Field, Relationship, SQLModel)        │
├─────────────────────────────────────────┤
│  SQLAlchemy Core + ORM                  │  ← ตัวจริงที่คุยกับ DB
│  (Column, BigInteger, ForeignKey, ...)  │
├─────────────────────────────────────────┤
│  psycopg v3                             │  ← driver
├─────────────────────────────────────────┤
│  PostgreSQL (Neon) + pgvector extension │
└─────────────────────────────────────────┘
```

| ตัว | คืออะไร | ทำไมต้องมี |
|---|---|---|
| **SQLModel** | ชั้นบางๆ ที่ครอบ SQLAlchemy + Pydantic | เขียน model สั้นลง ได้ validation ฟรี |
| **SQLAlchemy** | ORM/Core ตัวจริง | SQLModel ไม่ได้ห่อทุกอย่าง ของที่ขาดต้องเรียกตรง |
| **pgvector** | **ไม่ใช่ ORM** — extension + column type | ให้ SQLAlchemy รู้จักชนิด `VECTOR(768)` |
| **psycopg v3** | database driver | ตัวส่ง SQL จริงไป Postgres |

ทำไมโค้ดถึง import จาก sqlalchemy ตรงๆ:

```python
from sqlmodel import Field, Relationship, SQLModel       # ชั้นบน
from sqlalchemy import Column, BigInteger, ForeignKey    # ของที่ SQLModel ไม่ได้ห่อ
from sqlalchemy.dialects.postgresql import JSONB         # ชนิดเฉพาะ Postgres
from pgvector.sqlalchemy import Vector                   # ชนิดจาก extension
```

ไม่ได้แปลว่าใช้ ORM ปนกัน — เป็น stack เดียวกันคนละชั้นเท่านั้น

**ผลที่ตามมาใน `.env`:** เพราะตัวที่อ่าน connection string คือ SQLAlchemy
(ไม่ใช่ SQLModel) จึงต้องระบุ driver เป็น `postgresql+psycopg://`
ไม่ใช่ `postgresql://` ที่ Neon ก็อปมาให้ — ไม่งั้นมันจะไปหา `psycopg2`
(ตัวเก่า) ที่ไม่ได้ติดตั้งไว้

### 2. โครงสร้างโฟลเดอร์

```text
src/questly/
├── core/                        ← โมดูลของแอป ห้าม import ORM และห้าม import `ai`
│   ├── topics/                  ✅ reference slice — ทำตามรูปนี้
│   │   ├── models.py            dataclass(frozen=True, slots=True)
│   │   ├── ports.py             typing.Protocol
│   │   ├── service.py           sync ล้วน รับ repo ผ่าน constructor
│   │   └── routes.py            sync def, ดึง service จาก app.state
│   └── assignments/ …           รูปเดียวกัน SQL adapter อยู่ใน database/core/
│
├── database/                    ← ของพายคนเดียว ที่เดียวที่ import ORM ได้
│   │                              (ดู AGENTS.md หัวข้อ "Who may change database and locked files")
│   ├── __init__.py              import tables ทั้ง 2 schema (สำคัญกับ alembic)
│   ├── session.py               engine + session factory (sync)
│   ├── core/
│   │   ├── tables.py            SQLModel — schema `core`
│   │   └── *_repository.py      SQL adapter หนึ่งตัวต่อหนึ่ง port ของ core
│   ├── rag/
│   │   ├── tables.py            SQLModel — schema `rag`
│   │   └── vector_repository.py SQL adapter ของ ai/app/repository.py
│   └── storage/r2.py            adapter ของ object storage
│
├── ai/                          ← โมดูล AI ในแอปเดียวกัน เรียกผ่าน port ไม่มี route
└── web/                         ← Jinja2 templates

alembic/                         ← root ตาม convention
├── env.py                       target_metadata = SQLModel.metadata
│                                include_schemas=True
│                                ใช้ DATABASE_URL_UNPOOLED
└── versions/
```

กฎเหล็ก: ทิศทางการ import

```text
core/assignments/models.py      (dataclass)
core/assignments/ports.py       (Protocol)
            ↑ implement โดย ↓
database/core/assignment_repository.py   ← adapter ของ tech lead อยู่ตรงนี้เท่านั้น
            ↓ ใช้ ↓
database/core/tables.py         (SQLModel)
```

Adapter ต้องอยู่ฝั่ง `database/` เพราะมันต้อง import ทั้ง SQLModel และ dataclass
— จุดเชื่อมนี้ห้ามอยู่ฝั่ง `core/`

เรื่อง alembic ที่พลาดกันบ่อย: `--autogenerate` เห็นเฉพาะ table class ที่
**ถูก import แล้ว** เท่านั้น วางไฟล์ไว้เฉยๆ ไม่พอ ต้อง import ใน
`database/__init__.py`:

```python
from questly.database.core import tables as core_tables  # noqa: F401
from questly.database.rag import tables as rag_tables    # noqa: F401
```

ถ้าลืม มันจะไม่ error — แต่จะ generate migration ที่ขาดตารางไปเงียบๆ
และรอบถัดไปอาจสั่ง `drop_table` ตารางที่มีอยู่จริงใน DB **เปิดไฟล์ migration
ตรวจทุกครั้งก่อนรัน**

### 3. ERD

> แผนภาพนี้เก่ากว่า schema ของ Classroom ตาม ADR-0007 และ ADR-0008
> แหล่งความจริงคือ `database/core/tables.py` และ `database/rag/tables.py`

```mermaid
erDiagram
    direction TB

    %% ==================== schema: core ====================

    users {
        BIGSERIAL id PK
        TEXT email UK "index"
        TEXT display_name "nullable"
        TEXT role "CHECK: instructor | admin, default instructor"
        TIMESTAMPTZ created_at "default now()"
        TIMESTAMPTZ updated_at "default now(), onupdate"
    }

    knowledge_documents {
        BIGSERIAL id PK
        TEXT r2_object_key UK "natural key ใช้ cross-check ตอนคัดลอก"
        TEXT filename
        TEXT content_hash "sha256 กันไฟล์ซ้ำ"
        TEXT status "CHECK: uploaded | ingesting | ready | failed"
        JSONB metadata "topic, difficulty, course"
        BIGINT uploaded_by FK "nullable, ON DELETE SET NULL"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    generation_requests {
        BIGSERIAL id PK
        TEXT prompt
        JSONB filters "topic, difficulty — ต้อง match key ใน metadata"
        TEXT status "CHECK: pending | generating | completed | failed"
        TEXT error_code "nullable"
        BIGINT requested_by FK "nullable, ON DELETE SET NULL"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    generation_artifacts {
        BIGSERIAL id PK
        BIGINT request_id FK "NOT NULL, ON DELETE CASCADE"
        JSONB draft "title, statement, test_cases — ไม่มี default"
        JSONB citations "chunk_id, source_id, page, score, text_snapshot"
        TEXT model_provider "nullable"
        TEXT model_name "nullable, เช่น poolside/laguna-s-2.1:free"
        TEXT review_status "CHECK: pending | applied | discarded"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    assignments {
        BIGSERIAL id PK
        BIGINT artifact_id FK,UK "nullable ถ้าสร้างมือ, ON DELETE SET NULL"
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
        BIGINT core_document_id UK "logical link ไป core ไม่มี FK จริง"
        TEXT r2_object_key "ซ้ำกับ core ตั้งใจ ใช้ verify link"
        TEXT content_hash
        TEXT status "CHECK: pending | processing | ready | failed"
        TEXT embedding_model "nullable"
        INT embedding_dim "nullable"
        JSONB metadata "GIN index — คัดลอกมาเพื่อ filter"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    knowledge_chunks {
        BIGSERIAL id PK
        BIGINT source_id FK "NOT NULL, ON DELETE CASCADE"
        INT chunk_index "ลำดับใน source, index คู่กับ source_id"
        INT page "nullable"
        TEXT text
        INT token_count "nullable, คุม context budget"
        TEXT content_hash "UNIQUE คู่กับ source_id"
        TEXT embedding_model "ซ้ำจาก source ตั้งใจ กัน vector ปนรุ่น"
        JSONB metadata "question_no, content_type"
        VECTOR embedding "768 มิติ, nullable — HNSW เขียนมือใน migration"
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
    knowledge_documents ||..o| knowledge_sources : "คัดลอกข้าม schema (ไม่มี FK จริง)"
```

### 4. สิ่งที่ ORM สร้างให้ไม่ได้ — ต้องเขียนมือใน migration

**4.1 ก่อน migration แรก:**

```sql
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS rag;
CREATE EXTENSION IF NOT EXISTS vector;
```

**4.2 HNSW index (สำคัญที่สุด)** — autogenerate ไม่สร้างให้
ต้องเขียนเองใน `op.execute()`:

```sql
CREATE INDEX ON rag.knowledge_chunks
USING hnsw (embedding vector_cosine_ops);
```

ไม่มี → sequential scan ทั้งตาราง ตอนข้อมูลน้อยไม่รู้สึก
แต่หลักหมื่น chunk ขึ้นไปจะช้าทันที

**4.3 Cross-schema cleanup ไม่มี cascade.** ลบ `core.knowledge_documents`
แล้ว `rag.knowledge_sources` + `knowledge_chunks` **ไม่หายตาม**
เพราะไม่มี FK จริง ต้องเขียน cleanup ระดับ application (Core เรียก
use case ลบของโมดูล AI ผ่าน Python interface) ไม่งั้นจะมี orphan chunk
ที่ยัง retrieve เจอ ทั้งที่ไฟล์ต้นฉบับถูกลบแล้ว

### 5. Decision ที่ปิดแล้ว

| เรื่อง | สรุป | ผลกับโค้ด |
|---|---|---|
| `assignments.artifact_id` UNIQUE | **ใส่** | `unique=True` บน Column + `uselist=False` บน relationship |
| Alembic history | **รวม core+rag ไว้ด้วยกัน** ไม่แยก repo, single node | `database/__init__.py` ต้อง import tables ทั้ง 2 ฝั่ง |
| DB ของ teammate | **ใช้ตัวเดียวกัน** | ต้องมีข้อตกลงเรื่อง migration (ดู 5.1) |

**5.1 ใช้ DB ตัวเดียวกัน — สิ่งที่ต้องตกลงเพิ่ม.** ทีม 2 คนใช้ database
เดียวกันไม่ผิดสำหรับ MVP แต่มี 3 เคสที่จะเจอแน่ๆ ถ้าไม่คุยกันก่อน:

1. **`alembic downgrade` กระทบอีกคนทันที** — `alembic/` และ `database/` เป็นของ
   tech lead คนเดียว (AGENTS.md หัวข้อ "Who may change database and locked
   files") ดังนั้นคนรัน downgrade จะมีแค่เราคนเดียว — แต่ต้องบอกก่อนรัน
   ไม่งั้นแอปฝั่ง teammate จะพังกลางคันโดยเขาไม่รู้สาเหตุ
2. **Test data ปนกัน** — ถ้า teammate เขียน CRUD แล้วลอง insert/delete
   assignment ข้อมูลจะปนกับของเรา ทางแก้ง่ายสุดคือตกลง convention เช่น
   ใส่ prefix ในชื่อ title ตอนเทส หรือใช้ Neon branch แยกเฉพาะตอนรัน test
3. **ถ้าเปลี่ยนใจอยากแยกทีหลัง** — Neon มีฟีเจอร์ **branch** ที่ copy
   database ทั้งก้อนได้ในไม่กี่วินาที (copy-on-write ไม่เปลืองที่)
   ถ้าวันไหนเริ่มชนกันบ่อย แยก branch ให้ teammate ใช้เวลาไม่กี่นาที
   ไม่ต้องตัดสินใจตอนนี้

ทั้งสองข้อบันทึกแล้ว: migration history เดียวใน ADR-0004 และ repo กับแอปเดียวใน
ADR-0005

### 6. ที่ยังเปิดอยู่

| เรื่อง | สถานะ |
|---|---|
| Embedding model | ตัดสินแล้ว: `google/gemini-embedding-001` ผ่าน OpenRouter ที่ 768 มิติ (ADR-0009) `VECTOR(768)` ยังเป็น one-way door เปลี่ยนมิติ = migrate ทั้งตารางและ embed ทุก chunk ใหม่ |

## เทคโนโลยีที่ใช้

- Python 3.12+
- FastAPI และ Uvicorn
- SQLModel, SQLAlchemy และ Alembic
- Neon PostgreSQL และ pgvector
- OpenRouter (SDK `openrouter`) สำหรับ embedding และ generation
- Cloudflare R2 และ Boto3
- Jinja2
- Pytest และ Ruff
- `uv` สำหรับจัดการ Python และ dependencies
