"""R2ObjectStorage speaks the ObjectStorage port against a stubbed S3 client."""

from io import BytesIO

import boto3
import pytest
from botocore.config import Config
from botocore.response import StreamingBody
from botocore.stub import Stubber

from questly.core.documents.models import StoredObject
from questly.core.documents.ports import ObjectStorageError
from questly.database.storage.r2 import R2ObjectStorage

BUCKET = "questly-test"


@pytest.fixture()
def stubbed() -> tuple[R2ObjectStorage, Stubber]:
    client = boto3.client(
        "s3",
        region_name="auto",
        endpoint_url="http://r2.invalid",
        aws_access_key_id="x",
        aws_secret_access_key="x",
        config=Config(signature_version="s3v4"),
    )
    stubber = Stubber(client)
    stubber.activate()
    return R2ObjectStorage(client, BUCKET), stubber


def test_put_reports_where_the_bytes_landed(
    stubbed: tuple[R2ObjectStorage, Stubber],
) -> None:
    storage, stubber = stubbed
    stubber.add_response(
        "put_object",
        {},
        {"Bucket": BUCKET, "Key": "uploads/a.pdf", "Body": b"data"},
    )

    assert storage.put("uploads/a.pdf", b"data") == StoredObject(
        BUCKET, "uploads/a.pdf"
    )
    stubber.assert_no_pending_responses()


def test_get_returns_the_stored_bytes(
    stubbed: tuple[R2ObjectStorage, Stubber],
) -> None:
    storage, stubber = stubbed
    stubber.add_response(
        "get_object",
        {"Body": StreamingBody(BytesIO(b"data"), 4)},
        {"Bucket": BUCKET, "Key": "uploads/a.pdf"},
    )

    assert storage.get("uploads/a.pdf") == b"data"


def test_delete_removes_the_object(stubbed: tuple[R2ObjectStorage, Stubber]) -> None:
    storage, stubber = stubbed
    stubber.add_response(
        "delete_object", {}, {"Bucket": BUCKET, "Key": "uploads/a.pdf"}
    )

    storage.delete("uploads/a.pdf")

    stubber.assert_no_pending_responses()


@pytest.mark.parametrize("operation", ["put", "get", "delete"])
def test_client_failures_become_object_storage_errors(
    stubbed: tuple[R2ObjectStorage, Stubber], operation: str
) -> None:
    """The service restores a deleted object only when it sees this error type."""
    storage, stubber = stubbed
    stubber.add_client_error(f"{operation}_object", "InternalError")

    with pytest.raises(ObjectStorageError):
        if operation == "put":
            storage.put("uploads/a.pdf", b"data")
        elif operation == "get":
            storage.get("uploads/a.pdf")
        else:
            storage.delete("uploads/a.pdf")
