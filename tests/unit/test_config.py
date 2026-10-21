"""GENERATION_MODEL_ALLOWLIST and JUDGE0_* parsing in get_settings()."""

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


def test_base_url_defaults_to_localhost() -> None:
    assert config.get_settings().base_url == "http://localhost:8000"


def test_base_url_is_trimmed_and_trailing_slash_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BASE_URL", " https://questly.example.com/ ")

    assert config.get_settings().base_url == "https://questly.example.com"
