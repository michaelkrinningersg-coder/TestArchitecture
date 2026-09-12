"""Das Hauptfenster — Qt6-Fassung von ``arbeitsmaske`` und ``_bearbeiten_reiter``.

Der Bearbeiten-Reiter ist vollständig portiert: Bearbeiter, Serie mit Suche,
Untersuchungsmethode, Gerät, die Übersicht der offenen Serien und die
Dateiliste des Stationsordners. Die übrigen sechs Reiter des Originals stehen
als benannte Platzhalter darin — welcher Reiter hinter welchem Modul steckt und
wie groß es ist, sagt ``docs/portierung.md``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from labcontrol_qt.arbeit import im_hintergrund
from labcontrol_qt.kern import lims_db
from labcontrol_qt.quelle import Datenquelle, nur_zur_serie
from labcontrol_qt.raster import Rasteransicht
from labcontrol_qt.stil import Stil, beschriftung, karte, knopf

#: Wie weit ``Ältere Serien suchen`` zurückgeht — im Original einstellbar
#: unter Optionen, Blatt Serienliste.
SERIEN_JAHRE = 3

#: Was im Original noch aussteht. Der Platzhalter nennt Modul und Umfang,
#: damit niemand einen leeren Reiter für einen Fehler hält.
OFFENE_REITER = (
    ("Regelkarten", "labcontrol.py · _regelkarten_reiter", 1718),
    ("TRDF Prüfung", "trdfreiter.py", 2557),
    ("QP", "qpreiter.py", 4227),
    ("Saves", "labcontrol.py · _saves_reiter", 82),
    ("Abfragen", "labcontrol.py · _abfrage_reiter", 98),
    ("Info", "labcontrol.py · _info_reiter", 50),
)


class Hauptfenster(QMainWindow):
    """Kopfzeile, Reiter, Statuszeile."""

    abgemeldet = Signal()

    def __init__(self, quelle: Datenquelle, konfig) -> None:
        super().__init__()
        self.quelle = quelle
        self.konfig = konfig
        self.messfenster: list[QWidget] = []

        self.setWindowTitle("LabControl (Qt6) — Portierung")
        self.resize(1400, 860)
        self.setMinimumSize(1000, 640)

        rumpf = QWidget()
        rumpf.setStyleSheet(f"background: {Stil.BG};")
        lage = QVBoxLayout(rumpf)
        lage.setContentsMargins(0, 0, 0, 0)
        lage.setSpacing(0)
        lage.addWidget(self._kopfzeile())

        from PySide6.QtWidgets import QTabWidget

        self.reiter = QTabWidget()
        self.reiter.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {Stil.BORDER};
                                background: {Stil.BG}; }}
            QTabBar::tab {{ background: #e2e8f0; color: {Stil.TEXT};
                            padding: 8px 18px; margin-right: 2px;
                            border: 1px solid {Stil.BORDER};
                            border-bottom: none; }}
            QTabBar::tab:selected {{ background: {Stil.BG}; font-weight: 600; }}
        """)
        self.bearbeiten = BearbeitenReiter(quelle, konfig, self)
        self.reiter.addTab(self.bearbeiten, " Bearbeiten ")
        for name, herkunft, zeilen in OFFENE_REITER:
            self.reiter.addTab(_platzhalter(name, herkunft, zeilen), f" {name} ")

        inhalt = QWidget()
        inhalt_lage = QVBoxLayout(inhalt)
        inhalt_lage.setContentsMargins(14, 12, 14, 12)
        inhalt_lage.addWidget(self.reiter)
        lage.addWidget(inhalt, 1)

        self.setCentralWidget(rumpf)
        self.statusBar().showMessage("Bereit.")
        self.bearbeiten.melden.connect(
            lambda text: self.statusBar().showMessage(text, 8000))
        self.bearbeiten.stammdaten_laden()

    def _kopfzeile(self) -> QWidget:
        leiste = QWidget()
        leiste.setStyleSheet(f"background: {Stil.HEADER};")
        lage = QHBoxLayout(leiste)
        lage.setContentsMargins(16, 10, 16, 10)
        lage.setSpacing(12)

        marke = beschriftung("LabControl", groesse=13, farbe=Stil.HEADER_FG,
                             fett=True)
        self.zugangszeile = beschriftung(self.quelle.beschreibung(), groesse=9,
                                         farbe="#cbd5e1")
        lage.addWidget(marke)
        lage.addWidget(self.zugangszeile)
        lage.addStretch(1)

        optionen = knopf("Optionen", breite=110, hoehe=30, farbe="#334155")
        optionen.clicked.connect(self._optionen)
        abmelden = knopf("Abmelden", breite=110, hoehe=30, farbe="#334155")
        abmelden.clicked.connect(self._abmelden)
        lage.addWidget(optionen)
        lage.addWidget(abmelden)
        return leiste

    def _optionen(self) -> None:
        QMessageBox.information(
            self, "Optionen",
            "Der Optionsdialog ist noch nicht portiert.\n\n"
            "Im Original sind das rund 1 770 Zeilen in labcontrol.py: "
            "Verdünnung, Erkennung, Kontrollbereiche, Vorschau, "
            "Verschleppung, Regelkarten, Blindwert, Ziel, Feststoff, "
            "Sichtbarkeit und die Geräteoptionen.")

    def _abmelden(self) -> None:
        for fenster in list(self.messfenster):
            fenster.close()
        self.quelle.schliessen()
        self.abgemeldet.emit()
        self.close()

    def messfenster_zeigen(self, fenster: QWidget) -> None:
        """Hält ein Messfenster fest, damit es nicht sofort eingesammelt wird."""
        self.messfenster.append(fenster)
        fenster.destroyed.connect(
            lambda *_: self.messfenster.remove(fenster)
            if fenster in self.messfenster else None)
        fenster.show()


