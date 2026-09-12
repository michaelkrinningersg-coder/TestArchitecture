"""Start der portierten Anwendung.

    python -m labcontrol_qt              # mit Anmeldung an Oracle
    python -m labcontrol_qt --demo       # ohne Datenbank, mit erfundenen Daten
    python -m labcontrol_qt --selftest   # einmal hochfahren und prüfen
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from labcontrol_qt import APP_NAME, __version__
from labcontrol_qt.anmeldung import Anmeldemaske
from labcontrol_qt.arbeit import abwarten
from labcontrol_qt.hauptfenster import Hauptfenster
from labcontrol_qt.kern import config
from labcontrol_qt.quelle import DemoQuelle
from labcontrol_qt.stil import Stil, symbol


class Anwendung(QStackedWidget):
    """Hält Anmeldung und Hauptfenster und schaltet zwischen ihnen um."""

    def __init__(self, konfig, demo: bool = False) -> None:
        super().__init__()
        self.konfig = konfig
        self.setWindowTitle(f"{APP_NAME} {__version__} — Qt6-Portierung")
        self.setWindowIcon(symbol())
        self.resize(1400, 860)
        self.setStyleSheet(f"background: {Stil.BG};")

        self.maske = Anmeldemaske(konfig)
        self.maske.angemeldet.connect(self.uebernehmen)
        self.addWidget(self.maske)
        self.fenster: Hauptfenster | None = None
        if demo:
            self.maske.demo_starten()

    def uebernehmen(self, quelle) -> None:
        """Aus der Anmeldung wird die Arbeitsmaske."""
        if self.fenster is not None:
            self.removeWidget(self.fenster)
            self.fenster.deleteLater()
        self.fenster = Hauptfenster(quelle, self.konfig)
        self.fenster.abgemeldet.connect(self.abmelden)
        self.addWidget(self.fenster)
        self.setCurrentWidget(self.fenster)

    def abmelden(self) -> None:
        self.setCurrentWidget(self.maske)
        self.maske.passwort.clear()
        self.maske.passwort.setFocus()


def anwendung_bauen(argv: list[str] | None = None) -> QApplication:
    vorhanden = QApplication.instance()
    if vorhanden is not None:
        return vorhanden
    programm = QApplication(argv if argv is not None else sys.argv[:1])
    programm.setApplicationName(APP_NAME)
    programm.setApplicationVersion(__version__)
    programm.setOrganizationName("NW-FVA")
    programm.setWindowIcon(symbol())
    programm.setFont(Stil.schrift(10))
    return programm


def selbsttest() -> int:
    """Fährt die Oberfläche einmal hoch und prüft die tragenden Teile."""
    from labcontrol_qt.messfenster import Messfenster

    programm = anwendung_bauen()
    pruefungen: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory() as ordner:
        konfig = config.Config(runtime_dir=ordner)
        quelle = DemoQuelle(Path(ordner) / "stationen")
        fenster = Hauptfenster(quelle, konfig)
        fenster.show()
        programm.processEvents()
        abwarten()
        programm.processEvents()

        reiter = fenster.bearbeiten
        pruefungen.append(("Serienliste geladen", reiter.feld_serie.count() > 0))
        pruefungen.append(("Bearbeiterliste geladen",
                           reiter.feld_bearbeiter.count() > 0))
        pruefungen.append(("Übersicht der offenen Serien gefüllt",
                           reiter.uebersicht.modell.rowCount() > 0))

        serie = reiter.feld_serie.itemText(0)
        reiter.feld_suche.setText(serie[:5])
        programm.processEvents()
        pruefungen.append(("Seriensuche schränkt ein",
                           0 < reiter.feld_serie.count() <= len(reiter.serien_alle)))
        reiter.feld_suche.setText("")
        programm.processEvents()

        reiter._uebersicht_gewaehlt(0)
        for _ in range(30):
            programm.processEvents()
            abwarten(500)
            programm.processEvents()
            if reiter.vollstaendig():
                break
        pruefungen.append(("Klick in die Übersicht füllt die Auswahlkette",
                           reiter.vollstaendig()))

        auswahl = reiter.auswahl()
        quelle.messdateien_anlegen(auswahl["geraet"], [auswahl["serie"]])
        reiter._dateien_laden()
        abwarten()
        programm.processEvents()
        pruefungen.append(("Stationsordner gelesen",
                           len(reiter.dateien_gezeigt) > 0))

        pfad = os.path.join(reiter.dateien_ordner, reiter.dateien_gezeigt[0])
        mess = Messfenster(pfad, auswahl, quelle, fenster)
        mess.show()
        programm.processEvents()
        abwarten()
        programm.processEvents()
        pruefungen.append(("Laufdatei eingelesen",
                           mess.laufdatei.zeilenzahl > 0
                           and mess.laufdatei.spaltenzahl > 1))
        pruefungen.append(("Trennzeichen aus dem Inhalt erkannt",
                           mess.laufdatei.trennzeichen == "\t"))
        pruefungen.append(("Laufkontext zeigt die Prüfzuordnung",
                           mess.kontextraster.modell.rowCount() > 0))
        mess.close()
        fenster.close()

    for text, bestanden in pruefungen:
        print(f"  [{'ok' if bestanden else 'FEHLER'}] {text}")
    offen = [text for text, bestanden in pruefungen if not bestanden]
    if offen:
        print(f"Selbsttest fehlgeschlagen: {', '.join(offen)}")
        return 1
    print(f"Selbsttest bestanden — {APP_NAME} {__version__} (Qt6-Portierung)")
    return 0


def main(argv: list[str] | None = None) -> int:
    zerleger = argparse.ArgumentParser(
        prog="labcontrol_qt",
        description=f"{APP_NAME} — Qt6-Portierung der Tkinter-Anwendung")
    zerleger.add_argument("--version", action="version",
                          version=f"{APP_NAME} {__version__} (Qt6)")
    zerleger.add_argument("--demo", action="store_true",
                          help="ohne Datenbank starten, mit erfundenen Daten")
    zerleger.add_argument("--selftest", action="store_true",
                          help="einmal hochfahren, prüfen, Rückgabewert setzen")
    zerleger.add_argument("--screenshot", help="Fenster als PNG sichern")
    argumente = zerleger.parse_args(argv)

    if argumente.selftest:
        return selbsttest()

    programm = anwendung_bauen()
    konfig = config.Config()
    oberflaeche = Anwendung(konfig, demo=argumente.demo)
    oberflaeche.show()

    if argumente.screenshot:
        programm.processEvents()
        abwarten()
        programm.processEvents()
        oberflaeche.grab().save(argumente.screenshot)
        print(f"{argumente.screenshot}")
        return 0
    return programm.exec()
