"""GENERATION_MODEL_ALLOWLIST, JUDGE0_* and BASE_URL parsing in get_settings()."""

from collections.abc import Generator

import pytest

from questly import config


@pytest.fixture(autouse=True)
def required_environment(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    config.get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("GENERATION_MODEL", "configured/model")
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
