"""Tabellenmodell und Zellendarstellung für die Probenliste.

Das Modell hält nur die Liste der Datensätze und beantwortet Fragen zu den
Zellen, die die Ansicht gerade zeichnet — bei 100 000 Proben sind das dieselben
knapp 30 Zeilen wie bei 2 000.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from core.model import AMPEL_BG, AMPEL_FG, Status, status_of
from labcontrol.domain import (
    STATE_BG,
    STATE_FG,
    ReleaseState,
    SampleRecord,
    ampel_reason,
)


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    title: str
    width: int
    numeric: bool = False
    render: Callable[[SampleRecord], str] = lambda record: ""


def _number(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}".replace(",", " ")


COLUMNS: tuple[Column, ...] = (
    Column("sample_id", "Probe-ID", 112, render=lambda r: r.sample_id),
    Column("matrix", "Matrix", 104, render=lambda r: r.matrix),
    Column("analyte", "Analyt", 118, render=lambda r: r.analyte),
    Column("value", "Messwert", 106, numeric=True,
           render=lambda r: f"{_number(r.value)} {r.unit}".strip()),
    Column("target", "Sollwert", 90, numeric=True, render=lambda r: _number(r.target)),
    Column("deviation", "Abw. v. Toleranz", 120, numeric=True,
           render=lambda r: f"{r.deviation_ratio * 100:.0f} %"),
    Column("due", "Fällig", 90, render=lambda r: r.due.isoformat()),
    Column("analyst", "Prüfer", 112, render=lambda r: r.analyst),
    Column("ampel", "Bewertung", 96, render=lambda r: r.ampel().label),
    Column("state", "Freigabe", 104, render=lambda r: r.state.label),
)

AMPEL_COLUMN = next(i for i, c in enumerate(COLUMNS) if c.key == "ampel")
STATE_COLUMN = next(i for i, c in enumerate(COLUMNS) if c.key == "state")

#: Eigene Rolle, über die die Zellendarstellung an die Farben kommt.
PILL_ROLE = Qt.UserRole + 1
RECORD_ROLE = Qt.UserRole + 2


class SampleTableModel(QAbstractTableModel):
    """Verbindet die Datensätze mit der Tabellenansicht."""

    def __init__(self, records: list[SampleRecord] | None = None) -> None:
        super().__init__()
        self._records: list[SampleRecord] = records or []
        self._today = date.today()

    # -- Bestand ------------------------------------------------------------
    def set_records(self, records: list[SampleRecord]) -> None:
        self.beginResetModel()
        self._records = records
        self._today = date.today()
        self.endResetModel()

    def record_at(self, row: int) -> SampleRecord | None:
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def row_of(self, sample_id: str) -> int:
        for index, record in enumerate(self._records):
            if record.sample_id == sample_id:
                return index
        return -1

    def replace_record(self, record: SampleRecord) -> None:
        """Tauscht einen Datensatz aus, ohne die ganze Tabelle neu zu bauen."""
        row = self.row_of(record.sample_id)
        if row < 0:
            return
        self._records[row] = record
        self.dataChanged.emit(self.index(row, 0),
                              self.index(row, len(COLUMNS) - 1))

    # -- Schnittstelle des Modells -----------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self._records[index.row()]
        column = COLUMNS[index.column()]

        if role == Qt.DisplayRole:
            return column.render(record)
        if role == RECORD_ROLE:
            return record
        if role == PILL_ROLE:
            if index.column() == AMPEL_COLUMN:
                status = status_of(record, self._today)
                return (AMPEL_BG[status], AMPEL_FG[status], status is not Status.OK)
            if index.column() == STATE_COLUMN:
                return (STATE_BG[record.state], STATE_FG[record.state],
                        record.state is ReleaseState.BLOCKED)
            return None
        if role == Qt.TextAlignmentRole:
            if column.numeric:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.ForegroundRole and index.column() == AMPEL_COLUMN:
            return QColor(AMPEL_FG[status_of(record, self._today)])
        if role == Qt.ToolTipRole:
            return _tooltip(record, self._today)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return COLUMNS[section].title

    def sort_key(self, section: int) -> str:
        return COLUMNS[section].key


def _tooltip(record: SampleRecord, today: date) -> str:
    lines = [
        f"{record.sample_id} · {record.analyte} in {record.matrix}",
        f"Messwert {record.value:g} {record.unit} · Soll {record.target:g}"
        f" ± {record.tolerance:g}",
        f"Bewertung: {status_of(record, today).label}"
        f" — {ampel_reason(record, today)}",
        f"Fällig {record.due.isoformat()} · Prüfer {record.analyst}",
    ]
    if record.decided:
        stamp = record.decided_at.strftime("%d.%m.%Y %H:%M") if record.decided_at else ""
        lines.append(f"{record.state.label} von {record.decided_by} am {stamp}")
        if record.decision_note:
            lines.append(f"Begründung: {record.decision_note}")
    return "\n".join(lines)


class PillDelegate(QStyledItemDelegate):
    """Zeichnet Bewertung und Freigabe als farbige Plakette.

    Eine Einzelzelle einzufärben ist genau das, was ein ``ttk.Treeview`` nicht
    kann — dort bliebe nur die ganze Zeile.
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        pill = index.data(PILL_ROLE)
        if pill is None:
            super().paint(painter, option, index)
            return

        background, foreground, emphasised = pill
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        else:
            painter.fillRect(option.rect, option.palette.base())

        box = QRect(option.rect).adjusted(6, 4, -6, -4)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(box, box.height() / 2, box.height() / 2)

        font = QFont(option.font)
        font.setBold(emphasised)
        painter.setFont(font)
        painter.setPen(QColor(foreground))
        painter.drawText(box, Qt.AlignCenter, index.data(Qt.DisplayRole))
        painter.restore()
