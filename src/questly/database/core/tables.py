"""SQLModel ORM สำหรับ Postgres schema `core`

Source of truth: docs/HANDOFF-data-modeling.md §5 (ERD) และ §6 (เหตุผลแต่ละ field)

Decision ที่ตกลงแล้ว (§4) ที่ไฟล์นี้ยึดตาม:
- PK = BIGSERIAL (int) ไม่ใช่ UUID
- Sync ล้วน ไม่มี async engine/session ในไฟล์นี้ (อยู่ที่ database/session.py)
- ไม่มี FK ข้าม schema ไป rag เลย (ดู database/rag/tables.py คนละไฟล์)

กฎเหล็ก (database/README.md): "อย่าให้ Core service import ORM หรือ storage
client โดยตรง" — ไฟล์นี้ถูก import ได้เฉพาะจาก database/core/*_repository.py
(adapter ที่แปลง ORM row ↔ dataclass ใน core/assignments/models.py) เท่านั้น
ห้าม import ตรงจาก src/questly/core/*
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, Relationship, SQLModel

# No `from __future__ import annotations` here on purpose: SQLModel's
# Relationship() resolves its related class from the *runtime* annotation
# object, not from SQLAlchemy's Mapped[]-based declarative typing. Postponed
# evaluation turns every annotation into an unparsed string and breaks that
# resolution (confirmed: it fails identically whether the annotation is
# `list[X]` or `Mapped[list["X"]]`). A relationship pointing at a class defined
# later in this file is written as a quoted forward reference instead, e.g.
# `list["KnowledgeDocument"]` — the old PEP 484 style, not Mapped[].

SCHEMA = "core"


# ---------------------------------------------------------------------------
# Column helpers — เขียนเป็นฟังก์ชัน (ไม่ใช่ constant) เพราะ Column object
# ผูกกับตารางเดียวได้ตารางเดียว ต้อง instantiate ใหม่ทุกครั้งที่เรียกใช้
# ---------------------------------------------------------------------------


def _pk() -> int | None:
    """BIGSERIAL PK ตาม §4 (int ไม่ใช่ UUID)"""
    return Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )


def _created_at() -> datetime:
    return Field(
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now()
        ),
    )


def _updated_at() -> datetime:
    return Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),  # ทำงานเฉพาะตอน UPDATE ผ่าน SQLAlchemy session
            # เท่านั้น ถ้ามีใครแก้แถวด้วย raw SQL ตรงๆ ต้องอัปเดตคอลัมน์นี้เอง
        ),
    )


def _fk(
    target: str, *, ondelete: str, nullable: bool = False, unique: bool = False
) -> int:
    """BIGINT foreign key into another `core` table."""
    return Field(
        default=None if nullable else ...,
        sa_column=Column(
            BigInteger,
            ForeignKey(f"{SCHEMA}.{target}", ondelete=ondelete),
            nullable=nullable,
            unique=unique,
        ),
    )


def _timestamp(*, nullable: bool, server_now: bool = False) -> datetime:
    return Field(
        default=None if nullable else ...,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=nullable,
            server_default=func.now() if server_now else None,
        ),
    )


def _jsonb(default_sql: str) -> dict:
    return Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text(default_sql)),
    )


def _bigint_array() -> list[int]:
    return Field(
        default_factory=list,
        sa_column=Column(
            ARRAY(BigInteger), nullable=False, server_default=text("'{}'::bigint[]")
        ),
    )


def _metadata_jsonb() -> dict:
    """คอลัมน์ JSONB ชื่อ `metadata` ใน DB — Python attribute ต้องชื่อ
    `metadata_` เพราะ `metadata` เป็นชื่อ reserved ของ SQLModel/SQLAlchemy
    เอง (ใช้เก็บ MetaData registry ของทุก model class)"""
    return Field(
        default_factory=dict,
        sa_column=Column(
            "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
        ),
    )


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('student', 'instructor', 'admin')", name="ck_users_role"
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    email: str = Field(nullable=False, unique=True, index=True)
    full_name: str = Field(sa_column=Column(String(120), nullable=False))
    password_hash: str = Field(sa_column=Column(Text, nullable=False))
    role: str = Field(
        sa_column=Column(String, nullable=False, server_default="student")
    )
    email_verified_at: datetime | None = _timestamp(nullable=True)
    is_active: bool = Field(
        sa_column=Column(Boolean, nullable=False, server_default=text("true"))
    )
    last_active_at: datetime | None = _timestamp(nullable=True)
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()

    documents: list["KnowledgeDocument"] = Relationship(back_populates="uploader")
    generation_requests: list["GenerationRequest"] = Relationship(
        back_populates="requester"
    )


class KnowledgeDocument(SQLModel, table=True):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded', 'ingesting', 'ready', 'failed')",
            name="ck_knowledge_documents_status",
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    # natural key ใช้ cross-check ตอน mirror ไป rag.knowledge_sources
    r2_object_key: str = Field(nullable=False, unique=True, index=True)
    filename: str = Field(nullable=False)
    content_hash: str = Field(nullable=False)  # sha256 กันไฟล์ซ้ำ
    status: str = Field(nullable=False, default="uploaded")
    metadata_: dict = _metadata_jsonb()  # topic, difficulty, course
    # Nullable until CORE-16 gives the domain an uploader (ADR-0008).
    uploaded_by: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    page_count: int | None = Field(default=None)
    progress: int = Field(
        sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    error_code: str | None = Field(default=None)
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()

    uploader: User | None = Relationship(back_populates="documents")


class GenerationRequest(SQLModel, table=True):
    __tablename__ = "generation_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'generating', 'completed', 'failed')",
            name="ck_generation_requests_status",
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    prompt: str = Field(nullable=False)
    filters: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
        # topic, difficulty — ต้อง match key ใน knowledge_documents.metadata
    )
    status: str = Field(nullable=False, default="pending")
    error_code: str | None = Field(default=None)
    # ADR-0007 columns; nothing writes this table since Drafts got their own
    # (ADR-0008).
    requested_by: int = _fk("users.id", ondelete="RESTRICT")
    classroom_id: int = _fk("classrooms.id", ondelete="CASCADE")
    document_ids: list[int] = _bigint_array()
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()

    requester: User = Relationship(back_populates="generation_requests")
    artifacts: list["GenerationArtifact"] = Relationship(back_populates="request")


class GenerationArtifact(SQLModel, table=True):
    __tablename__ = "generation_artifacts"
    __table_args__ = (
        CheckConstraint(
            "review_status IN ('pending', 'applied', 'discarded')",
            name="ck_generation_artifacts_review_status",
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    request_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey(f"{SCHEMA}.generation_requests.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    draft: dict = Field(
        sa_column=Column(JSONB, nullable=False)
        # title, statement, test_cases — ไม่มี default ตั้งใจ ต้องส่งมาตอน insert เสมอ
    )
    citations: list[dict] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
        # แต่ละ item: {chunk_id, source_id, page, score, text_snapshot}
    )
    model_provider: str | None = Field(default=None)
    model_name: str | None = Field(default=None)  # เช่น "poolside/laguna-s-2.1:free"
    review_status: str = Field(nullable=False, default="pending")
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()

    request: GenerationRequest | None = Relationship(back_populates="artifacts")
    # uselist=False ระบุชัดว่าเป็น one-to-one — SQLAlchemy เดาเองได้จาก
    # unique=True บน assignments.artifact_id อยู่แล้ว แต่เขียนไว้กันพลาด
    # ถ้าวันหลังมีคนถอด unique ออก จะได้ error ตรงจุดแทนที่จะเงียบๆ
    # กลายเป็น list
    assignment: Optional["Assignment"] = Relationship(
        back_populates="artifact",
        sa_relationship_kwargs={"uselist": False},
    )


class Assignment(SQLModel, table=True):
    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name="ck_assignments_difficulty",
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    # NULL ได้ ถ้าสร้างมือผ่าน CRUD ปกติ (ไม่ผ่าน AI approval flow)
    #
    # unique=True บังคับความสัมพันธ์ 1 artifact : 0-หรือ-1 assignment ตาม ERD
    # (`||--o|`) กัน apply artifact เดิมซ้ำจนได้ assignment ซ้ำ
    #
    # ไม่ชนกับเคส manual CRUD เพราะ Postgres ถือว่า NULL แต่ละตัว "ไม่เท่ากัน"
    # ในการเช็ค UNIQUE — จึงมีแถวที่ artifact_id IS NULL ได้ไม่จำกัด
    artifact_id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey(f"{SCHEMA}.generation_artifacts.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
    )
    title: str = Field(nullable=False)
    problem_statement: str = Field(nullable=False)
    difficulty: str = Field(nullable=False)
    metadata_: dict = _metadata_jsonb()
    topic_id: int | None = _fk("topics.id", ondelete="SET NULL", nullable=True)
    owner_id: int | None = _fk("users.id", ondelete="SET NULL", nullable=True)
    current_version: int = Field(
        sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    # Judging settings; each Version keeps its own copy (ADR-0008).
    time_limit_ms: int = Field(
        sa_column=Column(Integer, nullable=False, server_default=text("1000"))
    )
    language: str = Field(
        sa_column=Column(String, nullable=False, server_default="python3")
    )
    show_hidden_names: bool = Field(
        sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()

    artifact: GenerationArtifact | None = Relationship(back_populates="assignment")
    test_cases: list["TestCase"] = Relationship(
        back_populates="assignment",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class TestCase(SQLModel, table=True):
    __tablename__ = "test_cases"
    __table_args__ = (
        # เปิดใช้ถ้าอยากกันลำดับชนกันในโจทย์เดียวกัน (§7.4 บอกว่ายังไม่ตกลง
        # ว่าต้องการไหม — default คือปิดไว้ก่อน):
        # UniqueConstraint(
        #     "assignment_id", "order_index", name="uq_test_cases_assignment_order"
        # ),
        CheckConstraint(
            "kind IN ('sample', 'hidden', 'edge')", name="ck_test_cases_kind"
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    assignment_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey(f"{SCHEMA}.assignments.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    input_data: str = Field(nullable=False)
    expected_output: str = Field(nullable=False)
    kind: str = Field(sa_column=Column(String, nullable=False, server_default="sample"))
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    order_index: int = Field(nullable=False, default=0)
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()

    assignment: Assignment | None = Relationship(back_populates="test_cases")


class Topic(SQLModel, table=True):
    """หัวข้อที่ใช้จัดหมวด Assignment (OPS-12)

    ไม่มี FK จาก assignments มาที่นี่ — ตอนนี้ assignments.metadata เก็บ topic
    เป็นค่าใน JSONB ตาม §6 ยังไม่ได้ตกลงว่าจะผูกเป็นความสัมพันธ์จริง
    """

    __tablename__ = "topics"
    __table_args__ = (
        # unique แบบ case-insensitive: กฎเดิมใน TopicService เทียบด้วย casefold
        # อยู่แล้ว เอามาเป็น index ของ DB เพื่อให้ adapter ทั้งสองตัวตอบเหมือนกัน
        # และให้ find_by_name ใช้ index ได้ ไม่ต้องโหลดทุกแถว
        Index("uq_topics_name_lower", text("lower(name)"), unique=True),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    name: str = Field(nullable=False)
    description: str | None = Field(default=None)
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()


# ---------------------------------------------------------------------------
# OPS-15: ADR-0007 "Schema changes" as amended by ADR-0008
# ---------------------------------------------------------------------------


class EmailVerificationToken(SQLModel, table=True):
    __tablename__ = "email_verification_tokens"
    __table_args__ = ({"schema": SCHEMA},)

    id: int | None = _pk()
    user_id: int = _fk("users.id", ondelete="CASCADE")
    token_hash: str = Field(nullable=False, unique=True)
    expires_at: datetime = _timestamp(nullable=False)
    used_at: datetime | None = _timestamp(nullable=True)
    created_at: datetime = _created_at()


class UserSession(SQLModel, table=True):
    """A login Session; named so it does not shadow `sqlmodel.Session`."""

    __tablename__ = "sessions"
    __table_args__ = ({"schema": SCHEMA},)

    id: int | None = _pk()
    user_id: int = _fk("users.id", ondelete="CASCADE")
    token_hash: str = Field(nullable=False, unique=True)
    csrf_token: str = Field(nullable=False)
    expires_at: datetime = _timestamp(nullable=False)
    revoked_at: datetime | None = _timestamp(nullable=True)
    created_at: datetime = _created_at()


class InstructorRequestRow(SQLModel, table=True):
    __tablename__ = "instructor_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_instructor_requests_status",
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    user_id: int = _fk("users.id", ondelete="CASCADE")
    faculty: str = Field(nullable=False)
    status: str = Field(
        sa_column=Column(String, nullable=False, server_default="pending")
    )
    requested_at: datetime = _timestamp(nullable=False, server_now=True)
    reviewed_by: int | None = _fk("users.id", ondelete="SET NULL", nullable=True)
    reviewed_at: datetime | None = _timestamp(nullable=True)
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class Classroom(SQLModel, table=True):
    __tablename__ = "classrooms"
    __table_args__ = ({"schema": SCHEMA},)

    id: int | None = _pk()
    instructor_id: int = _fk("users.id", ondelete="RESTRICT")
    course_code: str = Field(nullable=False)
    course_name: str = Field(nullable=False)
    section: str = Field(nullable=False)
    semester: str = Field(nullable=False)
    # NULL once join codes are turned off; Postgres lets NULLs repeat under UNIQUE.
    join_code: str | None = Field(default=None, unique=True, nullable=True)
    archived_at: datetime | None = _timestamp(nullable=True)
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()


class ClassroomMember(SQLModel, table=True):
    __tablename__ = "classroom_members"
    __table_args__ = (
        UniqueConstraint(
            "classroom_id", "student_id", name="uq_classroom_members_pair"
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    classroom_id: int = _fk("classrooms.id", ondelete="CASCADE")
    student_id: int = _fk("users.id", ondelete="CASCADE")
    joined_at: datetime = _timestamp(nullable=False)


class AssignmentVersionRow(SQLModel, table=True):
    __tablename__ = "assignment_versions"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "number", name="uq_assignment_versions_number"
        ),
        CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name="ck_assignment_versions_difficulty",
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    assignment_id: int = _fk("assignments.id", ondelete="CASCADE")
    number: int = Field(nullable=False)
    title: str = Field(nullable=False)
    problem_statement: str = Field(nullable=False)
    difficulty: str = Field(nullable=False)
    # [{input_data, expected_output, kind, note, order_index}], immutable.
    test_cases: list[dict] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    time_limit_ms: int = Field(nullable=False)
    language: str = Field(nullable=False)
    show_hidden_names: bool = Field(nullable=False)
    reason: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = _created_at()


class Posting(SQLModel, table=True):
    """An Assignment posted to a Classroom (CONTEXT.md "Posting")."""

    __tablename__ = "classroom_assignments"
    __table_args__ = (
        UniqueConstraint(
            "classroom_id", "assignment_id", name="uq_classroom_assignments_pair"
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    classroom_id: int = _fk("classrooms.id", ondelete="CASCADE")
    assignment_id: int = _fk("assignments.id", ondelete="CASCADE")
    deadline: datetime = _timestamp(nullable=False)
    max_score: int = Field(nullable=False)
    allow_late: bool = Field(nullable=False)
    allow_resubmission: bool = Field(nullable=False)
    published_at: datetime = _timestamp(nullable=False)
    closed_at: datetime | None = _timestamp(nullable=True)


class SubmissionRow(SQLModel, table=True):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint(
            "posting_id", "student_id", "attempt", name="uq_submissions_attempt"
        ),
        CheckConstraint(
            "status IN ('received', 'judging', 'done', 'failed')",
            name="ck_submissions_status",
        ),
        CheckConstraint(
            "recovery IN ('none', 'reconnected', 'rejudged')",
            name="ck_submissions_recovery",
        ),
        CheckConstraint(
            "(status = 'done') = (score IS NOT NULL)",
            name="ck_submissions_score_when_done",
        ),
        # ADR-0011: one pending Submission per Student and Posting. It also
        # holds exactly the rows `claim_due` reads.
        Index(
            "uq_submissions_one_unfinished",
            "posting_id",
            "student_id",
            unique=True,
            postgresql_where=text("status IN ('received', 'judging')"),
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    # CASCADE: unposting removes the Posting's Submissions (ADR-0008).
    posting_id: int = _fk("classroom_assignments.id", ondelete="CASCADE")
    student_id: int = _fk("users.id", ondelete="CASCADE")
    version_number: int = Field(nullable=False)
    code: str = Field(sa_column=Column(Text, nullable=False))
    language: str = Field(nullable=False)
    submitted_at: datetime = _timestamp(nullable=False)
    is_late: bool = Field(nullable=False)
    score: int | None = Field(default=None, nullable=True)
    max_score: int = Field(nullable=False)
    attempt: int = Field(nullable=False)
    status: str = Field(sa_column=Column(Text, nullable=False))
    recovery: str = Field(
        default="none",
        sa_column=Column(Text, nullable=False, server_default="none"),
    )
    runner_tokens: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
        ),
    )
    # The judging lease (ADR-0011); the domain model never sees it.
    claimed_until: datetime | None = _timestamp(nullable=True)


class SubmissionTestResult(SQLModel, table=True):
    __tablename__ = "submission_test_results"
    __table_args__ = (
        UniqueConstraint(
            "submission_id", "position", name="uq_submission_test_results_position"
        ),
        CheckConstraint(
            "kind IN ('sample', 'hidden', 'edge')",
            name="ck_submission_test_results_kind",
        ),
        CheckConstraint(
            "verdict IN ('passed', 'wrong_answer', 'time_limit', 'runtime_error')",
            name="ck_submission_test_results_verdict",
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    submission_id: int = _fk("submissions.id", ondelete="CASCADE")
    position: int = Field(nullable=False)
    ordinal: int = Field(nullable=False)
    kind: str = Field(nullable=False)
    note: str = Field(sa_column=Column(Text, nullable=False))
    verdict: str = Field(nullable=False)
    time_seconds: float = Field(sa_column=Column(Float, nullable=False))
    expected_output: str = Field(sa_column=Column(Text, nullable=False))
    actual_output: str = Field(sa_column=Column(Text, nullable=False))
    error: str = Field(sa_column=Column(Text, nullable=False))


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"
    __table_args__ = ({"schema": SCHEMA},)

    id: int | None = _pk()
    user_id: int = _fk("users.id", ondelete="CASCADE")
    kind: str = Field(nullable=False)  # CORE-18 defines the kinds
    payload: dict = _jsonb("'{}'::jsonb")
    created_at: datetime = _created_at()
    read_at: datetime | None = _timestamp(nullable=True)


class DraftRow(SQLModel, table=True):
    __tablename__ = "drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reviewing', 'published')", name="ck_drafts_status"
        ),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    classroom_id: int = _fk("classrooms.id", ondelete="CASCADE")
    requested_by: int = _fk("users.id", ondelete="CASCADE")
    prompt: str = Field(sa_column=Column(Text, nullable=False))
    # Read and written whole by the T-04 stepper, never queried by field.
    content: dict = _jsonb("'{}'::jsonb")
    citations: list[dict] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    settings: dict = _jsonb("'{}'::jsonb")
    document_ids: list[int] = _bigint_array()
    classroom_ids: list[int] = _bigint_array()
    status: str = Field(
        sa_column=Column(String, nullable=False, server_default="reviewing")
    )
    assignment_id: int | None = _fk(
        "assignments.id", ondelete="SET NULL", nullable=True
    )
    generated_at: datetime = _timestamp(nullable=False)
    created_at: datetime = _created_at()
    updated_at: datetime = _updated_at()


class GenerationEvent(SQLModel, table=True):
    """One successful generation or regeneration; the daily Quota counts these."""

    __tablename__ = "generation_events"
    __table_args__ = (
        Index("ix_generation_events_user_occurred", "user_id", "occurred_at"),
        {"schema": SCHEMA},
    )

    id: int | None = _pk()
    user_id: int = _fk("users.id", ondelete="CASCADE")
    occurred_at: datetime = _timestamp(nullable=False)


class AiSettings(SQLModel, table=True):
    """The one row of Admin AI settings. API keys and base URLs stay in the
    environment; this stores only which provider and model (ADR-0008)."""

    __tablename__ = "ai_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_ai_settings_single_row"),
        CheckConstraint("daily_quota > 0", name="ck_ai_settings_daily_quota"),
        CheckConstraint("max_pages > 0", name="ck_ai_settings_max_pages"),
        {"schema": SCHEMA},
    )

    id: int = Field(
        default=1,
        sa_column=Column(BigInteger, primary_key=True, server_default=text("1")),
    )
    provider: str = Field(nullable=False)
    model: str = Field(nullable=False)
    daily_quota: int = Field(nullable=False)
    max_pages: int = Field(nullable=False)
    require_citations: bool = Field(nullable=False)
    updated_at: datetime = _updated_at()
