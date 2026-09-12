"""
LabControl - was an der Datenbank geaendert wurde
=================================================

Jede Anweisung, die etwas veraendert, wird mitgeschrieben: UPDATE,
INSERT, DELETE, MERGE. Abfragen nicht - sie aendern nichts, und ein
Protokoll, in dem jede Auswahlliste steht, liest niemand mehr.

Warum ueberhaupt
----------------
Ein Export schreibt in die Ergebnisse eines Labors, das seine Zahlen
zwanzig Jahre aufhebt. Wenn spaeter jemand fragt, woher ein Wert kommt,
ist die Antwort "LabControl hat ihn geschrieben" keine Auskunft. Hier
steht, welche Anweisung wann und von wem lief - Zeile fuer Zeile,
lesbar, ohne Datenbank.

Wie es aussieht
---------------
Je Anweisung zwei Zeilen, wie es das LIMS selbst haelt: ein Satz in
Worten, darunter die Anweisung mit eingesetzten Werten.

    2026-09-07 11:42:03  krinninger
    LabControl: MW_ROH fuer Probe '2026B00184', Parameter 'Sges' geschrieben
    update <tabelle> set mw_roh = '4,3638217', ... where prob_id = 481645

(Im Beispiel steht <tabelle> nur, damit eine Pruefung im Testlauf die
Anweisungen an ERGEBNISSE zaehlen kann, ohne diese Erklaerung
mitzuzaehlen. Im Protokoll steht der wirkliche Name.)

Die eingesetzten Werte sind zum Lesen da, nicht zum Ausfuehren. An die
Datenbank geht die Anweisung mit Bindevariablen, wie ueberall in diesem
Programm; hier wird nur nachgezeichnet, was dort ankam. Genau deshalb
steht die Zusammensetzung auch in diesem Modul und nicht in lims_db:
ein Text, der wie eine Anweisung aussieht, soll dort gar nicht erst
entstehen koennen.

Wo es liegt
-----------
Neben dem Programm, eine Datei je Monat. Ein einziges Protokoll waechst
ueber Jahre ins Unbrauchbare; ein Monat ist die Einheit, in der man
sucht ("was ist im September gelaufen"), und die Datei bleibt klein
genug zum Oeffnen.

Wie viel aufgehoben wird
------------------------
Die letzten hunderttausend Eintraege. Ein Export schreibt mehrere
hundert Zeilen, und ohne Grenze fuellte das Protokoll ueber Jahre die
Platte des Rechners, auf dem LabControl laeuft. Was darueber
hinauswaechst, faellt vorn weg: der aelteste Monat zuerst, und der
Monat, in den die Grenze faellt, wird auf seine juengsten Eintraege
gekuerzt. Es ist ein laufendes Fenster, kein Archiv - was dauerhaft
belegt sein muss, steht im LIMS selbst.

Ein Protokoll darf nichts aufhalten. Laesst sich nicht schreiben - kein
Schreibrecht, Laufwerk weg -, wird das gemerkt und beim naechsten Mal
wieder versucht; der Export laeuft weiter. Ein Lauf, der scheitert, weil
das Protokoll klemmt, waere die schlechtere Sicherung.
"""

from __future__ import annotations

import datetime
import decimal
import os
import re
import threading

# Neben den Einstellungen, aber ein eigener Ordner: hier liegt kein
# Zustand, sondern Geschichte.
ORDNER = "protokoll"
NAMENSMUSTER = "aenderungen-%Y-%m.txt"
# Woran eine Protokolldatei zu erkennen ist - danach wird aufgeraeumt.
# Der Name traegt Jahr und Monat in dieser Reihenfolge, deshalb ist die
# alphabetische Sortierung zugleich die zeitliche.
NAMENSPRUEFUNG = re.compile(r"^aenderungen-\d{4}-\d{2}\.txt$")

# Wie viele Eintraege insgesamt aufgehoben werden.
HOECHSTZAHL = 100_000
# Dieselbe Zahl zum Hinschreiben, mit Punkten wie hierzulande ueblich.
GRENZTEXT = f"{HOECHSTZAHL:,}".replace(",", ".")

