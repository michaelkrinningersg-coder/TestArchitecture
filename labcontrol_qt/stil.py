"""Gemeinsame Bausteine der Oberfläche — Qt6-Fassung von ``widgets.py``.

Die Farben sind Wert für Wert dieselben wie im Tkinter-Original, damit die
portierte Oberfläche dieselbe Handschrift trägt. Was sich ändert, ist der
Weg dorthin: der abgerundete Knopf ist im Original ein selbst gezeichnetes
Canvas mit Hover-Logik (rund 120 Zeilen), hier ein Stylesheet; der verzögerte
Hinweis ist ein eigenes Toplevel-Fenster, hier ``setToolTip``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class Stil:
    """Farb- und Schriftschema, unverändert aus ``widgets.Style``."""

    BG = "#f1f5f9"            # slate-100  (Anwendungshintergrund)
    CARD = "#ffffff"          # Karten und Tafeln
    HEADER = "#0f172a"        # slate-900  (Kopfzeile dunkel)
    HEADER_FG = "#f8fafc"
    TEXT = "#1e293b"          # slate-800
    MUTED = "#64748b"         # slate-500
    BORDER = "#cbd5e1"        # slate-300
    ACCENT = "#2563eb"        # blue-600   (Hauptaktion)
    ACCENT_DARK = "#1d4ed8"
    ACCENT_FG = "#ffffff"

    OK = "#16a34a"
    WARN = "#b45309"
    ERROR = "#dc2626"
    WARN_BG = "#fffbeb"
    ERROR_BG = "#fef2f2"
    OK_BG = "#f0fdf4"

    FONT = "Segoe UI"
    FONT_FALLBACK = "DejaVu Sans"

    @classmethod
    def schrift(cls, groesse: int = 10, fett: bool = False) -> QFont:
        """Segoe UI unter Windows, sonst was die Plattform hergibt."""
        font = QFont(cls.FONT, groesse)
        font.setStyleHint(QFont.SansSerif)
        font.setFamilies([cls.FONT, cls.FONT_FALLBACK, "Arial"])
        font.setBold(fett)
        return font


#: Zellenfarben der Auswertung — dieselben Werte wie in ``tabelle.FARBEN``.
FARBEN = {"rot": "#b91c1c", "blau": "#1d4ed8", "gruen": "#15803d"}
OHNE_ZAHL_BG = "#fee2e2"


def mittel(name: str) -> str:
    """Findet Beigaben im Skript- wie im PyInstaller-Betrieb."""
    basis = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    kandidat = Path(basis) / name
    if kandidat.exists():
        return str(kandidat)
    return str(Path(basis) / "kern" / name)


def symbol() -> QIcon:
    """Das Anwendungssymbol; ohne Datei ein leeres, nie ein Absturz."""
    for name in ("Icon.ico", "Icon.png"):
        pfad = mittel(name)
        if os.path.exists(pfad):
            return QIcon(pfad)
    return QIcon()


def knopf(text: str, *, breite: int = 0, hoehe: int = 34,
          farbe: str = Stil.ACCENT, vordergrund: str = Stil.ACCENT_FG,
          fett: bool = True) -> QPushButton:
    """Ein abgerundeter Knopf.

    Im Original ein Canvas, das seinen Rahmen als geglättetes Polygon malt
    und Hover, Deaktivierung und Beschriftung selbst verwaltet. Qt kann das
    von Haus aus, samt Tastaturbedienung und Systemschriftgröße.
    """
    schaltflaeche = QPushButton(text)
    schaltflaeche.setCursor(Qt.PointingHandCursor)
    schaltflaeche.setMinimumHeight(hoehe)
    if breite:
        schaltflaeche.setMinimumWidth(breite)
    schaltflaeche.setFont(Stil.schrift(10, fett))
    schaltflaeche.setStyleSheet(f"""
        QPushButton {{
            background: {farbe};
            color: {vordergrund};
            border: none;
            border-radius: 6px;
            padding: 6px 16px;
        }}
        QPushButton:hover  {{ background: {_dunkler(farbe)}; }}
        QPushButton:pressed{{ background: {_dunkler(farbe, 0.75)}; }}
        QPushButton:disabled {{ background: #e2e8f0; color: #94a3b8; }}
    """)
    return schaltflaeche


def karte(rand: int = 1) -> QFrame:
    """Eine weiße Tafel mit hellem Rahmen — der Träger der meisten Blöcke.

    Der Wähler nennt ausdrücklich ``QFrame#karte``. Ohne den Namen gilt
    ``QFrame { border: ... }`` auch für jedes Kindwidget, das von QFrame
    erbt — und ein ``QLabel`` tut das. Jede Beschriftung bekäme sonst einen
    eigenen Rahmen.
    """
    rahmen = QFrame()
    rahmen.setObjectName("karte")
    rahmen.setStyleSheet(
        f"QFrame#karte {{ background: {Stil.CARD}; "
        f"border: {rand}px solid {Stil.BORDER}; border-radius: 4px; }}")
    return rahmen


def beschriftung(text: str, *, groesse: int = 10, farbe: str = Stil.TEXT,
                 fett: bool = False, umbruch: bool = False) -> QLabel:
    schild = QLabel(text)
    schild.setFont(Stil.schrift(groesse, fett))
    schild.setStyleSheet(f"color: {farbe}; background: transparent;")
    schild.setWordWrap(umbruch)
    return schild


def rollbereich(inhalt: QWidget, hintergrund: str = Stil.BG) -> QScrollArea:
    """Ein rollbarer Bereich.

    Im Original 60 Zeilen Leinwand, Rollleiste und zwei ``<Configure>``-
    Behandlungen, die sich gegenseitig aufschaukeln konnten — der Kommentar
    dort erzählt von einem Bau, der deshalb sechs Stunden hing. Qt hat dafür
    ein Widget.
    """
    bereich = QScrollArea()
    bereich.setWidgetResizable(True)
    bereich.setFrameShape(QFrame.NoFrame)
    bereich.setWidget(inhalt)
    bereich.setStyleSheet(f"QScrollArea {{ background: {hintergrund}; border: none; }}")
    return bereich


def tafel(hintergrund: str = Stil.BG, rand: int = 0, abstand: int = 8) -> QWidget:
    """Ein einfacher Behälter mit senkrechtem Layout."""
    behaelter = QWidget()
    behaelter.setStyleSheet(f"background: {hintergrund};")
    lage = QVBoxLayout(behaelter)
    lage.setContentsMargins(rand, rand, rand, rand)
    lage.setSpacing(abstand)
    return behaelter


def _dunkler(farbe: str, anteil: float = 0.85) -> str:
    """Verdunkelt eine Hex-Farbe für den Hover-Zustand."""
    ton = QColor(farbe)
    if not ton.isValid():
        return farbe
    return QColor(int(ton.red() * anteil), int(ton.green() * anteil),
                  int(ton.blue() * anteil)).name()
