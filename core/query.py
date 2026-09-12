"""Search, filter and sort — the part every variant shares.

Keeping this here is what makes the comparison honest: each variant's own file
contains nothing but its toolkit binding, so a measured difference is a
property of the toolkit and not of duplicated business logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from .data import TODAY
from .model import Sample, Status, status_of


@dataclass(frozen=True, slots=True)
class Column:
    """Presentation metadata for one table column, shared by all variants."""

    key: str
    title: str
    width: int
    numeric: bool = False
    render: Callable[[Sample], str] = lambda s: ""
    sort: Callable[[Sample], object] = lambda s: ""


def _fmt(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}".replace(",", " ")


COLUMNS: tuple[Column, ...] = (
    Column("sample_id", "Probe-ID", 120, render=lambda s: s.sample_id,
           sort=lambda s: s.sample_id),
    Column("matrix", "Matrix", 120, render=lambda s: s.matrix,
           sort=lambda s: s.matrix),
    Column("analyte", "Analyt", 130, render=lambda s: s.analyte,
           sort=lambda s: s.analyte),
    Column("value", "Messwert", 110, numeric=True,
           render=lambda s: f"{_fmt(s.value)} {s.unit}".strip(),
           sort=lambda s: s.value),
    Column("target", "Sollwert", 110, numeric=True,
           render=lambda s: _fmt(s.target), sort=lambda s: s.target),
    Column("deviation", "Abw. v. Toleranz", 130, numeric=True,
           render=lambda s: f"{s.deviation_ratio * 100:.0f} %",
           sort=lambda s: s.deviation_ratio),
    Column("due", "Fällig", 100, render=lambda s: s.due.isoformat(),
           sort=lambda s: s.due),
    Column("analyst", "Prüfer", 130, render=lambda s: s.analyst,
           sort=lambda s: s.analyst),
)

COLUMN_BY_KEY = {c.key: c for c in COLUMNS}

#: Extra column, rendered by each variant as its coloured Ampel cell.
STATUS_TITLE = "Status"
STATUS_WIDTH = 100


def matches(sample: Sample, needle: str) -> bool:
    """Case-insensitive substring search over the four identifying fields."""
    return (
        needle in sample.sample_id.lower()
        or needle in sample.matrix.lower()
        or needle in sample.analyte.lower()
        or needle in sample.analyst.lower()
    )


def apply_query(
    rows: list[Sample],
    text: str = "",
    status: Status | None = None,
    sort_key: str = "sample_id",
    descending: bool = False,
    today: date = TODAY,
) -> list[Sample]:
    """Filter ``rows`` by free text and Ampel state, then sort them."""
    needle = text.strip().lower()
    result = rows
    if needle:
        result = [s for s in result if matches(s, needle)]
    if status is not None:
        result = [s for s in result if status_of(s, today) is status]
    column = COLUMN_BY_KEY.get(sort_key)
    if column is not None:
        result = sorted(result, key=column.sort, reverse=descending)
    elif sort_key == "status":
        order = {Status.FAIL: 0, Status.WARN: 1, Status.OK: 2}
        result = sorted(result, key=lambda s: order[status_of(s, today)],
                        reverse=descending)
    elif result is rows:
        result = list(rows)
    return result


def summarise(rows: list[Sample], today: date = TODAY) -> dict[Status, int]:
    """Count the Ampel states in ``rows`` — the figure each variant's footer shows."""
    counts = {Status.OK: 0, Status.WARN: 0, Status.FAIL: 0}
    for sample in rows:
        counts[status_of(sample, today)] += 1
    return counts
