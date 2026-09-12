"""Ablage in SQLite — Proben und ein fortschreibender Prüfpfad.

Jede Änderung an einer Probe schreibt in derselben Transaktion einen Eintrag
in den Prüfpfad. Der Prüfpfad kennt absichtlich kein Ändern und kein Löschen:
er wächst nur. Dieses Modul kennt kein Qt und ist ohne Fenster prüfbar.
"""

from __future__ import annotations

import getpass
import random
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path

from core.model import Status, status_of
from labcontrol.domain import (
    EDITABLE_FIELDS,
    ReleaseState,
    SampleRecord,
    describe_changes,
    validate,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    row_id        INTEGER PRIMARY KEY,
    sample_id     TEXT    NOT NULL UNIQUE,
    matrix        TEXT    NOT NULL,
    analyte       TEXT    NOT NULL,
    value         REAL    NOT NULL,
    target        REAL    NOT NULL,
    tolerance     REAL    NOT NULL,
    unit          TEXT    NOT NULL,
    analyst       TEXT    NOT NULL,
    received      TEXT    NOT NULL,
    due           TEXT    NOT NULL,
    state         TEXT    NOT NULL DEFAULT 'offen',
    decided_at    TEXT,
    decided_by    TEXT    NOT NULL DEFAULT '',
    decision_note TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS samples_state ON samples(state);
CREATE INDEX IF NOT EXISTS samples_due   ON samples(due);

CREATE TABLE IF NOT EXISTS audit (
    entry_id  INTEGER PRIMARY KEY,
    at        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT '',
    note      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_sample ON audit(sample_id);
"""

#: Spalten, nach denen die Datenbank selbst sortieren kann.
SQL_SORT = {
    "sample_id": "sample_id",
    "matrix": "matrix",
    "analyte": "analyte",
    "value": "value",
    "target": "target",
    "deviation": "abs(value - target) / tolerance",
    "due": "due",
    "analyst": "analyst",
    "state": "state",
}

#: Diese Sortierung rechnet die Ampel aus und läuft deshalb in Python.
AMPEL_ORDER = {Status.FAIL: 0, Status.WARN: 1, Status.OK: 2}


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """Eine Zeile des Prüfpfads."""

    at: datetime
    actor: str
    action: str
    sample_id: str
    detail: str
    note: str


def current_user() -> str:
    """Wer gerade arbeitet — landet unverändert im Prüfpfad."""
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - je nach Betriebssystem
        return "unbekannt"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Storage:
    """Öffnet die Datenbank und ist die einzige Stelle, die sie beschreibt."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    # -- Lesen --------------------------------------------------------------
    def is_empty(self) -> bool:
        return self.connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0

    def count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]

    def get(self, sample_id: str) -> SampleRecord | None:
        row = self.connection.execute(
            "SELECT * FROM samples WHERE sample_id = ?", (sample_id,)).fetchone()
        return _to_record(row) if row else None

    def query(
        self,
        text: str = "",
        state: ReleaseState | None = None,
        ampel: Status | None = None,
        sort_key: str = "sample_id",
        descending: bool = False,
        today: date | None = None,
    ) -> list[SampleRecord]:
        """Sucht, filtert und sortiert.

        Text und Freigabestatus filtert die Datenbank, die Ampel filtert
        Python — sie ist eine Rechnung über den Regeln in ``core.model`` und
        wird bewusst nicht ein zweites Mal in SQL formuliert.
        """
        clauses, parameters = [], []
        needle = text.strip()
        if needle:
            clauses.append("(sample_id LIKE ? OR matrix LIKE ? OR analyte LIKE ?"
                           " OR analyst LIKE ?)")
            parameters += [f"%{needle}%"] * 4
        if state is not None:
            clauses.append("state = ?")
            parameters.append(state.value)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order = SQL_SORT.get(sort_key)
        statement = "SELECT * FROM samples" + where
        if order:
            statement += f" ORDER BY {order} {'DESC' if descending else 'ASC'}"

        records = [_to_record(row) for row
                   in self.connection.execute(statement, parameters)]

        day = today or date.today()
        if ampel is not None:
            records = [r for r in records if status_of(r, day) is ampel]
        if order is None:  # nach Ampel sortieren
            records.sort(key=lambda r: AMPEL_ORDER[status_of(r, day)],
                         reverse=descending)
        return records

    def summary(self, records: list[SampleRecord],
                today: date | None = None) -> dict[str, int]:
        """Zählt Ampel und Freigabestatus für die Fußzeile."""
        day = today or date.today()
        counts = {status.name: 0 for status in Status}
        counts.update({state.name: 0 for state in ReleaseState})
        for record in records:
            counts[status_of(record, day).name] += 1
            counts[record.state.name] += 1
        return counts

    def distinct(self, column: str) -> list[str]:
        if column not in {"matrix", "analyte", "analyst", "unit"}:
            raise ValueError(f"Spalte nicht vorgesehen: {column}")
        rows = self.connection.execute(
            f"SELECT DISTINCT {column} FROM samples ORDER BY {column}")
        return [row[0] for row in rows]

    def audit(self, sample_id: str | None = None, limit: int = 1_000) -> list[AuditEntry]:
        if sample_id:
            rows = self.connection.execute(
                "SELECT * FROM audit WHERE sample_id = ? ORDER BY entry_id DESC LIMIT ?",
                (sample_id, limit))
        else:
            rows = self.connection.execute(
                "SELECT * FROM audit ORDER BY entry_id DESC LIMIT ?", (limit,))
        return [
            AuditEntry(
                at=datetime.fromisoformat(row["at"]), actor=row["actor"],
                action=row["action"], sample_id=row["sample_id"],
                detail=row["detail"], note=row["note"],
            )
            for row in rows
        ]

    # -- Schreiben ----------------------------------------------------------
    def create(self, record: SampleRecord, actor: str | None = None) -> SampleRecord:
        """Legt eine Probe an und protokolliert das."""
        validate(record)
        actor = actor or current_user()
        stamp = _now().isoformat()
        with self.connection:
            if self.get(record.sample_id) is not None:
                raise ValueError(f"Die Probe-ID {record.sample_id} gibt es schon.")
            cursor = self.connection.execute(
                """INSERT INTO samples (sample_id, matrix, analyte, value, target,
                       tolerance, unit, analyst, received, due, state, decided_at,
                       decided_by, decision_note, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                _to_row(record) + (stamp, stamp))
            self._log(record.sample_id, "angelegt", actor,
                      f"Messwert {record.value:g} {record.unit}".strip(), "")
        return replace(record, row_id=cursor.lastrowid)

    def update(self, before: SampleRecord, after: SampleRecord,
               actor: str | None = None) -> SampleRecord:
        """Speichert eine Änderung; ohne Änderung passiert nichts."""
        validate(after)
        detail = describe_changes(before, after)
        if not detail:
            return before
        actor = actor or current_user()
        with self.connection:
            assignments = ", ".join(f"{field} = ?" for field in EDITABLE_FIELDS)
            values = [_encode(getattr(after, field)) for field in EDITABLE_FIELDS]
            self.connection.execute(
                f"UPDATE samples SET {assignments}, updated_at = ? WHERE sample_id = ?",
                values + [_now().isoformat(), before.sample_id])
            self._log(after.sample_id, "geändert", actor, detail, "")
        return replace(after, row_id=before.row_id)

    def decide(self, record: SampleRecord, state: ReleaseState, note: str,
               actor: str | None = None) -> SampleRecord:
        """Gibt frei, sperrt oder setzt zurück — immer mit Begründung."""
        if state is not ReleaseState.OPEN and not note.strip():
            raise ValueError("Eine Entscheidung ohne Begründung wird nicht gespeichert.")
        actor = actor or current_user()
        decided = record.with_decision(state, actor, note.strip(), _now())
        action = {
            ReleaseState.RELEASED: "freigegeben",
            ReleaseState.BLOCKED: "gesperrt",
            ReleaseState.OPEN: "zurückgesetzt",
        }[state]
        with self.connection:
            self.connection.execute(
                """UPDATE samples SET state = ?, decided_at = ?, decided_by = ?,
                       decision_note = ?, updated_at = ? WHERE sample_id = ?""",
                (state.value,
                 decided.decided_at.isoformat() if decided.decided_at else None,
                 actor, decided.decision_note, _now().isoformat(), record.sample_id))
            self._log(record.sample_id, action, actor,
                      f"Bewertung {record.ampel().label}", decided.decision_note)
        return decided

    def _log(self, sample_id: str, action: str, actor: str, detail: str,
             note: str) -> None:
        """Schreibt in den Prüfpfad. Nur diese Stelle tut das."""
        self.connection.execute(
            "INSERT INTO audit (at, actor, action, sample_id, detail, note)"
            " VALUES (?,?,?,?,?,?)",
            (_now().isoformat(), actor, action, sample_id, detail, note))

    # -- Erstbefüllung ------------------------------------------------------
    def seed(self, count: int = 2_000, seed: int = 17_025,
             today: date | None = None) -> int:
        """Füllt eine leere Datenbank mit einem plausiblen Arbeitsvorrat.

        Damit startet die Anwendung nicht ins Leere. Ein Teil der Proben ist
        schon entschieden, wie in einem laufenden Betrieb.
        """
        from core.data import generate

        if not self.is_empty():
            return 0
        day = today or date.today()
        rng = random.Random(seed)
        stamp = _now().isoformat()
        rows = []
        journal = []
        for sample in generate(count, seed=seed, today=day):
            record = SampleRecord(
                sample_id=sample.sample_id, matrix=sample.matrix,
                analyte=sample.analyte, value=sample.value, target=sample.target,
                tolerance=sample.tolerance, unit=sample.unit,
                analyst=sample.analyst, received=sample.received, due=sample.due,
            )
            roll = rng.random()
            if roll < 0.18:
                record = record.with_decision(
                    ReleaseState.RELEASED, sample.analyst,
                    "Ergebnis innerhalb der Toleranz, Kontrollkarte unauffällig.",
                    _now())
            elif roll < 0.24:
                record = record.with_decision(
                    ReleaseState.BLOCKED, sample.analyst,
                    "Abweichung bestätigt, Wiederholungsmessung angefordert.",
                    _now())
            rows.append(_to_row(record) + (stamp, stamp))
            if record.decided:
                journal.append((stamp, sample.analyst, record.state.label,
                                sample.sample_id, "Erstbefüllung",
                                record.decision_note))
        with self.connection:
            self.connection.executemany(
                """INSERT INTO samples (sample_id, matrix, analyte, value, target,
                       tolerance, unit, analyst, received, due, state, decided_at,
                       decided_by, decision_note, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
            self.connection.executemany(
                "INSERT INTO audit (at, actor, action, sample_id, detail, note)"
                " VALUES (?,?,?,?,?,?)", journal)
        return len(rows)


def _encode(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ReleaseState):
        return value.value
    return value


def _to_row(record: SampleRecord) -> tuple:
    return (
        record.sample_id, record.matrix, record.analyte, record.value,
        record.target, record.tolerance, record.unit, record.analyst,
        record.received.isoformat(), record.due.isoformat(), record.state.value,
        record.decided_at.isoformat() if record.decided_at else None,
        record.decided_by, record.decision_note,
    )


def _to_record(row: sqlite3.Row) -> SampleRecord:
    return SampleRecord(
        sample_id=row["sample_id"], matrix=row["matrix"], analyte=row["analyte"],
        value=row["value"], target=row["target"], tolerance=row["tolerance"],
        unit=row["unit"], analyst=row["analyst"],
        received=date.fromisoformat(row["received"]),
        due=date.fromisoformat(row["due"]),
        state=ReleaseState(row["state"]),
        decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
        decided_by=row["decided_by"], decision_note=row["decision_note"],
        row_id=row["row_id"],
    )
