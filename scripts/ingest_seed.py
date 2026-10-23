"""Preflight the 100 seed PDFs before writing embeddings to RAG.

Run from the repository root (read-only):

    uv run --env-file .env python scripts/ingest_seed.py \
        --owner-email instructor@kmitl.ac.th

The preflight reads the manifest, the registered Core documents, and R2 bytes.
It never changes Core, RAG, or R2. A later execution step will reuse the same
preflight before any writes.
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from questly.ai.ingestion.seed import SeedItem, SeedPreflightError, prepare_seed
from questly.config import MissingConfigurationError, get_settings
from questly.core.auth.models import Role
from questly.database.core.auth_repository import SQLAuthRepository
from questly.database.core.knowledge_document_repository import (
    SQLKnowledgeDocumentRepository,
)
from questly.database.session import build_engine, build_session_factory
from questly.database.storage.r2 import R2ObjectStorage, build_r2_client

MANIFEST = Path(__file__).resolve().parent / "seed" / "pith-100-manifest.csv"
EXPECTED_SEED_COUNT = 100
SEED_PREFIX = "sources/programming-in-th/"


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
    except (MissingConfigurationError, SeedPreflightError) as error:
        parser.exit(2, f"preflight failed: {error}\n")

    print(
        f"preflight passed: {len(prepared)} PDFs matched owned Core documents; "
        "no writes performed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
