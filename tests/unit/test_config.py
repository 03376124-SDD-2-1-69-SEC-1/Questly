"""GENERATION_MODEL_ALLOWLIST, JUDGE0_* and BASE_URL parsing in get_settings()."""

from collections.abc import Generator

import pytest

from questly import config

EMAIL_VARIABLES = (
    "EMAIL_BACKEND",
    "EMAIL_FROM",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_SECURITY",
    "RESEND_API_KEY",
    "APP_ENV",
)


@pytest.fixture(autouse=True)
def required_environment(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    config.get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("GENERATION_MODEL", "configured/model")
    # Blank rather than unset: load_dotenv() would refill an unset variable
    # from a developer's .env.
    for variable in EMAIL_VARIABLES:
        monkeypatch.setenv(variable, "")
    yield
    config.get_settings.cache_clear()


def test_unset_allowlist_allows_only_the_generation_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_MODEL_ALLOWLIST", "")

    assert config.get_settings().generation_model_allowlist == ("configured/model",)


def test_default_allowlist_is_the_ops_26_comparison_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env at all: A-01 gets the OPS-26 measured allowlist (ADR-0012)."""
    monkeypatch.delenv("GENERATION_MODEL", raising=False)
    monkeypatch.delenv("GENERATION_MODEL_ALLOWLIST", raising=False)

    settings = config.get_settings()

    assert settings.generation_model == config.DEFAULT_GENERATION_MODEL
    assert settings.generation_model_allowlist == config.DEFAULT_GENERATION_ALLOWLIST
    # the model every generation actually uses stays pickable on A-01
    assert settings.generation_model in settings.generation_model_allowlist


def test_allowlist_is_comma_separated_and_trimmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_MODEL_ALLOWLIST", " a/one , b/two:free,, ")

    assert config.get_settings().generation_model_allowlist == ("a/one", "b/two:free")


def test_judge0_is_off_when_its_url_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDGE0_URL", "  ")
    monkeypatch.setenv("JUDGE0_AUTH_TOKEN", "")

    settings = config.get_settings()

    assert (settings.judge0_url, settings.judge0_auth_token) == ("", "")


def test_judge0_url_and_token_are_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDGE0_URL", " http://judge0:2358 ")
    monkeypatch.setenv("JUDGE0_AUTH_TOKEN", " token ")

    settings = config.get_settings()

    assert (settings.judge0_url, settings.judge0_auth_token) == (
        "http://judge0:2358",
        "token",
    )


def test_base_url_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASE_URL", raising=False)

    assert config.get_settings().base_url == "http://localhost:8000"


def test_base_url_is_trimmed_and_trailing_slash_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BASE_URL", " https://questly.example.com/ ")

    assert config.get_settings().base_url == "https://questly.example.com"


def test_production_without_base_url_fails_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("BASE_URL", raising=False)

    with pytest.raises(config.MissingConfigurationError, match="BASE_URL"):
        config.get_settings()


def test_production_with_blank_base_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", " Production ")
    monkeypatch.setenv("BASE_URL", "   ")

    with pytest.raises(config.MissingConfigurationError, match="BASE_URL"):
        config.get_settings()


def test_production_accepts_a_configured_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BASE_URL", "https://questly.example.com")
    # production refuses the stub (OPS-37), so give it a real backend
    monkeypatch.setenv("EMAIL_BACKEND", "resend")
    monkeypatch.setenv("EMAIL_FROM", "noreply@questly.example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")

    assert config.get_settings().base_url == "https://questly.example.com"


@pytest.mark.parametrize("app_env", ["development", "test", ""])
def test_other_environments_default_base_url_to_localhost(
    monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.delenv("BASE_URL", raising=False)

    assert config.get_settings().base_url == "http://localhost:8000"


@pytest.mark.parametrize(
    "value",
    [
        "questly.example.com",
        "localhost:8000",
        "//questly.example.com",
        "ftp://questly.example.com",
        "/classes",
        "https://",
        "http:///path",
        "https://host:abc",
        "https://host:99999",
        "https://a b.com",
        "https://u:p@questly.example.com",
        "https://questly.example.com/?x=1",
        "https://questly.example.com/#top",
    ],
)
@pytest.mark.parametrize("app_env", ["development", "production"])
def test_base_url_without_http_scheme_and_host_is_rejected(
    monkeypatch: pytest.MonkeyPatch, app_env: str, value: str
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("BASE_URL", value)

    with pytest.raises(config.MissingConfigurationError, match="BASE_URL"):
        config.get_settings()


@pytest.mark.parametrize(
    "value", ["http://localhost:8000", "https://questly.example.com/"]
)
def test_base_url_with_http_or_https_scheme_is_accepted(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("BASE_URL", value)

    assert config.get_settings().base_url == value.rstrip("/")


def _smtp_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("EMAIL_FROM", "Questly <noreply@questly.example.com>")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USERNAME", "mailer")
    monkeypatch.setenv("SMTP_PASSWORD", "s3cret-smtp-password")


def test_email_backend_defaults_to_the_stub_outside_production() -> None:
    settings = config.get_settings()

    assert settings.email_backend == "stub"
    assert settings.app_env == ""


def test_email_backend_is_case_insensitive_and_trimmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_BACKEND", " Resend ")
    monkeypatch.setenv("EMAIL_FROM", "noreply@questly.example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_key")

    assert config.get_settings().email_backend == "resend"


def test_unknown_email_backend_names_the_allowed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_BACKEND", "sendgrid")

    with pytest.raises(config.MissingConfigurationError) as raised:
        config.get_settings()

    message = str(raised.value)
    assert "EMAIL_BACKEND" in message
    for allowed in ("stub", "smtp", "resend"):
        assert allowed in message


def test_smtp_reads_every_variable_and_keeps_the_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _smtp_environment(monkeypatch)
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_SECURITY", "SSL")

    settings = config.get_settings()

    assert settings.email_backend == "smtp"
    assert settings.email_from == "Questly <noreply@questly.example.com>"
    assert (settings.smtp_host, settings.smtp_port, settings.smtp_security) == (
        "smtp.example.com",
        2525,
        "ssl",
    )
    assert settings.smtp_username == "mailer"
    assert settings.smtp_password == "s3cret-smtp-password"


@pytest.mark.parametrize(
    ("security", "port"), [("", 587), ("starttls", 587), ("ssl", 465)]
)
def test_smtp_port_defaults_follow_the_security_mode(
    monkeypatch: pytest.MonkeyPatch, security: str, port: int
) -> None:
    _smtp_environment(monkeypatch)
    monkeypatch.setenv("SMTP_SECURITY", security)

    settings = config.get_settings()

    assert settings.smtp_port == port
    assert settings.smtp_security == (security or "starttls")


def test_smtp_lists_every_missing_variable_and_no_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")

    with pytest.raises(config.MissingConfigurationError) as raised:
        config.get_settings()

    message = str(raised.value)
    for name in ("EMAIL_FROM", "SMTP_USERNAME", "SMTP_PASSWORD"):
        assert name in message
    assert "SMTP_HOST" not in message
    assert "smtp.example.com" not in message


def test_resend_lists_every_missing_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_BACKEND", "resend")

    with pytest.raises(config.MissingConfigurationError) as raised:
        config.get_settings()

    message = str(raised.value)
    assert "EMAIL_FROM" in message
    assert "RESEND_API_KEY" in message


@pytest.mark.parametrize("security", ["tls", "none", "STARTTLS-ish"])
def test_invalid_smtp_security_is_rejected(
    monkeypatch: pytest.MonkeyPatch, security: str
) -> None:
    _smtp_environment(monkeypatch)
    monkeypatch.setenv("SMTP_SECURITY", security)

    with pytest.raises(config.MissingConfigurationError, match="SMTP_SECURITY"):
        config.get_settings()


@pytest.mark.parametrize("port", ["abc", "0", "65536", "-1", "58.7", "٤٦٥"])
def test_invalid_smtp_port_is_rejected(
    monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    _smtp_environment(monkeypatch)
    monkeypatch.setenv("SMTP_PORT", port)

    with pytest.raises(config.MissingConfigurationError, match="SMTP_PORT"):
        config.get_settings()


@pytest.mark.parametrize("backend", [None, "", "  ", "stub", "STUB"])
def test_production_refuses_a_missing_or_stub_email_backend(
    monkeypatch: pytest.MonkeyPatch, backend: str | None
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BASE_URL", "https://questly.example.com")
    if backend is not None:
        monkeypatch.setenv("EMAIL_BACKEND", backend)

    with pytest.raises(config.MissingConfigurationError, match="EMAIL_BACKEND"):
        config.get_settings()


def test_production_accepts_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BASE_URL", "https://questly.example.com")
    _smtp_environment(monkeypatch)

    settings = config.get_settings()

    assert (settings.app_env, settings.email_backend) == ("production", "smtp")


def test_secrets_never_appear_in_the_settings_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _smtp_environment(monkeypatch)
    monkeypatch.setenv("RESEND_API_KEY", "re_the-api-key")

    shown = repr(config.get_settings())

    assert "s3cret-smtp-password" not in shown
    assert "re_the-api-key" not in shown
    assert "smtp.example.com" in shown
