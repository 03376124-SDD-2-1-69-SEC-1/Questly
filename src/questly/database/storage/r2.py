"""R2 (S3-compatible) adapter for the ObjectStorage port.

Per database/README.md rule 5, this module — and everything under `database/` —
is the only place allowed to build the storage client directly. `core/*` modules
only ever see the `ObjectStorage` Protocol and a plain dataclass.

Nothing here runs at import time: `main.py` builds the client from `Settings`
and puts the adapter on `app.state`.
"""

from __future__ import annotations

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from questly.config import Settings
from questly.core.documents.models import StoredObject
from questly.core.documents.ports import ObjectStorageError


def build_r2_client(settings: Settings) -> BaseClient:
    """Return a boto3 S3 client pointed at the configured R2 bucket."""
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


class R2ObjectStorage:
    """Store uploaded bytes in a Cloudflare R2 bucket."""

    def __init__(self, client: BaseClient, bucket: str) -> None:
        """Initialize the adapter with a client and the bucket it writes to."""
        self._client = client
        self._bucket = bucket

    def put(self, key: str, data: bytes) -> StoredObject:
        """Store `data` under `key` and report where it landed."""
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError from error
        return StoredObject(bucket=self._bucket, key=key)

    def get(self, key: str) -> bytes:
        """Return the bytes stored under `key`."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError from error

    def delete(self, key: str) -> None:
        """Remove the object under `key`; S3 treats a missing key as success."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError from error


def check_r2(client: BaseClient, bucket: str) -> dict:
    """Confirm the R2 bucket is reachable."""
    client.head_bucket(Bucket=bucket)
    return {"connected": True, "bucket": bucket}