# Woran ein Eintrag endet. Jeder steht als Block, getrennt durch eine
# Leerzeile - so laesst er sich zaehlen, ohne ihn zu deuten.
TRENNER = "\n\n"

# Womit eine Anweisung anfangen muss, damit sie hier landet.
AENDERND = ("UPDATE", "INSERT", "DELETE", "MERGE", "TRUNCATE", "ALTER",
            "DROP", "CREATE")

_sperre = threading.Lock()

# Die letzte Stoerung beim Schreiben - fuer die Statuszeile. Leer heisst:
# nichts zu melden.
meldung = ""


def ist_aendernd(sql) -> bool:
    """Veraendert diese Anweisung etwas?

    Entschieden am ersten Wort. Ein SELECT, das in einer Unterabfrage
    ein UPDATE erwaehnt, gibt es nicht - und ein UPDATE faengt mit
    UPDATE an.
    """
    text = str(sql or "").lstrip()
    # Fuehrende Kommentare weg: eine Anweisung darf erklaert sein.
    while text.startswith("--") or text.startswith("/*"):
        if text.startswith("--"):
            text = text.split("\n", 1)[1].lstrip() if "\n" in text else ""
        else:
            text = text.split("*/", 1)[1].lstrip() if "*/" in text else ""
    return text[:9].upper().split(" ")[0].upper() in AENDERND


_PLATZHALTER = re.compile(r":([A-Za-z_][A-Za-z_0-9]*)")


def als_text(wert) -> str:
    """Ein Wert, wie er im Protokoll steht - nur zum Lesen.

    Zahlen ohne Anfuehrungszeichen, alles andere mit; ein Apostroph im
    Text wird verdoppelt, damit die Zeile aussieht wie eine Anweisung
    und nicht wie eine zerbrochene.
    """
    if wert is None:
        return "null"
    if isinstance(wert, bool):
        return "1" if wert else "0"
    if isinstance(wert, (int, float, decimal.Decimal)):
        return str(wert)
    if isinstance(wert, (datetime.datetime, datetime.date)):
        return "'" + wert.strftime("%d.%m.%Y %H:%M:%S").strip() + "'"
    return "'" + str(wert).replace("'", "''") + "'"


def ausgeschrieben(sql, bindungen=None) -> str:
    """Die Anweisung mit eingesetzten Werten, in einer Zeile.

    Zum Lesen, nicht zum Ausfuehren: ausgefuehrt wird immer die Fassung
    mit Bindevariablen. Ein Name, zu dem kein Wert vorliegt, bleibt
    stehen - dann sieht man, dass er fehlte, statt eine Luecke zu lesen.
    """
    bindungen = bindungen or {}
    einzeilig = " ".join(str(sql or "").split())
    if not isinstance(bindungen, dict):
        return einzeilig
    return _PLATZHALTER.sub(
        lambda treffer: (als_text(bindungen[treffer.group(1)])
                         if treffer.group(1) in bindungen
                         else treffer.group(0)),
        einzeilig)


def dateiname(zeitpunkt=None) -> str:
    return (zeitpunkt or datetime.datetime.now()).strftime(NAMENSMUSTER)


# --------------------------------------------------------------------------
# Das laufende Fenster: die letzten HOECHSTZAHL Eintraege
# --------------------------------------------------------------------------

def dateien(ordner: str) -> list[str]:
    """Die Protokolldateien, aelteste zuerst.

    Sortiert wird ueber den Namen. Er traegt Jahr und Monat in dieser
    Reihenfolge, deshalb ist die alphabetische Folge zugleich die
    zeitliche - kein Blick auf Dateizeiten noetig, die beim Kopieren
    ohnehin verlorengehen.
    """
    try:
        namen = sorted(name for name in os.listdir(ordner)
                       if NAMENSPRUEFUNG.match(name))
    except OSError:
        return []
    return [os.path.join(ordner, name) for name in namen]