class BearbeitenReiter(QWidget):
    """Auswahl von Bearbeiter, Serie, Methode und Gerät bis zur Datei."""

    melden = Signal(str)

    def __init__(self, quelle: Datenquelle, konfig,
                 fenster: Hauptfenster | None = None) -> None:
        super().__init__()
        self.quelle = quelle
        self.konfig = konfig
        self.fenster = fenster

        self.serien_alle: list[str] = []
        self.serien_vollstaendig = False
        self.methoden: dict[str, object] = {}      # Anzeigetext -> um_id
        self.geraete: dict[str, tuple] = {}        # Anzeigetext -> (stat_id, Name)
        self.uebersicht_zeilen: list[dict] = []
        self.dateien_alle: list[str] = []
        self.dateien_gezeigt: list[str] = []
        self.dateien_ordner = ""
        self.gewaehlte_datei: str | None = None
        # Einmalige Fortsetzungen: ein Klick in die Übersicht setzt Serie,
        # Methode und Gerät auf einmal, die beiden hinteren Listen kommen
        # aber erst aus dem Hintergrund. Statt darauf zu warten, hängt die
        # nächste Stufe am Abschluss der vorigen.
        self._nach_methoden = None
        self._nach_geraeten = None

        lage = QVBoxLayout(self)
        lage.setContentsMargins(14, 14, 14, 14)
        lage.setSpacing(12)
        lage.addWidget(self._bearbeiterkarte())
        lage.addWidget(self._auswahlkarte())

        self.auswahl_status = beschriftung("", groesse=9, farbe=Stil.MUTED,
                                           umbruch=True)
        lage.addWidget(self.auswahl_status)
        lage.addWidget(self._listen(), 1)
        self._auswahl_geaendert()

    # -- Aufbau -------------------------------------------------------------
    def _bearbeiterkarte(self) -> QWidget:
        tafel = karte()
        raster = QGridLayout(tafel)
        raster.setContentsMargins(16, 12, 16, 12)
        raster.setHorizontalSpacing(12)
        raster.setVerticalSpacing(2)

        raster.addWidget(beschriftung("Bearbeiter", groesse=9, farbe=Stil.MUTED),
                         0, 0)
        self.feld_bearbeiter = QComboBox()
        self.feld_bearbeiter.setMinimumWidth(220)
        self.feld_bearbeiter.currentTextChanged.connect(self._bearbeiter_gewaehlt)
        raster.addWidget(self.feld_bearbeiter, 1, 0)
        raster.addWidget(
            beschriftung("gilt für die ganze Sitzung und wird später zu jedem "
                         "gesendeten Ergebnis geschrieben", groesse=9,
                         farbe=Stil.MUTED), 1, 1)

        raster.addWidget(beschriftung("Messdateien liegen unter", groesse=9,
                                      farbe=Stil.MUTED), 2, 0, 1, 2)
        pfad = QLabel(lims_db.geraetebasis() + r"\<Stationsname>")
        pfad.setStyleSheet(f"color: {Stil.TEXT}; font-family: Consolas, "
                           f"'DejaVu Sans Mono', monospace; font-size: 12px;")
        pfad.setTextInteractionFlags(Qt.TextSelectableByMouse)
        raster.addWidget(pfad, 3, 0, 1, 2)
        raster.setColumnStretch(1, 1)
        return tafel

    def _auswahlkarte(self) -> QWidget:
        tafel = karte()
        raster = QGridLayout(tafel)
        raster.setContentsMargins(16, 12, 16, 12)
        raster.setHorizontalSpacing(12)
        raster.setVerticalSpacing(4)

        raster.addWidget(beschriftung("Serie, Untersuchungsmethode und Gerät "
                                      "wählen", groesse=11, fett=True), 0, 0, 1, 3)

        raster.addWidget(beschriftung("Serie suchen (schränkt die Liste darunter "
                                      "ein)", groesse=9, farbe=Stil.MUTED), 1, 0)
        self.feld_suche = QLineEdit()
        self.feld_suche.setClearButtonEnabled(True)
        self.feld_suche.textChanged.connect(self._serienliste_filtern)
        raster.addWidget(self.feld_suche, 2, 0)

        self.knopf_aeltere = knopf("Ältere Serien suchen", hoehe=30,
                                   farbe="#6b7268")
        self.knopf_aeltere.setToolTip(
            "Sucht die Serien, die es nur noch in ERGEBNISSE gibt.\n"
            f"Zurück bis {lims_db.serien_ab(SERIEN_JAHRE)}.")
        self.knopf_aeltere.clicked.connect(self._aeltere_serien_laden)
        raster.addWidget(self.knopf_aeltere, 2, 1)

        for spalte, text in enumerate(("Serie", "Untersuchungsmethode", "Gerät")):
            raster.addWidget(beschriftung(text, groesse=9, farbe=Stil.MUTED),
                             3, spalte)

        self.feld_serie = QComboBox()
        self.feld_serie.currentIndexChanged.connect(self._serie_gewaehlt)
        self.feld_methode = QComboBox()
        self.feld_methode.setEnabled(False)
        self.feld_methode.currentIndexChanged.connect(self._methode_gewaehlt)
        self.feld_geraet = QComboBox()
        self.feld_geraet.setEnabled(False)
        self.feld_geraet.currentIndexChanged.connect(
            lambda _index: self._auswahl_geaendert())
        for spalte, feld in enumerate((self.feld_serie, self.feld_methode,
                                       self.feld_geraet)):
            feld.setMinimumWidth(200)
            raster.addWidget(feld, 4, spalte)
            raster.setColumnStretch(spalte, 1)

        knoepfe = QHBoxLayout()
        self.knopf_datei = knopf("Datei wählen …", breite=190, hoehe=38)
        self.knopf_datei.setEnabled(False)
        self.knopf_datei.clicked.connect(self._datei_waehlen)
        self.knopf_verwerfen = knopf("Auswahl verwerfen", breite=190, hoehe=38,
                                     farbe="#6b7268")
        self.knopf_verwerfen.setEnabled(False)
        self.knopf_verwerfen.setToolTip(
            "Verwirft die gewählte Datei und die Kombination aus\n"
            "Serie, Untersuchungsmethode und Gerät")
        self.knopf_verwerfen.clicked.connect(self._auswahl_verwerfen)
        knoepfe.addWidget(self.knopf_datei)
        knoepfe.addWidget(self.knopf_verwerfen)
        knoepfe.addStretch(1)
        raster.addLayout(knoepfe, 5, 0, 1, 3)
        return tafel

    def _listen(self) -> QWidget:
        teiler = QSplitter(Qt.Horizontal)

        links = QWidget()
        links_lage = QVBoxLayout(links)
        links_lage.setContentsMargins(0, 0, 6, 0)
        links_lage.setSpacing(4)
        kopf_links = QHBoxLayout()
        kopf_links.addWidget(
            beschriftung('Offene Serien (Flag "In Arbeit") — eine Zeile wählen '
                         'übernimmt Serie, Methode und Gerät', groesse=9,
                         farbe=Stil.MUTED, umbruch=True), 1)
        self.knopf_serien_neu = knopf("Neu laden", breite=110, hoehe=28,
                                      farbe="#6b7268", fett=False)
        self.knopf_serien_neu.setToolTip("Holt die offenen Serien erneut aus dem LIMS")
        self.knopf_serien_neu.clicked.connect(self._uebersicht_laden)
        kopf_links.addWidget(self.knopf_serien_neu)
        links_lage.addLayout(kopf_links)
        self.uebersicht_status = beschriftung("", groesse=9, farbe=Stil.MUTED)
        links_lage.addWidget(self.uebersicht_status)
        self.uebersicht = Rasteransicht(hoehe=10)
        self.uebersicht.zellklick(lambda zeile, _spalte: self._uebersicht_gewaehlt(zeile))
        links_lage.addWidget(self.uebersicht, 1)

        rechts = DateiListe(self)
        self.dateibereich = rechts

        teiler.addWidget(links)
        teiler.addWidget(rechts)
        teiler.setStretchFactor(0, 3)
        teiler.setStretchFactor(1, 2)
        teiler.setSizes([760, 520])
        return teiler

    # -- Laden --------------------------------------------------------------
    def stammdaten_laden(self) -> None:
        """Bearbeiter, Serien und die Übersicht — alle drei nebenher."""
        self.melden.emit("Stammdaten werden geladen …")

        def fertig(daten):
            bearbeiter, serien = daten
            self.feld_bearbeiter.blockSignals(True)
            self.feld_bearbeiter.clear()
            self.feld_bearbeiter.addItems(bearbeiter)
            gemerkt = str(self.konfig.get("bearbeiter") or "")
            if gemerkt in bearbeiter:
                self.feld_bearbeiter.setCurrentText(gemerkt)
            self.feld_bearbeiter.blockSignals(False)

            self.serien_alle = serien
            self.serien_vollstaendig = False
            self._serienliste_filtern()
            self.melden.emit(f"{len(serien)} Serien, {len(bearbeiter)} Bearbeiter.")

        im_hintergrund(lambda: (self.quelle.bearbeiter(), self.quelle.serien()),
                       fertig, self._schiefgegangen)
        self._uebersicht_laden()

    def _uebersicht_laden(self) -> None:
        self.knopf_serien_neu.setEnabled(False)
        self.uebersicht_status.setText("Offene Serien werden geholt …")

        def fertig(zeilen):
            self.knopf_serien_neu.setEnabled(True)
            self.uebersicht_zeilen = zeilen
            self.uebersicht.fuellen(
                ["Serie", "UM-ID", "Untersuchungsmethode", "Stat-ID", "Gerät"],
                [[z["serie"], z["um_id"], z["kuerzel"], z["stat_id"], z["station"]]
                 for z in zeilen], breite_nach_inhalt=True)
            self.uebersicht_status.setText(
                f"{len(zeilen)} offene Serien" if zeilen
                else "Keine Serie trägt das Flag „In Arbeit“.")

        def schief(fehler):
            self.knopf_serien_neu.setEnabled(True)
            self.uebersicht_status.setText(lims_db.fehlertext(fehler))

        im_hintergrund(self.quelle.offene_serien, fertig, schief)

    def _aeltere_serien_laden(self) -> None:
        self.knopf_aeltere.setEnabled(False)
        self.auswahl_status.setText(
            f"Serien ab {lims_db.serien_ab(SERIEN_JAHRE)} werden in ERGEBNISSE "
            f"gesucht …")

        def fertig(serien):
            self.knopf_aeltere.setEnabled(True)
            self.serien_alle = lims_db.serien_vereinen(self.serien_alle, serien)
            self.serien_vollstaendig = True
            self._serienliste_filtern()
            self._auswahl_geaendert()

        def schief(fehler):
            self.knopf_aeltere.setEnabled(True)
            self._schiefgegangen(fehler)

        im_hintergrund(lambda: self.quelle.serien_historisch(SERIEN_JAHRE),
                       fertig, schief)

    # -- Auswahlkette -------------------------------------------------------
    def _serienliste_filtern(self, *_args) -> None:
        gewaehlt = self.feld_serie.currentText()
        passend = lims_db.serien_filtern(self.serien_alle, self.feld_suche.text())
        self.feld_serie.blockSignals(True)
        self.feld_serie.clear()
        self.feld_serie.addItems(passend)
        if gewaehlt in passend:
            self.feld_serie.setCurrentText(gewaehlt)
        else:
            self.feld_serie.setCurrentIndex(-1)
        self.feld_serie.blockSignals(False)
        if gewaehlt not in passend:
            self._serie_gewaehlt()

    def _bearbeiter_gewaehlt(self, name: str) -> None:
        if name:
            self.konfig.set("bearbeiter", name)
            self.konfig.speichern()

    def _serie_gewaehlt(self, *_args) -> None:
        self.feld_methode.clear()
        self.feld_methode.setEnabled(False)
        self.feld_geraet.clear()
        self.feld_geraet.setEnabled(False)
        self.methoden.clear()
        self.geraete.clear()
        self._auswahl_geaendert()
        serie = self.feld_serie.currentText()
        if not serie:
            return

        def fertig(zeilen):
            self.methoden = {f"{kuerzel} ({um_id})": um_id
                             for um_id, kuerzel in zeilen}
            self.feld_methode.blockSignals(True)
            self.feld_methode.clear()
            self.feld_methode.addItems(list(self.methoden))
            self.feld_methode.setCurrentIndex(-1)
            self.feld_methode.blockSignals(False)
            self.feld_methode.setEnabled(bool(self.methoden))
            self.melden.emit(f"{len(self.methoden)} Untersuchungsmethoden zu "
                             f"{serie}." if self.methoden
                             else f"Zu {serie} steht keine Untersuchungsmethode.")
            weiter, self._nach_methoden = self._nach_methoden, None
            if weiter is not None:
                weiter()

        im_hintergrund(lambda: self.quelle.methoden(serie), fertig,
                       self._schiefgegangen)

    def _methode_gewaehlt(self, *_args) -> None:
        self.feld_geraet.clear()
        self.feld_geraet.setEnabled(False)
        self.geraete.clear()
        self._auswahl_geaendert()
        serie = self.feld_serie.currentText()
        um_id = self.methoden.get(self.feld_methode.currentText())
        if not serie or um_id is None:
            return

        def fertig(zeilen):
            self.geraete = {f"{station} ({stat_id})": (stat_id, station)
                            for stat_id, station in zeilen}
            self.feld_geraet.blockSignals(True)
            self.feld_geraet.clear()
            self.feld_geraet.addItems(list(self.geraete))
            self.feld_geraet.setCurrentIndex(-1)
            self.feld_geraet.blockSignals(False)
            self.feld_geraet.setEnabled(bool(self.geraete))
            weiter, self._nach_geraeten = self._nach_geraeten, None
            if weiter is not None:
                weiter()

        im_hintergrund(lambda: self.quelle.geraete(serie, um_id), fertig,
                       self._schiefgegangen)

    def _uebersicht_gewaehlt(self, zeile: int) -> None:
        """Ein Klick in die Übersicht übernimmt alle drei Angaben auf einmal.

        Methode und Gerät stehen erst fest, wenn ihre Listen aus dem
        Hintergrund da sind. Deshalb hängt jede Stufe am Abschluss der
        vorigen — und zwar mit dem Wert aus der angeklickten Zeile, nicht
        geraten: dieselbe Serie kann mehrere Methoden führen.
        """
        if not 0 <= zeile < len(self.uebersicht_zeilen):
            return
        eintrag = self.uebersicht_zeilen[zeile]

        def geraet_setzen() -> None:
            ziel = f"{eintrag['station']} ({eintrag['stat_id']})"
            if ziel in self.geraete:
                self.feld_geraet.setCurrentText(ziel)

        def methode_setzen() -> None:
            ziel = f"{eintrag['kuerzel']} ({eintrag['um_id']})"
            if ziel not in self.methoden:
                return
            self._nach_geraeten = geraet_setzen
            vorher = self.feld_methode.currentText()
            self.feld_methode.setCurrentText(ziel)
            if self.feld_methode.currentText() == vorher:
                # Stand schon da: dann feuert kein Signal, und das Laden
                # der Geräte muss von Hand angestoßen werden.
                self._methode_gewaehlt()

        self.feld_suche.setText("")
        if eintrag["serie"] not in self.serien_alle:
            self.serien_alle = lims_db.serien_vereinen(self.serien_alle,
                                                       [eintrag["serie"]])
            self._serienliste_filtern()

        self._nach_methoden = methode_setzen
        vorher = self.feld_serie.currentText()
        self.feld_serie.setCurrentText(eintrag["serie"])
        if self.feld_serie.currentText() == vorher:
            self._serie_gewaehlt()

    def auswahl(self) -> dict:
        """Was gerade gewählt ist — leere Felder als leerer Text."""
        stat_id, station = self.geraete.get(self.feld_geraet.currentText(),
                                            (None, ""))
        return {"bearbeiter": self.feld_bearbeiter.currentText(),
                "serie": self.feld_serie.currentText(),
                "methode": self.feld_methode.currentText(),
                "um_id": self.methoden.get(self.feld_methode.currentText()),
                "geraet": station, "stat_id": stat_id}

    def vollstaendig(self) -> bool:
        gewaehlt = self.auswahl()
        return bool(gewaehlt["serie"] and gewaehlt["um_id"] is not None
                    and gewaehlt["stat_id"] is not None)

    def _auswahl_geaendert(self) -> None:
        fertig = self.vollstaendig()
        self.knopf_datei.setEnabled(fertig)
        self.knopf_verwerfen.setEnabled(
            fertig or bool(self.feld_serie.currentText()))
        gewaehlt = self.auswahl()
        if fertig:
            self.auswahl_status.setText(
                f"{gewaehlt['serie']} · {gewaehlt['methode']} · "
                f"{gewaehlt['geraet']}")
            self._dateien_laden()
        else:
            fehlt = [name for name, wert in (("Serie", gewaehlt["serie"]),
                                             ("Untersuchungsmethode",
                                              gewaehlt["um_id"]),
                                             ("Gerät", gewaehlt["stat_id"]))
                     if not wert and wert != 0]
            self.auswahl_status.setText(
                "Es fehlt noch: " + ", ".join(fehlt) +
                " — solange zeigt die Liste links alles, was „In Arbeit“ trägt."
                if fehlt else "")
            self.dateibereich.leeren()

    def _auswahl_verwerfen(self) -> None:
        self.gewaehlte_datei = None
        self.feld_suche.setText("")
        self.feld_serie.setCurrentIndex(-1)
        self._serie_gewaehlt()
        self.melden.emit("Auswahl verworfen. Der Bearbeiter bleibt.")

    # -- Dateien ------------------------------------------------------------
    def _dateien_laden(self) -> None:
        gewaehlt = self.auswahl()
        if not gewaehlt["geraet"]:
            return
        station = gewaehlt["geraet"]

        def arbeit():
            ordner = self.quelle.stationsordner(station)
            return ordner, self.quelle.messdateien(ordner)

        def fertig(ergebnis):
            self.dateien_ordner, self.dateien_alle = ergebnis
            self.dateibereich.ordner_zeigen(self.dateien_ordner)
            self._dateiliste_zeigen()

        def schief(fehler):
            self.dateibereich.leeren()
            self.melden.emit(f"Stationsordner nicht lesbar: {fehler}")

        im_hintergrund(arbeit, fertig, schief)

    def _dateiliste_zeigen(self) -> None:
        serie = self.feld_serie.currentText()
        # "gefiltert" und "kein Treffer" sind zwei verschiedene Lagen. Passt
        # keine Datei, zeigt nur_zur_serie() alle — dann wäre die Liste zwar
        # ungefiltert, aber aus einem anderen Grund als bei abgeschaltetem
        # Haken, und die Kopfzeile muss das sagen.
        if self.dateibereich.nur_passende() and serie:
            treffer = [name for name in self.dateien_alle
                       if serie.upper() in name.upper()]
            gezeigt = nur_zur_serie(self.dateien_alle, serie)
            hatte_treffer = bool(treffer)
        else:
            gezeigt, hatte_treffer = list(self.dateien_alle), True
        self.dateien_gezeigt = gezeigt
        self.dateibereich.fuellen(gezeigt, serie, hatte_treffer,
                                  len(self.dateien_alle))

    def datei_uebernehmen(self, name: str) -> None:
        """Eine Datei aus der Liste oder dem Dialog — mit Rückfrage."""
        pfad = name if os.path.isabs(name) else os.path.join(self.dateien_ordner,
                                                             name)
        self.gewaehlte_datei = pfad
        antwort = QMessageBox.question(
            self, "Laufdatei einlesen", f"Datei jetzt einlesen?\n\n{pfad}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if antwort != QMessageBox.Yes:
            self.melden.emit(f"{os.path.basename(pfad)} gewählt, nicht eingelesen.")
            return
        self._einlesen(pfad)

    def _einlesen(self, pfad: str) -> None:
        from labcontrol_qt.messfenster import Messfenster

        try:
            fenster = Messfenster(pfad, self.auswahl(), self.quelle, self.fenster)
        except Exception as fehler:                          # noqa: BLE001
            QMessageBox.warning(self, "Datei nicht lesbar", str(fehler))
            return
        if self.fenster is not None:
            self.fenster.messfenster_zeigen(fenster)
        else:
            fenster.show()
        self.melden.emit(f"{os.path.basename(pfad)} eingelesen.")

    def _datei_waehlen(self) -> None:
        start = self.dateien_ordner or str(Path.home())
        pfad, _ = QFileDialog.getOpenFileName(
            self, "Laufdatei wählen", start,
            "Messdateien (*.csv *.txt *.tsv *.dat *.xlsx *.xlsm);;Alle Dateien (*)")
        if pfad:
            self.datei_uebernehmen(pfad)

    def dateien_ablegen(self, pfade: list[str]) -> None:
        """Kopiert abgelegte Dateien in den Stationsordner.

        Im Original hängt das an ``tkinterdnd2``, das die tkdnd-Bibliothek
        zur Laufzeit nachlädt und je nach Windows-Variante auch nicht — dann
        steht in der Oberfläche, dass Ziehen nicht zur Verfügung steht. Qt
        bringt es mit.
        """
        if not self.dateien_ordner:
            return
        kopiert = []
        for quelle in pfade:
            if not os.path.isfile(quelle):
                continue
            ziel = os.path.join(self.dateien_ordner, os.path.basename(quelle))
            try:
                shutil.copy2(quelle, ziel)
                kopiert.append(os.path.basename(ziel))
            except OSError as fehler:
                self.melden.emit(f"{os.path.basename(quelle)} nicht kopiert: "
                                 f"{fehler}")
        if kopiert:
            self.melden.emit(f"{len(kopiert)} Datei(en) in den Stationsordner "
                             f"kopiert: {', '.join(kopiert)}")
            self._dateien_laden()

    def _schiefgegangen(self, fehler: Exception) -> None:
        self.melden.emit(lims_db.fehlertext(fehler))


class DateiListe(QWidget):
    """Rechte Spalte: der Inhalt des Stationsordners, als Ablageziel."""

    def __init__(self, reiter: BearbeitenReiter) -> None:
        super().__init__()
        self.reiter = reiter
        self.setAcceptDrops(True)

        lage = QVBoxLayout(self)
        lage.setContentsMargins(6, 0, 0, 0)
        lage.setSpacing(4)

        kopf = QHBoxLayout()
        self.haken = QCheckBox("nur Dateien zur gewählten Serie")
        self.haken.setChecked(True)
        self.haken.toggled.connect(lambda _an: reiter._dateiliste_zeigen())
        kopf.addWidget(self.haken)
        kopf.addStretch(1)
        self.knopf_neu = knopf("Neu laden", breite=110, hoehe=28,
                               farbe="#6b7268", fett=False)
        self.knopf_neu.setToolTip("Liest den Stationsordner erneut ein")
        self.knopf_neu.clicked.connect(reiter._dateien_laden)
        kopf.addWidget(self.knopf_neu)
        lage.addLayout(kopf)

        self.kopfzeile = beschriftung("Dateien im Stationsordner", groesse=9,
                                      farbe=Stil.MUTED, umbruch=True)
        self.ablage_hinweis = beschriftung(
            "Dateien aus dem Explorer hierher ziehen — sie werden in den "
            "Stationsordner kopiert.", groesse=9, farbe=Stil.MUTED, umbruch=True)
        lage.addWidget(self.kopfzeile)
        lage.addWidget(self.ablage_hinweis)

        self.liste = Rasteransicht(hoehe=10)
        self.liste.zellklick(lambda zeile, _spalte: self._gewaehlt(zeile))
        lage.addWidget(self.liste, 1)

    def nur_passende(self) -> bool:
        return self.haken.isChecked()

    def ordner_zeigen(self, ordner: str) -> None:
        self.kopfzeile.setText(f"Dateien in {ordner}")

    def fuellen(self, dateien: list[str], serie: str, hatte_treffer: bool,
                gesamt: int) -> None:
        self.liste.fuellen(["Datei"], [[name] for name in dateien],
                           breite_nach_inhalt=True)
        if not dateien:
            self.kopfzeile.setText(f"Der Stationsordner ist leer "
                                   f"({self.reiter.dateien_ordner})")
        elif not hatte_treffer:
            # Passte keine einzige, werden alle gezeigt — sonst sähe der
            # Ordner leer aus und versteckte genau die gesuchte Datei.
            self.kopfzeile.setText(f"Keine Datei nennt {serie} — es werden "
                                   f"alle {gesamt} Dateien gezeigt")
        elif self.nur_passende() and serie:
            self.kopfzeile.setText(f"{len(dateien)} von {gesamt} Dateien, "
                                   f"gefiltert auf {serie}")
        else:
            self.kopfzeile.setText(f"{gesamt} Dateien im Stationsordner")

    def leeren(self) -> None:
        self.liste.leeren()
        self.kopfzeile.setText("Dateien im Stationsordner — erst Serie, "
                               "Methode und Gerät wählen")

    def _gewaehlt(self, zeile: int) -> None:
        if 0 <= zeile < len(self.reiter.dateien_gezeigt):
            self.reiter.datei_uebernehmen(self.reiter.dateien_gezeigt[zeile])

    # -- Ablage aus dem Explorer -------------------------------------------
    def dragEnterEvent(self, ereignis: QDragEnterEvent) -> None:
        if ereignis.mimeData().hasUrls() and self.reiter.dateien_ordner:
            ereignis.acceptProposedAction()

    def dropEvent(self, ereignis: QDropEvent) -> None:
        pfade = [url.toLocalFile() for url in ereignis.mimeData().urls()
                 if url.isLocalFile()]
        if pfade:
            self.reiter.dateien_ablegen(pfade)
            ereignis.acceptProposedAction()


def _platzhalter(name: str, herkunft: str, zeilen: int) -> QWidget:
    """Ein Reiter, der ehrlich sagt, dass er noch nicht portiert ist."""
    tafel = karte()
    lage = QVBoxLayout(tafel)
    lage.setContentsMargins(28, 28, 28, 28)
    lage.setSpacing(10)
    lage.addWidget(beschriftung(f"{name} — noch nicht portiert", groesse=14,
                                fett=True))
    lage.addWidget(beschriftung(
        f"Im Tkinter-Original steckt dieser Reiter in {herkunft} und umfasst "
        f"rund {zeilen:,} Zeilen.".replace(",", " "), groesse=10,
        farbe=Stil.MUTED, umbruch=True))
    lage.addWidget(beschriftung(
        "Die Fachlogik dahinter liegt unverändert in labcontrol_qt/kern/ und "
        "wird beim Portieren dieses Reiters nicht angefasst — zu bauen ist "
        "nur die Oberfläche.", groesse=10, farbe=Stil.MUTED, umbruch=True))
    lage.addStretch(1)

    aussen = QWidget()
    aussen_lage = QVBoxLayout(aussen)
    aussen_lage.setContentsMargins(14, 14, 14, 14)
    aussen_lage.addWidget(tafel)
    return aussen
