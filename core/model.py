"""Domain model and traffic-light (Ampel) evaluation for LabControl.

Deliberately free of any UI import: the same rules back the Qt, Tkinter and
web variant, so a difference measured between them is a difference of the UI
layer and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Protocol

#: Deviation (as a fraction of the permitted tolerance) from which a result is
#: still inside tolerance but no longer comfortable.
WARN_RATIO = 0.75

#: A sample whose due date is this close counts as urgent.
WARN_DAYS = 2


class Status(Enum):
    """The three Ampel states, ordered from harmless to blocking."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"

    @property
    def label(self) -> str:
        return {"ok": "frei", "warn": "prüfen", "fail": "gesperrt"}[self.value]


@dataclass(frozen=True, slots=True)
class Sample:
    """A single measured result awaiting release."""

    sample_id: str
    matrix: str
    analyte: str
    value: float
    target: float
    tolerance: float
    unit: str
    analyst: str
    received: date
    due: date

    @property
    def deviation(self) -> float:
        """Absolute deviation from the target value."""
        return abs(self.value - self.target)

    @property
    def deviation_ratio(self) -> float:
        """Deviation as a fraction of the permitted tolerance."""
        if self.tolerance <= 0:
            return 0.0 if self.deviation == 0 else float("inf")
        return self.deviation / self.tolerance


class Evaluable(Protocol):
    """Anything the Ampel can judge: a deviation from target and a due date.

    The comparison variants pass a :class:`Sample`, the LabControl application
    passes its own record type — both are judged by the rules below and by
    nothing else.
    """

    @property
    def deviation_ratio(self) -> float: ...

    @property
    def due(self) -> date: ...


def status_of(sample: Evaluable, today: date) -> Status:
    """Evaluate the Ampel state of ``sample`` as seen on ``today``.

    Out of tolerance or past its due date blocks the release; close to either
    limit asks for a second look.
    """
    days_left = (sample.due - today).days
    if sample.deviation_ratio > 1.0 or days_left < 0:
        return Status.FAIL
    if sample.deviation_ratio >= WARN_RATIO or days_left <= WARN_DAYS:
        return Status.WARN
    return Status.OK


#: One palette for all three variants, so the screenshots stay comparable.
AMPEL_BG = {Status.OK: "#e7f6ec", Status.WARN: "#fdf3d7", Status.FAIL: "#fbe3e3"}
AMPEL_FG = {Status.OK: "#1c5c33", Status.WARN: "#6b4e05", Status.FAIL: "#8a1f1f"}
