"""Woher die Oberfläche ihre Daten bekommt.

Die Fenster sprechen nie mit ``lims_db``, sondern mit einer *Quelle*. Das ist
im Original nicht so — dort rufen die Reiter die Datenschicht direkt an — und
hier aus zwei Gründen anders:

1. **Prüfbarkeit.** Mit der Demoquelle startet die Anwendung ohne Oracle, ohne
   VPN und ohne Netzlaufwerk. Genau so entstehen die Bildschirmfotos und
   laufen die Tests.
2. **Der 32-bit-Zwang.** Die LIMS-Datenbank der NW-FVA ist eine 11.2 — zu alt
   für den Thin Mode von python-oracledb. Das Original löst das mit dem
   32-bit-Oracle-Client und baut deshalb eine 32-bit-exe. Qt 6 gibt es nicht
   für 32 Bit. Diese Naht ist die Stelle, an der eine dritte Quelle andocken
   kann, die den Datenbankteil in einem eigenen 32-bit-Prozess hält.
   Siehe ``docs/portierung.md``.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from labcontrol_qt.kern import lims_db


@runtime_checkable
class Datenquelle(Protocol):
    """Was die Oberfläche von einer Quelle erwartet."""

    def beschreibung(self) -> str: ...
    def bearbeiter(self) -> list[str]: ...
    def serien(self) -> list[str]: ...
    def serien_historisch(self, jahre: int, ohne_jahr: bool) -> list[str]: ...
    def methoden(self, serie: str) -> list[tuple]: ...
    def geraete(self, serie: str, um_id) -> list[tuple]: ...
    def offene_serien(self) -> list[dict]: ...
    def pruefungen(self, stat_id) -> list[dict]: ...
    def stationsordner(self, station: str) -> str: ...
    def messdateien(self, ordner: str) -> list[str]: ...
    def schliessen(self) -> None: ...


class LimsQuelle:
    """Die echte Quelle: ein angemeldeter Zugang auf die Oracle-Datenbank."""

    def __init__(self, zugang) -> None:
        self.zugang = zugang

    def beschreibung(self) -> str:
        return (f"{self.zugang.benutzer} an {self.zugang.alias} "
                f"({self.zugang.modus or 'nicht verbunden'}, "
                f"{self.zugang.verbindungsart()})")

    def bearbeiter(self) -> list[str]:
        return lims_db.bearbeiter_liste(self.zugang)

    def serien(self) -> list[str]:
        return lims_db.serien_liste(self.zugang)

    def serien_historisch(self, jahre: int, ohne_jahr: bool = False) -> list[str]:
        """Die Serien, die nur noch ERGEBNISSE kennt — auf Zuruf.

        Läuft nicht von allein: ohne Index auf ``ERGEBNISSE.SERIE`` liest die
        Datenbank dafür die größte Tabelle des LIMS.
        """
        return lims_db.serien_historisch(self.zugang, jahre, ohne_jahr=ohne_jahr)

    def methoden(self, serie: str) -> list[tuple]:
        return lims_db.methoden_fuer_serie(self.zugang, serie)

    def geraete(self, serie: str, um_id) -> list[tuple]:
        return lims_db.geraete_fuer(self.zugang, serie, um_id)

    def offene_serien(self) -> list[dict]:
        return lims_db.offene_serien(self.zugang)

    def pruefungen(self, stat_id) -> list[dict]:
        return lims_db.pruefungen_fuer_station(self.zugang, stat_id)

    def stationsordner(self, station: str) -> str:
        return lims_db.stationsordner(station)

    def messdateien(self, ordner: str) -> list[str]:
        return _dateien_lesen(ordner)

    def schliessen(self) -> None:
        self.zugang.schliessen()


# ---------------------------------------------------------------------------
# Offline
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _Gerät:
    stat_id: int
    station: str


BEARBEITER = ("M. KRINNINGER", "S. BAUER", "T. VOGEL", "A. REINDL", "K. SOMMER")
METHODEN = ((41, "ICP-MS"), (44, "ICP-OES"), (52, "IC-Anionen"),
            (58, "pH-LF"), (63, "CN-Analyse"))
GERAETE = {
    41: (_Gerät(1210, "ICPMS Agilent 7900"), _Gerät(1211, "ICPMS Agilent 8900")),
    44: (_Gerät(1230, "ICPOES Varian 725"),),
    52: (_Gerät(1250, "IC Dionex ICS-2100"),),
    58: (_Gerät(1270, "Titrator Metrohm 888"),),
    63: (_Gerät(1290, "CN Elementar vario EL"),),
}


class DemoQuelle:
    """Eine Quelle ohne Datenbank — dieselben Formen, erfundene Inhalte.

    Die Rückgaben haben Feld für Feld dieselbe Gestalt wie die von
    ``lims_db``: Methoden als ``(um_id, kuerzel)``, Geräte als
    ``(stat_id, station)``, offene Serien als Wörterbuch mit den fünf
    Schlüsseln. Wer gegen die Demoquelle entwickelt, entwickelt gegen die
    echte mit.
    """

    def __init__(self, ordner: str | os.PathLike | None = None,
                 saat: int = 17_025) -> None:
        self._zufall = random.Random(saat)
        self._ordner = Path(ordner) if ordner else None
        self._serien = self._serien_bauen()

    def _serien_bauen(self) -> list[str]:
        heute = date.today()
        serien = []
        for wochen_zurueck in range(0, 60):
            tag = heute - timedelta(weeks=wochen_zurueck)
            jahr, woche, _ = tag.isocalendar()
            for art in ("W", "B", "P"):
                serien.append(f"{jahr}{art}{woche:03d}")
        return sorted(set(serien), reverse=True)

    # -- Schnittstelle ------------------------------------------------------
    def beschreibung(self) -> str:
        return "Demobetrieb — keine Datenbankverbindung"

    def bearbeiter(self) -> list[str]:
        return list(BEARBEITER)

    def serien(self) -> list[str]:
        return list(self._serien)

    def serien_historisch(self, jahre: int, ohne_jahr: bool = False) -> list[str]:
        """Im Demobetrieb ein paar Jahrgänge mehr, damit der Knopf etwas tut."""
        aeltere = []
        for jahr in range(date.today().year - jahre, date.today().year):
            aeltere += [f"{jahr}{art}{woche:03d}"
                        for art in ("W", "B") for woche in (5, 19, 33, 47)]
        return sorted(set(aeltere), reverse=True)

    def methoden(self, serie: str) -> list[tuple]:
        if not serie:
            return []
        anzahl = 2 + (sum(ord(zeichen) for zeichen in serie) % 3)
        return [(um_id, kuerzel) for um_id, kuerzel in METHODEN[:anzahl]]

    def geraete(self, serie: str, um_id) -> list[tuple]:
        if not serie or um_id is None:
            return []
        return [(geraet.stat_id, geraet.station)
                for geraet in GERAETE.get(int(um_id), ())]

    def offene_serien(self) -> list[dict]:
        offen = []
        for serie in self._serien[:9]:
            for um_id, kuerzel in self.methoden(serie)[:2]:
                for stat_id, station in self.geraete(serie, um_id):
                    offen.append({"serie": serie, "um_id": um_id,
                                  "kuerzel": kuerzel, "stat_id": stat_id,
                                  "station": station})
        return offen[:24]

    def pruefungen(self, stat_id) -> list[dict]:
        vorrat = (("Wiederholproben", "freigegeben"),
                  ("Blindwert", "freigegeben"),
                  ("Kontrollstandard", "freigegeben"),
                  ("Verschleppung", "in Pflege"),
                  ("Regelkarte", "freigegeben"),
                  ("Feststoffverbrennung", "gesperrt"))
        return [{"gepr_id": 100 + nummer, "pruefung": name, "status": status,
                 "laeuft": lims_db.pruefung_laeuft(status)}
                for nummer, (name, status) in enumerate(vorrat)]

    def stationsordner(self, station: str) -> str:
        """Ein Ordner unter dem Demoverzeichnis statt unter ``G:``."""
        if self._ordner is None:
            return lims_db.stationsordner(station, anlegen=False)
        sicher = "".join("_" if zeichen in '<>:"/\\|?*' else zeichen
                         for zeichen in station).strip()
        ziel = self._ordner / sicher
        ziel.mkdir(parents=True, exist_ok=True)
        return str(ziel)

    def messdateien(self, ordner: str) -> list[str]:
        return _dateien_lesen(ordner)

    def schliessen(self) -> None:
        return None

    # -- Beigaben für den Demobetrieb --------------------------------------
    def messdateien_anlegen(self, station: str, serien: list[str]) -> list[str]:
        """Legt ein paar Laufdateien an, damit die Dateiliste etwas zeigt."""
        ordner = Path(self.stationsordner(station))
        angelegt = []
        for serie in serien:
            for name, trenner in ((f"{serie}_lauf1.csv", "\t"),
                                  (f"export_{serie}.txt", ";")):
                ziel = ordner / name
                if not ziel.exists():
                    ziel.write_text(self._laufdatei(serie, trenner),
                                    encoding="utf-8")
                angelegt.append(str(ziel))
        return angelegt

    def _laufdatei(self, serie: str, trenner: str) -> str:
        """Eine Laufdatei im Zuschnitt einer ICP-MS-Ausgabe."""
        spalten = ["Sample List - Sample Name", "Acq. Date-Time",
                   "206Pb (mp_KED-H2) - Value", "111Cd (mp_KED-H2) - Value",
                   "63Cu (mp_KED-H2) - Value", "Dilution"]
        zeilen = [trenner.join(spalten)]
        namen = (["1/BlindMWN1.1", "K26MS", "K26MSHg"]
                 + [f"{serie[:4]}P{nummer:05d}" for nummer in range(1, 29)]
                 + ["2/BlindMWN1.1", "K26MSUpdate"])
        for lauf, name in enumerate(namen, start=1):
            werte = [name,
                     f"2026-09-{(lauf % 28) + 1:02d} {8 + lauf % 9:02d}:14:07"]
            for _ in range(3):
                werte.append(f"{self._zufall.uniform(0.0004, 3.5):.6f}")
            werte.append(f"{self._zufall.choice((1, 1, 1, 2, 5, 10))}")
            zeilen.append(trenner.join(werte))
        return "\n".join(zeilen) + "\n"


def _dateien_lesen(ordner: str) -> list[str]:
    """Der Inhalt eines Stationsordners, Dateien only, alphabetisch.

    Fehlt der Ordner oder ist er nicht erreichbar — auf einem Netzlaufwerk
    der Normalfall, nicht die Ausnahme —, kommt eine leere Liste zurück und
    kein Fehler: die Auswahl darüber bleibt bedienbar.
    """
    try:
        with os.scandir(ordner) as eintraege:
            return sorted(eintrag.name for eintrag in eintraege
                          if eintrag.is_file())
    except OSError:
        return []


def nur_zur_serie(dateien: list[str], serie: str) -> list[str]:
    """Filtert auf Dateien, deren Name die Serie enthält.

    Passt keine einzige, kommen **alle** zurück — genau wie im Original:
    eine leere Liste sähe aus wie ein leerer Ordner und würde die gesuchte
    Datei verstecken.
    """
    if not serie:
        return list(dateien)
    treffer = [name for name in dateien if serie.upper() in name.upper()]
    return treffer or list(dateien)
