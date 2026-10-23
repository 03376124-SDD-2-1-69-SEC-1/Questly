"""Shared CSV export helpers: formula-safe cells, Bangkok time, the response.

Both Instructor exports (summary.csv, submissions.csv) go through here so
they cannot drift apart. Not a service, model or port, so it may build a
FastAPI `Response`.
"""

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Response

CSV_ZONE = ZoneInfo("Asia/Bangkok")

# Spelled as an escape: a literal BOM in the source is invisible.
BOM = "\ufeff"

# A spreadsheet reads a text cell starting with one of these as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

CsvCell = str | int | float | bool


def csv_safe(text: str) -> str:
    """Prefix `'` so a spreadsheet shows `text` instead of running it."""
    if text.startswith(_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def bangkok_time(value: datetime) -> str:
    """A stored UTC time as `2026-10-21 14:05:09` in Bangkok time."""
    return value.astimezone(CSV_ZONE).strftime("%Y-%m-%d %H:%M:%S")


def csv_response(
    filename: str,
    header: Sequence[str],
    rows: Iterable[Sequence[CsvCell]],
) -> Response:
    """A UTF-8 CSV download with a BOM so Excel reads Thai text correctly.

    Every text cell, header included, goes through `csv_safe`; numbers and
    booleans are written as they are.
    """
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([csv_safe(cell) for cell in header])
    for row in rows:
        writer.writerow(
            [csv_safe(cell) if isinstance(cell, str) else cell for cell in row]
        )
    return Response(
        content=BOM + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