def eintraege_in(pfad: str) -> int:
    """Wie viele Eintraege in einer Datei stehen.

    Gezaehlt wird die Leerzeile zwischen zwei Eintraegen - stueckweise
    gelesen, damit auch eine Datei von dreissig Megabyte nicht im
    Speicher landen muss. Ein Zeichen Ueberlappung zwischen den
    Stuecken, sonst entginge ein Trenner, der genau auf die Grenze
    faellt.
    """
    anzahl = 0
    rest = ""
    try:
        with open(pfad, encoding="utf-8", errors="replace") as datei:
            while True:
                stueck = datei.read(1 << 20)
                if not stueck:
                    break
                anzahl += (rest + stueck).count(TRENNER)
                rest = stueck[-1:]
    except OSError:
        return 0
    return anzahl


def eintraege_gesamt(ordner: str) -> int:
    return sum(eintraege_in(pfad) for pfad in dateien(ordner))


def _kuerzen(pfad: str, behalten: int) -> bool:
    """Laesst in einer Datei nur die juengsten `behalten` Eintraege stehen.

    Geschrieben wird ueber eine Nebendatei: bricht es mittendrin ab, ist
    das Protokoll noch da - eine halbe Datei waere schlimmer als eine zu
    lange.
    """
    try:
        with open(pfad, encoding="utf-8", errors="replace") as datei:
            inhalt = datei.read()
        stuecke = [teil for teil in inhalt.split(TRENNER) if teil.strip()]
        if behalten <= 0:
            os.remove(pfad)
            return True
        if len(stuecke) <= behalten:
            return True
        neu = TRENNER.join(stuecke[-behalten:]) + TRENNER
        zwischen = pfad + ".neu"
        try:
            with open(zwischen, "w", encoding="utf-8") as datei:
                datei.write(neu)
            os.replace(zwischen, pfad)
        except OSError:
            # Die halbe Nebendatei hat nichts verloren - sie truege beim
            # naechsten Mal einen Teil des Protokolls doppelt.
            try:
                os.remove(zwischen)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False


def aufraeumen(ordner: str, hoechstzahl: int = HOECHSTZAHL) -> int:
    """Wirft weg, was ueber die Grenze hinaus alt ist - und zaehlt neu.

    Gegangen wird von hinten: der juengste Monat zuerst, bis die Grenze
    erreicht ist. Der Monat, in den sie faellt, wird auf seine juengsten
    Eintraege gekuerzt; was davor liegt, faellt ganz weg.

    Zurueck kommt, wie viele Eintraege danach dastehen. Geht etwas
    schief, bleibt alles liegen: ein Protokoll, das sich nicht kuerzen
    laesst, ist immer noch besser als keines.
    """
    vorhanden = dateien(ordner)
    if not vorhanden:
        return 0
    gezaehlt = [(pfad, eintraege_in(pfad)) for pfad in vorhanden]
    gesamt = sum(anzahl for _, anzahl in gezaehlt)
    if gesamt <= hoechstzahl:
        return gesamt
    behalten = 0
    grenze = len(gezaehlt)                  # ab hier faellt alles weg
    for stelle in range(len(gezaehlt) - 1, -1, -1):
        anzahl = gezaehlt[stelle][1]
        if behalten + anzahl >= hoechstzahl:
            _kuerzen(gezaehlt[stelle][0], hoechstzahl - behalten)
            behalten = hoechstzahl
            grenze = stelle
            break
        behalten += anzahl
        grenze = stelle
    for pfad, _ in gezaehlt[:grenze]:
        try:
            os.remove(pfad)
        except OSError:
            # Bleibt sie liegen, steht mehr da als gewollt - das ist der
            # harmlosere Ausgang und faellt beim naechsten Mal wieder an.
            behalten += eintraege_in(pfad)
    return behalten


