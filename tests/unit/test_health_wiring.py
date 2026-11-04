"""The three availability flags create_app hands the health checker, per adapter mix."""

import dataclasses

import pytest

from questly import main
from questly.ai.client import StubGenerationClient
from questly.config import PRODUCTION, Settings
from questly.core.generation.ports import GenerationClient
from questly.integrations.email import StubEmailSender
from questly.integrations.judge0 import StubCodeRunner
from tests.fakes.app import fake_settings
from tests.fakes.generation import FakeGenerationClient, binary_search_response
from tests.fakes.submissions import ScriptedCodeRunner


class Flags:
    """What create_app passed to the checker and to AuthService."""

    def __init__(self) -> None:
        self.health: dict[str, bool] = {}
        self.show_demo_link: bool | None = None


@pytest.fixture()
def flags(monkeypatch: pytest.MonkeyPatch) -> Flags:
    seen = Flags()
    real_auth_service = main.AuthService

    def checker(_session_factory: object, **kwargs: bool) -> object:
        seen.health = kwargs
        return object()

    def auth_service(*args: object, **kwargs: object) -> object:
        seen.show_demo_link = bool(kwargs["show_demo_link"])
        return real_auth_service(*args, **kwargs)

    monkeypatch.setattr(main, "SQLSystemHealthChecker", checker)
    monkeypatch.setattr(main, "AuthService", auth_service)
    return seen


def _build(flags: Flags, settings: Settings, **injected: object) -> Flags:
    main.create_app(settings=settings, **injected)
    return flags


def _settings(**override: object) -> Settings:
    return dataclasses.replace(fake_settings(), **override)


@pytest.mark.parametrize(
    ("injected", "settings", "expected"),
    [
        ({}, {}, False),
        ({"code_runner": StubCodeRunner()}, {}, False),
        ({"code_runner": ScriptedCodeRunner()}, {}, True),
        ({}, {"judge0_url": "http://judge0.test"}, True),
    ],
)
def test_sandbox_flag_follows_the_runner(
    flags: Flags, injected: dict[str, object], settings: dict[str, object], expected: bool
) -> None:
    _build(flags, _settings(**settings), **injected)

    assert flags.health["sandbox_available"] is expected


@pytest.mark.parametrize(
    ("settings", "expected", "demo_link"),
    [
        ({}, False, True),
        ({"app_env": PRODUCTION}, False, False),
        (
            {
                "email_backend": "smtp",
                "smtp_host": "smtp.test",
                "smtp_port": 465,
                "smtp_username": "user",
                "smtp_password": "pass",
                "smtp_security": "ssl",
                "email_from": "no-reply@questly.test",
            },
            True,
            False,
        ),
        (
            {
                "email_backend": "resend",
                "resend_api_key": "re_key",
                "email_from": "no-reply@questly.test",
            },
            True,
            False,
        ),
    ],
)
def test_email_flag_and_demo_link_follow_the_sender(
    flags: Flags, settings: dict[str, object], expected: bool, demo_link: bool
) -> None:
    _build(flags, _settings(**settings))

    assert flags.health["email_available"] is expected
    assert flags.show_demo_link is demo_link


def test_an_injected_stub_sender_is_unavailable_and_shows_the_link(
    flags: Flags,
) -> None:
    _build(flags, _settings(), email_sender=StubEmailSender())

    assert flags.health["email_available"] is False
    assert flags.show_demo_link is True


@pytest.mark.parametrize(
    ("client", "key", "expected"),
    [
        (None, "", False),
        (None, "sk-test", True),
        (StubGenerationClient(), "", False),
        (StubGenerationClient(), "sk-test", True),
        (FakeGenerationClient(binary_search_response()), "", True),
    ],
)
def test_generation_flag_is_key_configured_or_a_non_stub_client(
    flags: Flags, client: GenerationClient | None, key: str, expected: bool
) -> None:
    _build(flags, _settings(openrouter_api_key=key), generation_client=client)

    assert flags.health["generation_available"] is expected
