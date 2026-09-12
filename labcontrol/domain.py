"""Fachliche Begriffe der Anwendung: Probe, Bewertung, Freigabe.

Die Ampel bewertet, ein Mensch entscheidet. Beides ist hier getrennt: die
Bewertung (:class:`~core.model.Status`) rechnet sich aus Messwert und Frist,
der Freigabestatus (:class:`ReleaseState`) wird von einer Person gesetzt und
im Prüfpfad festgehalten. Dieses Modul kennt kein Qt.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum

from core.model import WARN_DAYS, WARN_RATIO, Status, status_of


class ReleaseState(Enum):
    """Wo die Probe im Freigabelauf steht."""

    OPEN = "offen"
    RELEASED = "freigegeben"
    BLOCKED = "gesperrt"

    @property
    def label(self) -> str:
        return self.value


#: Farben des Freigabestatus, bewusst anders als die Ampelfarben.
STATE_FG = {
    ReleaseState.OPEN: "#4a5a6a",
    ReleaseState.RELEASED: "#1c5c33",
    ReleaseState.BLOCKED: "#8a1f1f",
}
STATE_BG = {
    ReleaseState.OPEN: "#eceff2",
    ReleaseState.RELEASED: "#e7f6ec",
    ReleaseState.BLOCKED: "#fbe3e3",
}


@dataclass(frozen=True, slots=True)
class SampleRecord:
    """Eine Probe, wie sie in der Datenbank steht.

    Unveränderlich: Änderungen entstehen über :func:`dataclasses.replace`, was
    den Vergleich von alt und neu für den Prüfpfad trivial macht.
    """

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
    state: ReleaseState = ReleaseState.OPEN
    decided_at: datetime | None = None
    decided_by: str = ""
    decision_note: str = ""
    row_id: int | None = None

    # -- Bewertung ----------------------------------------------------------
    @property
    def deviation(self) -> float:
        return abs(self.value - self.target)

    @property
    def deviation_ratio(self) -> float:
        if self.tolerance <= 0:
            return 0.0 if self.deviation == 0 else float("inf")
        return self.deviation / self.tolerance

    def ampel(self, today: date | None = None) -> Status:
        """Bewertung nach denselben Regeln wie in den Vergleichsvarianten."""
        return status_of(self, today or date.today())

    @property
    def decided(self) -> bool:
        return self.state is not ReleaseState.OPEN

    def with_decision(self, state: ReleaseState, actor: str, note: str,
                      when: datetime | None = None) -> SampleRecord:
        return replace(self, state=state, decided_by=actor, decision_note=note,
                       decided_at=when or datetime.now(timezone.utc))


def ampel_reason(record: SampleRecord, today: date | None = None) -> str:
    """Sagt in Worten, warum die Ampel so steht, wie sie steht.

    Ohne das steht in der Oberfläche „gesperrt (13 % der Toleranz)", und wer
    das liest, hält es für einen Fehler — dabei ist die Frist abgelaufen.
    """
    day = today or date.today()
    days_left = (record.due - day).days
    percent = record.deviation_ratio * 100
    if record.deviation_ratio > 1.0:
        return f"{percent:.0f} % der Toleranz — außerhalb"
    if days_left < 0:
        tage = "Tag" if abs(days_left) == 1 else "Tagen"
        return f"Frist seit {abs(days_left)} {tage} überschritten"
    if record.deviation_ratio >= WARN_RATIO:
        return f"{percent:.0f} % der Toleranz ausgeschöpft"
    if days_left <= WARN_DAYS:
        tage = "Tag" if days_left == 1 else "Tagen"
        return f"fällig in {days_left} {tage}"
    return f"{percent:.0f} % der Toleranz, fällig in {days_left} Tagen"


#: Feldbeschriftungen für Dialoge und Prüfpfad — eine Quelle für beides.
FIELD_LABELS = {
    "sample_id": "Probe-ID",
    "matrix": "Matrix",
    "analyte": "Analyt",
    "value": "Messwert",
    "target": "Sollwert",
    "tolerance": "Toleranz",
    "unit": "Einheit",
    "analyst": "Prüfer",
    "received": "Eingang",
    "due": "Fällig",
}

EDITABLE_FIELDS = tuple(FIELD_LABELS)


class ValidationError(ValueError):
    """Eine Eingabe, die so nicht gespeichert werden darf."""


def validate(record: SampleRecord) -> None:
    """Prüft eine Probe vor dem Speichern und wirft bei Verstoß."""
    if not record.sample_id.strip():
        raise ValidationError("Die Probe-ID darf nicht leer sein.")
    if not record.analyte.strip():
        raise ValidationError("Ohne Analyt lässt sich nichts bewerten.")
    if not record.analyst.strip():
        raise ValidationError("Es muss ein Prüfer eingetragen sein.")
    if record.tolerance <= 0:
        raise ValidationError("Die Toleranz muss größer als null sein.")
    if record.due < record.received:
        raise ValidationError("Die Frist liegt vor dem Eingang der Probe.")


def describe_changes(before: SampleRecord, after: SampleRecord) -> str:
    """Beschreibt für den Prüfpfad, was sich geändert hat."""
    parts = []
    for field, label in FIELD_LABELS.items():
        old, new = getattr(before, field), getattr(after, field)
        if old != new:
            parts.append(f"{label}: {_render(old)} → {_render(new)}")
    return "; ".join(parts)


def _render(value: object) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
