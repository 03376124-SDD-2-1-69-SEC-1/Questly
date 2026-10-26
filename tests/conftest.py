"""Guards for the only tests allowed to reach real external infrastructure.

Tests marked `postgres` connect to a throwaway Neon branch created by
`scripts/run_integration_tests.py` and deleted when the run ends. They read
`POSTGRES_TEST_URL` and nothing else:
`DATABASE_URL` points at the branch the whole team develops against, and these
tests insert and delete rows.

Tests marked `r2` connect to a dedicated `greader-ci` R2 bucket through
`R2_TEST_*`. `R2_BUCKET_NAME` and friends stay dummy in CI on purpose — see
AGENTS.md — so these tests read only the `R2_TEST_*` variables.

Locally the marked tests skip themselves, so `uv run pytest` stays green on a
machine with no test database or bucket. In CI the variables must be present,
otherwise a failed setup step would leave every marked test skipped and the
run green while nothing was verified.

Tests marked `judge0` run real programs on a Judge0 CE instance named by
`JUDGE0_TEST_URL` (and `JUDGE0_TEST_AUTH_TOKEN` when it needs one), never by
the app's `JUDGE0_URL`. CI has no Judge0, so they skip whenever the URL is
unset, in CI as well.
"""

import os
import uuid
from urllib.parse import urlsplit

import boto3
import pytest

from questly.database.storage.safety import assert_safe_prefix, assert_valid_r2_endpoint

TEST_URL_VAR = "POSTGRES_TEST_URL"
SHARED_URL_VARS = ("DATABASE_URL", "DATABASE_URL_UNPOOLED")
REQUIRED_DRIVER = "postgresql+psycopg"

R2_TEST_VARS = (
    "R2_TEST_ENDPOINT_URL",
    "R2_TEST_ACCESS_KEY_ID",
    "R2_TEST_SECRET_ACCESS_KEY",
    "R2_TEST_BUCKET_NAME",
)
R2_TEST_PREFIX_VAR = "R2_TEST_PREFIX"

JUDGE0_TEST_URL_VAR = "JUDGE0_TEST_URL"
JUDGE0_TEST_AUTH_TOKEN_VAR = "JUDGE0_TEST_AUTH_TOKEN"


def _running_in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}


def _with_psycopg_driver(url: str) -> str:
    """Return the DSN with the +psycopg tag the project requires.

    The Neon CLI prints `postgresql://…`, and SQLAlchemy resolves a bare
    `postgresql://` to psycopg2, which is not a project dependency.
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        pytest.fail(f"{TEST_URL_VAR} is not a database URL")
    if scheme == REQUIRED_DRIVER:
        return url
    if scheme in {"postgres", "postgresql"}:
        return f"{REQUIRED_DRIVER}://{rest}"
    pytest.fail(f"{TEST_URL_VAR} must use {REQUIRED_DRIVER}://, got {scheme}://")


def _endpoint(url: str) -> str | None:
    """Identify the compute a DSN points at, ignoring Neon's pooler suffix.

    `ep-x-pooler.region.neon.tech` and `ep-x.region.neon.tech` are two doors
    into one database, so both must compare equal.
    """
    host = urlsplit(url).hostname
    if host is None:
        return None
    label, separator, domain = host.partition(".")
    label = label.removesuffix("-pooler")
    return f"{label}{separator}{domain}"


def _reject_shared_database(url: str) -> None:
    target = _endpoint(url)
    for variable in SHARED_URL_VARS:
        shared = os.environ.get(variable)
        if shared and _endpoint(shared) == target:
            pytest.fail(
                f"{TEST_URL_VAR} points at the same compute as {variable}. "
                "These tests write and delete rows; point it at a throwaway "
                "Neon branch instead."
            )


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Return the DSN of the throwaway database, or fail explaining why not."""
    url = os.environ.get(TEST_URL_VAR)
    if not url:
        pytest.fail(
            f"{TEST_URL_VAR} is unset. In CI this means the Neon branch step "
            "did not run, so no database test verified anything."
        )
    url = _with_psycopg_driver(url)
    _reject_shared_database(url)
    return url


