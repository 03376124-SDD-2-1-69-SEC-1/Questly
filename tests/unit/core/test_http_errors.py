"""ErrorTable turns a listed domain error into the `{code, message}` response."""

import pytest
from fastapi import HTTPException

from questly.core.http_errors import ErrorRule, ErrorTable, fail


class MissingError(Exception):
    pass


class RuleError(ValueError):
    pass


TABLE = ErrorTable(
    {
        MissingError: ErrorRule(404, "missing", "It is gone"),
        RuleError: ErrorRule(409, "rule", lambda error: f"Rule: {error}"),
        ValueError: ErrorRule(422, "invalid", str),
    }
)


def raised(error: Exception) -> HTTPException:
    with pytest.raises(HTTPException) as caught, TABLE.translate():
        raise error
    return caught.value


def test_fail_builds_the_code_and_message_detail() -> None:
    with pytest.raises(HTTPException) as caught:
        fail(418, "teapot", "Short and stout")

    assert caught.value.status_code == 418
    assert caught.value.detail == {"code": "teapot", "message": "Short and stout"}


def test_fixed_message_rule() -> None:
    error = raised(MissingError("ignored"))

    assert error.status_code == 404
    assert error.detail == {"code": "missing", "message": "It is gone"}


def test_str_message_rule_reads_the_error() -> None:
    error = raised(ValueError("add at least one test case"))

    assert error.status_code == 422
    assert error.detail == {"code": "invalid", "message": "add at least one test case"}


def test_first_matching_rule_wins_for_a_subclass() -> None:
    error = raised(RuleError("bad"))

    assert error.status_code == 409
    assert error.detail == {"code": "rule", "message": "Rule: bad"}


def test_unlisted_errors_and_clean_runs_pass_through() -> None:
    with pytest.raises(KeyError), TABLE.translate():
        raise KeyError

    with TABLE.translate():
        pass
