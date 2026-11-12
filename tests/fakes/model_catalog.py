from questly.core.admin.models import AvailableModel

DEFAULT_MODELS = (
    AvailableModel("poolside/laguna-s-2.1:free", False),
    AvailableModel("other/model", True),
)


class FakeModelCatalog:
    """A model list tests set by hand; `models` may be replaced between calls."""

    def __init__(self, models: tuple[AvailableModel, ...] = DEFAULT_MODELS) -> None:
        self.models = models

    def available_models(self) -> tuple[AvailableModel, ...]:
        return self.models
