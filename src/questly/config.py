"""The one place Questly reads its environment.

Every other module takes a `Settings` instance instead of touching `os.environ`,
so importing `questly.main` never depends on a populated `.env` and a test can
build an app without one. `get_settings()` reads the environment when it is
called, not when this module is imported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit

from dotenv import load_dotenv

DEFAULT_MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024

REQUIRED_DRIVER = "postgresql+psycopg"

DEFAULT_BASE_URL = "http://localhost:8000"
PRODUCTION = "production"

EMAIL_BACKENDS = ("stub", "smtp", "resend")
SMTP_SECURITIES = ("ssl", "starttls")
DEFAULT_SMTP_PORTS = {"ssl": 465, "starttls": 587}


class MissingConfigurationError(RuntimeError):
    """Raised when a required environment variable is absent or unusable."""


DEFAULT_GENERATION_MODEL = "poolside/laguna-s-2.1:free"
# OPS-26 (ADR-0012) measured six `:free` models on three Thai seed problems;
# laguna-s was the only one that parsed every reply and whose solution ran
# (exit 0) on every one of its own inputs. laguna-xs and nemotron-3-super
# below were also measured, and scored worse — why they are here anyway, and
# the lighter options the team may still pick, are in the ADR's
# "Open question: allowlist membership". Re-run
# scripts/compare_generation_models.py when OpenRouter's free tier changes.
# Do not add other providers here: ADR-0009.
DEFAULT_GENERATION_ALLOWLIST = (
    DEFAULT_GENERATION_MODEL,
    "poolside/laguna-xs-2.1:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
)
# Changing it means re-embedding every chunk, and the replacement must return
# 768 dimensions (VECTOR(768) in `rag`).
DEFAULT_EMBEDDING_MODEL = "google/gemini-embedding-001"


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything Questly needs from its environment, resolved once."""

    database_url: str
    r2_endpoint_url: str
    r2_bucket_name: str
    r2_access_key_id: str
    r2_secret_access_key: str
    max_upload_size_bytes: int = DEFAULT_MAX_UPLOAD_SIZE_BYTES
    # OpenRouter — optional: empty string means demo/stub mode
    openrouter_api_key: str = ""
    generation_model: str = DEFAULT_GENERATION_MODEL
    # Models an Admin may pick on A-01; the default comes from the OPS-26
    # comparison (ADR-0012) and always contains generation_model.
    generation_model_allowlist: tuple[str, ...] = DEFAULT_GENERATION_ALLOWLIST
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    # Judge0 CE: empty URL means no Judge0, and Run/Submit use the stub runner.
    judge0_url: str = ""
    judge0_auth_token: str = ""
    # Public origin that email links are built on:
    # {base_url}/verify and {base_url}/classes/...
    base_url: str = DEFAULT_BASE_URL
    app_env: str = ""
    # Outgoing email (OPS-37): "stub" logs instead of sending.
    email_backend: str = "stub"
    email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = DEFAULT_SMTP_PORTS["starttls"]
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    smtp_security: str = "starttls"
    resend_api_key: str = field(default="", repr=False)


