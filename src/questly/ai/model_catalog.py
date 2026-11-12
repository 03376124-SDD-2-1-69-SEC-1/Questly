"""Adapters satisfying core.admin's ModelCatalog Protocol (OPS-55).

OpenRouter's free tier changes weekly, so the models an Admin may pick on A-01
and the models generation falls back to come from OpenRouter's own list, read
through the `openrouter` SDK and cached for 30 minutes.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Protocol

from questly.core.admin.models import AvailableModel
from questly.core.generation.ports import Clock

logger = logging.getLogger(__name__)

_CACHE_SECONDS = 30 * 60
_PAGE_SIZE = 1000
# After a failed read, ask again soon instead of serving a stale or empty list
# for the whole cache window.
_RETRY_SECONDS = 60
_FREE_MODEL_SUFFIX = ":free"
_STRUCTURED_PARAMETERS = frozenset({"response_format", "structured_outputs"})


class _Pricing(Protocol):
    prompt: str
    completion: str


class _Architecture(Protocol):
    output_modalities: list[str]


class _Model(Protocol):
    id: str
    pricing: _Pricing
    architecture: _Architecture
    supported_parameters: list[str]


class _ModelList(Protocol):
    data: list[_Model]


class _ModelListResponse(Protocol):
    result: _ModelList


class _Models(Protocol):
    def list(self, *, limit: int) -> _ModelListResponse: ...


class ModelListClient(Protocol):
    """The slice of `openrouter.OpenRouter` this adapter uses."""

    @property
    def models(self) -> _Models: ...


def _is_free(price: str) -> bool:
    try:
        return float(price) == 0
    except (TypeError, ValueError):
        return False


def _free_text_models(models: list[_Model]) -> tuple[AvailableModel, ...]:
    return tuple(
        AvailableModel(
            id=model.id,
            supports_response_format=bool(
                _STRUCTURED_PARAMETERS & set(model.supported_parameters)
            ),
        )
        for model in models
        if model.id.endswith(_FREE_MODEL_SUFFIX)
        and _is_free(model.pricing.prompt)
        and _is_free(model.pricing.completion)
        and "text" in model.architecture.output_modalities
    )


class OpenRouterFreeModelCatalog:
    """OpenRouter's free text models, in OpenRouter's order.

    `allowlist` (GENERATION_MODEL_ALLOWLIST) narrows the list to those ids and
    is the answer until a read has succeeded once. Empty means no narrowing.
    """

    def __init__(
        self,
        client: ModelListClient,
        clock: Clock,
        allowlist: tuple[str, ...] = (),
    ) -> None:
        self._client = client
        self._clock = clock
        self._allowlist = allowlist
        self._lock = threading.Lock()
        self._loaded: tuple[AvailableModel, ...] | None = None
        self._next_read_at: datetime | None = None

    def available_models(self) -> tuple[AvailableModel, ...]:
        now = self._clock.now()
        with self._lock:
            if self._next_read_at is None or now >= self._next_read_at:
                self._read(now)
            loaded = self._loaded
        if loaded is None:
            return tuple(AvailableModel(id, False) for id in self._allowlist)
        if not self._allowlist:
            return loaded
        return tuple(model for model in loaded if model.id in self._allowlist)

    def _read(self, now: datetime) -> None:
        try:
            # shortcut: one page, the SDK maximum of 1000 models (OpenRouter
            # lists a few hundred); follow `.next()` if it ever lists more.
            listed = self._client.models.list(limit=_PAGE_SIZE)
            models = _free_text_models(listed.result.data)
        except Exception:  # noqa: BLE001 -- A-01 and generation must survive an outage
            logger.warning(
                "could not read the OpenRouter model list; keeping the last good one",
                exc_info=True,
            )
            self._next_read_at = now + timedelta(seconds=_RETRY_SECONDS)
            return
        self._loaded = models
        self._next_read_at = now + timedelta(seconds=_CACHE_SECONDS)


class StubModelCatalog:
    """Demo mode and CI: no API key, so the list is whatever is configured."""

    def __init__(self, model_ids: tuple[str, ...]) -> None:
        self._models = tuple(AvailableModel(id, False) for id in model_ids)

    def available_models(self) -> tuple[AvailableModel, ...]:
        return self._models