class Protokoll:
    """Die Ablage - eine Datei je Monat, angehaengt."""

    def __init__(self, ordner: str, benutzer="", hoechstzahl=None):
        self.ordner = ordner
        self.benutzer = benutzer
        self.meldung = ""
        self.hoechstzahl = (HOECHSTZAHL if hoechstzahl is None
                            else int(hoechstzahl))
        # Wie viele Eintraege dastehen. None heisst: noch nicht gezaehlt.
        # Gezaehlt wird einmal je Sitzung, danach mitgefuehrt - alles
        # andere hiesse, bei jedem Export dreissig Megabyte zu lesen.
        self._bestand = None

    def pfad(self, zeitpunkt=None) -> str:
        return os.path.join(self.ordner, dateiname(zeitpunkt))

    def eintragen(self, sql, bindungen=None, hinweis="", zeitpunkt=None):
        """Schreibt eine Anweisung ins Protokoll - wenn sie eine ist.

        Zurueck kommt, ob etwas geschrieben wurde. Eine Abfrage gibt
        False, ohne dass etwas schiefgegangen waere.
        """
        if not ist_aendernd(sql):
            return False
        jetzt = zeitpunkt or datetime.datetime.now()
        zeilen = [f"{jetzt:%Y-%m-%d %H:%M:%S}"
                  + (f"  {self.benutzer}" if self.benutzer else "")]
        if hinweis:
            zeilen.append(f"LabControl: {hinweis}")
        zeilen.append(ausgeschrieben(sql, bindungen))
        return self._anhaengen("\n".join(zeilen) + TRENNER, jetzt, 1)

    def viele(self, sql, saetze, hinweise=None, zeitpunkt=None) -> int:
        """Dasselbe fuer ein Buendel - je Satz eine Anweisung.

        `hinweise` ist eine Liste von Saetzen, eine je Anweisung, oder
        None. Gebuendelt geschrieben heisst nicht gebuendelt
        protokolliert: was in der Datenbank ankommt, sind einzelne
        Anweisungen, und so stehen sie hier.
        """
        if not ist_aendernd(sql):
            return 0
        jetzt = zeitpunkt or datetime.datetime.now()
        stuecke = []
        for nummer, satz in enumerate(saetze):
            teile = [f"{jetzt:%Y-%m-%d %H:%M:%S}"
                     + (f"  {self.benutzer}" if self.benutzer else "")]
            hinweis = (hinweise[nummer]
                       if hinweise and nummer < len(hinweise) else "")
            if hinweis:
                teile.append(f"LabControl: {hinweis}")
            teile.append(ausgeschrieben(sql, satz))
            stuecke.append("\n".join(teile))
        if not stuecke:
            return 0
        return len(stuecke) if self._anhaengen(
            TRENNER.join(stuecke) + TRENNER, jetzt, len(stuecke)) else 0

    def _anhaengen(self, text: str, zeitpunkt, anzahl: int = 1) -> bool:
        """Haengt an - und haelt nichts auf, wenn es nicht geht."""
        global meldung
        try:
            with _sperre:
                os.makedirs(self.ordner, exist_ok=True)
                with open(self.pfad(zeitpunkt), "a", encoding="utf-8") as datei:
                    datei.write(text)
            self.meldung = ""
        except OSError as fehler:
            self.meldung = (f"Das Aenderungsprotokoll liess sich nicht "
                            f"schreiben: {fehler}")
            meldung = self.meldung
            return False
        self._grenze_halten(anzahl)
        return True

    def _grenze_halten(self, geschrieben: int):
        """Haelt das Protokoll auf den letzten `hoechstzahl` Eintraegen.

        Gezaehlt wird einmal - beim ersten Schreiben dieser Sitzung -,
        danach wird mitgezaehlt. Aufgeraeumt wird nur, wenn die Grenze
        wirklich ueberschritten ist; im Alltag kostet das nichts.

        Auch das darf nichts aufhalten: geht es schief, steht mehr da
        als gewollt, und das ist der harmlosere Ausgang.
        """
        if self.hoechstzahl <= 0:
            return
        try:
            with _sperre:
                if self._bestand is None:
                    self._bestand = eintraege_gesamt(self.ordner)
                else:
                    self._bestand += geschrieben
                if self._bestand > self.hoechstzahl:
                    self._bestand = aufraeumen(self.ordner, self.hoechstzahl)
        except OSError:
            self._bestand = None            # beim naechsten Mal neu zaehlen