def _required(variable: str) -> str:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise MissingConfigurationError(
            f"{variable} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def _upload_size_limit() -> int:
    raw = os.environ.get("MAX_UPLOAD_SIZE_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_UPLOAD_SIZE_BYTES
    if not raw.isdigit() or int(raw) <= 0:
        raise MissingConfigurationError(
            f"MAX_UPLOAD_SIZE_BYTES must be a positive integer, got {raw!r}."
        )
    return int(raw)


def _database_url() -> str:
    """Return the DSN, refusing anything SQLAlchemy would resolve to psycopg2.

    Only psycopg v3 is a project dependency, and a bare `postgresql://` URL
    fails later with an unhelpful `NoSuchModuleError`.
    """
    url = _required("DATABASE_URL")
    scheme, separator, _ = url.partition("://")
    if not separator:
        raise MissingConfigurationError("DATABASE_URL is not a database URL.")
    if scheme != REQUIRED_DRIVER:
        raise MissingConfigurationError(
            f"DATABASE_URL must use {REQUIRED_DRIVER}://, got {scheme}://"
        )
    return url


def _is_http_origin(value: str) -> bool:
    """True for http(s)://host[:port][/path] with no userinfo, query or fragment."""
    if any(character.isspace() for character in value):
        return False
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.username is not None or parts.query or parts.fragment:
        return False
    try:
        _ = parts.port  # raises ValueError on a non-numeric or out-of-range port
    except ValueError:
        return False
    return True


def _base_url() -> str:
    """Return the public origin for email links, without a trailing slash.

    Development and tests fall back to localhost. Production has no safe
    default, so an unset BASE_URL stops the app at startup rather than mailing
    links that point at localhost. A value without an http(s) scheme and a
    host would produce relative or broken links, so it is refused everywhere.
    """
    raw = os.environ.get("BASE_URL", "").strip()
    if not raw:
        app_env = os.environ.get("APP_ENV", "").strip().lower()
        if app_env == PRODUCTION:
            raise MissingConfigurationError(
                "BASE_URL is not set. It is required when APP_ENV=production: "
                "set it to the app's public origin, e.g. https://questly.example.com."
            )
        return DEFAULT_BASE_URL
    if not _is_http_origin(raw):
        raise MissingConfigurationError(
            "BASE_URL must start with http:// or https:// and name a host, "
            f"got {raw!r}."
        )
    return raw.rstrip("/")


def _app_env() -> str:
    return os.environ.get("APP_ENV", "").strip().lower()


@dataclass(frozen=True, slots=True)
class _EmailEnvironment:
    backend: str
    sender: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_security: str
    resend_api_key: str


def _env(variable: str) -> str:
    return os.environ.get(variable, "").strip()


def _email_environment() -> _EmailEnvironment:
    """Resolve EMAIL_BACKEND and the variables the chosen backend needs.

    Production refuses the stub, which only logs: an unset EMAIL_BACKEND there
    stops the app at startup. A backend with missing variables raises once,
    naming every missing variable and never a value.
    """
    production = _app_env() == PRODUCTION
    backend = _env("EMAIL_BACKEND").lower()
    if not backend:
        if production:
            raise MissingConfigurationError(
                "EMAIL_BACKEND is not set. It is required when APP_ENV=production: "
                "set it to smtp or resend."
            )
        backend = "stub"
    if backend not in EMAIL_BACKENDS:
        raise MissingConfigurationError(
            f"EMAIL_BACKEND must be one of {', '.join(EMAIL_BACKENDS)}, "
            f"got {backend!r}."
        )
    if backend == "stub" and production:
        raise MissingConfigurationError(
            "EMAIL_BACKEND=stub only logs emails and is not allowed when "
            "APP_ENV=production: set EMAIL_BACKEND to smtp or resend."
        )

    required = {
        "stub": (),
        "smtp": ("EMAIL_FROM", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"),
        "resend": ("EMAIL_FROM", "RESEND_API_KEY"),
    }[backend]
    missing = [variable for variable in required if not _env(variable)]
    if missing:
        raise MissingConfigurationError(
            f"EMAIL_BACKEND={backend} needs {', '.join(missing)}, which "
            f"{'is' if len(missing) == 1 else 'are'} not set."
        )

    security = "starttls"
    port = DEFAULT_SMTP_PORTS[security]
    if backend == "smtp":
        security = _env("SMTP_SECURITY").lower() or security
        if security not in SMTP_SECURITIES:
            raise MissingConfigurationError(
                f"SMTP_SECURITY must be one of {', '.join(SMTP_SECURITIES)}, "
                f"got {security!r}."
            )
        port = DEFAULT_SMTP_PORTS[security]
        raw_port = _env("SMTP_PORT")
        if raw_port:
            if not (raw_port.isascii() and raw_port.isdigit()) or not (
                1 <= int(raw_port) <= 65535
            ):
                raise MissingConfigurationError(
                    f"SMTP_PORT must be a port from 1 to 65535, got {raw_port!r}."
                )
            port = int(raw_port)

    return _EmailEnvironment(
        backend=backend,
        sender=_env("EMAIL_FROM"),
        smtp_host=_env("SMTP_HOST"),
        smtp_port=port,
        smtp_username=_env("SMTP_USERNAME"),
        smtp_password=_env("SMTP_PASSWORD"),
        smtp_security=security,
        resend_api_key=_env("RESEND_API_KEY"),
    )


def _model_allowlist(generation_model: str) -> tuple[str, ...]:
    """GENERATION_MODEL_ALLOWLIST, comma-separated.

    Unset, it falls back to DEFAULT_GENERATION_ALLOWLIST (ADR-0012) when the
    configured model is one of those, and to just generation_model otherwise.
    """
    raw = os.environ.get("GENERATION_MODEL_ALLOWLIST", "")
    models = tuple(model.strip() for model in raw.split(",") if model.strip())
    if not models:
        if generation_model in DEFAULT_GENERATION_ALLOWLIST:
            return DEFAULT_GENERATION_ALLOWLIST
        return (generation_model,)
    return models


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Read the environment once and return the resolved configuration."""
    load_dotenv()
    generation_model = (
        os.environ.get("GENERATION_MODEL", DEFAULT_GENERATION_MODEL).strip()
        or DEFAULT_GENERATION_MODEL
    )
    base_url = _base_url()
    email = _email_environment()
    return Settings(
        database_url=_database_url(),
        r2_endpoint_url=_required("R2_ENDPOINT_URL"),
        r2_bucket_name=_required("R2_BUCKET_NAME"),
        r2_access_key_id=_required("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=_required("R2_SECRET_ACCESS_KEY"),
        max_upload_size_bytes=_upload_size_limit(),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
        generation_model=generation_model,
        generation_model_allowlist=_model_allowlist(generation_model),
        embedding_model=(
            os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
            or DEFAULT_EMBEDDING_MODEL
        ),
        judge0_url=os.environ.get("JUDGE0_URL", "").strip(),
        judge0_auth_token=os.environ.get("JUDGE0_AUTH_TOKEN", "").strip(),
        base_url=base_url,
        app_env=_app_env(),
        email_backend=email.backend,
        email_from=email.sender,
        smtp_host=email.smtp_host,
        smtp_port=email.smtp_port,
        smtp_username=email.smtp_username,
        smtp_password=email.smtp_password,
        smtp_security=email.smtp_security,
        resend_api_key=email.resend_api_key,
    )
