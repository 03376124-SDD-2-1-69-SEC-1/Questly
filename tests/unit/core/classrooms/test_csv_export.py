"""Shared CSV export helpers: formula-safe cells, Bangkok time, the response."""

import csv
import io
from datetime import UTC, datetime

import pytest

from questly.core.classrooms.csv_export import bangkok_time, csv_response, csv_safe


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_safe_prefixes_a_formula_start(prefix: str) -> None:
    assert csv_safe(f'{prefix}HYPERLINK("http://x")') == (
        f'\'{prefix}HYPERLINK("http://x")'
    )


@pytest.mark.parametrize("text", ["", "Nattapong", "66010001", "a=b", " =x", "'=x"])
def test_csv_safe_leaves_other_text_alone(text: str) -> None:
    assert csv_safe(text) == text


def test_bangkok_time_is_seven_hours_ahead_of_utc() -> None:
    assert bangkok_time(datetime(2026, 10, 21, 18, 30, 5, tzinfo=UTC)) == (
        "2026-10-22 01:30:05"
    )


def test_csv_response_writes_a_bom_safe_cells_and_numbers_unchanged() -> None:
    response = csv_response(
        "out.csv", ["=Header", "Score"], [["=1+1", 10], ["-", -2.5], ["ok", True]]
    )

    assert response.media_type == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="out.csv"'
    assert response.body.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(response.body.decode("utf-8-sig"))))
    assert rows == [
        ["'=Header", "Score"],
        ["'=1+1", "10"],
        ["'-", "-2.5"],
        ["ok", "True"],
    ]
