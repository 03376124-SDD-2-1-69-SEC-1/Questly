"""The one place a domain error becomes a JSON error response.

Every JSON route answers a failure with `{"detail": {"code", "message"}}`; only
`fail` builds that detail. A slice keeps its own error classes and declares which
status and code each one gets in an `ErrorTable`; its routes then run a use case
under `table.translate()`. This module imports no slice.
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import NoReturn

from fastapi import HTTPException

Message = str | Callable[[Exception], str]


@dataclass(frozen=True)
class ErrorRule:
    """Status and code for one domain error.

    `message` is fixed text, or a function of the error; pass `str` to answer with
    the error's own text.
    """

    status_code: int
    code: str
    message: Message


def fail(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> NoReturn:
    """Raise the `{code, message}` error response, with optional extra headers."""
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=None if headers is None else dict(headers),
    )


class ErrorTable:
    """A slice's domain errors and the response each one gets.

    The first rule whose class matches the raised error wins (`isinstance`), so
    list a subclass before its base class.
    """

    def __init__(self, rules: Mapping[type[Exception], ErrorRule]) -> None:
        self._rules = dict(rules)

    @contextmanager
    def translate(self) -> Iterator[None]:
        """Run a use case; a listed domain error becomes its error response."""
        try:
            yield
        except tuple(self._rules) as error:
            rule = next(r for cls, r in self._rules.items() if isinstance(error, cls))
            message = rule.message(error) if callable(rule.message) else rule.message
            fail(rule.status_code, rule.code, message)
