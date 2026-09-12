"""Das Fenster zur eingelesenen Laufdatei — Qt6-Fassung von ``messfenster.py``.

Portiert sind die beiden Reiter, auf denen alles Weitere aufsetzt:

* **Laufdatei** — die Datei als Raster, eine Zeile je Probe, mit
  vorangestellter Zeilennummer. Gelesen wird durch das unveränderte
  ``dateien.py``: Trennzeichen und Kodierung kommen aus dem Inhalt, nicht
  aus der Endung.
* **Laufkontext** — die Momentaufnahme der Stammdaten, gegen die der ganze
  Lauf geprüft wird, mit Ladezeitpunkt und der Prüfzuordnung des Geräts.

Die Auswertungsreiter des Originals (Standards, Proben, Parameter × Proben,
Messung, Datenbankabfrage) stehen als benannte Platzhalter darin.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from labcontrol_qt.arbeit import im_hintergrund
from labcontrol_qt.kern import dateien, lims_db
from labcontrol_qt.quelle import Datenquelle
from labcontrol_qt.raster import Rasteransicht
from labcontrol_qt.stil import Stil, beschriftung, karte, knopf, symbol

#: Was im Original noch als Auswertungsreiter folgt.
OFFENE_REITER = (
    ("Standards", "STANDARDVERWALTUNG und STANDARD_PARA, doppelt eingegrenzt "
                  "über UM_ID und die Parameter des Laufs"),
    ("Proben", "ERGEBNISSE über Serie + UM_ID + GEGR_ID, mit Kenndaten und "
               "nachgerechnetem Endfaktor"),
    ("Parameter × Proben", "die Messlinien aus PM_WELLEN, deren PM_CODE genau "
                           "einem Spaltenkopf der Laufdatei entspricht"),
    ("Messung", "dieselben Achsen mit den gemessenen Zahlen aus der Laufdatei"),
    ("Datenbankabfrage", "dieselben Zeilen mit ERGEBNISSE.MW_ROH daneben"),
)


class Messfenster(QMainWindow):
    """Ein Fenster je eingelesener Laufdatei."""

    def __init__(self, pfad: str, auswahl: dict, quelle: Datenquelle,
                 eltern: QWidget | None = None) -> None:
        super().__init__(eltern)
        self.pfad = pfad
        self.auswahl = auswahl
        self.quelle = quelle
        self.geladen_am = dt.datetime.now()
        self.pruefungen: list[dict] = []

        # Absichtlich vor dem Fensterbau: schlägt das Lesen fehl, soll gar
        # kein Fenster entstehen, sondern der Aufrufer die Meldung zeigen.
        self.laufdatei = dateien.lies(pfad)

        self.setWindowTitle(f"{self.laufdatei.name} — LabControl")
        self.setWindowIcon(symbol())
        self.resize(1320, 800)

        rumpf = QWidget()
        rumpf.setStyleSheet(f"background: {Stil.BG};")
        lage = QVBoxLayout(rumpf)
        lage.setContentsMargins(14, 12, 14, 12)
        lage.setSpacing(10)
        lage.addWidget(self._kopf())

        self.reiter = QTabWidget()
        self.reiter.addTab(self._laufdatei_reiter(), " Laufdatei ")
        self.reiter.addTab(self._kontext_reiter(), " Laufkontext ")
        for name, beschreibung in OFFENE_REITER:
            self.reiter.addTab(_platzhalter(name, beschreibung), f" {name} ")
        lage.addWidget(self.reiter, 1)

        self.setCentralWidget(rumpf)
        self.statusBar().showMessage(
            f"{self.laufdatei.zeilenzahl} Zeilen, "
            f"{self.laufdatei.spaltenzahl} Spalten gelesen.")
        self._kontext_laden()

    # -- Kopf ---------------------------------------------------------------
    def _kopf(self) -> QWidget:
        tafel = karte()
        raster = QGridLayout(tafel)
        raster.setContentsMargins(16, 12, 16, 12)
        raster.setHorizontalSpacing(24)
        raster.setVerticalSpacing(2)

        raster.addWidget(beschriftung(self.laufdatei.name, groesse=13, fett=True),
                         0, 0, 1, 4)
        # Wie die Datei gelesen wurde, gehört sichtbar dazu: die ICP-MS-Datei
        # heißt .csv und ist Tab-getrennt.
        hinweis = self.laufdatei.herkunft()
        if self.laufdatei.abgeschnitten:
            hinweis += (f" · auf {dateien.MAX_ZEILEN} Zeilen begrenzt, "
                        f"die Datei ist länger")
        raster.addWidget(beschriftung(hinweis, groesse=9, farbe=Stil.MUTED),
                         1, 0, 1, 4)

        felder = (("Serie", self.auswahl.get("serie", "")),
                  ("Untersuchungsmethode", self.auswahl.get("methode", "")),
                  ("Gerät", self.auswahl.get("geraet", "")),
                  ("Bearbeiter", self.auswahl.get("bearbeiter", "")))
        for spalte, (name, wert) in enumerate(felder):
            raster.addWidget(beschriftung(name, groesse=9, farbe=Stil.MUTED),
                             2, spalte)
            raster.addWidget(beschriftung(wert or "—", groesse=10), 3, spalte)
        raster.setColumnStretch(3, 1)
        return tafel

    # -- Reiter Laufdatei ---------------------------------------------------
    def _laufdatei_reiter(self) -> QWidget:
        seite = QWidget()
        lage = QVBoxLayout(seite)
        lage.setContentsMargins(12, 12, 12, 12)
        lage.setSpacing(8)

        lage.addWidget(beschriftung(
            "Die Datei als Raster — eine Zeile je Probe, die Spalten der Datei "
            "als Spalten. Interpretiert wird hier nichts: Werte stehen als Text, "
            "so wie sie in der Datei stehen.", groesse=9, farbe=Stil.MUTED,
            umbruch=True))

        raster = Rasteransicht(hoehe=20)
        spalten = ["#"] + list(self.laufdatei.spalten)
        zeilen = [[nummer] + list(zeile)
                  for nummer, zeile in enumerate(self.laufdatei.zeilen, start=1)]
        raster.fuellen(spalten, zeilen, breite_nach_inhalt=True)
        lage.addWidget(raster, 1)
        self.raster_laufdatei = raster
        return seite

    # -- Reiter Laufkontext -------------------------------------------------
    def _kontext_reiter(self) -> QWidget:
        seite = QWidget()
        lage = QVBoxLayout(seite)
        lage.setContentsMargins(12, 12, 12, 12)
        lage.setSpacing(8)

        lage.addWidget(beschriftung(
            "Die Momentaufnahme der LIMS-Stammdaten, gegen die dieser Lauf "
            "geprüft wird. Einmal beim Einlesen gezogen — würde jede Prüfung "
            "ihre Referenzwerte einzeln holen, könnte sich ein Wert mitten in "
            "der Bearbeitung ändern.", groesse=9, farbe=Stil.MUTED, umbruch=True))

        self.kontextkopf = Rasteransicht(hoehe=8)
        lage.addWidget(self.kontextkopf)

        reihe = QHBoxLayout()
        reihe.addWidget(beschriftung(
            "Prüfzuordnung des Geräts — angezeigt werden alle zugeordneten "
            "Prüfungen, durchgeführt nur die freigegebenen.", groesse=9,
            farbe=Stil.MUTED, umbruch=True), 1)
        self.knopf_neu = knopf("Neu laden", breite=110, hoehe=28,
                               farbe="#6b7268", fett=False)
        self.knopf_neu.clicked.connect(self._kontext_laden)
        self.knopf_csv = knopf("Als CSV sichern", breite=150, hoehe=28,
                               farbe="#6b7268", fett=False)
        self.knopf_csv.setToolTip("Schreibt einen Beleg, gegen welchen Stand "
                                  "bewertet wurde")
        self.knopf_csv.clicked.connect(self._kontext_sichern)
        reihe.addWidget(self.knopf_neu)
        reihe.addWidget(self.knopf_csv)
        lage.addLayout(reihe)

        self.kontextraster = Rasteransicht(hoehe=10)
        lage.addWidget(self.kontextraster, 1)
        return seite

    def _kontext_laden(self) -> None:
        stat_id = self.auswahl.get("stat_id")
        self.knopf_neu.setEnabled(False)
        self.geladen_am = dt.datetime.now()
        self._kontextkopf_zeigen()
        if stat_id is None:
            self.knopf_neu.setEnabled(True)
            self.kontextraster.fuellen(
                ["Hinweis"], [["Ohne Gerät gibt es keine Prüfzuordnung."]])
            return

        def fertig(pruefungen):
            self.knopf_neu.setEnabled(True)
            self.pruefungen = pruefungen
            self.kontextraster.fuellen(
                ["GEPR-ID", "Prüfung", "Status", "wird durchgeführt"],
                [[p["gepr_id"], p["pruefung"], p["status"],
                  "ja" if p["laeuft"] else "nein"] for p in pruefungen],
                breite_nach_inhalt=True,
                marken={(zeile, 3): ("gruen" if p["laeuft"] else "rot")
                        for zeile, p in enumerate(pruefungen)})
            laufend = sum(1 for p in pruefungen if p["laeuft"])
            self.statusBar().showMessage(
                f"{len(pruefungen)} Prüfungen zugeordnet, {laufend} freigegeben.",
                8000)

        def schief(fehler):
            self.knopf_neu.setEnabled(True)
            self.kontextraster.fuellen(["Fehler"],
                                       [[lims_db.fehlertext(fehler)]])

        im_hintergrund(lambda: self.quelle.pruefungen(stat_id), fertig, schief)

    def _kontextkopf_zeigen(self) -> None:
        self.kontextkopf.fuellen(
            ["Angabe", "Wert"],
            [["geladen am", self.geladen_am.strftime("%d.%m.%Y %H:%M:%S")],
             ["Datenbank", self.quelle.beschreibung()],
             ["Serie", self.auswahl.get("serie", "")],
             ["Untersuchungsmethode", self.auswahl.get("methode", "")],
             ["Gerät", self.auswahl.get("geraet", "")],
             ["Bearbeiter", self.auswahl.get("bearbeiter", "")],
             ["Laufdatei", self.pfad]],
            breite_nach_inhalt=True)

    def _kontext_sichern(self) -> None:
        """Schreibt den Beleg, gegen welchen Stand bewertet wurde."""
        vorschlag = str(Path.home() / f"laufkontext_{self.laufdatei.name}.csv")
        pfad, _ = QFileDialog.getSaveFileName(self, "Laufkontext sichern",
                                              vorschlag, "CSV-Datei (*.csv)")
        if not pfad:
            return
        try:
            with open(pfad, "w", encoding="utf-8-sig", newline="") as strom:
                schreiber = csv.writer(strom, delimiter=";")
                for zeile in self.kontextkopf.inhalt[1]:
                    schreiber.writerow(zeile)
                schreiber.writerow([])
                kopf, zeilen = self.kontextraster.inhalt
                schreiber.writerow(kopf)
                schreiber.writerows(zeilen)
        except OSError as fehler:
            QMessageBox.warning(self, "Nicht geschrieben", str(fehler))
            return
        self.statusBar().showMessage(f"Laufkontext nach {pfad} geschrieben.", 8000)


def _platzhalter(name: str, beschreibung: str) -> QWidget:
    tafel = karte()
    lage = QVBoxLayout(tafel)
    lage.setContentsMargins(28, 28, 28, 28)
    lage.setSpacing(10)
    lage.addWidget(beschriftung(f"{name} — noch nicht portiert", groesse=14,
                                fett=True))
    lage.addWidget(beschriftung(beschreibung, groesse=10, farbe=Stil.MUTED,
                                umbruch=True))
    lage.addStretch(1)

    aussen = QWidget()
    aussen_lage = QVBoxLayout(aussen)
    aussen_lage.setContentsMargins(14, 14, 14, 14)
    aussen_lage.addWidget(tafel)
    return aussen
