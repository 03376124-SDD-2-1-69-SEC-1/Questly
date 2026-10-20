"""Seed R2 ด้วย PDF โจทย์ 100 ข้อจาก programming.in.th

รายการโจทย์อยู่ที่ scripts/seed/pith-100-manifest.csv

รัน (จาก root ของ repo):
    # ดูก่อนว่าจะอัปไป bucket ไหน กี่ไฟล์
    uv run --env-file .env python scripts/seed_pith_to_r2.py --dry-run
    # อัปจริง
    uv run --env-file .env python scripts/seed_pith_to_r2.py
    # ลงทะเบียนทั้ง 100 ไฟล์ใน core.knowledge_documents ในชื่อ instructor คนนี้
    uv run --env-file .env python scripts/seed_pith_to_r2.py \
        --register-db --owner-email <email>

- ใช้ตัวแปร R2_* ชุดเดียวกับแอป (R2_ENDPOINT_URL, R2_BUCKET_NAME,
  R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)
- PDF ที่โหลดมาเก็บ cache ไว้ที่ data/pith/ (อยู่ใน .gitignore แล้ว) รันซ้ำไม่โหลดใหม่
- object ที่มีอยู่แล้วบน R2 จะข้าม — รันซ้ำได้ปลอดภัย และสคริปต์นี้ไม่ลบอะไรเลย
- ผลลัพธ์ data/pith/uploaded.csv มี r2_key + sha256 + topic/difficulty
  ไว้ insert core.knowledge_documents ตอนทำ ingest
- --register-db อ่าน sha256 จาก data/pith/uploaded.csv; ข้อไหนไม่มีในไฟล์
  จะโหลด object จาก R2 มา hash (อ่านอย่างเดียว ไม่เขียน R2) แถวที่
  content_hash ซ้ำของ owner คนเดียวกันจะข้าม — รันซ้ำไม่เกิดแถวซ้ำ
"""

from __future__ import annotations

import csv
import hashlib
import os
import sys
import time
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path

from questly.config import MissingConfigurationError, get_settings
from questly.core.auth.models import Role
from questly.core.documents.models import KnowledgeDocument
from questly.core.documents.ports import KnowledgeDocumentRepository
from questly.database.core.auth_repository import SQLAuthRepository
from questly.database.core.knowledge_document_repository import (
    SQLKnowledgeDocumentRepository,
)
from questly.database.session import build_engine, build_session_factory
from questly.database.storage.safety import assert_valid_r2_endpoint

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "seed" / "pith-100-manifest.csv"
CACHE_DIR = ROOT / "data" / "pith"
OUT_CSV = CACHE_DIR / "uploaded.csv"
REQUIRED_VARS = (
    "R2_ENDPOINT_URL",
    "R2_BUCKET_NAME",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)
SEED_COURSE = "seed-pith"


def fetch_pdf(url: str, dest: Path) -> bytes:
    if dest.exists():
        return dest.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "questly-seed/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if not resp.headers.get("Content-Type", "").startswith("application/pdf"):
            raise RuntimeError(f"not a PDF: {url}")
        data = resp.read()
    dest.write_bytes(data)
    time.sleep(0.3)  # อย่ายิงเว็บเขารัว
    return data


def object_exists(client, bucket: str, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def connect_r2():
    """Return (client, bucket), or exit when the R2_* variables are unusable."""
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    if missing:
        sys.exit(f"missing env: {', '.join(missing)} (ลืม --env-file .env หรือเปล่า)")
    try:
        assert_valid_r2_endpoint(os.environ["R2_ENDPOINT_URL"])
    except ValueError as error:
        sys.exit(str(error))

    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    return client, os.environ["R2_BUCKET_NAME"]


def register_seed(
    rows: Iterable[Mapping[str, str]],
    repository: KnowledgeDocumentRepository,
    owner_id: int,
) -> tuple[int, int]:
    """Insert one document per manifest row; return (created, skipped)."""
    created = skipped = 0
    for row in rows:
        if repository.get_by_content_hash(row["sha256"], owner_id) is not None:
            skipped += 1
            continue
        repository.create(
            KnowledgeDocument(
                filename=row["title"],
                object_key=row["r2_key"],
                content_hash=row["sha256"],
                metadata={
                    "topic": row["topic"],
                    "difficulty": row["difficulty"],
                    "course": SEED_COURSE,
                    "task_id": row["task_id"],
                },
            ),
            owner_id,
        )
        created += 1
    return created, skipped


def with_hashes(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Attach each row's sha256 from uploaded.csv, else from the R2 object."""
    known: dict[str, str] = {}
    if OUT_CSV.exists():
        with OUT_CSV.open(encoding="utf-8-sig") as f:
            known = {r["task_id"]: r["sha256"] for r in csv.DictReader(f)}
    missing = [r for r in rows if r["task_id"] not in known]
    if missing:
        client, bucket = connect_r2()
        for r in missing:
            body = client.get_object(Bucket=bucket, Key=r["r2_key"])["Body"].read()
            known[r["task_id"]] = hashlib.sha256(body).hexdigest()
    return [{**r, "sha256": known[r["task_id"]]} for r in rows]


def register_main() -> None:
    if "--dry-run" in sys.argv:
        sys.exit("--register-db ไม่มีโหมด --dry-run")
    try:
        email = sys.argv[sys.argv.index("--owner-email") + 1]
    except (ValueError, IndexError):
        sys.exit("--register-db ต้องมี --owner-email <email>")
    try:
        settings = get_settings()
    except MissingConfigurationError as error:
        sys.exit(str(error))
    session_factory = build_session_factory(build_engine(settings))

    owner = SQLAuthRepository(session_factory).find_user_by_email(email)
    if owner is None:
        sys.exit(f"ไม่พบ user {email} ใน core.users")
    if owner.role is not Role.INSTRUCTOR:
        sys.exit(f"{email} เป็น {owner.role.value} ไม่ใช่ instructor")

    rows = with_hashes(list(csv.DictReader(MANIFEST.open(encoding="utf-8-sig"))))
    created, skipped = register_seed(
        rows, SQLKnowledgeDocumentRepository(session_factory), owner.id
    )
    print(f"owner={email}  created={created}  skipped={skipped}")


def main() -> None:
    if "--register-db" in sys.argv:
        register_main()
        return
    dry_run = "--dry-run" in sys.argv
    client, bucket = connect_r2()

    rows = list(csv.DictReader(MANIFEST.open(encoding="utf-8-sig")))
    print(f"bucket={bucket}  files={len(rows)}  dry_run={dry_run}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    uploaded = skipped = 0
    for n, r in enumerate(rows, 1):
        data = fetch_pdf(r["pdf_url"], CACHE_DIR / f"{r['task_id']}.pdf")
        r["sha256"] = hashlib.sha256(data).hexdigest()
        r["size_bytes"] = len(data)

        if object_exists(client, bucket, r["r2_key"]):
            status = "exists"
            skipped += 1
        elif dry_run:
            status = "would upload"
        else:
            client.put_object(
                Bucket=bucket,
                Key=r["r2_key"],
                Body=data,
                ContentType="application/pdf",
                # R2 metadata ต้องเป็น ASCII — ชื่อโจทย์ภาษาไทยเก็บใน DB แทน
                Metadata={
                    "task-id": r["task_id"],
                    "topic": r["topic"],
                    "difficulty": r["difficulty"],
                    "sha256": r["sha256"],
                },
            )
            status = "uploaded"
            uploaded += 1
        print(f"[{n:3}/{len(rows)}] {r['r2_key']:<48} {status}")

    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"uploaded={uploaded} skipped={skipped} -> {OUT_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
