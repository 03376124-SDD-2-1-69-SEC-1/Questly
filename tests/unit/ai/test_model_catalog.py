"""OpenRouterFreeModelCatalog: OpenRouter's free text models, cached 30 minutes."""

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace

import pytest

from questly.ai.model_catalog import OpenRouterFreeModelCatalog, StubModelCatalog
from questly.core.admin.models import AvailableModel
from tests.fakes.auth import FakeClock


def _model(
    model_id: str,
    *,
    prompt: str = "0",
    completion: str = "0",
    output: tuple[str, ...] = ("text",),
    parameters: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        id=model_id,
        pricing=SimpleNamespace(prompt=prompt, completion=completion),
        architecture=SimpleNamespace(output_modalities=list(output)),
        supported_parameters=list(parameters),
    )


@dataclass
class FakeSdk:
    """Stands in for `OpenRouter(...)`: only `models.list().result.data`."""

    data: list[SimpleNamespace] = field(default_factory=list)
    fail: bool = False
    calls: int = 0

    @property
    def models(self) -> SimpleNamespace:
        return SimpleNamespace(list=self._list)

    def _list(self) -> SimpleNamespace:
        self.calls += 1
        if self.fail:
            raise ConnectionError("no route to openrouter")
        return SimpleNamespace(result=SimpleNamespace(data=self.data))


def _ids(catalog: OpenRouterFreeModelCatalog) -> list[str]:
    return [model.id for model in catalog.available_models()]


def test_only_free_text_models_are_kept_in_openrouters_order() -> None:
    sdk = FakeSdk(
        [
            _model("b/free:free"),
            _model("a/paid"),
            _model("c/priced:free", prompt="0.000001"),
            _model("d/costly-output:free", completion="0.5"),
            _model("e/image:free", output=("image",)),
            _model("f/both:free", output=("text", "image")),
            _model("g/odd-price:free", prompt="free"),
            _model("h/zero-float:free", prompt="0.0"),
        ]
    )

    catalog = OpenRouterFreeModelCatalog(sdk, FakeClock())

    assert _ids(catalog) == ["b/free:free", "f/both:free", "h/zero-float:free"]


@pytest.mark.parametrize(
    ("parameters", "supported"),
    [
        ((), False),
        (("temperature", "tools"), False),
        (("response_format",), True),
        (("structured_outputs",), True),
        (("temperature", "response_format", "structured_outputs"), True),
    ],
)
def test_response_format_support_comes_from_supported_parameters(
    parameters: tuple[str, ...], supported: bool
) -> None:
    sdk = FakeSdk([_model("a/m:free", parameters=parameters)])

    models = OpenRouterFreeModelCatalog(sdk, FakeClock()).available_models()

    assert models == (AvailableModel("a/m:free", supported),)


def test_the_list_is_cached_for_thirty_minutes() -> None:
    sdk = FakeSdk([_model("a/m:free")])
    clock = FakeClock()
    catalog = OpenRouterFreeModelCatalog(sdk, clock)

    catalog.available_models()
    clock.advance(timedelta(minutes=29, seconds=59))
    catalog.available_models()
    assert sdk.calls == 1

    sdk.data = [_model("b/m:free")]
    clock.advance(timedelta(seconds=1))
    assert _ids(catalog) == ["b/m:free"]
    assert sdk.calls == 2


def test_a_failed_refresh_keeps_the_last_good_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sdk = FakeSdk([_model("a/m:free")])
    clock = FakeClock()
    catalog = OpenRouterFreeModelCatalog(sdk, clock)
    catalog.available_models()
    sdk.fail = True
    clock.advance(timedelta(minutes=31))

    with caplog.at_level(logging.WARNING, logger="questly.ai.model_catalog"):
        assert _ids(catalog) == ["a/m:free"]

    assert "could not read the OpenRouter model list" in caplog.text


def test_a_failed_read_is_retried_after_a_minute_not_thirty() -> None:
    sdk = FakeSdk([_model("a/m:free")], fail=True)
    clock = FakeClock()
    catalog = OpenRouterFreeModelCatalog(sdk, clock)

    assert _ids(catalog) == []
    clock.advance(timedelta(seconds=30))
    assert _ids(catalog) == []
    assert sdk.calls == 1

    sdk.fail = False
    clock.advance(timedelta(seconds=30))
    assert _ids(catalog) == ["a/m:free"]


def test_a_catalog_that_never_loaded_is_empty_without_an_allowlist() -> None:
    catalog = OpenRouterFreeModelCatalog(FakeSdk(fail=True), FakeClock())

    assert catalog.available_models() == ()


def test_a_catalog_that_never_loaded_falls_back_to_the_allowlist() -> None:
    catalog = OpenRouterFreeModelCatalog(
        FakeSdk(fail=True), FakeClock(), allowlist=("x/one:free", "y/two:free")
    )

    assert catalog.available_models() == (
        AvailableModel("x/one:free", False),
        AvailableModel("y/two:free", False),
    )


def test_an_allowlist_narrows_the_loaded_list_and_keeps_its_order() -> None:
    sdk = FakeSdk([_model("a/m:free"), _model("b/m:free"), _model("c/m:free")])

    catalog = OpenRouterFreeModelCatalog(
        sdk, FakeClock(), allowlist=("c/m:free", "a/m:free", "gone/m:free")
    )

    assert _ids(catalog) == ["a/m:free", "c/m:free"]


def test_stub_catalog_lists_what_it_was_given() -> None:
    assert StubModelCatalog(("a/m:free",)).available_models() == (
        AvailableModel("a/m:free", False),
    )