def _r2_config_present() -> bool:
    return all(os.environ.get(variable) for variable in R2_TEST_VARS)


@pytest.fixture(scope="session")
def r2_test_bucket() -> tuple:
    """Return (client, bucket_name) for the dedicated CI bucket, or fail."""
    missing = [variable for variable in R2_TEST_VARS if not os.environ.get(variable)]
    if missing:
        pytest.fail(
            f"{', '.join(missing)} unset. In CI this means the R2 test "
            "credentials were not configured, so no r2 test verified anything."
        )
    bucket = os.environ["R2_TEST_BUCKET_NAME"]
    app_bucket = os.environ.get("R2_BUCKET_NAME")
    if app_bucket and bucket == app_bucket:
        pytest.fail(
            "R2_TEST_BUCKET_NAME is the same as R2_BUCKET_NAME. These tests "
            "write and delete objects; point it at the dedicated greader-ci "
            "bucket instead."
        )
    endpoint = os.environ["R2_TEST_ENDPOINT_URL"]
    try:
        assert_valid_r2_endpoint(endpoint)
    except ValueError as error:
        pytest.fail(f"R2_TEST_ENDPOINT_URL is invalid: {error}")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["R2_TEST_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_TEST_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    return client, bucket


@pytest.fixture()
def r2_test_prefix(r2_test_bucket: tuple):
    """Unique key prefix per test, nested under the run's shared prefix.

    Falls back to a local prefix when `R2_TEST_PREFIX` is unset (CI always
    sets it; a developer running these against the CI bucket by hand may not).
    Deletes everything it created afterwards — the CI cleanup script is a
    safety net for a killed runner, not a substitute for this.
    """
    client, bucket = r2_test_bucket
    base = os.environ.get(R2_TEST_PREFIX_VAR, "").strip()
    if not base:
        base = f"local/{uuid.uuid4().hex}/"
    if not base.endswith("/"):
        base += "/"
    prefix = f"{base}{uuid.uuid4().hex}/"
    try:
        assert_safe_prefix(prefix)
    except ValueError as error:
        pytest.fail(f"{R2_TEST_PREFIX_VAR} produced an unsafe prefix: {error}")

    yield prefix

    assert_safe_prefix(prefix)
    paginator = client.get_paginator("list_objects_v2")
    keys = [
        {"Key": entry["Key"]}
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
        for entry in page.get("Contents", [])
    ]
    if keys:
        client.delete_objects(Bucket=bucket, Delete={"Objects": keys})


@pytest.fixture(scope="session")
def judge0_test_url() -> str:
    """Return the base URL of the Judge0 CE the smoke tests may use, or fail."""
    url = os.environ.get(JUDGE0_TEST_URL_VAR, "").strip()
    if not url:
        pytest.fail(f"{JUDGE0_TEST_URL_VAR} is unset.")
    return url


@pytest.fixture(scope="session")
def judge0_test_auth_token() -> str:
    """The X-Auth-Token for that instance; empty when it needs none."""
    return os.environ.get(JUDGE0_TEST_AUTH_TOKEN_VAR, "").strip()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip database/R2 tests locally; let them run and fail loudly in CI.

    Judge0 tests skip whenever JUDGE0_TEST_URL is unset, CI included.
    """
    skip_postgres = not (os.environ.get(TEST_URL_VAR) or _running_in_ci())
    skip_r2 = not (_r2_config_present() or _running_in_ci())
    skip_judge0 = not os.environ.get(JUDGE0_TEST_URL_VAR, "").strip()
    postgres_skip = pytest.mark.skip(reason=f"needs {TEST_URL_VAR}; see .env.example")
    r2_skip = pytest.mark.skip(reason=f"needs {', '.join(R2_TEST_VARS)}; see AGENTS.md")
    judge0_skip = pytest.mark.skip(reason=f"needs {JUDGE0_TEST_URL_VAR}")
    for item in items:
        if skip_postgres and "postgres" in item.keywords:
            item.add_marker(postgres_skip)
        if skip_r2 and "r2" in item.keywords:
            item.add_marker(r2_skip)
        if skip_judge0 and "judge0" in item.keywords:
            item.add_marker(judge0_skip)
