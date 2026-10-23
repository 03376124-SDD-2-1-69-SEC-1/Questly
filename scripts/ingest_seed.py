"""Preflight or ingest the 100 registered seed PDFs.

Run from the repository root (read-only):

    uv run --env-file .env python scripts/ingest_seed.py \
        --owner-email instructor@kmitl.ac.th

The default preflight reads Core, RAG, and R2 without writing. Execution must
be explicitly requested with --execute and is only appropriate after the
owner resolves every preflight failure and approves changes to shared data.
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session

from questly.ai.embeddings.openrouter import create_openrouter_embedding_provider
from questly.ai.ingestion.pdf_extractor import PyMuPDFPageExtractor
from questly.ai.ingestion.pipeline import PdfIngestionService
from questly.ai.ingestion.seed import (
    SeedIngestionError,
    SeedItem,
    SeedPreflightError,
    SeedStoredChunk,
    SeedStoredSource,
    ingest_prepared,
    inspect_existing,
    prepare_seed,
)
from questly.config import MissingConfigurationError, get_settings
from questly.core.auth.models import Role
from questly.database.core.auth_repository import SQLAuthRepository
from questly.database.core.knowledge_document_repository import (
    SQLKnowledgeDocumentRepository,
)
from questly.database.rag.tables import KnowledgeChunk, KnowledgeSource
from questly.database.rag.vector_repository import PostgresVectorRepository
from questly.database.session import build_engine, build_session_factory
from questly.database.storage.r2 import R2ObjectStorage, build_r2_client

MANIFEST = Path(__file__).resolve().parent / "seed" / "pith-100-manifest.csv"
EXPECTED_SEED_COUNT = 100
SEED_PREFIX = "sources/programming-in-th/"


class SQLSeedSourceInspector:
    """Read the fields needed to verify an existing RAG source and its chunks."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_by_core_document_id(self, core_document_id: int) -> SeedStoredSource | None:
        with self._session_factory() as session:
            source = session.exec(
                select(KnowledgeSource).where(
                    KnowledgeSource.core_document_id == core_document_id
                )
            ).one_or_none()
            if source is None or source.id is None:
                return None
            chunks = session.exec(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.source_id == source.id)
                .order_by(KnowledgeChunk.chunk_index)
            ).all()
            return SeedStoredSource(
                core_document_id=source.core_document_id,
                r2_object_key=source.r2_object_key,
                content_hash=source.content_hash,
                status=source.status,
                embedding_model=source.embedding_model,
                embedding_dim=source.embedding_dim,
                metadata=source.metadata_,
                chunks=tuple(
                    SeedStoredChunk(
                        chunk_index=chunk.chunk_index,
                        page=chunk.page,
                        embedding_model=chunk.embedding_model,
                        has_embedding=chunk.embedding is not None,
                        task_id=chunk.metadata_.get("task_id"),
                    )
                    for chunk in chunks
                ),
            )


def load_manifest(path: Path = MANIFEST) -> tuple[SeedItem, ...]:
    """Accept exactly the intended 100 unique, labeled seed objects."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"task_id", "r2_key", "topic", "difficulty"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise SeedPreflightError("manifest is missing required columns")
        items = tuple(
            SeedItem(
                task_id=row["task_id"].strip(),
                r2_key=row["r2_key"].strip(),
                topic=row["topic"].strip(),
                difficulty=row["difficulty"].strip(),
            )
            for row in reader
        )
    if len(items) != EXPECTED_SEED_COUNT:
        raise SeedPreflightError(
            f"manifest has {len(items)} entries, expected {EXPECTED_SEED_COUNT}"
        )
    if len({item.task_id for item in items}) != len(items):
        raise SeedPreflightError("manifest has duplicate task IDs")
    if len({item.r2_key for item in items}) != len(items):
        raise SeedPreflightError("manifest has duplicate R2 keys")
    for item in items:
        if (
            not item.task_id
            or not item.topic
            or item.difficulty
            not in {
                "easy",
                "medium",
                "hard",
            }
        ):
            raise SeedPreflightError(f"{item.task_id!r}: incomplete seed labels")
        if item.r2_key != f"{SEED_PREFIX}{item.task_id}.pdf":
            raise SeedPreflightError(f"{item.task_id}: unexpected R2 key")
    return items


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--execute", action="store_true", help="write verified seed embeddings"
    )
    args = parser.parse_args(argv)

    if args.env_file is not None:
        if not args.env_file.is_file():
            parser.error("--env-file does not exist")
        load_dotenv(args.env_file)

    try:
        items = load_manifest()
        settings = get_settings()
        session_factory = build_session_factory(build_engine(settings))
        owner = SQLAuthRepository(session_factory).find_user_by_email(args.owner_email)
        if owner is None or owner.role is not Role.INSTRUCTOR:
            raise SeedPreflightError("owner must be an existing Instructor")
        documents = SQLKnowledgeDocumentRepository(session_factory)
        storage = R2ObjectStorage(build_r2_client(settings), settings.r2_bucket_name)
        prepared = prepare_seed(
            items, owner_id=owner.id, documents=documents, storage=storage
        )
        inspector = SQLSeedSourceInspector(session_factory)
        existing = inspect_existing(
            prepared, inspector, embedding_model=settings.embedding_model
        )
        if args.execute:
            embedder = create_openrouter_embedding_provider(
                settings.openrouter_api_key, settings.embedding_model
            )
            ingester = PdfIngestionService(
                PyMuPDFPageExtractor(),
                embedder,
                PostgresVectorRepository(session_factory),
            )
            summary = ingest_prepared(
                prepared,
                owner_id=owner.id,
                documents=documents,
                inspector=inspector,
                ingester=ingester,
                embedding_model=settings.embedding_model,
            )
    except (MissingConfigurationError, SeedPreflightError, SeedIngestionError) as error:
        parser.exit(2, f"preflight failed: {error}\n")

    if args.execute:
        print(
            f"ingestion complete: {summary.created} created, "
            f"{summary.existing} verified"
        )
    else:
        print(
            f"preflight passed: {len(prepared)} PDFs matched owned Core documents; "
            f"{existing} already ingested; no writes performed"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
