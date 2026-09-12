"""Die Rasteranzeige, die sich Haupt- und Messfenster teilen.

Qt6-Fassung von ``tabelle.py`` und ``zellenraster.py``. Die beiden sind im
Original zwei getrennte Bauten, weil eine ``ttk.Treeview`` nur ganze Zeilen
einfärben kann: ``zellenraster.py`` malt deshalb auf 684 Zeilen ein eigenes
Raster, nur damit eine einzelne Zelle eine Farbe bekommt. In Qt trägt das
Modell die Farbe je Zelle und die Ansicht zeichnet sie — beides zusammen
steht hier.

Gezeigt werden dabei nur die sichtbaren Zeilen: eine Laufdatei darf bis
50 000 Zeilen haben (``dateien.MAX_ZEILEN``), und die wandern nicht mehr
einzeln in ein Widget.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from labcontrol_qt.kern import lims_db
from labcontrol_qt.stil import FARBEN, OHNE_ZAHL_BG, Stil

#: So breit darf eine Spalte höchstens werden, damit ein langer Freitext
#: nicht die ganze Tabelle aus dem Bild schiebt.
MAX_SPALTENBREITE = 360
MIN_SPALTENBREITE = 60

#: Nach so vielen Zeilen hört das Messen der Spaltenbreite auf. Bei 50 000
#: Zeilen ist der Unterschied zwischen "die ersten 200 angesehen" und "alle
#: angesehen" nicht sichtbar, wohl aber die Wartezeit.
BREITENPROBE = 200

#: Ab diesem Anteil lesbarer Zahlen gilt eine Spalte als Zahlenspalte.
#: Bewusst nicht höher: in einer Messspalte stehen regelmäßig Werte wie
#: ``<0,01`` unterhalb der Bestimmungsgrenze. Die machen aus der Spalte
#: keine Textspalte — sie linksbündig zu setzen, verschöbe alle Zahlen.
ZAHLENANTEIL = 0.6


class Rastermodell(QAbstractTableModel):
    """Spalten, Zeilen und die Farbe je Zelle."""

    def __init__(self) -> None:
        super().__init__()
        self._spalten: list[str] = []
        self._ueberschriften: list[str] = []
        self._zeilen: list[Sequence[str]] = []
        self._marken: dict[tuple[int, int], str] = {}
        self._rechts: set[int] = set()
        self._hinweis: Callable[[int, int], str] | None = None

    # -- Bestand ------------------------------------------------------------
    def fuellen(self, spalten: Sequence[str], zeilen: Iterable[Sequence],
                ueberschriften: Sequence[str] | None = None,
                marken: dict[tuple[int, int], str] | None = None) -> None:
        """Zeigt Spalten und Zeilen.

        ``ueberschriften`` trennt das Angezeigte von der Kennung — im
        Original nötig, weil eine Treeview je Spalte eine eindeutige Kennung
        braucht; hier bleibt es erhalten, damit über der Spalte der kurze
        Name steht und im Hinweis der lange.

        ``marken`` färbt einzelne Zellen: ``{(zeile, spalte): "rot"}``.
        """
        self.beginResetModel()
        self._spalten = list(spalten)
        self._ueberschriften = list(ueberschriften or spalten)
        self._zeilen = [["" if wert is None else str(wert) for wert in zeile]
                        for zeile in zeilen]
        self._marken = dict(marken or {})
        self._rechts = _zahlenspalten(self._zeilen, len(self._spalten))
        self.endResetModel()

    def leeren(self) -> None:
        self.fuellen([], [])

    def hinweis_quelle(self, funktion: Callable[[int, int], str] | None) -> None:
        """Legt fest, was beim Überfahren einer Zelle erscheint."""
        self._hinweis = funktion

    @property
    def inhalt(self) -> tuple[list[str], list[list[str]]]:
        """Was gerade auf dem Bildschirm steht — Vorlage für den CSV-Export."""
        return list(self._ueberschriften), [list(zeile) for zeile in self._zeilen]

    def zeile(self, nummer: int) -> Sequence[str]:
        return self._zeilen[nummer] if 0 <= nummer < len(self._zeilen) else []

    def spaltenname(self, spalte: int) -> str:
        return self._spalten[spalte] if 0 <= spalte < len(self._spalten) else ""

    # -- Schnittstelle des Modells -----------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._zeilen)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._spalten)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        zeile, spalte = index.row(), index.column()
        werte = self._zeilen[zeile]

        if role == Qt.DisplayRole:
            return werte[spalte] if spalte < len(werte) else ""
        if role == Qt.ForegroundRole:
            marke = self._marken.get((zeile, spalte))
            if marke in FARBEN:
                return QColor(FARBEN[marke])
            return None
        if role == Qt.BackgroundRole:
            if self._marken.get((zeile, spalte)) == "ohne_zahl":
                return QColor(OHNE_ZAHL_BG)
            return None
        if role == Qt.TextAlignmentRole:
            if spalte in self._rechts:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and self._hinweis is not None:
            return self._hinweis(zeile, spalte) or None
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if role == Qt.DisplayRole:
            return self._ueberschriften[section]
        if role == Qt.ToolTipRole:
            # Über der Spalte der kurze Name, im Hinweis der vollständige.
            lang = self._spalten[section]
            return lang if lang != self._ueberschriften[section] else None
        return None


class Rasteransicht(QWidget):
    """Tabelle mit beiden Bildlaufleisten und mitwachsenden Spaltenbreiten."""

    def __init__(self, eltern: QWidget | None = None, hoehe: int = 12) -> None:
        super().__init__(eltern)
        self.modell = Rastermodell()

        self.ansicht = QTableView()
        self.ansicht.setModel(self.modell)
        self.ansicht.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ansicht.setSelectionMode(QAbstractItemView.SingleSelection)
        self.ansicht.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ansicht.setAlternatingRowColors(True)
        self.ansicht.setShowGrid(False)
        self.ansicht.setWordWrap(False)
        self.ansicht.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.ansicht.verticalHeader().setVisible(False)
        self.ansicht.verticalHeader().setDefaultSectionSize(24)
        kopf = self.ansicht.horizontalHeader()
        kopf.setSectionResizeMode(QHeaderView.Interactive)
        kopf.setStretchLastSection(True)
        self.ansicht.setFont(Stil.schrift(9))
        self.ansicht.setStyleSheet(f"""
            QTableView {{
                background: {Stil.CARD};
                border: 1px solid {Stil.BORDER};
                gridline-color: {Stil.BORDER};
                selection-background-color: {Stil.ACCENT};
                selection-color: #ffffff;
                alternate-background-color: #f8fafc;
            }}
            QHeaderView::section {{
                background: #eef2f7;
                border: 0;
                border-right: 1px solid {Stil.BORDER};
                border-bottom: 1px solid {Stil.BORDER};
                padding: 5px 8px;
                font-weight: 600;
            }}
        """)
        self.setMinimumHeight(hoehe * 24)

        lage = QVBoxLayout(self)
        lage.setContentsMargins(0, 0, 0, 0)
        lage.addWidget(self.ansicht)

    # -- Anzeige ------------------------------------------------------------
    def fuellen(self, spalten: Sequence[str], zeilen: Iterable[Sequence],
                breite_nach_inhalt: bool = False,
                ueberschriften: Sequence[str] | None = None,
                marken: dict[tuple[int, int], str] | None = None) -> None:
        self.modell.fuellen(spalten, zeilen, ueberschriften, marken)
        self._breiten_setzen(breite_nach_inhalt)

    def leeren(self) -> None:
        self.modell.leeren()

    @property
    def inhalt(self) -> tuple[list[str], list[list[str]]]:
        return self.modell.inhalt

    def hinweis_quelle(self, funktion: Callable[[int, int], str] | None) -> None:
        self.modell.hinweis_quelle(funktion)

    def zellklick(self, funktion: Callable[[int, int], None]) -> None:
        self.ansicht.clicked.connect(
            lambda index: funktion(index.row(), index.column()))

    def spaltenklick(self, funktion: Callable[[int], None]) -> None:
        self.ansicht.horizontalHeader().sectionClicked.connect(funktion)

    def gewaehlte_zeile(self) -> int:
        zeilen = self.ansicht.selectionModel().selectedRows()
        return zeilen[0].row() if zeilen else -1

    def zeile_waehlen(self, nummer: int) -> None:
        if 0 <= nummer < self.modell.rowCount():
            self.ansicht.selectRow(nummer)

    def _breiten_setzen(self, nach_inhalt: bool) -> None:
        """Spaltenbreiten aus Überschrift und — auf Wunsch — Inhalt.

        Bei Messdateien reicht die Überschrift nicht: sie ist oft kurz und
        der Wert lang, oder umgekehrt.
        """
        kopf = self.ansicht.horizontalHeader()
        masse = self.ansicht.fontMetrics()
        _, zeilen = self.modell.inhalt
        for spalte in range(self.modell.columnCount()):
            text = self.modell.headerData(spalte, Qt.Horizontal, Qt.DisplayRole)
            # 44 statt der bloßen Textbreite: die Kopfzelle polstert
            # selbst (5/8 px) und hält Platz für den Sortierpfeil.
            breite = masse.horizontalAdvance(str(text)) + 44
            if nach_inhalt:
                for zeile in zeilen[:BREITENPROBE]:
                    if spalte < len(zeile):
                        breite = max(breite,
                                     masse.horizontalAdvance(zeile[spalte]) + 24)
            kopf.resizeSection(spalte, max(MIN_SPALTENBREITE,
                                           min(breite, MAX_SPALTENBREITE)))


def _zahlenspalten(zeilen: Sequence[Sequence[str]], anzahl: int) -> set[int]:
    """Welche Spalten rechtsbündig gehören.

    Entschieden wird nach der Mehrheit der Werte, nicht nach dem ersten:
    eine Zahlenspalte mit einem ``<0,01`` darin bleibt eine Zahlenspalte.
    """
    if not zeilen:
        return set()
    probe = zeilen[:BREITENPROBE]
    rechts = set()
    for spalte in range(anzahl):
        zahlen = besetzt = 0
        for zeile in probe:
            if spalte >= len(zeile):
                continue
            wert = zeile[spalte].strip()
            if not wert:
                continue
            besetzt += 1
            if _ist_zahl(wert):
                zahlen += 1
        if besetzt and zahlen / besetzt >= ZAHLENANTEIL:
            rechts.add(spalte)
    return rechts


def _ist_zahl(text: str) -> bool:
    """Was das Original für eine Zahl hält — auch mit Dezimalkomma.

    Bewusst ``lims_db.als_zahl`` und keine eigene Prüfung: gäbe es zwei
    Auffassungen davon, was eine Zahl ist, stünde irgendwann ein Wert in der
    Anzeige rechts und in der Auswertung links.
    """
    return lims_db.als_zahl(text) is not None
