"""CSV-Ausgabe der gerade sichtbaren Liste."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from core.model import status_of
from labcontrol.domain import SampleRecord

HEADER = ("Probe-ID", "Matrix", "Analyt", "Messwert", "Einheit", "Sollwert",
          "Toleranz", "Abweichung in % der Toleranz", "Eingang", "Fällig",
          "Prüfer", "Bewertung", "Freigabe", "entschieden am", "entschieden von",
          "Begründung")


def write_csv(path: str | Path, records: list[SampleRecord],
              today: date | None = None) -> int:
    """Schreibt ``records`` nach ``path`` und gibt die Zeilenzahl zurück.

    Semikolon und BOM, damit Excel die Datei ohne Nachfragen richtig öffnet.
    """
    day = today or date.today()
    with open(path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(HEADER)
        for record in records:
            writer.writerow((
                record.sample_id, record.matrix, record.analyte,
                f"{record.value:g}", record.unit, f"{record.target:g}",
                f"{record.tolerance:g}", f"{record.deviation_ratio * 100:.0f}",
                record.received.isoformat(), record.due.isoformat(),
                record.analyst, status_of(record, day).label, record.state.label,
                record.decided_at.strftime("%Y-%m-%d %H:%M") if record.decided_at else "",
                record.decided_by, record.decision_note,
            ))
    return len(records)
