"""
LabControl - Datenschicht (Oracle)
==================================

Kapselt den kompletten Datenbankzugriff. Wie die db.py von LabDoku ist diese
Datei bewusst frei von jeder GUI-Abhaengigkeit, damit sie unabhaengig (auch
automatisiert) getestet werden kann.

Verbindungsweg
--------------
Die Connect-Deskriptoren aus der tnsnames.ora sind fest eingebaut. Es werden
weder TNS_ADMIN noch ORACLE_HOME noch ein OLE-DB-Provider benoetigt.

  * Thin Mode (Standard): python-oracledb spricht das Oracle-Protokoll direkt
    ueber TCP. Kein Oracle-Client noetig, setzt aber Datenbank 12.1+ voraus.
  * Thick Mode: wird automatisch nachgeladen, sobald die Datenbank fuer den
    Thin Mode zu alt ist (Fehler DPY-3010). Dafuer muss das Programm dieselbe
    Bitness haben wie der Client unter C:\\Oracle\\11.2.0 - der ist 32-bit,
    also die x86-Variante der exe verwenden.

Schreiben
---------
Geschrieben wird ausschliesslich mit UPDATE auf genau eine Spalte, immer mit
Bedingung. Bezeichner werden gegen die echte Spaltenliste der Tabelle
abgeglichen (Bezeichner lassen sich in SQL nicht binden, deshalb ist dieser
Abgleich die eigentliche Absicherung), alle Werte gehen als Bind-Variablen in
die Anweisung.
"""

from __future__ import annotations

import csv
import datetime as dt
import decimal
import io
import os
import re
import shutil
import struct
import threading
import time

import oracledb

import protokoll

# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------

# Inhalt der tnsnames.ora, direkt eingebaut.
DESKRIPTOREN = {
    "LIMSTEST": "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db.nw-fva.de)(PORT=1521))"
                "(CONNECT_DATA=(SERVICE_NAME=LIMSTEST.NW-FVA)))",
    "LIMS":     "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db.nw-fva.de)(PORT=1521))"
                "(CONNECT_DATA=(SERVICE_NAME=LIMS.NW-FVA)))",
    "ECO":      "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db.nw-fva.de)(PORT=1521))"
                "(CONNECT_DATA=(SERVICE_NAME=ECO.NW-FVA)))",
}

# Suchpfade fuer den Thick-Mode-Fallback, erster Treffer gewinnt.
# LIMS_ORACLE_CLIENT bleibt als alter Name gueltig, damit eine bereits
# gesetzte Variable nach der Umbenennung weiter greift.
CLIENT_PFADE = [p for p in (os.environ.get("LABCONTROL_ORACLE_CLIENT"),
                            os.environ.get("LIMS_ORACLE_CLIENT"),
                            r"C:\Oracle\11.2.0\bin") if p]
BITNESS = 8 * struct.calcsize("P")          # 32 oder 64

MAX_ZEILEN = 1000                           # Obergrenze der Abfrageanzeige
VORSCHAU_ZEILEN = 1000                      # Zeilen rund um eine Aenderung:
                                            # Vorschau, Stand in der offenen
                                            # Transaktion und Nachweis nach COMMIT
AENDERUNG_FRIST = 300                       # Sekunden bis eine offene
                                            # Aenderung verfaellt

# Nur Bezeichner aus Buchstaben, Ziffern, _ $ # und einem optionalen Schemapraefix.
TABELLEN_MUSTER = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*(\.[A-Za-z][A-Za-z0-9_$#]*)?$")
SPALTEN_MUSTER = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")

# Spaltentypen, deren Werte als Zahl gebunden werden muessen.
ZAHLENTYPEN = (oracledb.DB_TYPE_NUMBER, oracledb.DB_TYPE_BINARY_DOUBLE,
               oracledb.DB_TYPE_BINARY_FLOAT)

oracledb.defaults.fetch_lobs = False        # CLOBs direkt als str

_thick_aktiv = False


# --------------------------------------------------------------------------
# Verbindung
# --------------------------------------------------------------------------

def lade_client() -> None:
    """Laedt den Oracle Client fuer den Thick Mode aus dem ersten passenden Pfad."""
    letzter_fehler: Exception | None = None
    for pfad in CLIENT_PFADE:
        if not os.path.isdir(pfad):
            continue
        try:
            oracledb.init_oracle_client(lib_dir=pfad)
            return
        except Exception as fehler:          # falsche Bitness, unvollstaendig, ...
            letzter_fehler = fehler
    if letzter_fehler is not None:
        raise letzter_fehler
    # Kein Verzeichnis gefunden: Standardsuche ueber PATH versuchen.
    oracledb.init_oracle_client()


class Zugang:
    """Zugangsdaten einer angemeldeten Sitzung.

    Das Passwort lebt nur hier im Arbeitsspeicher und wird nirgends
    gespeichert oder protokolliert.
    """

    # Die Verbindungen koennen in einem Vorrat liegen. Jede Abfrage baut
    # sonst ihre eigene auf - mit Anmeldung, und das ist eine Rundreise
    # mehr als die Abfrage selbst: fuer einen Durchgang vom Anmelden bis
    # zum Export sind das ein Dutzend Anmeldungen. Aus dem Vorrat kommt
    # die Verbindung sofort, und `close()` gibt sie dorthin zurueck statt
    # sie abzubauen - der uebrige Code bleibt, wie er ist.
    #
    # Von Haus aus ist der Vorrat *aus*. Die NW-FVA arbeitet auf Oracle
    # 11.2 ueber den nachgeladenen Client, und dort liess sich nicht
    # pruefen, ob ein Sitzungsvorrat traegt - ein Startbildschirm, der
    # leer bleibt, ist teurer als die Sekunden, die er spart.
    # Eingeschaltet wird er ueber die Umgebungsvariable
    # LABCONTROL_VERBINDUNGSVORRAT=1; laesst er sich dann nicht anlegen,
    # wird ohne ihn weitergearbeitet und der Grund gemerkt.
    VORRAT_MIN = 1
    VORRAT_MAX = 4
    VORRAT_PRUEFUNG = 60        # Sekunden, ab denen vor der Ausgabe geprueft wird
    VORRAT_SCHALTER = "LABCONTROL_VERBINDUNGSVORRAT"
    VORRAT_AN = ("1", "ja", "an", "true", "wahr")

    def __init__(self, benutzer: str, passwort: str, alias: str):
        self.benutzer = benutzer
        self.passwort = passwort
        self.alias = alias
        self.modus = ""
        self._vorrat = None
        self._ohne_vorrat = not self.vorrat_gewuenscht()
        # Steht hier etwas, ist ein gewuenschter Vorrat gescheitert.
        self.vorrat_meldung = ""
        # Die Abfragen laufen im Hintergrundfaden; zwei zugleich duerfen
        # nicht zwei Vorraete anlegen.
        self._sperre = threading.Lock()
        # Das Aenderungsprotokoll. Wird von aussen gesetzt (der
        # Startbildschirm weiss, wo es hingehoert); ohne es laeuft alles
        # wie bisher, nur ungeschrieben.
        self.protokoll = None

    @classmethod
    def vorrat_gewuenscht(cls) -> bool:
        return os.environ.get(cls.VORRAT_SCHALTER, "").strip().lower() \
            in cls.VORRAT_AN

    def verbinden(self):
        """Eine Verbindung - aus dem Vorrat, wenn es einen gibt.

        Zurueck kommt sie eingepackt: der Umschlag schreibt jede
        aendernde Anweisung ins Protokoll, sobald sie festgeschrieben
        ist. Ohne Protokoll (`self.protokoll` ist None) gibt er die
        Verbindung unveraendert weiter - dann kostet er nichts.
        """
        return _mit_protokoll(self._verbinden(), self.protokoll)

    def _verbinden(self):
        with self._sperre:
            if self._ohne_vorrat:
                return self._einzeln()
            if self._vorrat is None:
                try:
                    self._vorrat = self._vorrat_bauen()
                    self.vorrat_meldung = ""
                except Exception as fehler:                 # noqa: BLE001
                    if isinstance(fehler, oracledb.Error) \
                            and _anmeldung_falsch(fehler):
                        raise
                    # Laesst sich kein Vorrat anlegen, wird wie bisher je
                    # Abfrage verbunden. Langsamer, aber es laeuft - und
                    # der Grund geht nicht verloren.
                    self._ohne_vorrat = True
                    self.vorrat_meldung = str(fehler).splitlines()[0]
                    return self._einzeln()
            vorrat = self._vorrat
        # Das Warten auf eine freie Verbindung gehoert nicht unter die
        # Sperre - sonst warteten alle Faeden auf denselben Riegel.
        return vorrat.acquire()

    def verbindungsart(self) -> str:
        """Woher die Verbindungen kommen - fuer die Kopfzeile."""
        if self._vorrat is not None:
            return "Vorrat"
        return f"ohne Vorrat ({self.vorrat_meldung})" if self.vorrat_meldung \
            else "ohne Vorrat"

    def schliessen(self):
        """Baut den Vorrat ab - beim Abmelden und beim Beenden."""
        with self._sperre:
            vorrat, self._vorrat = self._vorrat, None
        if vorrat is not None:
            try:
                vorrat.close(force=True)
            except Exception:                           # noqa: BLE001
                pass

    def _vorrat_bauen(self):
        """Legt den Vorrat an - im richtigen Modus.

        Welcher gilt, klaert eine einzelne Verbindung vorweg: der Thin
        Mode spricht erst mit Oracle 12.1, und der Umstieg auf den
        nachgeladenen Oracle Client haengt an der Fehlermeldung des ersten
        Verbindungsversuchs (DPY-3010). Die NW-FVA arbeitet auf 11.2, dort
        faellt diese Entscheidung immer. Sie hier zu treffen statt sie dem
        Vorrat zu ueberlassen kostet einmal je Sitzung eine Verbindung und
        erspart, dass ein Vorrat im falschen Modus scheitert und alles
        stillschweigend auf den langsamen Weg zurueckfaellt.
        """
        self._einzeln().close()
        angaben = dict(user=self.benutzer, password=self.passwort,
                       dsn=DESKRIPTOREN.get(self.alias, self.alias),
                       min=self.VORRAT_MIN, max=self.VORRAT_MAX, increment=1)
        try:
            return oracledb.create_pool(ping_interval=self.VORRAT_PRUEFUNG,
                                        **angaben)
        except TypeError:
            # Aeltere Treiber kennen die Pruefung vor der Ausgabe nicht.
            return oracledb.create_pool(**angaben)

    def _einzeln(self):
        """Der alte Weg: eine eigene Verbindung, im Thin oder Thick Mode."""
        global _thick_aktiv
        dsn = DESKRIPTOREN.get(self.alias, self.alias)
        try:
            verbindung = oracledb.connect(user=self.benutzer, password=self.passwort,
                                          dsn=dsn)
        except oracledb.Error as fehler:
            if _thick_aktiv or "DPY-3010" not in str(fehler):
                raise
            # Datenbank ist aelter als 12.1 -> Oracle Client nachladen.
            lade_client()
            _thick_aktiv = True
            verbindung = oracledb.connect(user=self.benutzer, password=self.passwort,
                                          dsn=dsn)
        self.modus = "Thick Mode" if _thick_aktiv else "Thin Mode"
        return verbindung


# Falsche Zugangsdaten sind kein Grund, es ohne Vorrat noch einmal zu
# versuchen - das kostete nur eine zweite Fehlmeldung und, bei mehreren
# Versuchen, die Sperrung des Kontos.
ANMELDEFEHLER = ("ORA-01017", "ORA-28000", "ORA-28001", "ORA-01005")


# --------------------------------------------------------------------------
# Der Umschlag um die Verbindung: jede Aenderung ins Protokoll
# --------------------------------------------------------------------------
#
# Angesetzt wird an *einer* Stelle - an der Verbindung, die jede Abfrage
# und jede Anweisung bekommt. Jeden Schreibweg einzeln zu protokollieren
# hiesse, den naechsten zu vergessen; hier kommt keiner vorbei.
#
# Geschrieben wird erst beim COMMIT. Was zurueckgerollt wird, ist nicht
# geschehen und gehoert nicht ins Protokoll - der Export etwa versucht
# das Buendel, rollt bei einer fehlenden Zeile zurueck und schreibt
# danach Satz fuer Satz. Ohne diese Zwischenstufe stuende der erste
# Versuch mit im Protokoll, obwohl ihn niemand mehr sieht.


class _Sammlung:
    """Die Aenderungen einer Transaktion - bis zum COMMIT."""

    def __init__(self, buch):
        self.buch = buch
        self.offen = []

    def merken(self, sql, bindungen, hinweis=""):
        self.offen.append(("einzeln", sql, bindungen, hinweis))

    def merken_viele(self, sql, saetze, hinweise=None):
        self.offen.append(("viele", sql, [dict(s) for s in saetze], hinweise))

    def bestaetigt(self):
        offen, self.offen = self.offen, []
        for art, sql, werte, hinweis in offen:
            if art == "viele":
                self.buch.viele(sql, werte, hinweis)
            else:
                self.buch.eintragen(sql, werte, hinweis)

    def verworfen(self):
        self.offen = []


class _Protokollcursor:
    """Ein Cursor, der jede aendernde Anweisung meldet.

    `hinweis` und `hinweise` lassen sich vor dem Ausfuehren setzen: dann
    steht im Protokoll ein Satz in Worten ueber der Anweisung. Ohne sie
    steht nur die Anweisung da - immer noch besser als nichts.
    """

    def __init__(self, cursor, sammlung):
        object.__setattr__(self, "_cursor", cursor)
        object.__setattr__(self, "_sammlung", sammlung)
        object.__setattr__(self, "hinweis", "")
        object.__setattr__(self, "hinweise", None)

    def execute(self, sql, *args, **rest):
        ergebnis = self._cursor.execute(sql, *args, **rest)
        # Erst ausfuehren, dann merken: was scheitert, ist nicht
        # geschehen. Und was keine Zeile getroffen hat, hat nichts
        # geaendert - eine Anweisung ins Leere gehoert nicht in ein
        # Protokoll ueber Aenderungen.
        if self._merkt(sql):
            bindungen = args[0] if args else rest
            self._sammlung.merken(sql, bindungen, self.hinweis)
        return ergebnis

    def executemany(self, sql, saetze, *args, **rest):
        ergebnis = self._cursor.executemany(sql, saetze, *args, **rest)
        if self._merkt(sql):
            self._sammlung.merken_viele(sql, saetze, self.hinweise)
        return ergebnis

    def _merkt(self, sql) -> bool:
        if self._sammlung is None or not protokoll.ist_aendernd(sql):
            return False
        return bool(self._cursor.rowcount)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *rest):
        return self._cursor.__exit__(*rest)

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_cursor"), name)

    def __setattr__(self, name, wert):
        if name in ("hinweis", "hinweise"):
            object.__setattr__(self, name, wert)
        else:
            setattr(self._cursor, name, wert)


class _Protokollverbindung:
    """Eine Verbindung, deren Cursor mitschreiben."""

    def __init__(self, verbindung, buch):
        object.__setattr__(self, "_verbindung", verbindung)
        object.__setattr__(self, "_sammlung", _Sammlung(buch))

    def cursor(self, *args, **rest):
        return _Protokollcursor(self._verbindung.cursor(*args, **rest),
                                self._sammlung)

    def commit(self, *args, **rest):
        ergebnis = self._verbindung.commit(*args, **rest)
        self._sammlung.bestaetigt()
        return ergebnis

    def rollback(self, *args, **rest):
        self._sammlung.verworfen()
        return self._verbindung.rollback(*args, **rest)

    def close(self, *args, **rest):
        # Nicht festgeschriebenes faellt beim Schliessen weg - dann ist
        # es auch nicht geschehen.
        self._sammlung.verworfen()
        return self._verbindung.close(*args, **rest)

    def __enter__(self):
        self._verbindung.__enter__()
        return self

    def __exit__(self, art, wert, spur):
        # Ein `with` auf der Verbindung schreibt beim sauberen Verlassen
        # fest und rollt sonst zurueck - dasselbe gilt fuers Protokoll.
        if art is None:
            self._sammlung.bestaetigt()
        else:
            self._sammlung.verworfen()
        return self._verbindung.__exit__(art, wert, spur)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_verbindung"), name)

    def __setattr__(self, name, wert):
        setattr(self._verbindung, name, wert)


def _mit_protokoll(verbindung, buch):
    """Packt die Verbindung ein - oder gibt sie unveraendert weiter."""
    if buch is None or verbindung is None:
        return verbindung
    return _Protokollverbindung(verbindung, buch)


def _anmeldung_falsch(fehler: Exception) -> bool:
    text = str(fehler)
    return any(code in text for code in ANMELDEFEHLER)


def anmelden(benutzer: str, passwort: str, alias: str) -> Zugang:
    """Prueft die Zugangsdaten mit einer echten Verbindung und gibt den Zugang."""
    if alias not in DESKRIPTOREN:
        raise ValueError(f"Unbekannte Datenbank '{alias}'.")
    if not benutzer.strip() or not passwort:
        raise ValueError("Benutzer und Passwort werden benoetigt.")
    zugang = Zugang(benutzer.strip(), passwort, alias)
    zugang.verbinden().close()
    return zugang


def fehlernummer(fehler: Exception):
    """Die ORA-Nummer einer Datenbankausnahme - None, wenn es keine gibt.

    Gebraucht wird sie, um *eine* Ursache abzufangen und alle anderen
    weiterzureichen. Ein pauschales "ging nicht" wuerde einen echten
    Fehler in der Abfrage still verschlucken.
    """
    for stelle in getattr(fehler, "args", ()):
        nummer = getattr(stelle, "code", None)
        if nummer is not None:
            try:
                return int(nummer)
            except (TypeError, ValueError):
                return None
    treffer = re.search(r"ORA-(\d+)", str(fehler))
    return int(treffer.group(1)) if treffer else None


def fehlertext(fehler: Exception) -> str:
    """Macht aus einer Ausnahme eine Meldung, die einem Anwender weiterhilft."""
    if isinstance(fehler, oracledb.Error) and fehler.args:
        info = fehler.args[0]
        text = getattr(info, "message", str(fehler))
    else:
        text = str(fehler)

    if "DPI-1047" in text or "DPI-1072" in text:
        pfade = ", ".join(CLIENT_PFADE) or "(kein Pfad konfiguriert)"
        text += (
            f"\n\nDer Oracle Client konnte nicht geladen werden. Gesucht wurde in: "
            f"{pfade}. Haeufigste Ursache: unterschiedliche Bitness. Dieses "
            f"Programm laeuft als {BITNESS}-bit; der Client unter "
            f"C:\\Oracle\\11.2.0 ist 32-bit. In dem Fall die Datei "
            f"LabControl-x86.exe verwenden. Ein anderer Client-Pfad laesst "
            f"sich ueber die Umgebungsvariable LABCONTROL_ORACLE_CLIENT vorgeben."
        )
    return text


# --------------------------------------------------------------------------
# Absicherung von Bezeichnern und Werten
# --------------------------------------------------------------------------

def spalteninfo(cursor, tabelle: str) -> dict:
    """Liefert die Spalten einer Tabelle als {NAME: FetchInfo}.

    Das ist zugleich die Absicherung fuer Spaltennamen: ein Bezeichner, der
    hier nicht vorkommt, geht nie in ein SQL-Statement ein.
    """
    cursor.execute(f"SELECT * FROM {tabelle} WHERE 1 = 0")
    return {beschreibung.name.upper(): beschreibung
            for beschreibung in cursor.description}


def pruefe_spalte(name: str, spalten: dict, rolle: str) -> str:
    """Prueft einen Spaltennamen gegen die tatsaechlichen Spalten der Tabelle."""
    if not SPALTEN_MUSTER.match(name):
        raise ValueError(f"{rolle} '{name}' ist kein gueltiger Spaltenname.")
    oben = name.upper()
    if oben not in spalten:
        raise ValueError(
            f"{rolle} '{name}' gibt es in der Tabelle nicht. "
            f"Vorhanden sind: {', '.join(sorted(spalten))}"
        )
    return oben


def gebundener_wert(info, text: str):
    """Wandelt eine Eingabe passend zum Datentyp der Spalte.

    Numerische Spalten bekommen ein Decimal statt eines float - bei Messwerten
    und Verrechnungen rundet Binaergleitkomma anders als Oracle. Ein Komma als
    Dezimaltrennzeichen wird akzeptiert.
    """
    text = text.strip()
    if info.type_code in ZAHLENTYPEN:
        try:
            return decimal.Decimal(text.replace(",", "."))
        except decimal.InvalidOperation:
            raise ValueError(
                f"'{text}' ist keine Zahl, die Spalte {info.name} ist aber numerisch."
            )
    return text


def pruefe_eingaben(w: dict) -> None:
    """Prueft alles, wofuer keine Datenbank noetig ist."""
    if not TABELLEN_MUSTER.match(w["tabelle"]):
        raise ValueError(
            "Tabellenname ungueltig. Erlaubt sind Buchstaben, Ziffern, _ $ # und "
            "optional ein Schemapraefix."
        )
    for rolle, name in (("Bedingungsspalte", w["bedingung_spalte"]),
                        ("Zielspalte", w["ziel_spalte"])):
        if not SPALTEN_MUSTER.match(name):
            raise ValueError(f"{rolle} '{name}' ist kein gueltiger Spaltenname.")
    if not w["bedingung_wert"].strip():
        raise ValueError(
            "Ohne Bedingungswert wuerde die Aenderung jede Zeile der Tabelle "
            "treffen. Abgebrochen."
        )
    if not w["neuer_wert"].strip():
        raise ValueError("Es ist kein neuer Wert angegeben.")


def schreibbausteine(cursor, w: dict) -> dict:
    """Prueft die Eingaben und liefert die geprueften SQL-Bausteine."""
    pruefe_eingaben(w)
    spalten = spalteninfo(cursor, w["tabelle"])
    bedingung = pruefe_spalte(w["bedingung_spalte"], spalten, "Bedingungsspalte")
    ziel = pruefe_spalte(w["ziel_spalte"], spalten, "Zielspalte")

    bindungen = {
        "bedwert": gebundener_wert(spalten[bedingung], w["bedingung_wert"]),
        "neuwert": gebundener_wert(spalten[ziel], w["neuer_wert"]),
    }
    bedingungstext = f"{bedingung} = :bedwert"
    if w["alter_wert"].strip():
        bindungen["altwert"] = gebundener_wert(spalten[ziel], w["alter_wert"])
        bedingungstext += f" AND {ziel} = :altwert"

    return {"bedingung": bedingungstext, "ziel": ziel, "bindungen": bindungen}


# --------------------------------------------------------------------------
# Lesen
# --------------------------------------------------------------------------

def abfrage(zugang: Zugang, tabelle: str, serie: str) -> dict:
    """Liest bis MAX_ZEILEN Datensaetze einer Serie."""
    if not TABELLEN_MUSTER.match(tabelle):
        raise ValueError(
            "Tabellenname ungueltig. Erlaubt sind Buchstaben, Ziffern, _ $ # und "
            "optional ein Schemapraefix (z. B. LIMSADMIN.ERGEBNISSE)."
        )
    sql = f"SELECT * FROM {tabelle} WHERE SERIE = :serie AND ROWNUM <= :maxzeilen"
    verbindung = zugang.verbinden()
    with verbindung:
        with verbindung.cursor() as cursor:
            cursor.execute(sql, serie=serie, maxzeilen=MAX_ZEILEN)
            spalten = [b.name for b in cursor.description]
            zeilen = cursor.fetchall()
    return {"spalten": spalten, "zeilen": zeilen, "serie": serie,
            "tabelle": tabelle, "modus": zugang.modus}


# --------------------------------------------------------------------------
# Auswahl: Bearbeiter, Serie, Untersuchungsmethode, Geraet
# --------------------------------------------------------------------------
#
# Die Tabellen werden ohne Schemapraefix angesprochen; sie liegen im Schema des
# angemeldeten Benutzers oder sind ueber Synonyme erreichbar.
#
# Kette: Serie -> Untersuchungsmethode (aus TEILPROBEN) -> Geraet (aus
# SERIEN_MW_ANHANG). Ohne Auswahl zeigt offene_serien() alles, was in
# SERIEN_MW_ANHANG als "In Arbeit" markiert ist.

STATIONSGRUPPE_GERAETE = 10                 # SGRU_ID der Messgeraete


def _zeilen(zugang, sql: str, bindungen: dict | None = None, verbindung=None):
    """Fuehrt eine Leseabfrage aus und gibt die Zeilen.

    Mit `verbindung` laesst sich eine fertige Verbindung hereinreichen - das
    nutzen die Tests, um ohne Datenbank zu pruefen.
    """
    if verbindung is not None:
        with verbindung.cursor() as cursor:
            cursor.execute(sql, bindungen or {})
            return cursor.fetchall()
    verbindung = zugang.verbinden()
    with verbindung:
        with verbindung.cursor() as cursor:
            cursor.execute(sql, bindungen or {})
            return cursor.fetchall()


def eindeutige_bezeichnungen(eintraege: list) -> list[str]:
    """Baut Anzeigetexte, die sich sicher wieder zuordnen lassen.

    Normalerweise reicht der Name (Kuerzel bzw. Stationsname). Kommt ein Name
    doppelt vor, wuerde die Zuordnung zur ID mehrdeutig - dann bekommt jeder
    Eintrag seine ID angehaengt. Ein Name, den es gar nicht gibt, wird durch
    die ID ersetzt.
    """
    namen = [(str(name).strip() if name is not None and str(name).strip()
              else f"ID {kennung}") for kennung, name in eintraege]
    if len(set(namen)) == len(namen):
        return namen
    return [f"{name} ({kennung})" for name, (kennung, _) in zip(namen, eintraege)]


def bearbeiter_liste(zugang, verbindung=None) -> list[str]:
    """Alle Anwender aus den Stammdaten - Auswahlliste fuer den Bearbeiter."""
    zeilen = _zeilen(zugang,
                     "SELECT username FROM anwenderstammdaten "
                     "WHERE username IS NOT NULL ORDER BY username",
                     verbindung=verbindung)
    return [str(zeile[0]).strip() for zeile in zeilen if str(zeile[0]).strip()]


# Wie weit die Serienliste im Startbildschirm zurueckreicht, in Jahren.
# Drei sind der Normalfall: aeltere Serien nacharbeiten zu muessen kommt
# vor, aber selten. Einstellbar unter Optionen.
SERIEN_JAHRE = 3


def serien_ab(jahre=None, jetzt=None) -> str:
    """Das aelteste Jahr, das die Serienliste noch zeigt - als Text.

    Die Seriennummer traegt ihr Jahr vorn ("2026P007"), deshalb genuegt
    ein Textvergleich: alles ab "2023" ist von 2023 oder juenger. Bei drei
    Jahren und heute 2026 heisst das 2023, 2022 faellt heraus.
    """
    zahl = als_zahl(jahre)
    schritte = int(zahl) if zahl is not None and int(zahl) >= 0 \
        else SERIEN_JAHRE
    jahr = (jetzt or dt.date.today()).year - schritte
    return f"{max(jahr, 0):04d}"


# Eine Seriennummer beginnt mit ihrem Jahr ("2026P007"). Was das nicht
# tut, ist ein Sonderfall - und ohne Jahr greift auch die Grenze nicht.
JAHR_REGEL = "REGEXP_LIKE(serie, '^[0-9]{4}')"


def serien_liste(zugang, jahre=None, jetzt=None, ohne_jahr=False,
                 verbindung=None) -> list[str]:
    """Alle Serien, neueste zuerst - auch die, die nur ERGEBNISSE noch kennt.

    Beide Haelften in einem Zug. Der Startbildschirm holt sie einzeln
    (`serien_aktuell`, dann `serien_historisch`), damit die laufenden
    Serien nicht auf die Suche in ERGEBNISSE warten muessen.

    In SERIEN steht, was noch laeuft; eine abgeschlossene Serie
    verschwindet dort wieder. Zum Nacharbeiten braucht man sie trotzdem,
    und ihre Ergebniszeilen bleiben stehen - deshalb beide Quellen.

    ERGEBNISSE ist die groesste Tabelle des LIMS; ohne Grenze waere das
    ein Durchlauf ueber alles. Begrenzt wird ueber die Seriennummer selbst
    (`serie >= '2023'`) und nicht ueber ein Datumsfeld: so bleibt es ein
    Vergleich auf der Spalte, die ohnehin gesucht wird, und ein Index
    darauf ist weiter benutzbar. Was noch in SERIEN steht, kommt in jedem
    Fall mit - die Grenze soll die Liste erweitern, nicht kuerzen.

    `ohne_jahr` nimmt auch die Serien auf, deren Nummer nicht mit einer
    vierstelligen Jahreszahl beginnt. Fuer sie gilt keine Grenze - sie
    haben kein Jahr, an dem sie zu messen waere -, und die Abfrage kann
    den Bereich dann nicht mehr eingrenzen. Deshalb ist der Haken in den
    Optionen von Haus aus nicht gesetzt.
    """
    laufende = serien_aktuell(zugang, ohne_jahr, verbindung=verbindung)
    aeltere = serien_historisch(zugang, jahre, jetzt, ohne_jahr,
                                verbindung=verbindung)
    return serien_vereinen(laufende, aeltere)


def serien_aktuell(zugang, ohne_jahr=False, verbindung=None) -> list[str]:
    """Die Serien aus SERIEN - was noch laeuft.

    Die kleine Haelfte der Auswahl und die, die man im Alltag braucht:
    SERIEN fuehrt nur, was noch offen ist. Deshalb steht sie fuer sich -
    so ist die Liste da, bevor die Suche ueber ERGEBNISSE beginnt.
    """
    bedingung = ("serie IS NOT NULL" if ohne_jahr
                 else "serie IS NOT NULL AND " + JAHR_REGEL)
    zeilen = _zeilen(zugang, """
        SELECT DISTINCT serie FROM serien
         WHERE """ + bedingung + """
         ORDER BY serie DESC
    """, verbindung=verbindung)
    return _seriennamen(zeilen)


def serien_historisch(zugang, jahre=None, jetzt=None, ohne_jahr=False,
                      verbindung=None) -> list[str]:
    """Die Serien, die nur noch ERGEBNISSE kennt.

    Das ist die teure Haelfte: ERGEBNISSE ist die groesste Tabelle des
    LIMS, und ohne Index auf SERIE liest die Datenbank sie ganz. Deshalb
    steht sie fuer sich - der Startbildschirm soll nicht darauf warten.
    """
    bedingung = ("serie IS NOT NULL AND (serie >= :ab OR NOT "
                 + JAHR_REGEL + ")" if ohne_jahr else
                 "serie IS NOT NULL AND serie >= :ab AND " + JAHR_REGEL)
    zeilen = _zeilen(zugang, """
        SELECT DISTINCT serie FROM ergebnisse
         WHERE """ + bedingung + """
         ORDER BY serie DESC
    """, {"ab": serien_ab(jahre, jetzt)}, verbindung=verbindung)
    return _seriennamen(zeilen)


def _seriennamen(zeilen) -> list[str]:
    return [str(zeile[0]).strip() for zeile in zeilen
            if zeile[0] is not None and str(zeile[0]).strip()]


def serien_vereinen(*listen) -> list[str]:
    """Alle Serien, jede einmal, neueste zuerst."""
    zusammen = set()
    for liste in listen:
        zusammen.update(liste or ())
    return sorted(zusammen, reverse=True)


def serien_filtern(serien: list[str], suchtext: str) -> list[str]:
    """Schraenkt die Serienliste auf alles ein, was den Suchtext enthaelt."""
    suchtext = suchtext.strip().upper()
    if not suchtext:
        return list(serien)
    return [serie for serie in serien if suchtext in serie.upper()]


def methoden_fuer_serie(zugang, serie: str, verbindung=None) -> list[tuple]:
    """Die Untersuchungsmethoden, zu denen es in dieser Serie Teilproben gibt.

    LEFT JOIN, damit eine um_id ohne Eintrag in UNTERSUCHUNGSMETHODE nicht
    stillschweigend verschwindet - sie taucht dann mit ihrer ID auf.

    Findet sich in TEILPROBEN nichts, wird in ERGEBNISSE nachgesehen: bei
    einer alten Serie kann die Teilprobe geraeumt sein, waehrend ihre
    Ergebniszeilen stehen bleiben. Bleibt auch das leer, sagt
    SERIEN_MW_ANHANG, welche Methode angesetzt ist - eine frisch
    angelegte Serie hat noch keine Ergebniszeilen. Die weiteren Abfragen
    laufen nur dann, und
    sie fragt nach *einer* Serie - das ist keine Suche ueber die ganze
    Tabelle.

    """
    bindungen = {"serie": serie}
    zeilen = _zeilen(zugang, """
        SELECT DISTINCT t.um_id, u.kuerzel
          FROM teilproben t
          LEFT JOIN untersuchungsmethode u ON u.id = t.um_id
         WHERE t.serie = :serie AND t.um_id IS NOT NULL
         ORDER BY u.kuerzel, t.um_id
    """, bindungen, verbindung=verbindung)
    if not zeilen:
        zeilen = _zeilen(zugang, """
            SELECT DISTINCT e.um_id, u.kuerzel
              FROM ergebnisse e
              LEFT JOIN untersuchungsmethode u ON u.id = e.um_id
             WHERE e.serie = :serie AND e.um_id IS NOT NULL
             ORDER BY u.kuerzel, e.um_id
        """, bindungen, verbindung=verbindung)
    if not zeilen:
        # Zuletzt die Zuordnung selbst: eine angesetzte Serie steht in
        # SERIEN_MW_ANHANG mit ihrer Methode, auch bevor Teilproben
        # angelegt oder Ergebniszeilen geschrieben sind.
        zeilen = _zeilen(zugang, """
            SELECT DISTINCT a.um_id, u.kuerzel
              FROM serien_mw_anhang a
              LEFT JOIN untersuchungsmethode u ON u.id = a.um_id
             WHERE a.serie = :serie AND a.um_id IS NOT NULL
             ORDER BY u.kuerzel, a.um_id
        """, bindungen, verbindung=verbindung)
    return [(zeile[0], zeile[1]) for zeile in zeilen]


def geraete_fuer(zugang, serie: str, um_id, verbindung=None) -> list[tuple]:
    """Die Messgeraete, die fuer diese Serie und Methode hinterlegt sind.

    Steht in SERIEN_MW_ANHANG nichts mehr - bei einer abgeschlossenen
    Serie kann die Zeile geraeumt sein -, sagen die Ergebniszeilen, an
    welchem Geraet gemessen wurde: ihre GEGR_ID ist dieselbe Nummer wie
    die STAT_ID der Station. Auch das nur, wenn der Normalweg leer bleibt.

    """
    bindungen = {"serie": serie, "um_id": um_id,
                 "gruppe": STATIONSGRUPPE_GERAETE}
    zeilen = _zeilen(zugang, """
        SELECT DISTINCT a.stat_id, s.station
          FROM serien_mw_anhang a
          JOIN stationen s ON s.id = a.stat_id
         WHERE a.serie = :serie AND a.um_id = :um_id
           AND s.sgru_id = :gruppe
         ORDER BY s.station
    """, bindungen, verbindung=verbindung)
    if not zeilen:
        zeilen = _zeilen(zugang, """
            SELECT DISTINCT e.gegr_id, s.station
              FROM ergebnisse e
              JOIN stationen s ON s.id = e.gegr_id
             WHERE e.serie = :serie AND e.um_id = :um_id
               AND e.gegr_id IS NOT NULL
               AND s.sgru_id = :gruppe
             ORDER BY s.station
        """, bindungen, verbindung=verbindung)
    return [(zeile[0], zeile[1]) for zeile in zeilen]


def serien_angesetzt(zugang, ohne_jahr=False, verbindung=None) -> list[str]:
    """Die Serien, die an einer Station stehen - in welchem Stand auch immer.

    SERIEN_MW_ANHANG haelt die Zuordnung Serie/Methode/Station. Das Flag
    sagt, wie weit sie ist: "In Arbeit" ist die laufende Messung, davor
    steht "Start". Hier wird bewusst *nicht* danach gefiltert - wer eine
    angesetzte Serie schon ansehen will, soll sie in der Auswahl finden.

    Die Uebersicht im Startbildschirm bleibt davon unberuehrt: sie zeigt
    weiter nur, was wirklich laeuft (`offene_serien`).

    Die Tabelle ist klein - sie fuehrt nur, was ansteht -, deshalb kostet
    diese Abfrage nichts und laeuft mit der Serienliste mit.
    """
    bedingung = ("serie IS NOT NULL" if ohne_jahr
                 else "serie IS NOT NULL AND " + JAHR_REGEL)
    zeilen = _zeilen(zugang, """
        SELECT DISTINCT serie FROM serien_mw_anhang
         WHERE """ + bedingung + """
         ORDER BY serie DESC
    """, verbindung=verbindung)
    return _seriennamen(zeilen)


def offene_serien(zugang, verbindung=None) -> list[dict]:
    """Alles, was in SERIEN_MW_ANHANG als "In Arbeit" markiert ist.

    Der Vergleich ist bewusst tolerant: eine abweichende Schreibweise wuerde
    sonst eine leere Liste ergeben, und das saehe aus wie "nichts zu tun".
    """
    zeilen = _zeilen(zugang, """
        SELECT DISTINCT a.serie, a.um_id, u.kuerzel, a.stat_id, s.station
          FROM serien_mw_anhang a
          JOIN stationen s ON s.id = a.stat_id
          LEFT JOIN untersuchungsmethode u ON u.id = a.um_id
         WHERE UPPER(TRIM(a.flag)) = 'IN ARBEIT'
           AND s.sgru_id = :gruppe
         ORDER BY a.serie DESC, u.kuerzel, s.station
    """, {"gruppe": STATIONSGRUPPE_GERAETE}, verbindung=verbindung)
    return [{"serie": str(z[0]).strip() if z[0] is not None else "",
             "um_id": z[1],
             "kuerzel": (str(z[2]).strip() if z[2] is not None
                         and str(z[2]).strip() else f"ID {z[1]}"),
             "stat_id": z[3],
             "station": (str(z[4]).strip() if z[4] is not None
                         and str(z[4]).strip() else f"ID {z[3]}")}
            for z in zeilen]


# --------------------------------------------------------------------------
# Pruefungen eines Geraets
# --------------------------------------------------------------------------
#
# Welche Pruefungen an einer Station laufen, steht im LIMS:
#   GERAETE_PRUEFUNGEN_ANHANG  verknuepft GEGR_ID (Geraetegruppe) mit GEPR_ID
#   GERAETE_PRUEFUNGEN         ID = GEPR_ID, PRUEFUNG = Name, STATUS
#
# Nicht GERAETE_GRUPPEN_ANHANG - die traegt GEGR_ID und GEME_ID und hat gar
# keine GEPR_ID.
#
# Die GEGR_ID ist derselbe Schluessel wie die STAT_ID der Station (und die ID
# in GERAETE_GRUPPEN), deshalb geht die Abfrage direkt mit der stat_id hinein.
# Sollten die beiden Nummernkreise spaeter auseinanderlaufen, ist hier eine
# Zeile zu aendern.
#
# Durchgefuehrt wird eine Pruefung nur mit Status "freigegeben". Zurueck kommen
# trotzdem *alle* zugeordneten Pruefungen, jede mit ihrem Status: sonst liesse
# sich die Frage "warum lief diese Pruefung nicht?" nur in der Datenbank
# beantworten.

# GERAETE_PRUEFUNGEN.STATUS kennt drei Werte: "freigegeben" laeuft,
# "obligat" laeuft ebenfalls - sie ist Pflicht und nicht abwaehlbar -,
# "gesperrt" laeuft nicht. Obligat als "laeuft nicht" zu lesen war ein
# Fehler: die Haelfte der Ansichten (Laufdatei, Messung, Ergebnis,
# Anforderung, Export) steht genau so in der Tabelle.
STATUS_LAEUFT = ("FREIGEGEBEN", "OBLIGAT")
STATUS_FREIGEGEBEN = "FREIGEGEBEN"


def pruefung_laeuft(status) -> bool:
    """Wird diese Pruefung am Geraet durchgefuehrt?

    Tolerant verglichen, damit eine abweichende Schreibweise keine
    Pruefung verschluckt.
    """
    return str(status or "").strip().upper() in STATUS_LAEUFT


def pruefungen_fuer_station(zugang, stat_id, verbindung=None) -> list[dict]:
    """Die Pruefungen, die dieser Station zugeordnet sind."""
    zeilen = _zeilen(zugang, """
        SELECT DISTINCT p.id, p.pruefung, p.status
          FROM geraete_pruefungen_anhang a
          JOIN geraete_pruefungen p ON p.id = a.gepr_id
         WHERE a.gegr_id = :gegr_id
         ORDER BY p.pruefung, p.id
    """, {"gegr_id": stat_id}, verbindung=verbindung)
    return [{"gepr_id": z[0],
             "pruefung": (str(z[1]).strip() if z[1] is not None
                          and str(z[1]).strip() else f"ID {z[0]}"),
             "status": str(z[2]).strip() if z[2] is not None else "",
             "laeuft": pruefung_laeuft(z[2])}
            for z in zeilen]


def laufende_pruefungen(pruefungen: list[dict]) -> list[dict]:
    """Die Pruefungen, die tatsaechlich durchgefuehrt werden."""
    return [p for p in pruefungen if p["laeuft"]]


# --------------------------------------------------------------------------
# Stammdaten eines Laufs
# --------------------------------------------------------------------------
#
# Was hier eingesammelt wird, baut nach, was im LIMS bisher ueber
# RELAQS_SOURCE / RELAQS_WORK / V_RELAQS_WORK zusammengetragen wurde. Das
# Altsystem loest LabControl ab; die Namen sind bewusst daran angelehnt,
# damit sich beide Seiten vergleichen lassen, solange noch beide laufen.
#
# Die Abfragen holen roh, was in der Datenbank steht. Das Zuordnen und
# Rechnen passiert danach in reinen Funktionen - die lassen sich ohne
# Oracle pruefen, und ein Pruefmodul bekommt spaeter schlichte Listen und
# Dictionaries statt eines Cursors.

# Der Status, den eine Ergebniszeile nach dem Export traegt, und das
# Korrekturkennzeichen daneben. Anders als jede Zahl werden beide
# gesetzt und nicht ergaenzt: sie beschreiben nicht den Messwert,
# sondern den Bearbeitungsstand der Zeile, und der ist nach dem Senden
# derselbe - ob der Wert aus diesem Lauf kam oder schon dastand. Es sind
# die einzigen Spalten, die der Export ueberschreibt.
PSTA_GESENDET = 2
KORREKTUR_FLAG = "F"

LSTA_FREIGEGEBEN = 0                        # LISTENSTATUS.ID des freigegebenen
                                            # Stands (VERFAHRENSKENNDATEN,
                                            # PRUEFMETHODEN)

ENDFAKTOR_TOLERANZ = decimal.Decimal("0.03")    # +-3% zwischen gespeichertem
                                                # END_FAKTOR und FAKTOR *
                                                # FAKTOR_WGH


def _dicts(zeilen, felder: tuple) -> list[dict]:
    return [dict(zip(felder, zeile)) for zeile in zeilen]


def als_zahl(wert):
    """Macht aus einem Datenbankwert eine Zahl - None, wenn es keine ist.

    STANDARD_PARA fuehrt SOLLWERT, GU und GO als VARCHAR2. Dort steht in der
    Regel eine Zahl, aber eben als Text und teils mit Dezimalkomma. Was sich
    nicht lesen laesst (etwa "<0,1"), gibt None zurueck statt zu raten - der
    Rohtext bleibt daneben stehen, damit nichts still verschwindet.
    """
    if wert is None:
        return None
    if ist_zahl(wert):
        return decimal.Decimal(str(wert))
    text = str(wert).strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return decimal.Decimal(text)
    except (decimal.InvalidOperation, ValueError):
        return None


# ------------------------------------------------------------ Probenschluessel

def probenschluessel(probe_nr, wdh_um, wdh_me) -> str:
    """Die Bezeichnung, unter der eine Probe in der Laufdatei steht.

    Wiederholungen bekommen im LIMS eine eigene PROBEN-Zeile mit derselben
    PROBE_NR und hochgezaehltem WDH_UM. In der Laufdatei stehen dann beide
    Zaehler hinter der Nummer: "2026P00554 2 1". Ist keiner der beiden
    ungleich 1, bleibt die Nummer wie sie ist.
    """
    nummer = str(probe_nr or "").strip()
    um = int(wdh_um) if wdh_um is not None else 1
    me = int(wdh_me) if wdh_me is not None else 1
    if um == 1 and me == 1:
        return nummer
    return f"{nummer} {um} {me}"


STANDARD_PRAEFIX = re.compile(r"^(\d+)/(.*)$")


def standard_zerlegen(bezeichnung: str) -> tuple[int | None, str]:
    """Trennt die Wiederholungsnummer vom Standardnamen.

    Blindwert- und Referenzstandards stehen in der Laufdatei mit einem
    vorangestellten Zaehler: "1/BlindMWN1.1", "2/BlindMWN1.1". Der Zaehler
    ist die laufende Wiederholung im Lauf, der Rest der Name aus
    STANDARDVERWALTUNG. Kontrollstandards kommen ohne Praefix.
    """
    text = str(bezeichnung or "").strip()
    treffer = STANDARD_PRAEFIX.match(text)
    if not treffer:
        return None, text
    return int(treffer.group(1)), treffer.group(2).strip()


# ------------------------------------------------------------------ Ergebnisse

ERGEBNIS_FELDER = (
    "prob_id", "probe_nr", "wdh_um", "wdh_me", "bemerkung",
    "serie", "um_id", "pm_id",
    "pm_ver", "para_id", "einh_id", "einh_ver_id", "vkd_id", "part_id",
    "gegr_id", "geme_id", "stan_id", "lnr", "mw", "mw_roh", "mw_org",
    "kommentar",
    "faktor", "faktor_wgh", "end_faktor",
    "parameter", "pruefmethode", "pm_lsta_id", "einheit", "einheit_ver",
    "probenart",
)


def ergebniszeilen(zugang, serie: str, um_id, gegr_id,
                   verbindung=None) -> list[dict]:
    """Alle Ergebniszeilen des Laufs - Serie, Untersuchungsmethode, Station.

    Das ist der Anker: welche Proben mit welchen Parametern gehoeren zu
    diesem Lauf. Bewusst ohne Obergrenze - eine Serie mit hundert Proben und
    einem Dutzend Parametern liegt jenseits der 1000 Zeilen der
    Abfrageanzeige, und eine stillschweigend abgeschnittene Probenliste
    waere schlimmer als eine langsame Abfrage.

    Die Verfahrenskenndaten haengen bewusst *nicht* mit im Join: dort kann
    mehr als eine Zeile passen, und ein Join wuerde die Ergebniszeile dann
    unbemerkt verdoppeln. Sie kommen ueber kenndaten_zuordnen() dazu.
    """
    zeilen = _zeilen(zugang, """
        SELECT e.prob_id, p.probe_nr, p.wdh_um, p.wdh_me, p.bemerkung,
               e.serie,
               e.um_id, e.pm_id, e.pm_ver, e.para_id, e.einh_id,
               e.einh_ver_id, e.vkd_id,
               e.part_id, e.gegr_id, e.geme_id, e.stan_id, e.lnr,
               e.mw, e.mw_roh, e.mw_org, e.kommentar,
               t.faktor, t.faktor_wgh, t.end_faktor,
               pa.name, pm.name, pm.lsta_id, ei.einheit, ev.einheit, ar.art
          FROM ergebnisse e
          JOIN proben p                   ON p.id = e.prob_id
          LEFT JOIN teilproben t          ON t.prob_id = e.prob_id
                                         AND t.um_id = e.um_id
          LEFT JOIN parameter pa          ON pa.id = e.para_id
          LEFT JOIN pruefmethoden pm      ON pm.id = e.pm_id
                                         AND pm.version = e.pm_ver
          LEFT JOIN einheiten ei          ON ei.id = e.einh_id
          LEFT JOIN einheiten ev          ON ev.id = e.einh_ver_id
          LEFT JOIN probenart ar          ON ar.id = e.part_id
         WHERE e.serie = :serie AND e.um_id = :um_id AND e.gegr_id = :gegr_id
         ORDER BY p.probe_nr, p.wdh_um, p.wdh_me, pa.name, e.pm_id, e.pm_ver
    """, {"serie": serie, "um_id": um_id, "gegr_id": gegr_id},
        verbindung=verbindung)
    ergebnisse = _dicts(zeilen, ERGEBNIS_FELDER)
    for zeile in ergebnisse:
        zeile["probe"] = probenschluessel(zeile["probe_nr"], zeile["wdh_um"],
                                          zeile["wdh_me"])
        # Freigegeben ist LSTA_ID 0 - nur solche Pruefmethoden stehen
        # spaeter zur Auswahl.
        zeile["pm_freigegeben"] = zeile["pm_lsta_id"] == LSTA_FREIGEGEBEN
        zeile.update(endfaktor_pruefen(zeile["faktor"], zeile["faktor_wgh"],
                                       zeile["end_faktor"]))
    return ergebnisse


def endfaktor_pruefen(faktor, faktor_wgh, end_faktor,
                      toleranz=ENDFAKTOR_TOLERANZ) -> dict:
    """Rechnet den Endfaktor nach: FAKTOR * FAKTOR_WGH.

    Nachgerechnet statt uebernommen, weil ein falscher Endfaktor jedes
    Ergebnis der Probe verschiebt und beim blossen Uebernehmen niemandem
    auffiele. Erlaubt sind 3% Abweichung - Rundung in der Datenbank soll
    keinen Alarm ausloesen.

    Eine leere Spalte gilt als 1: nicht jede Untersuchungsmethode fuehrt
    einen Wassergehalt, und wo nichts steht, ist nichts umzurechnen. Nur
    wenn alle drei leer sind, ist der Endfaktor 1 - und das ist dann
    keine Luecke, sondern die Aussage "unveraendert".
    """
    a, b = als_zahl(faktor), als_zahl(faktor_wgh)
    ist = als_zahl(end_faktor)
    eins = decimal.Decimal(1)
    ergaenzt = [name for name, wert in (("FAKTOR", a), ("FAKTOR_WGH", b))
                if wert is None]
    a = eins if a is None else a
    b = eins if b is None else b
    soll = a * b
    if ist is None:
        # Kein END_FAKTOR gepflegt: dann gilt das Produkt der beiden
        # anderen - beim ICP steht dort regelmaessig nichts.
        return {"endfaktor_soll": soll, "endfaktor_abweichung": None,
                "endfaktor_status": "ergaenzt",
                "endfaktor_hinweis": "END_FAKTOR leer, gerechnet mit "
                                     + " * ".join(
                                         t for t in
                                         (f"FAKTOR {_text_zahl(a)}",
                                          f"FAKTOR_WGH {_text_zahl(b)}"))
                                     + (f" ({', '.join(ergaenzt)} leer, "
                                        f"als 1 genommen)" if ergaenzt
                                        else "")}
    if soll == 0:
        abweichung = decimal.Decimal(0) if ist == 0 else None
    else:
        abweichung = (ist - soll) / soll
    if abweichung is None:
        status = "abweichung"
    else:
        status = "ok" if abs(abweichung) <= toleranz else "abweichung"
    return {"endfaktor_soll": soll, "endfaktor_abweichung": abweichung,
            "endfaktor_status": status,
            "endfaktor_hinweis": (f"{', '.join(ergaenzt)} leer, als 1 "
                                  f"genommen" if ergaenzt else "")}


def _text_zahl(wert) -> str:
    """Eine Zahl fuer den Hinweistext - ohne Exponent, mit Komma."""
    return format(wert, "f").rstrip("0").rstrip(".").replace(".", ",") \
        if wert == wert.to_integral_value() or True else str(wert)


# -------------------------------------------------------- Verfahrenskenndaten

# ERGEBNISSE.VKD_ID zeigt auf VERFAHRENSKENNDATEN.ID - und diese ID ist
# *keine* Zeilennummer, sondern der Stand der Kenndaten: im ganzen
# Auszug einer Serie kommen nur die Werte 1 und 2 vor. Eindeutig wird
# eine Zeile erst ueber
#
#     ID + PM_ID + PM_VER + UM_ID + PART_ID + LSTA_ID
#
# (nachgezaehlt an einem echten Auszug: 131 Zeilen, 131 Schluessel).
# Deshalb steht UM_ID in der Abfrage und die uebrigen vier Felder im
# Abgleich - eine Suche ueber die ID allein traefe die halbe Tabelle.
# Freigegeben ist LSTA_ID 0 (LISTENSTATUS: 0 freigegeben, 1 gesperrt,
# 2 in Arbeit, 3 unbekannt).
# Was zwischen Kenndatenzeile und Ergebniszeile uebereinstimmen muss -
# danach wird vorsortiert und danach wird verglichen.
# Pflicht ist, was eine Kenndatenzeile immer fuehrt: Pruefmethode,
# Version und Untersuchungsmethode.
KENNDATEN_SCHLUESSEL = ("pm_id", "pm_ver", "um_id")
# Eingrenzend wirkt, was sie fuehren *kann* - eine leere Spalte gilt fuer
# alle. PART_ID gehoert dazu: am TOC ist sie in VERFAHRENSKENNDATEN nicht
# gepflegt, und die UM_ID ist ohnehin je Probenart eine eigene. Eine leere
# PART_ID als Pflichtfeld liesse dort jede Zuordnung scheitern. Steht sie
# aber da, gilt sie auch gegen den Zeiger: die Grenzen einer anderen
# Probenart sind die falschen Grenzen.
KENNDATEN_PROBENART = "part_id"
# Der Geraetebezug zaehlt nur ohne Zeiger - die Kenndaten koennen an einer
# anderen Geraetegruppe haengen als der Lauf und trotzdem seine sein.
KENNDATEN_EINGRENZUNG = ("gegr_id", "geme_id")
# Fuer die Fehlermeldung: das sind die Merkmale, ueber die man dann
# spricht.
KENNDATEN_MERKMALE = KENNDATEN_SCHLUESSEL + ("part_id",)

KENNDATEN_FELDER = ("vkd_id", "pm_id", "pm_ver", "um_id", "part_id",
                    "gegr_id", "geme_id", "gera_id", "lsta_id",
                    "nwg_arbeit", "bg_arbeit", "ogrenze", "einh_id")


def kenndaten_fuer_lauf(zugang, serie: str, um_id, gegr_id,
                        verbindung=None) -> list[dict]:
    """Die freigegebenen Verfahrenskenndaten der Pruefmethoden dieses Laufs.

    Geholt werden die Kandidaten, nicht die Zuordnung: welche Zeile zu
    welchem Ergebnis gehoert, entscheidet kenndaten_zuordnen() - dort laesst
    sich das ohne Datenbank pruefen und eine Mehrdeutigkeit sichtbar machen.

    Genommen werden NWG_ARBEIT, BG_ARBEIT und OGRENZE (die Arbeitswerte),
    nicht NACHWEISGRENZE / BESTIMMUNGSGRENZE / ARBEITSBEREICH_OBEN.

    Eingegrenzt auf die Pruefmethoden des Laufs und auf seine
    Untersuchungsmethode: beides zusammen laesst je Parameter genau eine
    freigegebene Zeile uebrig. Welche davon zu welcher Ergebniszeile
    gehoert, entscheidet kenndaten_zuordnen() ueber den Zeiger VKD_ID.
    """
    zeilen = _zeilen(zugang, """
        SELECT v.id, v.pm_id, v.pm_ver, v.um_id, v.part_id,
               v.gegr_id, v.geme_id, v.gera_id, v.lsta_id,
               v.nwg_arbeit, v.bg_arbeit, v.ogrenze, v.einh_id
          FROM verfahrenskenndaten v
         WHERE v.lsta_id = :lsta
           AND v.um_id = :um_id
           AND (v.pm_id, v.pm_ver) IN (
                 SELECT DISTINCT e.pm_id, e.pm_ver FROM ergebnisse e
                  WHERE e.serie = :serie AND e.um_id = :um_id
                    AND e.gegr_id = :gegr_id)
         ORDER BY v.pm_id, v.pm_ver, v.id
    """, {"lsta": LSTA_FREIGEGEBEN, "serie": serie, "um_id": um_id,
          "gegr_id": gegr_id}, verbindung=verbindung)
    return _dicts(zeilen, KENNDATEN_FELDER)


def geraete_anhang(zugang, verbindung=None) -> set:
    """Welche Geraetemethode auf welchem Geraet laeuft: {(GERA_ID, GEME_ID)}.

    Der Umweg zum Geraet. VERFAHRENSKENNDATEN fuehrt GEGR_ID und GEME_ID,
    beide sind bei uns aber leer; gepflegt ist GERA_ID - das konkrete
    Geraet. Welche Geraetemethoden darauf laufen, sagt erst diese Tabelle,
    und ueber sie trifft sich eine Kenndatenzeile mit der GEME_ID einer
    Ergebniszeile.

    Die Tabelle ist klein (gut hundert Zeilen) und gilt fuer das ganze
    Haus - sie wird deshalb ganz geholt und nicht je Lauf eingegrenzt.
    """
    zeilen = _zeilen(zugang,
                     "SELECT gera_id, geme_id FROM geraete_anhang "
                     "WHERE gera_id IS NOT NULL AND geme_id IS NOT NULL",
                     verbindung=verbindung)
    return {(zeile[0], zeile[1]) for zeile in zeilen}


def _laeuft_auf_geraet(kenndatum: dict, geme_id, geraete) -> bool | None:
    """Laeuft die Geraetemethode der Ergebniszeile auf diesem Geraet?

    None heisst "nicht zu beantworten": ohne GERA_ID in der
    Kenndatenzeile, ohne GEME_ID in der Ergebniszeile oder ohne die
    Zuordnungstabelle laesst sich nichts sagen - dann entscheidet das
    Geraet eben nicht mit.
    """
    gera_id = kenndatum.get("gera_id")
    if gera_id is None or geme_id is None or not geraete:
        return None
    return (gera_id, geme_id) in geraete


def _passt_kenndatum(kenndatum: dict, ergebnis: dict,
                     ueber_vkd=False) -> bool:
    """Trifft diese Kenndatenzeile auf diese Ergebniszeile zu?

    Pflicht sind in beiden Faellen Pruefmethode, Version und
    Untersuchungsmethode.

    Dazu kommt die Probenart, sofern die Kenndatenzeile eine nennt - und
    zwar auf beiden Wegen: die Grenzen einer anderen Probenart sind die
    falschen Grenzen, auch wenn der Zeiger auf ihren Stand zeigt.
    VERFAHRENSKENNDATEN.ID ist der Stand, nicht die Zeile; derselbe Stand
    steht bei mehreren Probenarten.

    `ueber_vkd` ist der Weg ueber den Zeiger: die Ergebniszeile nennt in
    VKD_ID selbst ihre Kenndaten, und zusammen mit UM_ID, PM_ID, PM_VER
    und der Probenart ist das eindeutig. GEGR_ID und GEME_ID werden dann
    *nicht* verglichen - die Kenndaten koennen an einer anderen
    Geraetegruppe haengen als der Lauf, und trotzdem sind es seine.

    Eine leere Spalte heisst dabei ueberall "gilt fuer alle", nicht
    "passt zu keinem": PART_ID, GEGR_ID und GEME_ID sind bei uns
    haeufig leer.
    """
    for feld in KENNDATEN_SCHLUESSEL:
        if kenndatum[feld] != ergebnis[feld]:
            return False
    art = kenndatum[KENNDATEN_PROBENART]
    if art is not None and art != ergebnis[KENNDATEN_PROBENART]:
        return False
    if ueber_vkd:
        return kenndatum["vkd_id"] == ergebnis["vkd_id"]
    for feld in KENNDATEN_EINGRENZUNG:
        if kenndatum[feld] is not None and kenndatum[feld] != ergebnis[feld]:
            return False
    return True


def kenndaten_zuordnen(ergebnisse: list[dict], kenndaten: list[dict],
                       geraete=None) -> list[dict]:
    """Haengt Nachweis-, Bestimmungsgrenze und Obergrenze an die Ergebnisse.

    Passt genau eine Zeile, werden ihre Werte uebernommen. Passt keine oder
    passen mehrere, bleiben die Grenzen leer und die Ergebniszeile bekommt
    einen Vermerk: eine geratene Grenze waere schlimmer als eine fehlende,
    weil an ihr spaeter Messwerte verworfen werden.

    Passen mehrere, entscheidet das Geraet: `geraete` ist die Zuordnung
    aus GERAETE_ANHANG, und nur die Kenndatenzeile, deren GERA_ID die
    Geraetemethode dieser Ergebniszeile traegt, gehoert zu ihr. Das ist
    der Umweg, den es braucht, weil GEGR_ID und GEME_ID in
    VERFAHRENSKENNDATEN leer sind und stattdessen GERA_ID gepflegt ist.

    Passen danach immer noch mehrere, zaehlt die Probenart: eine Zeile,
    die genau diese PART_ID nennt, ist naeher dran als eine, die fuer
    alle Probenarten gilt.

    Verglichen wird nur innerhalb der Gruppe, die in Pruefmethode,
    Version und Untersuchungsmethode ohnehin uebereinstimmt -
    alles andere kann nicht passen. Ohne diese Vorsortierung waren es bei
    sechstausend Ergebniszeilen und hundertdreissig Kenndatenzeilen ueber
    anderthalb Millionen Vergleiche.
    """
    geraete = geraete or set()
    nach_methode = {}
    for kenndatum in kenndaten:
        nach_methode.setdefault(
            tuple(kenndatum[feld] for feld in KENNDATEN_SCHLUESSEL),
            []).append(kenndatum)
    for ergebnis in ergebnisse:
        gruppe = nach_methode.get(
            tuple(ergebnis[feld] for feld in KENNDATEN_SCHLUESSEL), ())
        # Erst der Zeiger aus der Ergebniszeile, dann der Vergleich ueber
        # das Geraet: steht in VKD_ID etwas, ist das die Auskunft der
        # Datenbank selbst und keine Ableitung.
        treffer = []
        if ergebnis.get("vkd_id") is not None:
            treffer = [k for k in gruppe
                       if _passt_kenndatum(k, ergebnis, ueber_vkd=True)]
        if not treffer:
            treffer = [k for k in gruppe if _passt_kenndatum(k, ergebnis)]
        # Bleiben mehrere, entscheidet das Geraet: nur die Zeile, deren
        # GERA_ID die Geraetemethode dieser Ergebniszeile traegt, gehoert
        # zu ihr. Erst hier, nicht vorher - sonst fiele eine Zeile heraus,
        # die sonst einwandfrei passt und nur kein Geraet nennt.
        if len(treffer) > 1:
            # Erst die Probenart: eine Zeile, die sie nennt, schlaegt die
            # allgemeine. Sonst waere eine gepflegte PART_ID nichts wert,
            # sobald daneben eine Zeile ohne steht.
            genau = [k for k in treffer
                     if k["part_id"] is not None
                     and k["part_id"] == ergebnis["part_id"]]
            if len(genau) == 1:
                treffer = genau
        if len(treffer) > 1:
            am_geraet = [k for k in treffer
                         if _laeuft_auf_geraet(k, ergebnis.get("geme_id"),
                                               geraete)]
            if len(am_geraet) == 1:
                treffer = am_geraet
        if len(treffer) == 1:
            gefunden = treffer[0]
            ergebnis.update({
                "vkd_gefunden": gefunden["vkd_id"],
                "nwg": gefunden["nwg_arbeit"],
                "bg": gefunden["bg_arbeit"],
                "ogrenze": gefunden["ogrenze"],
                # Der Vermerk gilt dem Widerspruch: die Ergebniszeile
                # nennt einen Stand, gefunden wurde ein anderer. Ist
                # VKD_ID leer, gibt es keinen Widerspruch - dann ist der
                # gefundene Stand die einzige Auskunft, die es gibt.
                "kenndaten_hinweis": (
                    "" if (ergebnis["vkd_id"] is None
                           or gefunden["vkd_id"] == ergebnis["vkd_id"])
                    else f"weicht von VKD_ID {ergebnis['vkd_id']} "
                         f"in ERGEBNISSE ab"),
            })
        else:
            ergebnis.update({
                "vkd_gefunden": None, "nwg": None, "bg": None,
                "ogrenze": None,
                "kenndaten_hinweis": (_ohne_kenndaten(ergebnis, kenndaten)
                                      if not treffer
                                      else f"{len(treffer)} Kenndatensaetze "
                                           f"passen - nicht eindeutig"),
            })
    return ergebnisse


def _kenndatentext(zeile: dict) -> str:
    """Die Kombination, um die es geht - in einer lesbaren Zeile."""
    return (f"PM {zeile['pm_id']}/{zeile['pm_ver']}, "
            f"UM {zeile['um_id']}, "
            f"Probenart {zeile['part_id'] if zeile['part_id'] is not None else '-'}")


def _ohne_kenndaten(ergebnis: dict, kenndaten: list[dict]) -> str:
    """Warum zu dieser Ergebniszeile nichts passt.

    "keine freigegebenen Kenndaten" laesst offen, ob gar keine Zeile da
    ist oder ob die Zeile da ist und nur nicht passt - und genau das ist
    die Frage, die man dann stellt. Deshalb stehen hier die Werte selbst:
    gesucht wird die eine Kombination, vorhanden sind die anderen. Wer das
    liest, sieht ohne Abfrage, welche Seite gepflegt werden muss.
    """
    if not kenndaten:
        # Zum ganzen Lauf steht keine freigegebene Zeile - dann fehlt
        # nicht dieser einen Ergebniszeile etwas, sondern das Verfahren
        # fuehrt keine Kenndaten (die pH-LF-Titration etwa). Das ist eine
        # Auskunft, keine Beanstandung, und sie gilt fuer alle Zeilen
        # gleich.
        return ("fuer diese Untersuchungsmethode und Station sind keine "
                "freigegebenen Verfahrenskenndaten gepflegt")
    zeiger = ergebnis.get("vkd_id")
    if zeiger is None:
        # Ohne Zeiger bleibt die Frage dieselbe: gibt es zur Pruefmethode
        # gar keine Zeile, oder gibt es sie und sie passt nicht? Das
        # sagen die Zeilen derselben Pruefmethode - stehen welche da,
        # unterscheiden sie sich in etwas, und genau das gehoert in den
        # Vermerk.
        nahe = [k for k in kenndaten
                if all(k[feld] == ergebnis[feld]
                       for feld in KENNDATEN_SCHLUESSEL)]
        if not nahe:
            return ("keine freigegebenen Kenndaten (ERGEBNISSE.VKD_ID ist "
                    "leer, und zur Pruefmethode steht keine freigegebene "
                    "Zeile in VERFAHRENSKENNDATEN)")
        return (f"keine freigegebenen Kenndaten (ERGEBNISSE.VKD_ID ist "
                f"leer) - gesucht {_kenndatentext(ergebnis)}, vorhanden "
                f"{_vorhandene(nahe)}")
    gefunden = [k for k in kenndaten if k["vkd_id"] == zeiger]
    if not gefunden:
        return (f"keine freigegebenen Kenndaten - VKD_ID {zeiger} ist "
                f"nicht freigegeben (LSTA_ID {LSTA_FREIGEGEBEN}) oder "
                f"gibt es nicht")
    # Was unterscheidet sich? Bei mehreren Zeilen desselben Standes zaehlt,
    # was allen gemeinsam ist - ein einzelner Ausreisser sagte nichts.
    abweichend = [feld.upper() for feld in KENNDATEN_MERKMALE
                  if all(k[feld] != ergebnis[feld] for k in gefunden)]
    return (f"VKD_ID {zeiger} passt nicht zur Ergebniszeile "
            f"({', '.join(abweichend) or 'kein Feld'} weicht ab) - gesucht "
            f"{_kenndatentext(ergebnis)}, vorhanden {_vorhandene(gefunden)}")


def _vorhandene(zeilen: list[dict]) -> str:
    """Die Kombinationen, die es gibt - gekuerzt, damit sie in eine Zeile
    passen."""
    text = " | ".join(dict.fromkeys(_kenndatentext(k) for k in zeilen))
    return text if len(text) <= 120 else text[:117] + "..."


# ------------------------------------------------------------------ Standards

STANDARD_FELDER = ("stan_id", "nummer", "bezeichnung", "typ", "mw",
                   "untere_grenze", "obere_grenze", "stpa_id",
                   "para_id", "um_id", "sollwert", "toleranz", "gu", "go",
                   "qc_gu", "qc_go", "pr_gu", "pr_go", "linie", "test",
                   "parameter")


def standards_fuer_lauf(zugang, serie: str, um_id, gegr_id,
                        verbindung=None) -> list[dict]:
    """Die Standards, die fuer diesen Lauf in Frage kommen.

    STANDARD_PARA.UM_ID sagt, in welcher Untersuchungsmethode ein Standard
    verwendet werden darf - das allein reicht aber nicht: unter derselben
    Untersuchungsmethode haengen Parameter, die an *dieser* Station gar
    nicht bestimmt werden. STANDARD_PARA kennt keine GEGR_ID, deshalb wird
    ueber die Parameter des Laufs eingegrenzt: was in ERGEBNISSE fuer Serie,
    Untersuchungsmethode und Station steht, wird gemessen - alles andere
    gehoert zu einem anderen Geraet und hat im Lauf nichts zu suchen.

    Mitgenommen werden alle drei Grenzpaare: GU/GO als Text aus der Pflege,
    QC_GU/QC_GO und PR_GU/PR_GO als Zahlen. Welches Paar eine Pruefung
    heranzieht, entscheidet spaeter das Pruefmodul, nicht die Abfrage.
    """
    zeilen = _zeilen(zugang, """
        SELECT s.id, s.nummer, s.bezeichnung, s.typ,
               s.mw, s.untere_grenze, s.obere_grenze,
               sp.id, sp.para_id, sp.um_id, sp.sollwert, sp.toleranz,
               sp.gu, sp.go, sp.qc_gu, sp.qc_go, sp.pr_gu, sp.pr_go,
               sp.linie, sp.test, pa.name
          FROM standardverwaltung s
          JOIN standard_para sp  ON sp.stan_id = s.id
          LEFT JOIN parameter pa ON pa.id = sp.para_id
         WHERE sp.um_id = :um_id
           AND sp.para_id IN (
                 SELECT DISTINCT e.para_id FROM ergebnisse e
                  WHERE e.serie = :serie AND e.um_id = :um_id
                    AND e.gegr_id = :gegr_id)
         ORDER BY s.bezeichnung, pa.name, sp.id
    """, {"serie": serie, "um_id": um_id, "gegr_id": gegr_id},
        verbindung=verbindung)
    standards = _dicts(zeilen, STANDARD_FELDER)
    for standard in standards:
        standard["sollwert_zahl"] = als_zahl(standard["sollwert"])
        standard["gu_zahl"] = als_zahl(standard["gu"])
        standard["go_zahl"] = als_zahl(standard["go"])
    return standards


# --------------------------------------------------------------------------
# Der Standardkatalog: welche Standards es gibt und wo sie gemessen werden
# --------------------------------------------------------------------------
#
# Fuer die Regelkarte eines Kontrollstandards genuegt der Lauf: er wird
# beim Export mitgeschrieben, weil er sonst verloren waere. Ein
# Standardmaterial dagegen steht im LIMS - es hat eine Ergebniszeile wie
# jede Probe, und ERGEBNISSE.STAN_ID sagt, welcher Standard es ist. Seine
# Geschichte muss also nicht gesammelt werden; sie ist schon da.
#
# Nur: an welchem Geraet? Ein Standard haengt nicht am Geraet, sondern an
# Untersuchungsmethode und Parameter (STANDARD_PARA). Das Geraet kommt
# ueber UM_ANHANG dazu - dort steht je Untersuchungsmethode, Probenart und
# Parameter, welche Geraetegruppe (GEGR_ID) es misst, mit welcher
# Geraetemethode (GEME_ID) und mit welcher Pruefmethode. Damit schliesst
# sich die Kette:
#
#   STANDARDVERWALTUNG  (Name, Art)
#     -> STANDARD_PARA  (UM_ID, PARA_ID, Sollwert, GU/GO)
#       -> UM_ANHANG    (GEGR_ID, GEME_ID, PM_ID/PM_VER, PART_ID)
#         -> STATIONEN  (der Name der Geraetegruppe)
#           -> ERGEBNISSE ueber STAN_ID + UM_ID + GEGR_ID + PM_ID/PM_VER
#
# Dieselbe Kette benutzt schon der Geraetewechsel; sie ist also nicht neu
# erfunden, sondern nur in die andere Richtung gelesen.

STANDARDLISTE_FELDER = ("gegr_id", "station", "typ", "stan_id", "nummer",
                        "bezeichnung", "part_id", "probenart")


def standardliste(zugang, typen=None, verbindung=None) -> list[dict]:
    """Welche Standards es gibt und an welcher Geraetegruppe - sonst nichts.

    Die grobe Ebene der Auswahl. Sie kommt ohne Parameter, Pruefmethode
    und Untersuchungsmethode aus, und genau das macht sie schnell: statt
    einer Zeile je Kombination aus Standard, Methode, Parameter und
    Pruefmethode steht hier eine je Standard, Geraetegruppe und
    Probenart - ein Bruchteil. Die Probenart gehoert dazu, weil die
    Auswahl mit ihr anfaengt: sie sagt, welche Geraete ueberhaupt in
    Frage kommen.

    Die feine Ebene holt `standardkatalog` nach, sobald Geraet und
    Standard gewaehlt sind. Vorher braucht sie niemand: aus ihr wird nur
    die Liste der Untersuchungsmethoden gebildet, und die steht erst zur
    Wahl, wenn die drei Listen darueber stehen.
    """
    bindungen = {"lsta": LSTA_FREIGEGEBEN, "gruppe": STATIONSGRUPPE_GERAETE}
    filter_typ = _typfilter(typen, bindungen)
    zeilen = _zeilen(zugang, f"""
        SELECT DISTINCT a.gegr_id, st.station, s.typ, s.id, s.nummer,
               s.bezeichnung, a.part_id, ar.art
          FROM standardverwaltung s
          JOIN standard_para sp      ON sp.stan_id = s.id
          JOIN um_anhang a           ON a.um_id = sp.um_id
                                    AND a.para_id = sp.para_id
          LEFT JOIN stationen st     ON st.id = a.gegr_id
                                    AND st.sgru_id = :gruppe
          LEFT JOIN probenart ar     ON ar.id = a.part_id
         WHERE EXISTS (SELECT 1 FROM pruefmethoden f
                        WHERE f.id = a.pm_id AND f.version = a.pm_ver
                          AND f.lsta_id = :lsta)
{filter_typ}         ORDER BY ar.art, st.station, s.typ, s.bezeichnung
    """, bindungen, verbindung=verbindung)
    return _dicts(zeilen, STANDARDLISTE_FELDER)


def _typfilter(typen, bindungen: dict) -> str:
    """Die Einschraenkung auf Standardarten - als Bindung, nie als Text.

    Verglichen wird ohne Ruecksicht auf Gross und Klein, weil die
    Schreibweise in der Pflege schwankt.
    """
    if not typen:
        return ""
    namen = [f":typ{nummer}" for nummer in range(len(typen))]
    for nummer, typ in enumerate(typen):
        bindungen[f"typ{nummer}"] = str(typ).strip().upper()
    return f"           AND UPPER(TRIM(s.typ)) IN ({', '.join(namen)})\n"


STANDARDKATALOG_FELDER = (
    "gegr_id", "station", "typ", "stan_id", "nummer", "bezeichnung",
    "um_id", "um_kuerzel", "part_id", "pm_id", "pm_ver", "para_id",
    "parameter", "pruefmethode", "probenart",
    "sollwert", "toleranz", "gu", "go",
)


def standardkatalog(zugang, typen=None, stan_id=None, gegr_id=None,
                    part_id=None, kuerzel=None, verbindung=None) -> list[dict]:
    """Alle Standards mit Geraetegruppe, Art, Untersuchungsmethode und
    Pruefmethoden.

    Eine Zeile je Kombination aus Standard, Untersuchungsmethode,
    Geraetegruppe, Probenart und Pruefmethode - das ist die feinste
    Ebene, auf der eine Regelkarte gefuehrt wird.

    `typen` schraenkt auf Standardarten ein (STANDARDVERWALTUNG.TYP).
    Uebergeben wird eine Liste von Texten; verglichen wird ohne Ruecksicht
    auf Gross und Klein, weil die Schreibweise in der Pflege schwankt.

    `stan_id`, `gegr_id` und `part_id` grenzen auf einen Standard an
    einer Geraetegruppe fuer eine Probenart ein. Genau so ruft die
    Auswahl im Startbildschirm es auf: Art, Probenart, Geraet und
    Standard stehen dann schon, und nur noch die Pruefmethoden dazu
    fehlen. Ueber den ganzen Katalog zu gehen, um am Ende eine Handvoll
    Zeilen zu zeigen, ist die Wartezeit nicht wert.

    `kuerzel` grenzt auf die Untersuchungsmethode ein - eingetippt statt
    ausgewaehlt, deshalb als Anfang verglichen und ohne Ruecksicht auf
    Gross und Klein: "icpms" findet ICPMS_AS und ICPMS_HG, das ganze
    Kuerzel findet genau eines.

    Angeboten wird nur, was freigegeben ist (PRUEFMETHODEN.LSTA_ID 0):
    eine gesperrte Methode misst nichts mehr, und eine Karte darueber
    waere ein Blick in die Vergangenheit ohne Fortsetzung.
    """
    bindungen = {"lsta": LSTA_FREIGEGEBEN, "gruppe": STATIONSGRUPPE_GERAETE}
    filter_typ = _typfilter(typen, bindungen)
    if stan_id is not None:
        filter_typ += "           AND s.id = :stan_id\n"
        bindungen["stan_id"] = stan_id
    if gegr_id is not None:
        filter_typ += "           AND a.gegr_id = :gegr_id\n"
        bindungen["gegr_id"] = gegr_id
    if part_id is not None:
        filter_typ += "           AND a.part_id = :part_id\n"
        bindungen["part_id"] = part_id
    if str(kuerzel or "").strip():
        filter_typ += ("           AND UPPER(TRIM(u.kuerzel)) LIKE "
                       ":kuerzel\n")
        bindungen["kuerzel"] = str(kuerzel).strip().upper() + "%"
    zeilen = _zeilen(zugang, f"""
        SELECT DISTINCT a.gegr_id, st.station, s.typ, s.id, s.nummer,
               s.bezeichnung, a.um_id, u.kuerzel, a.part_id, a.pm_id,
               a.pm_ver, a.para_id, pa.name, pm.name, ar.art,
               sp.sollwert, sp.toleranz, sp.gu, sp.go
          FROM standardverwaltung s
          JOIN standard_para sp      ON sp.stan_id = s.id
          JOIN um_anhang a           ON a.um_id = sp.um_id
                                    AND a.para_id = sp.para_id
          LEFT JOIN stationen st     ON st.id = a.gegr_id
                                    AND st.sgru_id = :gruppe
          LEFT JOIN untersuchungsmethode u ON u.id = a.um_id
          LEFT JOIN parameter pa     ON pa.id = a.para_id
          LEFT JOIN pruefmethoden pm ON pm.id = a.pm_id
                                    AND pm.version = a.pm_ver
          LEFT JOIN probenart ar     ON ar.id = a.part_id
         WHERE EXISTS (SELECT 1 FROM pruefmethoden f
                        WHERE f.id = a.pm_id AND f.version = a.pm_ver
                          AND f.lsta_id = :lsta)
{filter_typ}         ORDER BY st.station, s.typ, s.bezeichnung, u.kuerzel,
                  a.um_id, pa.name, a.pm_id, a.pm_ver
    """, bindungen, verbindung=verbindung)
    eintraege = _dicts(zeilen, STANDARDKATALOG_FELDER)
    for eintrag in eintraege:
        eintrag["sollwert_zahl"] = als_zahl(eintrag["sollwert"])
        eintrag["gu_zahl"] = als_zahl(eintrag["gu"])
        eintrag["go_zahl"] = als_zahl(eintrag["go"])
    return eintraege


MESSUNG_FELDER = ("serie", "prob_id", "probe_nr", "wdh_um", "wdh_me",
                  "mw_roh", "mw", "datum_zeit", "anwender", "lnr",
                  "um_id", "gegr_id", "pm_id", "pm_ver", "para_id",
                  "part_id", "stan_id", "end_faktor", "faktor",
                  "faktor_wgh", "bearbeitung")

# Wo der abgeschlossene Verlauf steht und wo das, was gerade laeuft.
# Beide Tabellenpaare tragen dieselben Spalten - KO_ERGEBNISSE hat so
# viele wie ERGEBNISSE, KO_PROBEN so viele wie PROBEN -, deshalb geht
# beides mit einer Abfrage.
MESSUNGSQUELLEN = ((("ko_ergebnisse", "ko_proben", "ko_teilproben"), 0),
                   (("ergebnisse", "proben", "teilproben"), 1))

# ORA-00942: Tabelle oder View existiert nicht.
FEHLT_DIE_TABELLE = 942

# Wie viele Punkte eine Karte holt, wenn nichts anderes gesagt ist - die
# juengsten. Fuenfzig, weil genau so weit auch die Rechnung reicht:
# Mittelwert und Streuung stehen auf den letzten fuenfzig gewerteten
# Punkten (regelkarte.FENSTER). Alles darueber waere ein Weg durchs
# Netz, den niemand zu sehen bekommt - bei einem Standard, der seit zehn
# Jahren mitlaeuft, Zehntausende Zeilen fuer ein Bild mit dreissig.
#
# Im Reiter Regelkarten steht die Zahl in einem Feld: wer weiter zurueck
# will, tippt eine groessere ein, und "alle" holt den ganzen Verlauf.
MESSUNGSGRENZE = 50


# Welche Spalte einen Punkt der Karte traegt. MW ist der Gehalt, MW_ROH
# die Konzentration der gemessenen Loesung; welche gilt, haengt an der
# Standardart und steht in standardkarte.WERTFELD. Hier steht sie, damit
# die Abfrage dieselbe Spalte auf "vorhanden" prueft, die nachher
# gezeichnet wird - sonst faellt eine Zeile heraus, die einen Wert hat.
WERTSPALTEN = ("mw", "mw_roh")

# Ein Standard traegt eine negative PROB_ID; jede wirkliche Probe hat
# eine positive. Das ist die Abkuerzung in die Ergebnistabellen: PROB_ID
# ist der Schluessel zur Probentabelle und deshalb indiziert, STAN_ID in
# aller Regel nicht.
#
# Die Bedingung ist neben "stan_id = :stan_id" logisch ueberfluessig -
# ein Standard hat ohnehin eine negative PROB_ID. Sie steht hier allein,
# damit die Datenbank statt eines Durchlaufs ueber die groesste Tabelle
# des LIMS den Index nach unten durchsehen kann; darunter liegen nur die
# Standards, und das sind wenige.
#
# Wenn ein Standard einmal eine positive PROB_ID traegt, fehlen seine
# Punkte auf der Karte. Das ist die Annahme, an der diese Zeile haengt.
STANDARD_PROB_ID = "e.prob_id < 0"


def _messungs_sql(quellen, bedingungen: str, grenze=None,
                  spalte: str = "mw") -> str:
    """Die Abfrage ueber eine oder beide Ergebnistabellen.

    Vorn steht die Bedingung auf die negative PROB_ID - siehe
    STANDARD_PROB_ID. Sie ist der Grund, warum die Karte nicht auf einen
    Durchlauf ueber die ganze Tabelle wartet.

    Sortiert wird um die Vereinigung herum: ein ORDER BY innerhalb eines
    Zweigs gilt in Oracle fuer die ganze Vereinigung und laesst sich dann
    nicht mehr auf beide Zweige beziehen.
    """
    if spalte not in WERTSPALTEN:
        # Der Name geht in den Text der Anweisung - er darf deshalb nur
        # aus dieser Liste kommen und nie von aussen.
        raise ValueError(f"Unbekannte Wertspalte: {spalte!r}")
    zweige = []
    for (ergebnisse, proben, teilproben), laufend in quellen:
        zweige.append(f"""
        SELECT e.serie, e.prob_id, p.probe_nr, p.wdh_um, p.wdh_me,
               e.mw_roh, e.mw, e.datum_zeit, e.anwender, e.lnr,
               e.um_id, e.gegr_id, e.pm_id, e.pm_ver, e.para_id,
               e.part_id, e.stan_id,
               t.end_faktor, t.faktor, t.faktor_wgh,
               {int(laufend)} AS bearbeitung
          FROM {ergebnisse} e
          LEFT JOIN {proben} p ON p.id = e.prob_id
          LEFT JOIN {teilproben} t ON t.prob_id = e.prob_id
                                  AND t.um_id = e.um_id
         WHERE {STANDARD_PROB_ID}
           AND e.stan_id = :stan_id AND e.um_id = :um_id
           AND e.{spalte} IS NOT NULL
{bedingungen}""")
    innen = ("SELECT * FROM ("
             + "\n         UNION ALL\n".join(zweige)
             + "\n        ) ORDER BY datum_zeit DESC, serie DESC, "
               "probe_nr DESC, wdh_um DESC, wdh_me DESC")
    if not grenze:
        return innen
    # Oracle 11.2 kennt kein FETCH FIRST - die Grenze kommt ueber ROWNUM,
    # und zwar *um* die Sortierung herum: innen stuende sie vor dem
    # Sortieren und schnitte irgendwelche Zeilen heraus statt der
    # juengsten. Zurueck kommen sie damit vom Juengsten zum Aeltesten;
    # umgedreht wird in `standardmessungen`.
    return (f"SELECT * FROM (\n{innen}\n        ) WHERE ROWNUM <= :grenze")


def standardmessungen(zugang, stan_id, um_id, para_id=None, gegr_id=None,
                      pm_id=None, pm_ver=None,
                      von=None, bis=None, grenze=MESSUNGSGRENZE,
                      spalte="mw", verbindung=None) -> list[dict]:
    """Die gebuchten Messungen eines Standards - der Verlauf aus dem LIMS.

    Eingegrenzt wird auf den Standard, die Untersuchungsmethode und den
    Parameter - und damit auf genau das, was eine Regelkarte ausmacht:
    STANDARD_PARA fuehrt Sollwert und Grenzen je Standard, Methode und
    Parameter und kennt keine Geraetegruppe. Derselbe Parameter kann an
    zwei Geraeten gemessen werden; beide Messungen gehoeren zu derselben
    Vorgabe und deshalb auf dieselbe Karte. An welchem Geraet und mit
    welcher Pruefmethode eine Zeile entstand, kommt mit und steht am
    Punkt.

    `gegr_id` und `pm_id`/`pm_ver` grenzen freiwillig weiter ein - fuer
    den Blick auf ein einzelnes Geraet. Ohne sie kommen auch die Zeilen
    mit, deren GEGR_ID leer ist: das Feld ist in ERGEBNISSE nullable,
    und ein harter Gleichheitsvergleich liesse eine solche Zeile
    lautlos verschwinden.

    Gelesen wird aus beiden Ergebnistabellen: der abgeschlossene Verlauf
    steht in KO_ERGEBNISSE, was gerade bearbeitet wird, noch in
    ERGEBNISSE. Die Zeilen aus der zweiten tragen `bearbeitung` - die
    Karte zeigt sie in eigener Farbe, denn ihr Wert kann sich noch
    aendern.

    Der Endfaktor kommt mit: er ist die Bruecke zwischen beiden Ebenen,
    und die Regelkarte rechnet damit die Grenzen des LIMS auf die
    gemessene Loesung zurueck. Verbunden wird nach links - hat ein
    Standard keine Zeile in TEILPROBEN, bleibt das Feld leer und der
    Faktor wird aus den Werten selbst gebildet.

    Beide Werte kommen mit: MW, der Gehalt, und MW_ROH, die Konzentration
    der gemessenen Loesung. `spalte` sagt, welche von beiden zaehlt - sie
    entscheidet, welche Zeilen ueberhaupt kommen. Standardmaterial und
    Blindwertstandard sind gewoehnliche Proben mit Einwaage; auf ihren
    Gehalt beziehen sich Zertifikat und Grenzen, also MW. Beim
    Kontrollstandard ist der Endfaktor 1 und beides dieselbe Zahl - seine
    Karte kommt ohnehin nicht von hier, sondern aus der eigenen Ablage.

    `von` und `bis` grenzen ueber DATUM_ZEIT ein - beides einschliesslich
    und beides freiwillig.

    `grenze` sagt, wie viele Punkte hoechstens kommen - die juengsten.
    Ein Standard, der seit zehn Jahren mitlaeuft, hat Zehntausende
    Zeilen; gezeichnet wird daraus ein Bild, das dreissig zeigt, und
    gerechnet wird ueber die letzten fuenfzig. Der Rest waere nur
    Wartezeit. `grenze=None` holt alles.

    Zeilen ohne diesen Wert bleiben draussen: eine Ergebniszeile ohne
    Wert ist eine geplante Messung, kein Punkt einer Karte.
    """
    bedingungen = ""
    bindungen = {"stan_id": stan_id, "um_id": um_id}
    if para_id is not None:
        bedingungen += "           AND e.para_id = :para_id\n"
        bindungen["para_id"] = para_id
    if gegr_id is not None:
        bedingungen += "           AND e.gegr_id = :gegr_id\n"
        bindungen["gegr_id"] = gegr_id
    if pm_id is not None:
        bedingungen += "           AND e.pm_id = :pm_id\n"
        bindungen["pm_id"] = pm_id
    if pm_ver is not None:
        bedingungen += "           AND e.pm_ver = :pm_ver\n"
        bindungen["pm_ver"] = pm_ver
    if von is not None:
        bedingungen += "           AND e.datum_zeit >= :von\n"
        bindungen["von"] = von
    if bis is not None:
        bedingungen += "           AND e.datum_zeit <= :bis\n"
        bindungen["bis"] = bis
    if grenze:
        bindungen["grenze"] = int(grenze)
    try:
        zeilen = _zeilen(zugang,
                         _messungs_sql(MESSUNGSQUELLEN, bedingungen, grenze,
                                       spalte),
                         bindungen, verbindung=verbindung)
    except Exception as fehler:                     # nur die fehlende Tabelle
        if fehlernummer(fehler) != FEHLT_DIE_TABELLE:
            raise
        # Gibt es das Archiv in dieser Datenbank nicht, bleibt der Verlauf
        # aus ERGEBNISSE - lieber die halbe Karte als gar keine.
        zeilen = _zeilen(zugang, _messungs_sql(MESSUNGSQUELLEN[1:],
                                               bedingungen, grenze, spalte),
                         bindungen, verbindung=verbindung)
    # Gelesen wird vom Juengsten zum Aeltesten, damit die Grenze die
    # juengsten Punkte trifft. Eine Karte ist ein Verlauf und laeuft
    # vorwaerts - hier wird sie umgedreht.
    messungen = _dicts(zeilen, MESSUNG_FELDER)[::-1]
    for messung in messungen:
        messung["probe"] = probenschluessel(messung["probe_nr"],
                                            messung["wdh_um"],
                                            messung["wdh_me"])
        messung["zahl"] = als_zahl(messung[spalte])
        messung["bearbeitung"] = bool(messung.get("bearbeitung"))
    return messungen


# --------------------------------------------------------------- Parameterspalten

WELLEN_FELDER = ("pmwe_id", "pm_id", "pm_ver", "pm_code", "kurzname",
                 "wellenlaenge", "hfa_code", "ugrenze", "ogrenze", "rsdproz",
                 "rsdabs", "para_id", "pruefmethode", "parameter")


def wellen_fuer_lauf(zugang, serie: str, um_id, gegr_id,
                     verbindung=None) -> list[dict]:
    """Die Messlinien der Pruefmethoden, die in diesem Lauf vorkommen.

    Eingegrenzt auf die PM_ID/PM_VER-Kombinationen aus ERGEBNISSE: sonst
    muesste die ganze Tabelle durchsucht werden, und ein PM_CODE aus einer
    fremden Methode koennte zufaellig auf eine Spalte der Laufdatei passen.

    PM_CODE ist der vollstaendige Spaltenkopf der Geraetedatei, etwa
    "75As (mp_KED-H2) - Value" - verglichen wird deshalb auf Gleichheit,
    nicht auf Enthaltensein. UGRENZE und OGRENZE geben an, in welchem
    Bereich diese Linie gilt; das braucht spaeter, wer mehrere Linien
    desselben Parameters zu einer Spalte zusammenfasst.
    """
    zeilen = _zeilen(zugang, """
        SELECT w.id, w.pm_id, w.pm_ver, w.pm_code, w.kurzname,
               w.wellenlaenge, w.hfa_code, w.ugrenze, w.ogrenze,
               w.rsdproz, w.rsdabs, pm.para_id, pm.name, pa.name
          FROM pm_wellen w
          JOIN pruefmethoden pm  ON pm.id = w.pm_id AND pm.version = w.pm_ver
          LEFT JOIN parameter pa ON pa.id = pm.para_id
         WHERE (w.pm_id, w.pm_ver) IN (
                 SELECT DISTINCT e.pm_id, e.pm_ver FROM ergebnisse e
                  WHERE e.serie = :serie AND e.um_id = :um_id
                    AND e.gegr_id = :gegr_id)
         ORDER BY pa.name, w.pm_code, w.id
    """, {"serie": serie, "um_id": um_id, "gegr_id": gegr_id},
        verbindung=verbindung)
    return _dicts(zeilen, WELLEN_FELDER)


# --------------------------------------------------------------------------
# Geraetewechsel: dieselbe Untersuchung an einer anderen Station
# --------------------------------------------------------------------------
#
# Ein Lauf ist im LIMS auf eine Station gebucht: SERIEN_MW_ANHANG.STAT_ID
# sagt, an welchem Geraet die Serie laeuft, und jede Ergebniszeile traegt
# GEGR_ID (dieselbe Nummer als Geraetegruppe), GEME_ID (die Geraetemethode)
# und damit PM_ID/PM_VER - die Pruefmethode gehoert zum Geraet.
#
# Gemessen wird trotzdem manchmal am anderen Geraet: das eine ist belegt,
# das andere frei. Dann stehen die Ergebniszeilen bei der falschen Station,
# und die Werte des Laufs passen zu keiner Zeile.
#
# Welche Stationen fuer einen Parameter ueberhaupt in Frage kommen, steht in
# UM_ANHANG: je Untersuchungsmethode (UM_ID), Probenart (PART_ID) und
# Parameter (PARA_ID) eine Zeile je moeglicher Geraetegruppe, mit GEME_ID
# und der zugehoerigen PM_ID/PM_VER. Mehr als eine Zeile heisst: zwischen
# diesen Stationen laesst sich umsetzen. Erfunden wird dabei nichts - die
# neue Pruefmethode kommt aus derselben Zeile wie die neue Station.

UMANHANG_FELDER = ("id", "lnr", "um_id", "part_id", "para_id", "pm_id",
                   "pm_ver", "gegr_id", "geme_id", "einh_id", "einh_ver_id",
                   "vkd_id", "marker",
                   "station", "parameter", "pruefmethode", "probenart")

STAND_FELDER = ("pm_id", "pm_ver", "part_id", "vkd_id", "gera_id",
                "nwg_arbeit", "bg_arbeit", "ogrenze")


def kenndaten_staende(zugang, um_id, verbindung=None) -> list[dict]:
    """Welche Staende der Verfahrenskenndaten es je Zielmethode gibt.

    VERFAHRENSKENNDATEN.ID ist der Stand, nicht die Zeile: zu einer
    Pruefmethode koennen mehrere freigegebene Staende stehen, und welcher
    gilt, sagt die Ergebniszeile mit ihrer VKD_ID. Deshalb kommen hier
    alle Kandidaten zurueck und die Wahl faellt dort, wo der Stand der
    Ergebniszeile bekannt ist.

    Eingegrenzt auf die Untersuchungsmethode und auf die Pruefmethoden,
    die UM_ANHANG fuer sie fuehrt - mehr kann beim Umsetzen nicht
    herauskommen.

    Mit NWG, BG und Obergrenze: so laesst sich vor dem Schreiben sehen,
    welche Grenzen nach dem Umsetzen gelten - und ob sie sich ueberhaupt
    aendern.
    """
    zeilen = _zeilen(zugang, """
        SELECT DISTINCT v.pm_id, v.pm_ver, v.part_id, v.id, v.gera_id,
               v.nwg_arbeit, v.bg_arbeit, v.ogrenze
          FROM verfahrenskenndaten v
         WHERE v.lsta_id = :lsta AND v.um_id = :um_id
           AND (v.pm_id, v.pm_ver) IN (SELECT a.pm_id, a.pm_ver
                                         FROM um_anhang a
                                        WHERE a.um_id = :um_id)
         ORDER BY v.pm_id, v.pm_ver, v.part_id, v.id
    """, {"lsta": LSTA_FREIGEGEBEN, "um_id": um_id}, verbindung=verbindung)
    return _dicts(zeilen, STAND_FELDER)


def geraetewahl_fuer_lauf(zugang, serie: str, um_id, gegr_id,
                          verbindung=None) -> list[dict]:
    """Die moeglichen Stationen je Parameter dieses Laufs - aus UM_ANHANG.

    Eingegrenzt auf die Parameter und Probenarten, die in diesem Lauf
    tatsaechlich vorkommen: UM_ANHANG fuehrt die ganze
    Untersuchungsmethode, und eine Station, die zu keinem Parameter des
    Laufs gehoert, waere nur eine Falle in der Auswahlliste.

    Angeboten wird nur, was freigegeben ist (PRUEFMETHODEN.LSTA_ID 0):
    eine gesperrte Methode laesst sich nicht buchen, und sie zur Wahl zu
    stellen hiesse, den Fehler erst beim Schreiben zu merken.

    Welcher Stand der Verfahrenskenndaten zur Zielmethode gehoert, steht
    hier bewusst *nicht*: dazu gehoert der Stand der Ergebniszeile, und
    den kennt erst `laufkontext.wechselplan`. Die Kandidaten liefert
    `kenndaten_staende`.

    Der Name der Station kommt wie ueberall sonst aus der Gruppe der
    Messgeraete. STATIONEN fuehrt dieselbe Nummer in mehreren Gruppen -
    ohne den Filter stand in der Auswahlliste der Name aus einer fremden
    Gruppe (etwa ein Kuerzel einer Untersuchungsmethode) statt des
    Geraets. LEFT JOIN bleibt es trotzdem: findet sich dort kein Name,
    soll die Station als "ID 50" zur Wahl stehen und nicht verschwinden.
    """
    zeilen = _zeilen(zugang, """
        SELECT a.id, a.lnr, a.um_id, a.part_id, a.para_id, a.pm_id, a.pm_ver,
               a.gegr_id, a.geme_id, a.einh_id, a.einh_ver_id, a.vkd_id,
               a.marker, s.station, pa.name, pm.name, ar.art
          FROM um_anhang a
          LEFT JOIN stationen s     ON s.id = a.gegr_id
                                   AND s.sgru_id = :gruppe
          LEFT JOIN parameter pa    ON pa.id = a.para_id
          LEFT JOIN pruefmethoden pm ON pm.id = a.pm_id
                                    AND pm.version = a.pm_ver
          LEFT JOIN probenart ar    ON ar.id = a.part_id
         WHERE a.um_id = :um_id
           AND EXISTS (SELECT 1 FROM pruefmethoden f
                        WHERE f.id = a.pm_id AND f.version = a.pm_ver
                          AND f.lsta_id = :lsta)
           AND EXISTS (SELECT 1 FROM ergebnisse e
                        WHERE e.serie = :serie AND e.um_id = a.um_id
                          AND e.gegr_id = :gegr_id
                          AND e.para_id = a.para_id
                          AND e.part_id = a.part_id)
         ORDER BY pa.name, s.station, a.pm_id, a.pm_ver
    """, {"serie": serie, "um_id": um_id, "gegr_id": gegr_id,
          "lsta": LSTA_FREIGEGEBEN,
          "gruppe": STATIONSGRUPPE_GERAETE}, verbindung=verbindung)
    return _dicts(zeilen, UMANHANG_FELDER)


def kenndaten_zeiger(staende: list[dict], ziel: dict, jetzt,
                     geraete=None) -> tuple:
    """Welcher Stand nach dem Umsetzen in ERGEBNISSE.VKD_ID gehoert.

    Zurueck kommen der Zeiger und - wenn er sich nicht bestimmen laesst -
    der Grund. Die Reihenfolge:

      1. Gibt es die Zeile mit demselben Stand wie bisher, bleibt er.
         Der Stand gehoert zum Verfahren, nicht zum Geraet; er wechselt
         beim Umsetzen normalerweise gar nicht.
      2. Bleiben mehrere, zaehlt die Probenart: ein Stand, der genau
         diese PART_ID nennt, schlaegt den, der fuer alle gilt.
      3. Bleiben mehrere, entscheidet das Geraet: nur der Stand, dessen
         GERA_ID die Geraetemethode der Zielstation traegt (GERAETE_ANHANG),
         kommt in Frage.
      4. Gibt es genau einen, gilt der.
      5. Sonst bleibt der alte Zeiger stehen, und es wird gesagt.

    Eine leere PART_ID im Stand heisst "gilt fuer alle Probenarten" -
    am TOC ist die Spalte nicht gepflegt, und ein Gleichheitsvergleich
    liesse dort jedes Umsetzen an fehlenden Kenndaten scheitern.
    """
    zeilen = [zeile for zeile in staende
              if zeile["pm_id"] == ziel["pm_id"]
              and zeile["pm_ver"] == ziel["pm_ver"]
              and zeile["part_id"] in (None, ziel["part_id"])]
    passend = [zeile["vkd_id"] for zeile in zeilen]
    if jetzt in passend:
        return jetzt, ""
    if len(passend) > 1:
        genau = [zeile for zeile in zeilen
                 if zeile["part_id"] is not None
                 and zeile["part_id"] == ziel["part_id"]]
        if len(genau) == 1:
            return genau[0]["vkd_id"], ""
    if len(passend) > 1:
        am_geraet = [zeile["vkd_id"] for zeile in zeilen
                     if _laeuft_auf_geraet(zeile, ziel.get("geme_id"),
                                           geraete)]
        if len(am_geraet) == 1:
            return am_geraet[0], ""
    if len(passend) == 1:
        return passend[0], ""
    if not passend:
        return None, ("fuer die Zielmethode sind keine freigegebenen "
                      "Verfahrenskenndaten hinterlegt")
    return None, (f"{len(passend)} freigegebene Staende der "
                  f"Verfahrenskenndaten passen zur Zielmethode "
                  f"({', '.join(str(v) for v in sorted(passend))}), und "
                  f"der bisherige Stand {jetzt} ist nicht darunter")


LAUFPARAMETER_FELDER = ("para_id", "part_id", "pm_id", "pm_ver", "gegr_id",
                        "geme_id", "vkd_id", "parameter", "pruefmethode",
                        "probenart", "zeilen", "mit_wert")


def laufparameter(zugang, serie: str, um_id, gegr_id,
                  verbindung=None) -> list[dict]:
    """Was dieser Lauf an Parametern hat - und wo sie gerade gebucht sind.

    Je Parameter, Probenart und Pruefmethode eine Zeile, mit der Anzahl der
    Ergebniszeilen dahinter: das ist die Menge, die ein Umsetzen bewegen
    wuerde. Ohne die Anzahl liesse sich vorher nicht sagen, wie gross der
    Eingriff ist.

    Daneben, wie viele davon schon einen Messwert tragen (MW_ROH): die
    bleiben beim Umsetzen stehen. Ein gebuchtes Ergebnis gehoert zu der
    Station, an der es gemessen wurde - es nachtraeglich auf ein anderes
    Geraet zu schieben, waere eine falsche Auskunft.
    """
    zeilen = _zeilen(zugang, """
        SELECT e.para_id, e.part_id, e.pm_id, e.pm_ver, e.gegr_id, e.geme_id,
               MAX(e.vkd_id), MAX(pa.name), MAX(pm.name), MAX(ar.art),
               COUNT(*), COUNT(e.mw_roh)
          FROM ergebnisse e
          LEFT JOIN parameter pa     ON pa.id = e.para_id
          LEFT JOIN pruefmethoden pm ON pm.id = e.pm_id
                                    AND pm.version = e.pm_ver
          LEFT JOIN probenart ar     ON ar.id = e.part_id
         WHERE e.serie = :serie AND e.um_id = :um_id AND e.gegr_id = :gegr_id
         GROUP BY e.para_id, e.part_id, e.pm_id, e.pm_ver, e.gegr_id, e.geme_id
         ORDER BY MAX(pa.name), e.pm_id, e.pm_ver
    """, {"serie": serie, "um_id": um_id, "gegr_id": gegr_id},
        verbindung=verbindung)
    return _dicts(zeilen, LAUFPARAMETER_FELDER)


# --- Schreiben ------------------------------------------------------------
#
# Anders als der Export aendert das Umsetzen Schluesselspalten. Deshalb
# gilt hier:
#
#   * Getroffen wird nur, was der Lauf ist: Serie, Untersuchungsmethode,
#     alte Station, Parameter, Probenart und die alte Pruefmethode stehen
#     alle in der Bedingung. Eine Zeile derselben Probe aus einem anderen
#     Lauf bleibt unberuehrt.
#   * Vorher wird geprueft, ob die Zielzeile schon existiert. Gaebe es sie,
#     stuenden nach dem Umsetzen zwei Zeilen mit demselben Schluessel -
#     oder die Datenbank wiese es mit ORA-00001 ab, mitten im Lauf.
#   * Alles in einer Transaktion, mit den Serienzeilen zusammen.

# Eine einzelne Probe umsetzen heisst: dieselben Anweisungen, nur um
# PROB_ID enger. Genommen wird eine Probe je Durchgang statt einer
# IN-Liste - eine Bindungsliste wechselnder Laenge waere fuer Oracle jedes
# Mal eine neue Anweisung, und die Zahl der Proben eines Laufs ist klein.
def _je_probe(sql: str, spalte: str = "") -> str:
    """Haengt die Einschraenkung auf eine Probe an."""
    return sql.rstrip() + f"\n           AND {spalte}prob_id = :prob_id\n    "


def geraetewechsel_sql(nur_probe=False) -> str:
    """Setzt die Ergebniszeilen eines Parameters auf die neue Station.

    Mitgeschrieben wird die VKD_ID: sie verweist auf die
    Verfahrenskenndaten, und die haengen an der Pruefmethode. Bliebe sie
    stehen, zeigte die Zeile nach dem Umsetzen auf die Grenzen des alten
    Geraets. Steht in der UM_ANHANG-Zeile keine, bleibt die alte stehen -
    NVL laesst sie unberuehrt, statt sie zu leeren.

    Angefasst werden nur Zeilen ohne Messwert. Ein gebuchtes MW_ROH
    gehoert zu der Station, an der es gemessen wurde; es nachtraeglich
    auf ein anderes Geraet zu schieben, waere eine falsche Auskunft.

    `nur_probe` grenzt zusaetzlich auf eine PROB_ID ein - dann wechselt
    diese eine Probe das Geraet und die uebrigen bleiben stehen.
    """
    sql = """
        UPDATE ergebnisse
           SET gegr_id = :neu_gegr, geme_id = :neu_geme,
               pm_id   = :neu_pm_id, pm_ver = :neu_pm_ver,
               vkd_id  = NVL(:neu_vkd, vkd_id)
         WHERE serie = :serie AND um_id = :um_id
           AND gegr_id = :alt_gegr AND part_id = :part_id
           AND para_id = :para_id
           AND pm_id = :alt_pm_id AND pm_ver = :alt_pm_ver
           AND mw_roh IS NULL
    """
    return _je_probe(sql) if nur_probe else sql


# Ein Bindungsname, den die Anweisung nicht kennt, ist fuer Oracle ein
# Fehler (DPY-4008) - nicht etwa ein ueberzaehliger Wert, den es
# stillschweigend liegen laesst. Beim Umsetzen gehen dieselben Angaben an
# drei Anweisungen, und jede braucht davon nur einen Teil: die Zaehlung
# der stehengebliebenen Zeilen kennt kein Ziel, die Kollisionspruefung
# keine GEME_ID. Deshalb bekommt jede genau das, was in ihrem Text steht.
_PLATZHALTER = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def bindungen_fuer(sql: str, bindungen: dict) -> dict:
    """Die Bindungen, die in dieser Anweisung wirklich vorkommen."""
    namen = set(_PLATZHALTER.findall(sql))
    return {name: wert for name, wert in bindungen.items() if name in namen}


def stehengeblieben_sql(nur_probe=False) -> str:
    """Zaehlt die Zeilen, die wegen ihres Messwerts nicht mitgehen."""
    sql = """
        SELECT COUNT(*) FROM ergebnisse
         WHERE serie = :serie AND um_id = :um_id
           AND gegr_id = :alt_gegr AND part_id = :part_id
           AND para_id = :para_id
           AND pm_id = :alt_pm_id AND pm_ver = :alt_pm_ver
           AND mw_roh IS NOT NULL
    """
    return _je_probe(sql) if nur_probe else sql


def kollision_sql(nur_probe=False) -> str:
    """Zaehlt die Proben, bei denen die Zielzeile schon steht."""
    sql = """
        SELECT COUNT(*)
          FROM ergebnisse alt
         WHERE alt.serie = :serie AND alt.um_id = :um_id
           AND alt.gegr_id = :alt_gegr AND alt.part_id = :part_id
           AND alt.para_id = :para_id
           AND alt.pm_id = :alt_pm_id AND alt.pm_ver = :alt_pm_ver
           AND alt.mw_roh IS NULL
           AND EXISTS (SELECT 1 FROM ergebnisse neu
                        WHERE neu.prob_id = alt.prob_id
                          AND neu.um_id = alt.um_id
                          AND neu.gegr_id = :neu_gegr
                          AND neu.pm_id = :neu_pm_id
                          AND neu.pm_ver = :neu_pm_ver)
    """
    return _je_probe(sql, "alt.") if nur_probe else sql


def serienzeile_sql() -> str:
    """Zaehlt die Zeile der Serie fuer eine Station."""
    return """
        SELECT COUNT(*) FROM serien_mw_anhang
         WHERE serie = :serie AND um_id = :um_id AND stat_id = :stat_id
    """


def serie_umstellen_sql() -> str:
    """Schreibt die Serie auf die neue Station um - wenn alles umzieht."""
    return """
        UPDATE serien_mw_anhang SET stat_id = :neu_stat
         WHERE serie = :serie AND um_id = :um_id AND stat_id = :alt_stat
    """


# Die einzige Spalte, die beim Anlegen der zweiten Serienzeile nicht aus
# der alten uebernommen wird - sie ist ja gerade die neue. Alles andere
# wird kopiert, LNR eingeschlossen: die Tabelle fuehrt dieselbe LNR fuer
# beide Stationen derselben Serie (nachgesehen: 2026P007 steht mit
# STAT_ID 50 und 53 da, beide mit LNR 3). Eine neu vergebene Nummer waere
# also falsch, keine Vorsichtsmassnahme. Eine ID hat die Tabelle nicht.
SERIENZEILE_OHNE = ("STAT_ID",)


def serie_anlegen_sql(spalten) -> str:
    """Legt die Serienzeile fuer die neue Station an - als Abbild der alten.

    Kopiert wird die vorhandene Zeile, gesetzt wird nur STAT_ID. Welche
    Spalten SERIEN_MW_ANHANG sonst fuehrt (LNR, FLAG, SGRU_ID, LAUF,
    BEARBEITER), weiss die Datenbank - die Namen kommen aus ihrer eigenen
    Spaltenliste, nie aus einer Eingabe.
    """
    namen = [name for name in spalten if name.upper() not in SERIENZEILE_OHNE]
    felder = ", ".join(["stat_id"] + namen)
    quellen = ", ".join([":neu_stat"] + namen)
    return f"""
        INSERT INTO serien_mw_anhang ({felder})
        SELECT {quellen} FROM serien_mw_anhang
         WHERE serie = :serie AND um_id = :um_id AND stat_id = :alt_stat
    """


def serie_verwaist_sql() -> str:
    """Zaehlt, was an der alten Station von dieser Serie noch haengt.

    Nicht nur die Zeilen ohne Messwert und nicht nur die umgesetzten
    Parameter: *jede* Ergebniszeile dieser Serie und Untersuchungsmethode
    an dieser Station zaehlt. Genau das ist die Frage, ob die Serie dort
    noch gebraucht wird - eine Zeile mit MW_ROH bleibt schliesslich
    stehen und gehoert weiterhin dorthin.
    """
    return """
        SELECT COUNT(*) FROM ergebnisse
         WHERE serie = :serie AND um_id = :um_id AND gegr_id = :stat_id
    """


def serie_entfernen_sql() -> str:
    """Nimmt die Serienzeile einer Station aus SERIEN_MW_ANHANG."""
    return """
        DELETE FROM serien_mw_anhang
         WHERE serie = :serie AND um_id = :um_id AND stat_id = :stat_id
    """


def _serienzeile_aufraeumen(cursor, serie: dict) -> str:
    """Raeumt die Serienzeile der alten Station weg - wenn sie leer ist.

    Wird ein einzelner Parameter umgesetzt, bekommt die Serie eine zweite
    Zeile in SERIEN_MW_ANHANG. Wandert er spaeter zurueck, bliebe die
    Zeile der Zwischenstation stehen und die Serie taucht an einem
    Geraet auf, an dem nichts mehr von ihr liegt.

    Entfernt wird deshalb genau dann, wenn an der alten Station keine
    einzige Ergebniszeile dieser Serie und Untersuchungsmethode mehr
    steht. Das deckt alle Faelle ab, ohne je zuviel zu loeschen:

      * eine einzelne Probe umgesetzt - die uebrigen Proben stehen dort
        noch, es bleibt;
      * ein Parameter von mehreren - die anderen stehen dort noch, es
        bleibt;
      * Zeilen mit MW_ROH bleiben an der alten Station, also bleibt auch
        die Serienzeile;
      * erst wenn nichts mehr da ist, geht sie weg.

    Gezaehlt wird *nach* dem Umsetzen und in derselben Transaktion: was
    gerade gewandert ist, zaehlt nicht mehr mit, und ein Fehlschlag rollt
    beides zurueck.
    """
    if serie["alt_stat"] == serie["neu_stat"]:
        return ""
    bindungen = {"serie": serie["serie"], "um_id": serie["um_id"],
                 "stat_id": serie["alt_stat"]}
    cursor.execute(serie_verwaist_sql(), bindungen)
    treffer = cursor.fetchone()
    if treffer and treffer[0]:
        return ""
    cursor.execute(serie_entfernen_sql(), bindungen)
    entfernt = cursor.rowcount or 0
    if not entfernt:
        return ""
    return (f" An der alten Station steht keine Ergebniszeile dieser Serie "
            f"mehr - ihre Zeile in SERIEN_MW_ANHANG wurde entfernt "
            f"({entfernt}).")


def geraet_umsetzen(zugang: Zugang, vorhaben: dict) -> dict:
    """Setzt einen Lauf auf eine andere Station um - alles oder nichts.

    `vorhaben` beschreibt, was geschehen soll:

        {"serie": "2026W052", "um_id": 251,
         "alt_stat": 41, "neu_stat": 50,
         "schritte": [{"para_id": 39, "part_id": 2,
                       "alt_pm_id": 39, "alt_pm_ver": 122,
                       "neu_pm_id": 39, "neu_pm_ver": 148,
                       "neu_geme": 148, "neu_vkd": 1,
                       "parameter": "Chlorid"}, ...],
         "serie_umstellen": True,
         "proben": [{"prob_id": 11, "bezeichnung": "2026P00554 1 1"}]}

    `proben` grenzt auf einzelne Proben ein - dann wechselt nur deren
    Ergebniszeile das Geraet, alle anderen bleiben stehen. Ohne den
    Eintrag geht der ganze Lauf. Die Serie wird dabei nie umgestellt: es
    bleibt ja etwas an der alten Station.

    `serie_umstellen` heisst: es zieht alles um, die vorhandene Zeile in
    SERIEN_MW_ANHANG wird auf die neue Station gesetzt. Bleibt etwas
    zurueck, braucht es zwei Zeilen - dann wird die zweite angelegt.

    Zurueck kommt, was geschehen ist: getroffene Zeilen je Parameter,
    uebersprungene Parameter samt Grund und was mit der Serie geschah.
    """
    verbindung = zugang.verbinden()
    bericht = {"geaendert": 0, "schritte": [], "uebersprungen": [],
               "geblieben": 0, "serie": ""}
    serie = {"serie": vorhaben["serie"], "um_id": vorhaben["um_id"],
             "alt_stat": vorhaben["alt_stat"], "neu_stat": vorhaben["neu_stat"]}
    # Ohne Einschraenkung geht der ganze Lauf; sonst je Probe ein
    # Durchgang. Eine IN-Liste waere fuer Oracle bei jeder Laenge eine
    # neue Anweisung, und ein Lauf hat Dutzende Proben, keine Tausende.
    proben = list(vorhaben.get("proben") or [])
    nur_probe = bool(proben)
    kollision = kollision_sql(nur_probe)
    geblieben_sql = stehengeblieben_sql(nur_probe)
    wechsel = geraetewechsel_sql(nur_probe)
    try:
        with verbindung.cursor() as cursor:
            for schritt in vorhaben["schritte"]:
                grund = ""
                zeilen = geblieben = 0
                for probe in (proben or [None]):
                    bindungen = {
                        "serie": serie["serie"], "um_id": serie["um_id"],
                        "alt_gegr": serie["alt_stat"],
                        "neu_gegr": serie["neu_stat"],
                        "part_id": schritt["part_id"],
                        "para_id": schritt["para_id"],
                        "alt_pm_id": schritt["alt_pm_id"],
                        "alt_pm_ver": schritt["alt_pm_ver"],
                        "neu_pm_id": schritt["neu_pm_id"],
                        "neu_pm_ver": schritt["neu_pm_ver"],
                        "neu_geme": schritt["neu_geme"],
                        "neu_vkd": schritt.get("neu_vkd"),
                        "prob_id": probe["prob_id"] if probe else None,
                    }
                    cursor.execute(kollision,
                                   bindungen_fuer(kollision, bindungen))
                    treffer = cursor.fetchone()
                    doppelt = int(treffer[0]) if treffer and treffer[0] else 0
                    if doppelt:
                        grund = (f"{doppelt} Proben haben die Zielzeile "
                                 f"schon")
                        continue
                    # Was schon einen Messwert traegt, bleibt an der alten
                    # Station - gezaehlt wird es trotzdem, denn es
                    # entscheidet mit, ob die Serie umgestellt werden darf.
                    cursor.execute(geblieben_sql,
                                   bindungen_fuer(geblieben_sql, bindungen))
                    treffer = cursor.fetchone()
                    geblieben += (int(treffer[0])
                                  if treffer and treffer[0] else 0)
                    cursor.execute(wechsel,
                                   bindungen_fuer(wechsel, bindungen))
                    zeilen += cursor.rowcount or 0
                bericht["geblieben"] += geblieben
                if grund and not zeilen:
                    bericht["uebersprungen"].append(dict(schritt,
                                                         grund=grund))
                    continue
                bericht["geaendert"] += zeilen
                bericht["schritte"].append(dict(schritt, zeilen=zeilen,
                                                geblieben=geblieben))
            # Bleibt auch nur eine Zeile an der alten Station, braucht die
            # Serie dort weiterhin ihren Eintrag - und bei einer einzelnen
            # Probe bleibt immer etwas.
            umstellen = (bool(vorhaben.get("serie_umstellen"))
                         and not bericht["geblieben"] and not nur_probe)
            bericht["serie"] = _serienzeile_setzen(cursor, serie, umstellen)
            # Erst umsetzen, dann aufraeumen: gezaehlt wird der Stand
            # danach.
            bericht["serie"] += _serienzeile_aufraeumen(cursor, serie)
        verbindung.commit()
    except Exception:
        verbindung.rollback()
        raise
    finally:
        verbindung.close()
    return bericht


def _serienzeile_setzen(cursor, serie: dict, umstellen: bool) -> str:
    """Bringt SERIEN_MW_ANHANG auf den Stand - und sagt, was geschah."""
    cursor.execute(serienzeile_sql(), {"serie": serie["serie"],
                                       "um_id": serie["um_id"],
                                       "stat_id": serie["neu_stat"]})
    treffer = cursor.fetchone()
    if treffer and treffer[0]:
        return "Die Serie steht fuer die neue Station schon in SERIEN_MW_ANHANG."
    if umstellen:
        cursor.execute(serie_umstellen_sql(),
                       {"serie": serie["serie"], "um_id": serie["um_id"],
                        "alt_stat": serie["alt_stat"],
                        "neu_stat": serie["neu_stat"]})
        return (f"SERIEN_MW_ANHANG auf die neue Station umgestellt "
                f"({cursor.rowcount or 0} Zeile(n)).")
    spalten = spalteninfo(cursor, "serien_mw_anhang")
    cursor.execute(serie_anlegen_sql(sorted(spalten)),
                   {"serie": serie["serie"], "um_id": serie["um_id"],
                    "alt_stat": serie["alt_stat"],
                    "neu_stat": serie["neu_stat"]})
    return (f"Zweite Zeile in SERIEN_MW_ANHANG fuer die neue Station angelegt "
            f"({cursor.rowcount or 0} Zeile(n)) - die alte bleibt, weil "
            f"Parameter dort bleiben.")


# --------------------------------------------------------------------------
# Export der Ergebnisse
# --------------------------------------------------------------------------
#
# Geschrieben wird in ERGEBNISSE, und zwar nur in Zeilen, die dort schon
# stehen: LabControl legt keine Ergebniszeile an, es fuellt die vorgesehene.
#
# Nie ueberschrieben wird, was schon dasteht. Ein gefuellter Messwert kann
# von Hand gepflegt, aus einer anderen Quelle uebernommen oder bereits
# freigegeben sein - ihn kommentarlos zu ersetzen waere der eine Fehler,
# den niemand bemerkt. Deshalb steht die Bedingung im UPDATE selbst
# (IS NULL) und nicht nur in einer vorherigen Abfrage: zwischen Lesen und
# Schreiben kann jemand anderes gespeichert haben.

# Die Spalten, die der Export fuellt - jede mit NVL, keine wird
# ueberschrieben.
EXPORT_SPALTEN = ("MW_ROH", "MW_ORG", "MW_N", "MW", "ANWENDER", "V_FAKTOR",
                  "FC7", "FC8", "FC9", "DATUM_ZEIT")

# Die einzige Spalte, die der Export setzt statt ergaenzt: der
# Bearbeitungsstand. Sie steht hier einzeln und mit Namen, damit die
# Ausnahme eine bleibt - was nicht in dieser Liste steht, muss durch NVL.
EXPORT_GESETZT = ("PSTA_ID", "KORREKTUR_FLAG")


def export_sql() -> str:
    """Die Anweisung, die eine Ergebniszeile fuellt.

    Jede Spalte wird einzeln bedingt gesetzt: NVL laesst einen vorhandenen
    Wert stehen und nimmt den neuen nur, wo bisher nichts stand. So bleibt
    es bei einer Anweisung je Zeile, und keine Spalte kann versehentlich
    ueberschrieben werden.

    PSTA_ID und KORREKTUR_FLAG sind die Ausnahme, und sie ist eine
    gewollte: beide sind keine Messwerte, sondern die Aussage "diese
    Zeile ist bearbeitet". Sie werden deshalb *gesetzt* und nicht
    ergaenzt - auch ueber einen vorhandenen Stand hinweg. Es sind die
    einzigen beiden Spalten in ERGEBNISSE, die dieser Weg
    ueberschreibt; jede Zahl bleibt unberuehrt, wie sie war.

    Die Zeile wird ueber ihren vollstaendigen Schluessel angesprochen -
    PROB_ID, PM_ID, PM_VER, UM_ID und GEGR_ID. Weniger davon koennte mehr
    als eine Zeile treffen.
    """
    return """
        UPDATE ergebnisse SET
               mw_roh   = NVL(mw_roh,   :mw_roh),
               mw_org   = NVL(mw_org,   :mw_org),
               mw_n     = NVL(mw_n,     :mw_n),
               mw       = NVL(mw,       :mw),
               anwender = NVL(anwender, :anwender),
               v_faktor = NVL(v_faktor, :v_faktor),
               fc7      = NVL(fc7,      :fc7),
               fc8      = NVL(fc8,      :fc8),
               fc9      = NVL(fc9,      :fc9),
               datum_zeit = NVL(datum_zeit, :datum_zeit),
               psta_id  = :psta_id,
               korrektur_flag = :korrektur_flag
         WHERE prob_id = :prob_id AND pm_id = :pm_id AND pm_ver = :pm_ver
           AND um_id = :um_id AND gegr_id = :gegr_id
    """


# Der Schluessel einer Ergebniszeile - in dieser Reihenfolge ueberall.
ERGEBNIS_SCHLUESSEL = ("prob_id", "pm_id", "pm_ver", "um_id", "gegr_id")

# Wie viele Zeilen eine Abfrage nach belegten Werten auf einmal nennt.
# Oracle nimmt hoechstens tausend Elemente in einer IN-Liste; fuenf
# Spalten je Zeile lassen also zweihundert Zeilen zu. Hundert ist die
# Haelfte davon und damit auf der sicheren Seite.
BELEGT_BUENDEL = 100


def belegt_sql(anzahl: int) -> str:
    """Fragt fuer `anzahl` Ergebniszeilen den vorhandenen MW_ROH ab.

    Zusammengesetzt wird ausschliesslich aus Bindenamen, die hier
    durchgezaehlt werden - kein Wert und kein Bezeichner kommt von
    aussen in den Text. Die Werte selbst gehen als Bindevariablen.
    """
    if anzahl < 1:
        raise ValueError("Ohne Zeilen gibt es nichts abzufragen")
    paare = ", ".join(f"(:p{n}, :m{n}, :v{n}, :u{n}, :g{n})"
                      for n in range(anzahl))
    return f"""
        SELECT prob_id, pm_id, pm_ver, um_id, gegr_id, mw_roh
          FROM ergebnisse
         WHERE mw_roh IS NOT NULL
           AND (prob_id, pm_id, pm_ver, um_id, gegr_id) IN ({paare})
    """


def _schluessel(satz: dict) -> tuple:
    return tuple(satz[name] for name in ERGEBNIS_SCHLUESSEL)


def belegte_werte(verbindung, saetze: list[dict]) -> dict:
    """Was in diesen Ergebniszeilen schon steht - {Schluessel: MW_ROH}.

    Der Export ueberschreibt nichts: jede Spalte wird mit NVL gesetzt,
    ein vorhandener Wert bleibt stehen. Nur sieht man das der Datenbank
    hinterher nicht an - die Anweisung trifft ihre Zeile, ob sie etwas
    aendert oder nicht. Wer wissen will, was wirklich neu geschrieben
    wurde, muss vorher nachsehen, und genau das tut diese Abfrage.

    Zugleich ist sie die einzige verlaessliche Grundlage fuer die
    Exportvorschau: der Laufkontext wird beim Oeffnen der Laufdatei
    gelesen und weiss nichts von einem Export, der seither gelaufen ist.
    """
    schluessel = [_schluessel(satz) for satz in saetze]
    gefunden = {}
    if not schluessel:
        return gefunden
    with verbindung.cursor() as cursor:
        for anfang in range(0, len(schluessel), BELEGT_BUENDEL):
            teil = schluessel[anfang:anfang + BELEGT_BUENDEL]
            binde = {}
            for nummer, (prob, pm, ver, um, gegr) in enumerate(teil):
                binde.update({f"p{nummer}": prob, f"m{nummer}": pm,
                              f"v{nummer}": ver, f"u{nummer}": um,
                              f"g{nummer}": gegr})
            cursor.execute(belegt_sql(len(teil)), binde)
            for zeile in cursor.fetchall():
                gefunden[tuple(zeile[:5])] = als_text(zeile[5])
    return gefunden


# --------------------------------------------------------------------------
# Probenkommentare
# --------------------------------------------------------------------------
#
# Ein Kommentar gehoert zur Ergebniszeile, nicht zum Lauf: "Probe war
# truebe", "zweiter Aufschluss". Er steht in ERGEBNISSE.KOMMENTAR und
# wird von Hand gesetzt - anders als jeder Messwert ist er kein
# Ergebnis, sondern eine Bemerkung dazu.
#
# Geschrieben wird er einzeln und ausdruecklich: nicht beim Export
# nebenher, sondern wenn jemand ihn eintippt und bestaetigt. Deshalb
# eine eigene Anweisung und nicht eine weitere Spalte im Export - dort
# waere er eine Zeile Text unter zehn Zahlen, und niemand haette ihn
# geschrieben.


def kommentar_sql() -> str:
    """Setzt den Kommentar einer Ergebniszeile.

    Gesetzt, nicht ergaenzt: der Text im Feld ist der, den jemand
    gerade geschrieben hat, und er hat den vorhandenen dabei vor Augen
    gehabt - das Fenster zeigt ihn beim Oeffnen. Ein Anhaengen wuerde
    aus einer Korrektur eine Verdopplung machen.

    Die Zeile wird ueber ihren vollstaendigen Schluessel angesprochen,
    wie beim Export: weniger davon koennte mehr als eine treffen.
    """
    return """
        UPDATE ergebnisse SET kommentar = :kommentar
         WHERE prob_id = :prob_id AND pm_id = :pm_id AND pm_ver = :pm_ver
           AND um_id = :um_id AND gegr_id = :gegr_id
    """


def bemerkung_sql() -> str:
    """Setzt die Bemerkung einer Probe.

    PROBEN fuehrt eine Zeile je Teilprobe; angesprochen wird sie ueber
    ihre ID, und die ist eindeutig. Anders als der Kommentar an der
    Ergebniszeile gehoert die Bemerkung der Probe als Ganzes - sie gilt
    fuer jeden Parameter, der an ihr gemessen wurde.

    Gesetzt, nicht ergaenzt: das Fenster zeigt beim Oeffnen, was
    dasteht, und wer ergaenzen will, schreibt hinter den vorhandenen
    Text. Ein Anhaengen durch das Programm machte aus einer Korrektur
    eine Verdopplung.
    """
    return """
        UPDATE proben SET bemerkung = :bemerkung
         WHERE id = :prob_id
    """


def bemerkungen_schreiben(verbindung, bemerkungen, hinweise=None) -> int:
    """Schreibt die geaenderten Probenbemerkungen - ohne eigenes COMMIT.

    Ohne COMMIT, weil sie beim Export mitgehen: sie gehoeren in dieselbe
    Transaktion wie die Messwerte. Entweder steht beides in der
    Datenbank oder keines - eine Bemerkung zu einem Lauf, der nicht
    ankam, waere eine Auskunft ueber nichts.

    `bemerkungen` ist {PROB_ID: Text}; ein leerer Text nimmt die
    Bemerkung zurueck.
    """
    if not bemerkungen:
        return 0
    hinweise = hinweise or {}
    geschrieben = 0
    with verbindung.cursor() as cursor:
        for prob_id, text in sorted(bemerkungen.items()):
            _hinweis_setzen(cursor, hinweise.get(prob_id, ""))
            cursor.execute(bemerkung_sql(),
                           {"bemerkung": (text or "").strip() or None,
                            "prob_id": prob_id})
            geschrieben += cursor.rowcount or 0
    return geschrieben


def kommentar_schreiben(zugang: Zugang, satz: dict, hinweis="") -> int:
    """Schreibt einen Probenkommentar - und sagt, ob eine Zeile getroffen war.

    `satz` traegt den Schluessel der Ergebniszeile und den Text, sonst
    nichts: was hineingeht, muss genau auf die Anweisung passen. Ein
    leerer Text loescht den Kommentar; das ist gewollt, denn anders
    liesse sich eine Bemerkung nie zuruecknehmen.

    `hinweis` ist der Satz in Worten fuer das Aenderungsprotokoll.
    """
    verbindung = zugang.verbinden()
    try:
        with verbindung.cursor() as cursor:
            _hinweis_setzen(cursor, hinweis)
            cursor.execute(kommentar_sql(), satz)
            getroffen = cursor.rowcount or 0
        verbindung.commit()
        return getroffen
    except Exception:
        verbindung.rollback()
        raise
    finally:
        verbindung.close()


def _hinweis_setzen(cursor, hinweis: str):
    """Legt den Satz fuer das Protokoll an den Cursor - wo es einen gibt.

    Ohne Protokoll ist der Cursor der blanke von oracledb, und der
    nimmt keine fremden Felder an. Dann bleibt es bei der Anweisung
    allein.
    """
    if not hinweis:
        return
    try:
        cursor.hinweis = hinweis
    except (AttributeError, TypeError):
        pass


# Wie viele Saetze in einem Zug an die Datenbank gehen. Ein Lauf mit
# dreihundert Proben und zwanzig Elementen ergibt sechstausend Saetze;
# einzeln geschickt sind das sechstausend Rundreisen, bei drei
# Millisekunden also achtzehn Sekunden, in denen das Programm nichts tut
# als warten. Gebuendelt bleiben ein Dutzend Rundreisen uebrig. Nicht
# alles in einem Zug: ein Buendel liegt vollstaendig im Arbeitsspeicher
# des Treibers, und ein Buendel ist die Einheit, in der die Datenbank
# ihre Zeilenzahl meldet.
EXPORT_BUENDEL = 500


def exportieren(zugang: Zugang, saetze: list[dict], hinweise=None,
                bemerkungen=None, bemerkungshinweise=None) -> dict:
    """Schreibt die freigegebenen Werte und gibt Rechenschaft.

    Alles in einer Transaktion: entweder steht der ganze Lauf in der
    Datenbank oder keiner. Ein halb geschriebener Lauf waere schlimmer als
    ein gescheiterter - man saehe ihm nicht an, wo er aufgehoert hat.

    Zurueck kommt, was wirklich geschah - und das ist mehr als eine Zahl:

      * `getroffen`   - so viele Ergebniszeilen hat die Anweisung erreicht,
      * `bestehen`    - davon trugen so viele schon einen MW_ROH; dort hat
                        NVL den alten Wert stehen lassen, geschrieben
                        wurde nichts,
      * `geschrieben` - der Rest, also die wirklich gefuellten Zeilen,
      * `ohne_zeile`  - Saetze, zu denen es keine Ergebniszeile gibt; kein
                        Fehler, aber eine Auskunft.

    Die Unterscheidung ist nicht kosmetisch. Vorher meldete der Export
    "778 von 778 Zeilen geschrieben", obwohl 777 davon unveraendert
    blieben: gezaehlt wurden die getroffenen Zeilen, und getroffen wird
    eine Zeile auch dann, wenn NVL nichts an ihr aendert. Wer das liest,
    glaubt, sein Lauf sei im LIMS.

    Geschrieben wird gebuendelt. Stimmt danach die Summe der getroffenen
    Zeilen mit der Zahl der Saetze ueberein, ist alles angekommen und es
    gibt nichts weiter zu klaeren. Fehlt etwas oder scheitert ein Buendel,
    wird zurueckgerollt und der ganze Stapel einzeln geschrieben: nur so
    laesst sich sagen, *welcher* Satz keine Ergebniszeile hatte oder an
    welchem es lag. Das ist der Weg, den es bisher gab - langsamer, aber
    er benennt die Zeile.
    """
    verbindung = zugang.verbinden()
    try:
        # Vor dem Schreiben und in derselben Transaktion: danach liesse
        # sich nicht mehr sagen, was vorher dastand.
        belegt = belegte_werte(verbindung, saetze)
        try:
            bericht = _export_gebuendelt(verbindung, saetze, hinweise)
        except Exception:
            verbindung.rollback()
            bericht = _export_einzeln(verbindung, saetze, hinweise)
        # Die Probenbemerkungen gehen in derselben Transaktion mit: sie
        # gehoeren zu diesem Lauf, und eine Bemerkung zu einem Export,
        # der nicht ankam, waere eine Auskunft ueber nichts.
        bericht["bemerkungen"] = bemerkungen_schreiben(
            verbindung, bemerkungen, bemerkungshinweise)
        verbindung.commit()
    except Exception:
        verbindung.rollback()
        raise
    finally:
        verbindung.close()
    return _abgerechnet(bericht, saetze, belegt)


def _abgerechnet(bericht: dict, saetze: list[dict], belegt: dict) -> dict:
    """Trennt die gefuellten Zeilen von denen, die schon belegt waren."""
    ohne = {_schluessel(satz) for satz in bericht["ohne_zeile"]}
    bestehen = [satz for satz in saetze
                if _schluessel(satz) in belegt
                and _schluessel(satz) not in ohne]
    bericht = dict(bericht)
    bericht["getroffen"] = bericht.pop("geschrieben")
    bericht["bestehen"] = bestehen
    bericht["belegt"] = belegt
    bericht["geschrieben"] = max(0, bericht["getroffen"] - len(bestehen))
    return bericht


class Unvollstaendig(Exception):
    """Das Buendel hat nicht jede Zeile getroffen - wer, sagt der Einzelweg."""


def _export_gebuendelt(verbindung, saetze: list[dict], hinweise=None) -> dict:
    """Der schnelle Weg: je Buendel eine Rundreise.

    Gezaehlt wird die Summe, nicht die Zeile: die Zaehlung je Satz eines
    Buendels (`arraydmlrowcounts`) gibt es erst ab Oracle 12.1, und die
    NW-FVA arbeitet auf 11.2. Die Summe genuegt fuer die Frage, die
    wirklich zaehlt - jede Anweisung trifft ueber ihren vollstaendigen
    Schluessel hoechstens eine Zeile, also heisst "Summe gleich Anzahl"
    genau: jeder Satz hat seine Ergebniszeile gefunden. Das ist der
    Normalfall und kostet keine zusaetzliche Rundreise.

    Fehlt etwas, weiss dieser Weg nicht, welcher Satz es war - dann
    uebernimmt der einzelne.
    """
    geschrieben = 0
    with verbindung.cursor() as cursor:
        for anfang in range(0, len(saetze), EXPORT_BUENDEL):
            buendel = saetze[anfang:anfang + EXPORT_BUENDEL]
            # Fuer das Protokoll: je Satz sein Satz in Worten. Die
            # Datenbank bekommt davon nichts zu sehen.
            if hinweise is not None:
                cursor.hinweise = hinweise[anfang:anfang + len(buendel)]
            cursor.executemany(export_sql(), buendel)
            geschrieben += cursor.rowcount or 0
    if geschrieben != len(saetze):
        raise Unvollstaendig(
            f"{geschrieben} von {len(saetze)} Saetzen haben eine "
            f"Ergebniszeile getroffen")
    return {"geschrieben": geschrieben, "ohne_zeile": [],
            "versucht": len(saetze)}


def _export_einzeln(verbindung, saetze: list[dict], hinweise=None) -> dict:
    """Der langsame Weg: je Satz eine Anweisung, dafuer je Satz eine Auskunft."""
    geschrieben, ohne_zeile = 0, []
    with verbindung.cursor() as cursor:
        for nummer, satz in enumerate(saetze):
            if hinweise is not None and nummer < len(hinweise):
                cursor.hinweis = hinweise[nummer]
            cursor.execute(export_sql(), satz)
            if cursor.rowcount:
                geschrieben += cursor.rowcount
            else:
                ohne_zeile.append(satz)

    return {"geschrieben": geschrieben, "ohne_zeile": ohne_zeile,
            "versucht": len(saetze)}


# --------------------------------------------------------------------------
# Ablage der Messdateien
# --------------------------------------------------------------------------

# Fester Ablageort der Messdateien.
MESSDATEN_BASIS = r"G:\d\abt\labor\tools\labcontrol"

# Darunter liegen die Geraete beieinander, je Station ein Ordner. Sie
# standen frueher unmittelbar in der Ablage - zusammen mit den eigenen
# Ordnern des Programms, das dort mitliegt, und mit allem anderen, was
# sich dort ansammelt. Ein eigener Ordner haelt sie beisammen: was
# darin steht, ist ein Geraet, und was daneben steht, ist keins.
GERAETE_ORDNER = "Geraete"


def geraetebasis(basis: str | None = None) -> str:
    """Der Ordner, unter dem die Geraete liegen."""
    return os.path.join(basis or MESSDATEN_BASIS, GERAETE_ORDNER)

# Unter Windows in Dateinamen verbotene Zeichen.
_VERBOTEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def ordnername_fuer_station(station: str) -> str:
    """Macht aus einem Stationsnamen einen zulaessigen Ordnernamen."""
    name = _VERBOTEN.sub("_", str(station)).strip().rstrip(".")
    return name or "unbenannt"


def stationsordner(station: str, basis: str | None = None,
                   anlegen: bool = True) -> str:
    """Gibt den Ordner der Station unter dem Geraeteordner.

    Fehlt er, wird er angelegt - die Ordnerstruktur entsteht so mit der
    Nutzung, statt vorher von Hand gepflegt werden zu muessen. `basis` dient
    nur den Tests; im Betrieb gilt MESSDATEN_BASIS.

    Gemessen wird an der Ablage selbst, ob das Laufwerk verbunden ist:
    ein fehlender Geraeteordner ist kein fehlendes Laufwerk, sondern
    eine Ablage, in der noch kein Geraet steht - der Ordner entsteht
    dann hier.
    """
    basis = basis or MESSDATEN_BASIS
    if not os.path.isdir(basis):
        raise ValueError(
            f"Der Ordner '{basis}' ist nicht erreichbar. Ist das Laufwerk "
            f"verbunden?"
        )
    pfad = os.path.join(geraetebasis(basis), ordnername_fuer_station(station))
    if anlegen:
        try:
            os.makedirs(pfad, exist_ok=True)
        except OSError as fehler:
            raise ValueError(
                f"Der Ordner '{pfad}' konnte nicht angelegt werden: {fehler}"
            )
    return pfad


def _derselbe_ordner(einer: str, anderer: str) -> bool:
    """Zwei Pfade, die auf denselben Ordner zeigen.

    Ueber den echten Pfad verglichen und ohne Ruecksicht auf Gross und
    Klein: unter Windows ist "Einstellungen" derselbe Ordner wie
    "einstellungen", und ein verbundenes Laufwerk kann denselben Ort
    unter zwei Buchstaben zeigen.
    """
    try:
        return os.path.samefile(einer, anderer)
    except OSError:
        return os.path.normcase(os.path.abspath(einer)) \
            == os.path.normcase(os.path.abspath(anderer))


def geraeteordner(basis: str | None = None,
                  eigene: "list[str] | tuple[str, ...]" = ()) -> list[str]:
    """Die Unterordner des Geraeteordners - je Geraet einer.

    Sie sind die Liste der Geraete, fuer die sich etwas einstellen laesst:
    was keinen Ordner hat, liefert auch keine Laufdateien. Fehlt der
    Ordner, kommt eine leere Liste statt eines Fehlers - die
    Einstellungen sollen sich auch ohne verbundenes Netzlaufwerk oeffnen
    lassen.

    `eigene` sind die Arbeitsordner der Anwendung selbst - Einstellungen,
    Saves, Regelkarten. Sie liegen neben dem Programm und damit eine
    Ebene ueber den Geraeten; unter "Geraete" haben sie nichts zu
    suchen. Der Ausschluss bleibt trotzdem stehen: er kostet nichts und
    faengt den Fall ab, dass doch einmal einer dort landet.
    """
    ordner_basis = geraetebasis(basis)
    try:
        namen = os.listdir(ordner_basis)
    except OSError:
        return []
    ordner = [n for n in namen
              if os.path.isdir(os.path.join(ordner_basis, n))]
    if eigene:
        ordner = [n for n in ordner
                  if not any(_derselbe_ordner(os.path.join(ordner_basis, n), e)
                             for e in eigene)]
    return sorted(ordner, key=str.lower)


def dateien_im_ordner(pfad: str) -> list[str]:
    """Listet die Dateien eines Ordners, alphabetisch, ohne Unterordner."""
    try:
        eintraege = os.listdir(pfad)
    except OSError as fehler:
        raise ValueError(f"Der Ordner '{pfad}' ist nicht lesbar: {fehler}")
    return sorted((name for name in eintraege
                   if os.path.isfile(os.path.join(pfad, name))),
                  key=str.lower)


def zur_serie_passend(dateien: list[str], serie: str) -> tuple[list[str], bool]:
    """Schraenkt die Dateiliste auf die zur Serie gehoerenden Dateien ein.

    Passend heisst: der Serienname kommt im Dateinamen vor - das deckt sowohl
    "2026W052_lauf1.csv" als auch "export_2026W052.txt" ab.

    Gibt (Liste, wurde_eingeschraenkt). Findet sich nichts, kommen alle
    Dateien zurueck: eine leere Liste saehe aus wie ein leerer Ordner und
    wuerde die Datei verstecken, die man eigentlich sucht.
    """
    serie = (serie or "").strip().upper()
    if not serie:
        return list(dateien), False
    passend = [name for name in dateien if serie in name.upper()]
    if not passend:
        return list(dateien), False
    return passend, True


# --------------------------------------------------------------------------
# Aendern: Vorschau -> UPDATE (ohne COMMIT) -> COMMIT oder ROLLBACK
# --------------------------------------------------------------------------

# Uebernahme fremder Dateien in den Stationsordner
# -----------------------------------------------
#
# Wer eine Messdatei aus dem Explorer auf die Liste zieht, erwartet, dass
# sie danach dort liegt wie jede andere - der Stationsordner bleibt die
# eine Ablage, sonst zeigt die Liste auf Dateien, die woanders liegen und
# beim naechsten Rechner fehlen.
#
# Kopiert wird, nicht verschoben: die Quelle gehoert jemand anderem. Und es
# wird nie stillschweigend ueberschrieben - eine Messdatei ist ein Original.

UEBERNAHME_NEU = "neu"                  # kann kopiert werden
UEBERNAHME_VORHANDEN = "vorhanden"      # Name schon belegt, Rueckfrage noetig
UEBERNAHME_SCHON_DA = "schon_da"        # liegt bereits im Stationsordner
UEBERNAHME_KEINE_DATEI = "keine_datei"  # Ordner, Verknuepfung, nicht lesbar


def uebernahme_pruefen(quelle: str, ordner: str) -> dict:
    """Was passiert, wenn diese Datei in den Stationsordner soll?

    Nur geprueft, nicht kopiert - so kann die Oberflaeche erst fragen und
    dann handeln, statt beim Kopieren mitten im Stapel stehenzubleiben.
    """
    quelle = os.path.abspath(str(quelle).strip())
    name = os.path.basename(quelle)
    ziel = os.path.join(ordner, name)
    ergebnis = {"quelle": quelle, "name": name, "ziel": ziel}
    if not os.path.isfile(quelle):
        return dict(ergebnis, lage=UEBERNAHME_KEINE_DATEI)
    if _gleicher_ordner(os.path.dirname(quelle), ordner):
        return dict(ergebnis, ziel=quelle, lage=UEBERNAHME_SCHON_DA)
    if os.path.exists(ziel):
        return dict(ergebnis, lage=UEBERNAHME_VORHANDEN)
    return dict(ergebnis, lage=UEBERNAHME_NEU)


def _gleicher_ordner(einer: str, anderer: str) -> bool:
    """Windows unterscheidet keine Gross-/Kleinschreibung in Pfaden."""
    return (os.path.normcase(os.path.abspath(einer))
            == os.path.normcase(os.path.abspath(anderer)))


def datei_uebernehmen(quelle: str, ziel: str) -> str:
    """Kopiert die Datei an ihren Platz im Stationsordner.

    copy2 statt copy: das Aenderungsdatum einer Messdatei sagt, wann
    gemessen wurde, und darf beim Kopieren nicht auf heute springen.
    """
    shutil.copy2(quelle, ziel)
    return ziel


def dateien_uebernehmen(vorhaben: list[dict]) -> tuple[list[str], list[str]]:
    """Arbeitet einen geprueften Stapel ab.

    Zurueck kommen die uebernommenen Namen und die Fehlermeldungen. Ein
    Fehler bei einer Datei haelt den Stapel nicht auf - sonst bliebe nach
    einem gesperrten File der Rest liegen, ohne dass jemand erfaehrt, was
    fehlt.
    """
    uebernommen, fehler = [], []
    for eintrag in vorhaben:
        try:
            datei_uebernehmen(eintrag["quelle"], eintrag["ziel"])
            uebernommen.append(eintrag["name"])
        except OSError as ausnahme:
            fehler.append(f"{eintrag['name']}: {ausnahme}")
    return uebernommen, fehler


def vorschau(zugang: Zugang, w: dict) -> dict:
    """Zeigt, welche Zeilen eine Aenderung treffen wuerde. Schreibt nichts."""
    pruefe_eingaben(w)
    verbindung = zugang.verbinden()
    with verbindung:
        with verbindung.cursor() as cursor:
            bausteine = schreibbausteine(cursor, w)
            bindungen = bausteine["bindungen"]
            filter_ = {k: v for k, v in bindungen.items() if k != "neuwert"}

            cursor.execute(
                f"SELECT COUNT(*) FROM {w['tabelle']} WHERE {bausteine['bedingung']}",
                filter_,
            )
            anzahl = cursor.fetchone()[0]

            cursor.execute(
                f"SELECT * FROM {w['tabelle']} WHERE {bausteine['bedingung']} "
                f"AND ROWNUM <= :grenze",
                dict(filter_, grenze=VORSCHAU_ZEILEN),
            )
            spalten = [b.name for b in cursor.description]
            zeilen = cursor.fetchall()

    return {
        "anzahl": anzahl, "spalten": spalten, "zeilen": zeilen,
        "ziel": bausteine["ziel"], "bindungen": bindungen,
        "modus": zugang.modus,
        "sql": (f"UPDATE {w['tabelle']}\n"
                f"   SET {bausteine['ziel']} = :neuwert\n"
                f" WHERE {bausteine['bedingung']}"),
    }


class OffeneAenderung:
    """Ein ausgefuehrtes, noch nicht bestaetigtes UPDATE.

    Eine Transaktion lebt auf ihrer Verbindung - die bleibt deshalb zwischen
    UPDATE und COMMIT offen. Solange haelt sie Sperren auf den betroffenen
    Zeilen; andere Sitzungen warten darauf. Darum rollt ein Wecker die
    Aenderung nach AENDERUNG_FRIST von selbst zurueck, falls niemand
    entscheidet.
    """

    def __init__(self, verbindung, angaben: dict, bei_verfall=None):
        self.verbindung = verbindung
        self.tabelle = angaben["tabelle"]
        self.ziel = angaben["ziel"]
        self.bedingung_spalte = angaben["bedingung_spalte"]
        self.bindungen = angaben["bindungen"]
        self.geaendert = angaben["geaendert"]
        self.spalten = angaben["spalten"]
        self.zeilen = angaben["zeilen"]
        self.modus = angaben["modus"]
        self.beginn = time.monotonic()
        self.erledigt = False
        self._sperre = threading.Lock()
        self._bei_verfall = bei_verfall
        self._wecker = threading.Timer(AENDERUNG_FRIST, self._verfallen)
        self._wecker.daemon = True
        self._wecker.start()

    def _verfallen(self):
        if self._abschliessen(bestaetigen=False):
            if self._bei_verfall:
                self._bei_verfall()

    def _abschliessen(self, bestaetigen: bool) -> bool:
        """Committet oder rollt zurueck. Gibt False, wenn schon erledigt."""
        with self._sperre:
            if self.erledigt:
                return False
            self.erledigt = True
            self._wecker.cancel()
            try:
                if bestaetigen:
                    self.verbindung.commit()
                else:
                    self.verbindung.rollback()
            except Exception:
                pass
            return True

    def bestaetigen(self) -> dict:
        """COMMIT und Rueckgabe des frisch gelesenen Standes als Nachweis."""
        if not self._abschliessen(bestaetigen=True):
            raise ValueError("Diese Aenderung ist bereits abgeschlossen.")
        try:
            with self.verbindung.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {self.tabelle} "
                    f"WHERE {self.bedingung_spalte} = :bedwert AND ROWNUM <= :grenze",
                    {"bedwert": self.bindungen["bedwert"], "grenze": VORSCHAU_ZEILEN},
                )
                spalten = [b.name for b in cursor.description]
                zeilen = cursor.fetchall()
        finally:
            self._schliessen()
        return {"spalten": spalten, "zeilen": zeilen}

    def verwerfen(self) -> bool:
        """ROLLBACK. Gibt False, wenn die Aenderung schon abgeschlossen war."""
        erfolgt = self._abschliessen(bestaetigen=False)
        self._schliessen()
        return erfolgt

    def _schliessen(self):
        try:
            self.verbindung.close()
        except Exception:
            pass


def aendern(zugang: Zugang, w: dict, erwartete_zeilen: int,
            bei_verfall=None) -> OffeneAenderung:
    """Fuehrt das UPDATE aus, ohne zu bestaetigen.

    Trifft die Anweisung eine andere Zeilenzahl als die Vorschau zeigte, hat
    sich die Datenlage zwischenzeitlich geaendert - dann wird sofort
    zurueckgerollt statt blind zu schreiben.
    """
    pruefe_eingaben(w)
    verbindung = zugang.verbinden()
    try:
        with verbindung.cursor() as cursor:
            bausteine = schreibbausteine(cursor, w)
            bindungen = bausteine["bindungen"]

            cursor.execute(
                f"UPDATE {w['tabelle']} SET {bausteine['ziel']} = :neuwert "
                f"WHERE {bausteine['bedingung']}",
                bindungen,
            )
            geaendert = cursor.rowcount

            if geaendert != erwartete_zeilen:
                verbindung.rollback()
                verbindung.close()
                raise ValueError(
                    f"Abgebrochen und zurueckgerollt: die Anweisung haette "
                    f"{geaendert} Zeile(n) getroffen, die Vorschau zeigte "
                    f"{erwartete_zeilen}. Die Daten haben sich zwischenzeitlich "
                    f"geaendert. Es wurde nichts geschrieben."
                )

            # Innerhalb der eigenen Transaktion sichtbar: der neue Stand.
            cursor.execute(
                f"SELECT * FROM {w['tabelle']} WHERE {bausteine['ziel']} = :neuwert "
                f"AND {w['bedingung_spalte'].upper()} = :bedwert "
                f"AND ROWNUM <= :grenze",
                {"neuwert": bindungen["neuwert"], "bedwert": bindungen["bedwert"],
                 "grenze": VORSCHAU_ZEILEN},
            )
            spalten = [b.name for b in cursor.description]
            zeilen = cursor.fetchall()
    except Exception:
        try:
            verbindung.rollback()
            verbindung.close()
        except Exception:
            pass
        raise

    return OffeneAenderung(verbindung, {
        "tabelle": w["tabelle"], "ziel": bausteine["ziel"],
        "bedingung_spalte": w["bedingung_spalte"].upper(),
        "bindungen": bindungen, "geaendert": geaendert,
        "spalten": spalten, "zeilen": zeilen, "modus": zugang.modus,
    }, bei_verfall=bei_verfall)


# --------------------------------------------------------------------------
# Darstellung von Werten
# --------------------------------------------------------------------------

def als_text(wert) -> str:
    """Stellt einen Datenbankwert fuer die Anzeige dar."""
    if wert is None:
        return ""
    if isinstance(wert, bool):
        return str(wert)
    if isinstance(wert, dt.datetime):
        return wert.strftime("%d.%m.%Y %H:%M:%S")
    if isinstance(wert, dt.date):
        return wert.strftime("%d.%m.%Y")
    if isinstance(wert, bytes):
        return f"<{len(wert)} Byte>"
    return str(wert)


def ist_zahl(wert) -> bool:
    return isinstance(wert, (int, float, decimal.Decimal)) and not isinstance(wert, bool)


def csv_bytes(zeilen: list) -> bytes:
    """Schreibt Zeilen als CSV fuer Excel.

    Semikolon als Trennzeichen, Komma als Dezimaltrennzeichen, BOM damit Excel
    die Umlaute richtig liest.
    """
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";", lineterminator="\r\n")
    for zeile in zeilen:
        schreiber.writerow(
            "" if w is None
            else str(w).replace(".", ",") if isinstance(w, (float, decimal.Decimal))
            else als_text(w)
            for w in zeile
        )
    return puffer.getvalue().encode("utf-8-sig")


def als_csv(spalten: list, zeilen: list) -> bytes:
    """Baut eine CSV-Datei aus Ueberschriften und Zeilen."""
    return csv_bytes([list(spalten)] + [list(z) for z in zeilen])


def csv_bloecke(bloecke, abstand: int = 3) -> bytes:
    """Mehrere Tabellen in einer Datei, durch Leerzeilen getrennt.

    `bloecke` sind (Titel, Ueberschriften, Zeilen). Ein Reiter zeigt
    manchmal mehrere Tabellen untereinander - je Kontrollstandard eine.
    In einer CSV muessen sie auseinanderzuhalten sein: deshalb je Block
    seine eigene Ueberschriftenzeile und dazwischen Luft.
    """
    zeilen = []
    for titel, spalten, inhalt in bloecke:
        if zeilen:
            zeilen += [[] for _ in range(max(0, abstand))]
        if titel:
            zeilen.append([titel])
        zeilen.append(list(spalten))
        zeilen += [list(zeile) for zeile in inhalt]
    return csv_bytes(zeilen)


def dateiname_fuer(serie: str) -> str:
    return f"ergebnisse_{re.sub(r'[^A-Za-z0-9_-]', '', serie)}.csv"


# --------------------------------------------------------------------------
# Selbsttest
# --------------------------------------------------------------------------

def selbsttest() -> tuple[int, list[str]]:
    """Prueft ohne Datenbank, ob alle Laufzeitbestandteile mitgeliefert wurden.

    python-oracledb laedt cryptography erst beim Verbindungsaufbau nach; fehlt
    es im Paket, meldet es DPY-3016 - und zwar bevor ueberhaupt ein
    Netzwerkversuch stattfindet. Ein Verbindungsversuch ins Leere trennt daher
    die beiden Faelle.
    """
    zeilen = [f"LabControl Selbsttest ({BITNESS}-bit)"]
    try:
        import cryptography
        zeilen.append(f"  cryptography {cryptography.__version__} vorhanden")
    except Exception as fehler:
        zeilen.append(f"  FEHLER: cryptography fehlt ({fehler})")
        return 1, zeilen

    try:
        import tkinter
        zeilen.append("  tkinter vorhanden")
    except Exception as fehler:
        zeilen.append(f"  FEHLER: tkinter fehlt ({fehler})")
        return 1, zeilen

    # openpyxl wird erst beim Oeffnen einer xlsx-Datei importiert und faellt
    # sonst erst beim Anwender auf.
    try:
        import openpyxl
        zeilen.append(f"  openpyxl {openpyxl.__version__} vorhanden")
    except Exception as fehler:
        zeilen.append(f"  FEHLER: openpyxl fehlt ({fehler})")
        return 1, zeilen

    # tkinterdnd2 sucht die tkdnd-Bibliotheken zur Laufzeit neben sich. Ohne
    # --collect-all fehlen sie im Paket, und das Ziehen aus dem Explorer
    # fiele stillschweigend aus - erst beim Anwender.
    try:
        import tkinterdnd2
        ordner = os.path.join(os.path.dirname(tkinterdnd2.__file__), "tkdnd")
        vorhanden = sorted(os.listdir(ordner)) if os.path.isdir(ordner) else []
        if not vorhanden:
            zeilen.append("  FEHLER: tkinterdnd2 ohne tkdnd-Bibliotheken "
                          f"verpackt ({ordner})")
            return 1, zeilen
        zeilen.append(f"  tkinterdnd2 vorhanden, tkdnd fuer "
                      f"{', '.join(vorhanden)}")
    except Exception as fehler:                             # noqa: BLE001
        zeilen.append(f"  FEHLER: tkinterdnd2 fehlt ({fehler})")
        return 1, zeilen

    dsn = ("(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=127.0.0.1)(PORT=1521))"
           "(CONNECT_DATA=(SERVICE_NAME=SELBSTTEST)))")
    try:
        oracledb.connect(user="selbsttest", password="selbsttest", dsn=dsn)
        zeilen.append("  unerwartet: es kam eine Verbindung zustande")
    except Exception as fehler:
        meldung = str(fehler).splitlines()[0]
        if "DPY-3016" in meldung or "cryptography" in meldung:
            zeilen.append(f"  FEHLER: Thin Mode unvollstaendig verpackt - {meldung}")
            return 1, zeilen
        zeilen.append(f"  Thin Mode einsatzbereit (erwarteter Netzwerkfehler: {meldung})")
    return 0, zeilen


# --------------------------------------------------------------------------
# Die TRDF-Pruefung
# --------------------------------------------------------------------------
#
# Eine Serie der Probenvorbereitung, in der das LIMS aus Rohwerten zwoelf
# Groessen rechnet. Was hier geholt wird, ist alles, was noetig ist, um
# dieselbe Rechnung noch einmal zu machen: die Rohwertparameter mit ihren
# Formelkuerzeln, die Pruefmethoden mit ihren Formeln, und was das LIMS
# gebucht hat.
#
# Die Kette:
#   UNTERSUCHUNGSMETHODE  Kuerzel traegt "TRDF"
#     -> S_FAHRPLAN       welche Serien anstehen
#       -> UM_ROHWERTE    welcher Rohwert unter welchem Formelkuerzel
#         -> ROHWERTPARAMETER  wie er heisst
#       -> ERGEBNISSE     was gebucht ist, je PROB_ID und PM_ID
#         -> PRUEFMETHODEN  die Formel dahinter

# Woran eine TRDF-Methode zu erkennen ist.
TRDF_MARKE = "TRDF"

# Der Wiederfindungsgrad steht unter dieser Pruefmethode - so steht es
# auch in den Formeln des LIMS selbst.
WGH_PM_ID = 701

# Der Aufschluss daneben. CO3 kommt aus einer eigenen Methode, deren
# Kuerzel den Parameter im Namen traegt; Cges aus der allgemeinen.
ATNULL_MARKE = "ATNULL"
ATNULL_CO3_MARKE = "ATNULLCO3"
PARA_CGES = 31
PARA_CO3 = 33


def trdf_methoden(zugang, verbindung=None) -> list[tuple]:
    """Die Untersuchungsmethoden, deren Kuerzel "TRDF" traegt."""
    zeilen = _zeilen(zugang, """
        SELECT id, kuerzel FROM untersuchungsmethode
         WHERE UPPER(kuerzel) LIKE :marke
         ORDER BY kuerzel, id
    """, {"marke": f"%{TRDF_MARKE}%"}, verbindung=verbindung)
    return [(zeile[0], str(zeile[1] or "").strip()) for zeile in zeilen]


def trdf_serien(zugang, um_ids, verbindung=None) -> list[str]:
    """Die Serien aus dem Fahrplan, die eine TRDF-Methode fuehren.

    Derselbe Weg wie bei jeder anderen Methode - deshalb steht er in
    serien_mit_methoden(). Der eigene Name bleibt: der TRDF-Reiter
    fragt nach TRDF-Serien, nicht nach "Serien zu diesen IDs".
    """
    return serien_mit_methoden(zugang, um_ids, verbindung=verbindung)


ROHWERT_FELDER = ("rohw_id", "lnr", "formelkuerzel", "name", "kuerzel")


def trdf_rohwertparameter(zugang, um_id, verbindung=None) -> list[dict]:
    """Die Rohwerte dieser Methode - Name und Formelkuerzel.

    Damit wird die eingefuegte Spalte zum Formelkuerzel: der Kopf der
    Liste traegt den Namen, gerechnet wird mit dem Kuerzel. Beides steht
    im LIMS, nichts davon wird im Programm gepflegt.

    Das Formelkuerzel kann an der Methode haengen (UM_ROHWERTE) oder am
    Parameter selbst; das erste sticht.
    """
    zeilen = _zeilen(zugang, """
        SELECT r.id, u.lnr,
               NVL(u.formelkuerzel, r.formelkuerzel), r.name, r.kuerzel
          FROM um_rohwerte u
          JOIN rohwertparameter r ON r.id = u.rohw_id
         WHERE u.um_id = :um_id
         ORDER BY u.lnr, r.sort, r.id
    """, {"um_id": um_id}, verbindung=verbindung)
    return _dicts(zeilen, ROHWERT_FELDER)


TRDF_METHODEN_FELDER = ("pm_id", "pm_ver", "para_id", "name", "kurzname",
                        "formelkuerzel", "formel", "gegr_id", "geme_id",
                        "format", "parameter")


def trdf_pruefmethoden(zugang, serie: str, um_id, verbindung=None,
                       proben=None) -> list[dict]:
    """Die Pruefmethoden dieser Serie - mit ihren Formeln.

    Eingegrenzt auf das, was in ERGEBNISSE zu dieser Serie und Methode
    wirklich vorkommt, und mit *deren* PM_VER: zu einer Pruefmethode gibt
    es mehrere Staende, und gerechnet hat das LIMS mit dem, der an der
    Ergebniszeile haengt.

    Ohne GEME_ID ist es eine berechnete Groesse, mit ist es ein Rohwert -
    bis auf zwei Ausnahmen, die das Labor kennt. Verlassen wird sich
    darauf nicht: was eine Formel hat, wird gerechnet.
    """
    bindungen = {"um_id": um_id}
    wo = _wonach("e", serie, proben, bindungen)
    zeilen = _zeilen(zugang, f"""
        SELECT p.id, p.version, p.para_id, p.name, p.kurzname,
               p.formelkuerzel, p.formel, p.gegr_id, p.geme_id, p.format,
               pa.name
          FROM pruefmethoden p
          LEFT JOIN parameter pa ON pa.id = p.para_id
         WHERE (p.id, p.version) IN (SELECT DISTINCT e.pm_id, e.pm_ver
                                       FROM ergebnisse e
                                      WHERE {wo}
                                        AND e.um_id = :um_id)
         ORDER BY p.id, p.version
    """, bindungen, verbindung=verbindung)
    return _dicts(zeilen, TRDF_METHODEN_FELDER)


# Wie viele Probennummern eine Abfrage auf einmal vertraegt. Oracle
# nimmt in einer IN-Liste tausend; wer mehr hat, hat eine Serie.
PROBEN_HOECHSTENS = 500


def probenliste(text) -> list:
    """Aus einer Eingabe die Probennummern - eine Liste ohne Doppelte.

    Getrennt wird an allem, was kein Zeichen einer Probennummer ist:
    Komma, Strichpunkt, Leerzeichen, Zeilenumbruch. So laesst sich eine
    Spalte aus Excel ebenso einfuegen wie eine von Hand getippte Reihe.
    """
    gefunden, gesehen = [], set()
    for stueck in re.split(r"[^0-9A-Za-z_.\-]+", str(text or "")):
        nummer = stueck.strip()
        if nummer and nummer.upper() not in gesehen:
            gesehen.add(nummer.upper())
            gefunden.append(nummer)
    return gefunden


def _wonach(alias: str, serie, proben, bindungen: dict, spalte=None) -> str:
    """Woran eine TRDF-Abfrage haengt: an der Serie oder an Proben.

    Das ist der ganze Unterschied zwischen den beiden Wegen. Alles
    andere - die Methode, die Verbindung ueber PROB_ID, die Felder -
    bleibt gleich, und deshalb bleiben es dieselben Abfragen.
    """
    if proben:
        return _probenfilter(spalte or f"{alias}.prob_id", proben, bindungen)
    bindungen["serie"] = serie
    return f"{alias}.serie = :serie"


def _probenfilter(spalte: str, proben, bindungen: dict) -> str:
    """Die Bedingung, die eine Abfrage auf einzelne Proben einschraenkt.

    Gesucht wird ueber PROBEN.PROBE_NR; die Verbindung zu den
    Ergebnissen ist PROBEN.ID = ERGEBNISSE.PROB_ID. Verglichen wird
    ohne Ruecksicht auf Gross- und Kleinschreibung - eine Probennummer
    wird abgetippt, und niemand achtet dabei auf das B in der Mitte.
    """
    namen = []
    echte = [str(wert).strip() for wert in proben if str(wert).strip()]
    for nummer, wert in enumerate(echte[:PROBEN_HOECHSTENS]):
        schluessel = f"pnr{nummer}"
        bindungen[schluessel] = wert.upper()
        namen.append(f":{schluessel}")
    if not namen:
        return "1 = 0"           # keine Probe genannt, keine Zeile zurueck
    return (f"{spalte} IN (SELECT id FROM proben "
            f"WHERE UPPER(probe_nr) IN ({', '.join(namen)}))")


TRDF_ERGEBNIS_FELDER = ("prob_id", "probe_nr", "wdh_um", "wdh_me", "lnr",
                        "pm_id", "pm_ver", "um_id", "gegr_id", "mw_roh", "mw",
                        "psta_id", "korrektur_flag", "fc8", "serie")


def trdf_ergebnisse(zugang, serie: str, um_id, verbindung=None,
                    proben=None) -> list[dict]:
    """Was das LIMS zu dieser Serie gebucht hat - Rohwerte und Gerechnetes.

    Wiederholungen bleiben draussen: geprueft wird die Erstmessung.

    Mitgelesen wird der ganze Schluessel der Zeile - UM_ID und GEGR_ID
    gehoeren dazu - und ihr Bearbeitungsstand. Beides braucht der
    Ruecksprung: eine Zeile laesst sich nur ueber ihren vollstaendigen
    Schluessel ansprechen, und was vorher darin stand, muss in die
    Sicherung, bevor etwas ueberschrieben wird.
    """
    bindungen = {"um_id": um_id}
    wo = _wonach("e", serie, proben, bindungen)
    zeilen = _zeilen(zugang, f"""
        SELECT e.prob_id, p.probe_nr, p.wdh_um, p.wdh_me, e.lnr,
               e.pm_id, e.pm_ver, e.um_id, e.gegr_id, e.mw_roh, e.mw,
               e.psta_id, e.korrektur_flag, e.fc8, e.serie
          FROM ergebnisse e
          JOIN proben p ON p.id = e.prob_id
         WHERE {wo} AND e.um_id = :um_id
           AND NVL(p.wdh_um, 1) = 1 AND NVL(p.wdh_me, 1) = 1
         ORDER BY e.lnr, p.probe_nr, e.pm_id
    """, bindungen, verbindung=verbindung)
    return _dicts(zeilen, TRDF_ERGEBNIS_FELDER)


TRDF_ANHANG_FELDER = ("prob_id", "um_id", "rohw_id", "lnr", "formelkuerzel",
                      "art", "mw", "mw_old", "format")


def trdf_rohwerte_anhang(zugang, serie: str, um_id, verbindung=None,
                         proben=None) -> list:
    """Die Rohwerte, wie sie am Teilprobenanhang haengen.

    Das LIMS fuehrt sie zweimal: als Ergebniszeile unter ihrer
    Pruefmethode und hier, an der Teilprobe, unter ihrem Formelkuerzel.
    Aus dieser Stelle rechnen die Formeln - wer nur die Ergebniszeile
    korrigiert, aendert am Rechenweg des LIMS nichts.

    Der Schluessel ist (PROB_ID, UM_ID, ROHW_ID); die Serie steht nicht
    darin und kommt ueber TEILPROBEN dazu.
    """
    bindungen = {"um_id": um_id}
    wo = _wonach("t", serie, proben, bindungen, spalte="a.prob_id")
    zeilen = _zeilen(zugang, f"""
        SELECT a.prob_id, a.um_id, a.rohw_id, a.lnr, a.formelkuerzel,
               a.art, a.mw, a.mw_old, a.format
          FROM teilproben_anhang a
          JOIN teilproben t ON t.prob_id = a.prob_id AND t.um_id = a.um_id
         WHERE {wo} AND t.um_id = :um_id
         ORDER BY a.prob_id, a.lnr
    """, bindungen, verbindung=verbindung)
    gefunden = _dicts(zeilen, TRDF_ANHANG_FELDER)
    for eintrag in gefunden:
        eintrag["formelkuerzel"] = str(eintrag["formelkuerzel"] or "").strip()
    return gefunden


def trdf_wiederfindung(zugang, serie: str, verbindung=None,
                       proben=None) -> dict:
    """Der Wiederfindungsgrad je Probe - PROB_ID auf MW.

    Er haengt nicht an der TRDF-Methode, sondern an seiner eigenen; die
    Formeln des LIMS holen ihn ueber die PM_ID, und genau so wird er
    hier geholt.
    """
    bindungen = {"pm_id": WGH_PM_ID}
    wo = _wonach("e", serie, proben, bindungen)
    zeilen = _zeilen(zugang, f"""
        SELECT e.prob_id, e.mw
          FROM ergebnisse e
         WHERE {wo} AND e.pm_id = :pm_id
    """, bindungen, verbindung=verbindung)
    return {zeile[0]: zeile[1] for zeile in zeilen}


AUFSCHLUSS_FELDER = ("prob_id", "para_id", "kuerzel", "mw")


def trdf_aufschluss(zugang, serie: str, verbindung=None,
                    proben=None) -> list[dict]:
    """Cges und CO3 aus dem Aufschluss - zum Danebenstellen.

    Genommen wird MW, der Gehalt. Zwei Methoden: CO3 steht unter der,
    deren Kuerzel den Parameter im Namen traegt (ATNULLCO3), Cges unter
    der allgemeinen. Weil das eine im anderen steckt, wird beim Lesen
    nach dem Kuerzel unterschieden und nicht in der Anweisung.
    """
    bindungen = {"marke": f"%{ATNULL_MARKE}%",
                 "cges": PARA_CGES, "co3": PARA_CO3}
    wo = _wonach("e", serie, proben, bindungen)
    zeilen = _zeilen(zugang, f"""
        SELECT e.prob_id, e.para_id, u.kuerzel, e.mw
          FROM ergebnisse e
          JOIN untersuchungsmethode u ON u.id = e.um_id
         WHERE {wo}
           AND UPPER(u.kuerzel) LIKE :marke
           AND e.para_id IN (:cges, :co3)
    """, bindungen, verbindung=verbindung)
    gefunden = _dicts(zeilen, AUFSCHLUSS_FELDER)
    for eintrag in gefunden:
        eintrag["kuerzel"] = str(eintrag["kuerzel"] or "").strip()
    return [eintrag for eintrag in gefunden if aufschluss_passt(eintrag)]


def aufschluss_passt(eintrag: dict) -> bool:
    """Kommt dieser Wert aus der Methode, die fuer ihn zustaendig ist?"""
    co3_methode = ATNULL_CO3_MARKE in str(eintrag.get("kuerzel", "")).upper()
    if eintrag.get("para_id") == PARA_CO3:
        return co3_methode
    return not co3_methode


# --------------------------------------------------------------------------
# Der Rueckweg der TRDF-Pruefung
# --------------------------------------------------------------------------
# Dies ist der einzige Weg im Programm, der einen Messwert in ERGEBNISSE
# ueberschreibt. Er steht deshalb hier fuer sich, mit eigenem Namen und
# eigener Anweisung, und nicht im gewoehnlichen Export - dort laesst NVL
# jede Zahl stehen, und das soll dort so bleiben.
#
# Erlaubt ist er, weil er etwas anderes tut als der Export einer
# Laufdatei: dort kommen Werte von einem Geraet und fuellen leere
# Zeilen; hier korrigiert ein Mensch einen Rohwert, den das Labor als
# falsch erkannt hat, und die daraus folgenden Groessen muessen
# mitwandern. Ein zweiter Wert daneben waere keine Korrektur, sondern
# ein Widerspruch.
#
# Drei Riegel liegen davor, und sie liegen ausserhalb dieser Datei:
# geschrieben wird nur, was in der Uebersicht Zeile fuer Zeile mit altem
# und neuem Wert bestaetigt wurde; vorher geht der alte Stand in eine
# Sicherung; und getroffen wird ausschliesslich der volle Schluessel
# einer vorhandenen Zeile - angelegt wird nichts.

# Was in FC8 steht, damit im LIMS zu sehen ist, woher der Wert kommt.
TRDF_FC8 = "TRDF-Korrektur"

# MW und MW_ROH tragen bei TRDF denselben Wert: die Faktoren des LIMS
# (FAKTOR, FAKTOR_WGH, END_FAKTOR) gelten hier nicht, gerechnet wird
# ausschliesslich mit dem, was in den Formeln steht.
TRDF_EXPORT_SPALTEN = ("MW_ROH", "MW", "FC8", "PSTA_ID", "KORREKTUR_FLAG")


def trdf_export_sql() -> str:
    """Die Anweisung, die eine korrigierte Ergebniszeile ueberschreibt.

    Kein NVL: hier ist das Ueberschreiben der Zweck. Dafuer ist die
    Anweisung so eng wie moeglich - fuenf Spalten, der volle Schluessel,
    und keine Zeile, die es nicht schon gibt (ohne Treffer meldet der
    Aufrufer die Zeile, statt sie anzulegen).

    MW und MW_ROH bekommen denselben Wert. Bei TRDF gibt es keine
    Faktorverrechnung; ein Gehalt, der vom gemessenen Wert abweicht,
    waere hier eine zweite Aussage ueber dieselbe Sache.

    Die GEGR_ID darf leer sein - in ERGEBNISSE ist sie laut Schema
    nullable. Ein schlichtes `gegr_id = :gegr_id` traefe eine solche
    Zeile nie: in SQL ist NULL mit nichts gleich, auch nicht mit NULL,
    und die Anweisung liefe ins Leere, ohne dass etwas schiefginge.
    Deshalb der zweite Zweig; die Gleichheit steht zuerst, damit der
    Index weiter benutzt wird.
    """
    return """
        UPDATE ergebnisse SET
               mw_roh = :wert,
               mw     = :wert,
               fc8    = :fc8,
               psta_id = :psta_id,
               korrektur_flag = :korrektur_flag
         WHERE prob_id = :prob_id AND pm_id = :pm_id AND pm_ver = :pm_ver
           AND um_id = :um_id
           AND (gegr_id = :gegr_id OR (gegr_id IS NULL AND :gegr_id IS NULL))
    """


TRDF_SATZ_FELDER = ("wert", "fc8", "psta_id", "korrektur_flag", "prob_id",
                    "pm_id", "pm_ver", "um_id", "gegr_id")

# Zurueck gilt ein Feld mehr: MW steht in der Sicherung als eigene
# Spalte, und nur die Korrektur darf annehmen, dass es dasselbe ist wie
# MW_ROH. Vor ihr kann dort etwas anderes gestanden haben - eine alte
# Faktorverrechnung etwa -, und eine Sicherung, die das wegwirft,
# stellt nicht den Stand von vorher wieder her.
TRDF_ZURUECK_FELDER = TRDF_SATZ_FELDER + ("mw",)


def trdf_zurueck_sql() -> str:
    """Die Anweisung, die eine Ergebniszeile auf ihren Stand zurueckstellt.

    Wie `trdf_export_sql`, mit einem Unterschied: MW_ROH und MW bekommen
    nicht denselben Wert, sondern jeder seinen eigenen aus der
    Sicherung. Der Schluessel ist Wort fuer Wort derselbe - auch der
    zweite Zweig fuer die leere GEGR_ID.
    """
    return """
        UPDATE ergebnisse SET
               mw_roh = :wert,
               mw     = :mw,
               fc8    = :fc8,
               psta_id = :psta_id,
               korrektur_flag = :korrektur_flag
         WHERE prob_id = :prob_id AND pm_id = :pm_id AND pm_ver = :pm_ver
           AND um_id = :um_id
           AND (gegr_id = :gegr_id OR (gegr_id IS NULL AND :gegr_id IS NULL))
    """

# Der Anhang traegt den Wert und den Stand davor - kein Kennzeichen.
# Was geschehen ist, steht in der Ergebniszeile daneben und im Protokoll.
TRDF_ANHANG_SATZ_FELDER = ("wert", "prob_id", "um_id", "rohw_id")


def trdf_anhang_sql() -> str:
    """Die Anweisung, die einen Rohwert am Teilprobenanhang richtigstellt.

    MW_OLD nimmt den bisherigen Wert auf - so, wie das LIMS es selbst
    haelt: die Spalte traegt den Stand, der gerade ersetzt wurde. Dafuer
    steht dort `mw` und keine Bindevariable: Oracle wertet alle
    Zuweisungen einer UPDATE-Anweisung gegen den Stand *vor* der
    Aenderung aus, es kommt also der Wert hinein, der wirklich in der
    Zeile stand - und nicht der, den LabControl vor einer Minute gelesen
    hat.
    """
    return """
        UPDATE teilproben_anhang SET mw_old = mw, mw = :wert
         WHERE prob_id = :prob_id AND um_id = :um_id AND rohw_id = :rohw_id
    """


# Beim Zurueckspielen wird auch MW_OLD wieder gesetzt: die Sicherung
# haelt den ganzen Stand fest, und ein halb wiederhergestellter waere
# keiner. Deshalb eine zweite Anweisung - die Korrektur darf MW_OLD
# nicht binden (sonst schriebe sie einen veralteten Stand fort), das
# Zurueckspielen muss es.
TRDF_ANHANG_ZURUECK_FELDER = ("wert", "alt", "prob_id", "um_id", "rohw_id")


def trdf_anhang_zurueck_sql() -> str:
    """Die Anweisung, die den Anhang auf den gesicherten Stand zurueckstellt."""
    return """
        UPDATE teilproben_anhang SET mw = :wert, mw_old = :alt
         WHERE prob_id = :prob_id AND um_id = :um_id AND rohw_id = :rohw_id
    """


def trdf_anhang_satz_pruefen(satz: dict, leeren: bool = False):
    """Wie beim Ergebnis: nichts Halbes in die Anweisung."""
    fehlend = [feld for feld in TRDF_ANHANG_SATZ_FELDER
               if satz.get(feld) is None]
    if fehlend:
        raise ValueError("Unvollstaendiger Satz fuer den Teilprobenanhang: "
                         + ", ".join(fehlend))
    if not str(satz["wert"]).strip() and not leeren:
        raise ValueError("Ein leerer Wert wuerde die Zahl im LIMS loeschen.")


# Der eine Schluesselteil, der leer sein darf: ERGEBNISSE.GEGR_ID ist
# nullable, und eine Zeile ohne Geraetegruppe ist trotzdem eine Zeile.
# Die Anweisung faengt den Fall mit einem eigenen Zweig ab.
TRDF_DARF_LEER = ("gegr_id",)


def trdf_satz_pruefen(satz: dict, leeren: bool = False):
    """Laesst nur durch, was vollstaendig ist - und wirft sonst.

    Ein fehlender Schluesselteil wuerde die Anweisung nicht auf eine
    andere Zeile lenken (sie faende dann gar keine), aber ein leerer Wert
    wuerde eine Zahl loeschen. Beides wird hier abgefangen, bevor die
    Transaktion beginnt.

    Die GEGR_ID ist die Ausnahme: sie darf im LIMS leer sein, und dann
    muss auch der Satz sie leer tragen - sonst faende die Anweisung die
    Zeile nicht.

    `leeren` ist die zweite: die TRDF-Pruefung darf einen Wert auch
    loeschen. Das LIMS kennt dort drei Staende - eine Zahl, ein „x“
    (hier soll nichts stehen) und eine leere Zelle -, und wer eine
    Variante wechselt, stellt genau diese Staende her. Es geschieht nur
    ueber eine bestaetigte Uebersicht, die sagt, wie viele Werte
    geleert werden, und nach einer Sicherung.
    """
    fehlend = [feld for feld in TRDF_SATZ_FELDER
               if satz.get(feld) is None and feld not in TRDF_DARF_LEER]
    if "gegr_id" not in satz:
        fehlend.append("gegr_id")
    if fehlend:
        raise ValueError("Unvollstaendiger Satz fuer den TRDF-Rueckweg: "
                         + ", ".join(fehlend))
    if not str(satz["wert"]).strip() and not leeren:
        raise ValueError("Ein leerer Wert wuerde die Zahl im LIMS loeschen.")


# Beim Zurueckspielen gelten andere Regeln als beim Korrigieren, und
# zwar strengere und lockerere zugleich. Der Schluessel muss vollstaendig
# sein wie immer - er sucht die Zeile. Die geschriebenen Spalten aber
# duerfen leer sein, denn zurueckgestellt wird der Stand, den die
# Sicherung festhaelt: PSTA_ID ist im LIMS nullable, und wo vor der
# Korrektur kein Bearbeitungsstand und kein Wert stand, darf danach
# auch keiner stehen. Sonst waere das Zurueckspielen keines, sondern
# eine zweite Korrektur.
TRDF_ZURUECK_SCHLUESSEL = ("prob_id", "pm_id", "pm_ver", "um_id")


def trdf_zurueck_pruefen(satz: dict):
    """Der Schluessel muss stehen - der gesicherte Stand darf leer sein."""
    fehlend = [feld for feld in TRDF_ZURUECK_SCHLUESSEL
               if satz.get(feld) is None]
    # Da sein muessen sie alle: was die Anweisung bindet und der Satz
    # nicht traegt, faende Oracle nicht - und der Stand von damals
    # bliebe halb wiederhergestellt.
    fehlend += [feld for feld in TRDF_ZURUECK_FELDER if feld not in satz]
    if fehlend:
        raise ValueError("Unvollstaendiger Satz fuer das Zurueckspielen: "
                         + ", ".join(fehlend))


def trdf_nachlesen_sql() -> str:
    """Was jetzt wirklich in der Ergebniszeile steht.

    Derselbe Schluessel wie beim Schreiben - Wort fuer Wort, damit die
    Nachfrage dieselbe Zeile trifft und nicht eine benachbarte.
    """
    return """
        SELECT mw_roh FROM ergebnisse
         WHERE prob_id = :prob_id AND pm_id = :pm_id AND pm_ver = :pm_ver
           AND um_id = :um_id
           AND (gegr_id = :gegr_id OR (gegr_id IS NULL AND :gegr_id IS NULL))
    """


def trdf_anhang_nachlesen_sql() -> str:
    """Dasselbe fuer den Teilprobenanhang."""
    return """
        SELECT mw FROM teilproben_anhang
         WHERE prob_id = :prob_id AND um_id = :um_id AND rohw_id = :rohw_id
    """


def _angekommen(steht_da, gewollt) -> bool:
    """Steht jetzt da, was hingeschrieben werden sollte?

    Verglichen wird erst als Text, dann als Zahl: das LIMS speichert
    Messwerte als Zeichenkette, und ein angehaengtes Leerzeichen oder
    eine fehlende Null waere kein Unterschied in der Sache.
    """
    hier, dort = str(steht_da or "").strip(), str(gewollt or "").strip()
    if hier == dort:
        return True
    eine, andere = als_zahl(hier), als_zahl(dort)
    return eine is not None and andere is not None and eine == andere


def _nachpruefen(cursor, sql: str, saetze, schluessel: tuple) -> list:
    """Liest die geschriebenen Zeilen zurueck und meldet, was nicht ankam.

    Ein UPDATE, das keine Zeile trifft, ist kein Fehler - es ist nur
    nichts geschehen. Ein UPDATE, das eine Zeile trifft und trotzdem den
    alten Wert stehen laesst, waere einer, und beides sieht von aussen
    gleich aus. Deshalb wird nachgesehen, nachdem festgeschrieben wurde.
    """
    geblieben = []
    for satz in saetze:
        cursor.execute(sql, {feld: satz[feld] for feld in schluessel})
        zeile = cursor.fetchone()
        if zeile is None or not _angekommen(zeile[0], satz["wert"]):
            geblieben.append(satz)
    return geblieben


def _schreiben(cursor, sql: str, saetze, hinweise=None) -> tuple:
    """Fuehrt die Anweisung Satz fuer Satz aus - und zaehlt beides mit."""
    geschrieben, ohne_zeile = 0, []
    for nummer, satz in enumerate(saetze):
        if hinweise is not None and nummer < len(hinweise):
            cursor.hinweis = hinweise[nummer]
        cursor.execute(sql, satz)
        if cursor.rowcount:
            geschrieben += cursor.rowcount
        else:
            ohne_zeile.append(satz)
    return geschrieben, ohne_zeile


def trdf_anhang_zurueck_pruefen(satz: dict):
    """Beim Zurueckspielen darf MW_OLD leer sein - vorher war es das auch."""
    fehlend = [feld for feld in TRDF_ANHANG_ZURUECK_FELDER
               if feld != "alt" and satz.get(feld) is None]
    if fehlend:
        raise ValueError("Unvollstaendiger Satz fuer den Teilprobenanhang: "
                         + ", ".join(fehlend))
    if "alt" not in satz:
        raise ValueError("Ohne MW_OLD ist es kein Zurueckspielen.")


# ==========================================================================
# Die Stammdaten eines Standards - lesen und pflegen
# ==========================================================================
# STANDARD_PARA traegt je Standard, Untersuchungsmethode und Parameter
# den Sollwert, die prozentuale Toleranz und die Grenzen. Aus der
# Auswertung einer Regelkarte kommen neue Zahlen dafuer (siehe
# kartenauswertung); dieser Weg schreibt sie zurueck.
#
# Es ist die dritte Tabelle, in die LabControl ueberhaupt schreibt -
# nach ERGEBNISSE und SERIEN_MW_ANHANG. Und die erste, in der
# Stammdaten stehen: ein falscher Wert hier bewertet jede kuenftige
# Messung falsch. Deshalb geht der Weg nur ueber eine bestaetigte
# Vorschau und eine Sicherung, und geschrieben wird ueber die ID der
# gelesenen Zeile - nicht ueber einen zusammengesetzten Schluessel, der
# eine zweite Zeile treffen koennte.

STANDARDPARA_FELDER = (
    "id", "stan_id", "um_id", "para_id", "sollwert", "toleranz",
    "gu", "go", "qc_gu", "qc_go", "linie", "test", "parameter",
)

# Was sich pflegen laesst - und nichts sonst. Die Namen gehen in den
# Text der Anweisung; sie duerfen deshalb nur aus dieser Liste kommen
# und nie von aussen.
#
# GWO, PR_GU, PR_GO, LINIE, TEST, USER_NAME und USER_KOMMENTAR bleiben
# unberuehrt: sie gehoeren zur Pflege des LIMS und haben mit der
# Streuung einer Regelkarte nichts zu tun.
STANDARDPARA_PFLEGBAR = ("sollwert", "toleranz", "gu", "go",
                         "qc_gu", "qc_go")


def standardpara(zugang, stan_id=None, um_id=None, para_id=None,
                 verbindung=None) -> list[dict]:
    """Die gepflegten Zeilen eines Standards - mit ihrer ID.

    Die ID ist der Grund fuer diese Abfrage: geschrieben wird ueber sie,
    und dafuer muss sie gelesen worden sein. Ein zusammengesetzter
    Schluessel aus Standard, Methode und Parameter koennte zwei Zeilen
    treffen, wenn die Pflege einmal doppelt angelegt hat.
    """
    bedingungen, bindungen = "", {}
    for feld, wert in (("stan_id", stan_id), ("um_id", um_id),
                       ("para_id", para_id)):
        if wert is not None:
            bedingungen += f"           AND sp.{feld} = :{feld}\n"
            bindungen[feld] = wert
    zeilen = _zeilen(zugang, f"""
        SELECT sp.id, sp.stan_id, sp.um_id, sp.para_id, sp.sollwert,
               sp.toleranz, sp.gu, sp.go, sp.qc_gu, sp.qc_go, sp.linie,
               sp.test, pa.name
          FROM standard_para sp
          LEFT JOIN parameter pa ON pa.id = sp.para_id
         WHERE 1 = 1
{bedingungen}         ORDER BY pa.name, sp.um_id, sp.id
    """, bindungen, verbindung=verbindung)
    eintraege = _dicts(zeilen, STANDARDPARA_FELDER)
    for eintrag in eintraege:
        for feld in ("sollwert", "toleranz", "gu", "go", "qc_gu", "qc_go"):
            eintrag[f"{feld}_zahl"] = als_zahl(eintrag[feld])
    return eintraege


def standardpara_sql(felder) -> str:
    """Die Anweisung, die eine Stammdatenzeile pflegt.

    Nur die Felder, die wirklich geaendert werden, und nur solche aus
    STANDARDPARA_PFLEGBAR. Getroffen wird ueber die ID - genau eine
    Zeile oder keine.
    """
    fremd = [feld for feld in felder if feld not in STANDARDPARA_PFLEGBAR]
    if fremd:
        raise ValueError("Diese Spalten pflegt LabControl nicht: "
                         + ", ".join(sorted(fremd)))
    gewaehlt = [feld for feld in felder if feld in STANDARDPARA_PFLEGBAR]
    if not gewaehlt:
        raise ValueError("Kein pflegbares Feld angegeben.")
    satz = ", ".join(f"{feld} = :{feld}" for feld in gewaehlt)
    return f"        UPDATE standard_para SET {satz}\n         WHERE id = :id"


def standardpara_nachlesen_sql(felder) -> str:
    """Was nach dem Schreiben in der Zeile steht - zum Nachsehen."""
    gewaehlt = [feld for feld in felder if feld in STANDARDPARA_PFLEGBAR]
    if not gewaehlt:
        raise ValueError("Kein pflegbares Feld angegeben.")
    return (f"        SELECT {', '.join(gewaehlt)}\n"
            f"          FROM standard_para WHERE id = :id")


def standardpara_satz_pruefen(satz: dict):
    """Laesst nur durch, was vollstaendig und eine Zahl ist.

    Ein leerer Wert wuerde die Vorgabe im LIMS loeschen, und eine
    geloeschte Grenze bewertet nichts mehr - sie faellt niemandem auf.
    """
    if satz.get("id") is None:
        raise ValueError("Ohne die ID der Zeile wird nichts gepflegt.")
    gewaehlt = [feld for feld in satz
                if feld in STANDARDPARA_PFLEGBAR]
    if not gewaehlt:
        raise ValueError("Kein pflegbares Feld im Satz.")
    fremd = [feld for feld in satz
             if feld not in STANDARDPARA_PFLEGBAR and feld != "id"]
    if fremd:
        raise ValueError("Diese Spalten pflegt LabControl nicht: "
                         + ", ".join(sorted(fremd)))
    for feld in gewaehlt:
        if als_zahl(satz[feld]) is None:
            raise ValueError(f"Kein Wert fuer {feld} - eine leere Vorgabe "
                             f"bewertet nichts mehr.")


def standardpara_pflegen(zugang: Zugang, saetze: list[dict],
                         hinweise=None) -> dict:
    """Schreibt die gepflegten Zahlen - alles oder nichts.

    Eine Transaktion ueber alle Saetze: eine halb gepflegte Tabelle
    waere schlimmer als eine ungepflegte, denn niemand saehe, wo sie
    aufgehoert hat.

    Zurueck kommt, was geschah: `geschrieben` die Zahl der getroffenen
    Zeilen, `ohne_zeile` die Saetze, zu denen es keine ID im LIMS gibt
    (angelegt wird nichts - ein Parameter, der dort nicht gepflegt ist,
    gehoert nicht von hier aus hinein), und `nicht_uebernommen` die,
    bei denen nach dem Festschreiben noch der alte Wert steht.
    """
    for satz in saetze:
        standardpara_satz_pruefen(satz)
    if not saetze:
        return {"geschrieben": 0, "ohne_zeile": [], "versucht": 0,
                "nicht_uebernommen": []}
    verbindung = zugang.verbinden()
    try:
        with verbindung.cursor() as cursor:
            geschrieben, ohne_zeile = 0, []
            for nummer, satz in enumerate(saetze):
                if hinweise is not None and nummer < len(hinweise):
                    cursor.hinweis = hinweise[nummer]
                felder = [feld for feld in satz
                          if feld in STANDARDPARA_PFLEGBAR]
                cursor.execute(standardpara_sql(felder), satz)
                if cursor.rowcount:
                    geschrieben += cursor.rowcount
                else:
                    ohne_zeile.append(satz)
        verbindung.commit()
        with verbindung.cursor() as cursor:
            geblieben = _standardpara_nachpruefen(
                cursor, [satz for satz in saetze if satz not in ohne_zeile])
    except Exception:
        verbindung.rollback()
        raise
    finally:
        verbindung.close()
    return {"geschrieben": geschrieben, "ohne_zeile": ohne_zeile,
            "versucht": len(saetze), "nicht_uebernommen": geblieben}


def _standardpara_nachpruefen(cursor, saetze) -> list:
    """Liest die gepflegten Zeilen zurueck - steht dort jetzt die Zahl?"""
    geblieben = []
    for satz in saetze:
        felder = [feld for feld in satz if feld in STANDARDPARA_PFLEGBAR]
        cursor.execute(standardpara_nachlesen_sql(felder),
                       {"id": satz["id"]})
        zeile = cursor.fetchone()
        if zeile is None:
            geblieben.append(satz)
            continue
        for stelle, feld in enumerate(felder):
            if not _angekommen(zeile[stelle], satz[feld]):
                geblieben.append(satz)
                break
    return geblieben


def trdf_exportieren(zugang: Zugang, saetze: list[dict], hinweise=None,
                     anhang=None, anhang_hinweise=None,
                     zurueck: bool = False, leeren: bool = False) -> dict:
    """Schreibt die korrigierten Werte - alles oder nichts.

    Zwei Stellen, eine Transaktion: die Ergebniszeile und der
    Teilprobenanhang, an dem derselbe Rohwert ein zweites Mal haengt.
    Nur eine der beiden zu schreiben waere schlimmer als keine - dann
    stuenden im LIMS zwei Staende nebeneinander, und der Rechenweg des
    LIMS nutzt den, den man nicht sieht.

    Zurueck kommt, was geschah: `geschrieben` sind die getroffenen
    Ergebniszeilen, `ohne_zeile` die Saetze, zu denen es im LIMS keine
    gibt (sie werden gemeldet und nicht angelegt), und dasselbe fuer den
    Anhang unter `anhang_geschrieben` und `anhang_ohne_zeile`.

    `leeren` erlaubt es, einen Wert zu loeschen - die TRDF-Pruefung
    stellt damit die Staende her, die eine Variante verlangt. Ohne die
    Erlaubnis bleibt ein leerer Wert, was er war: ein Fehler.

    `zurueck` sagt, in welche Richtung geschrieben wird. Zurueck gilt
    der gesicherte Stand, und der darf leere Spalten haben - beim
    Korrigieren waere eine leere Spalte eine geloeschte Zahl.

    Nach dem Festschreiben wird nachgelesen: unter
    `nicht_uebernommen` stehen die Saetze, bei denen jetzt immer noch
    der alte Wert in der Datenbank steht. Getroffen und trotzdem nicht
    geaendert - das kann ein Ausloeser gewesen sein, ein Recht, das
    fehlt, oder eine Sicht statt einer Tabelle. Von aussen sieht es
    aus wie ein gelungener Export, und genau deshalb wird gefragt.
    """
    anhang = list(anhang or [])
    zurueck = zurueck or (bool(anhang) and "alt" in anhang[0])
    for satz in saetze:
        if zurueck:
            trdf_zurueck_pruefen(satz)
        else:
            trdf_satz_pruefen(satz, leeren)
    for satz in anhang:
        if zurueck:
            trdf_anhang_zurueck_pruefen(satz)
        else:
            trdf_anhang_satz_pruefen(satz, leeren)
    if not saetze and not anhang:
        return {"geschrieben": 0, "ohne_zeile": [], "versucht": 0,
                "anhang_geschrieben": 0, "anhang_ohne_zeile": [],
                "nicht_uebernommen": [], "anhang_nicht_uebernommen": []}
    verbindung = zugang.verbinden()
    try:
        with verbindung.cursor() as cursor:
            geschrieben, ohne_zeile = _schreiben(
                cursor,
                trdf_zurueck_sql() if zurueck else trdf_export_sql(),
                saetze, hinweise)
            am_anhang, anhang_ohne = _schreiben(
                cursor,
                trdf_anhang_zurueck_sql() if zurueck else trdf_anhang_sql(),
                anhang, anhang_hinweise)
        verbindung.commit()
        # Erst nach dem Festschreiben: vorher saehe die eigene
        # Transaktion ohnehin ihre eigenen Aenderungen.
        with verbindung.cursor() as cursor:
            geblieben = _nachpruefen(
                cursor, trdf_nachlesen_sql(),
                [satz for satz in saetze if satz not in ohne_zeile],
                ("prob_id", "pm_id", "pm_ver", "um_id", "gegr_id"))
            anhang_geblieben = _nachpruefen(
                cursor, trdf_anhang_nachlesen_sql(),
                [satz for satz in anhang if satz not in anhang_ohne],
                ("prob_id", "um_id", "rohw_id"))
    except Exception:
        verbindung.rollback()
        raise
    finally:
        verbindung.close()
    return {"geschrieben": geschrieben, "ohne_zeile": ohne_zeile,
            "versucht": len(saetze), "anhang_geschrieben": am_anhang,
            "anhang_ohne_zeile": anhang_ohne,
            "nicht_uebernommen": geblieben,
            "anhang_nicht_uebernommen": anhang_geblieben}


# --------------------------------------------------------------------------
# Die Qualitaetspruefung (QP)
# --------------------------------------------------------------------------
#
# Am Ende einer Wassermessreihe steht die Frage, ob die Serie als Ganzes
# stimmt: passen die Ionen zueinander, sind die Wiederholungen nah
# beieinander, muss etwas nachgemessen werden. Bisher lief das ueber
# einen CSV-Auszug aus dem LIMS und ein eigenes Werkzeug. Hier kommen die
# Werte aus derselben Quelle, aus der der Auszug kam - der
# Ergebnistabelle -, und damit stehen auch die Schluessel dabei: PROB_ID,
# PM_ID, PM_VER, PARA_ID, GEME_ID, VKD_ID. Ohne sie liesse sich eine
# Nachmessung nicht zurueckschreiben und keine Bestimmungsgrenze pruefen.
#
# Eine Wasser-QP besteht immer aus zwei Untersuchungsmethoden: der
# Alkalinitaet und dem Rest. Beide gehoeren in dieselbe Pruefung - eine
# Ionenbilanz ohne die Alkalinitaet gibt es nicht.
QP_KUERZEL = ("ALK2.1", "ANULLIC")


def qp_methoden(zugang, kuerzel=None, verbindung=None) -> list[tuple]:
    """Die Untersuchungsmethoden der QP - gesucht nach ihrem Kuerzel.

    Gesucht wird nach dem Kuerzel und nicht nach einer festen ID: die
    ID ist eine Nummer der Datenbank, das Kuerzel steht im Labor an der
    Methode. Zurueck kommt, was gefunden wurde - fehlt eine der beiden,
    sagt das der Reiter und holt trotzdem, was da ist.
    """
    gesucht = [str(name).strip().upper()
               for name in (kuerzel or QP_KUERZEL) if str(name).strip()]
    if not gesucht:
        return []
    namen = [f":k{nummer}" for nummer in range(len(gesucht))]
    bindungen = {f"k{nummer}": wert
                 for nummer, wert in enumerate(gesucht)}
    zeilen = _zeilen(zugang, f"""
        SELECT id, kuerzel FROM untersuchungsmethode
         WHERE UPPER(kuerzel) IN ({', '.join(namen)})
         ORDER BY kuerzel, id
    """, bindungen, verbindung=verbindung)
    return [(zeile[0], str(zeile[1] or "").strip()) for zeile in zeilen]


def serien_mit_methoden(zugang, um_ids, verbindung=None) -> list[str]:
    """Die Serien aus dem Fahrplan, die eine dieser Methoden fuehren.

    S_FAHRPLAN sagt, was ansteht, kennt aber keine UM_ID; die steht in
    TEILPROBEN. Deshalb der Weg ueber EXISTS - er fragt je Serie nach
    einer Teilprobe und nicht die Ergebnistabelle nach allen Serien.
    """
    if not um_ids:
        return []
    namen = [f":um{nummer}" for nummer in range(len(um_ids))]
    bindungen = {f"um{nummer}": wert for nummer, wert in enumerate(um_ids)}
    zeilen = _zeilen(zugang, f"""
        SELECT DISTINCT f.serie
          FROM s_fahrplan f
         WHERE f.serie IS NOT NULL
           AND EXISTS (SELECT 1 FROM teilproben t
                        WHERE t.serie = f.serie
                          AND t.um_id IN ({', '.join(namen)}))
         ORDER BY f.serie DESC
    """, bindungen, verbindung=verbindung)
    return _seriennamen(zeilen)


# Was A_WASSER ueber eine Wasserprobe weiss. Zwei Angaben braucht die
# Na/Cl-Bilanz daraus, und beide stehen in keiner anderen Tabelle:
#
#   FLUSS_KART   um welche Art Wasser es sich handelt - LYS
#                (Lysimeter), ST (Stammabfluss), KR (Kronendurchlass),
#                FN (Freilandniederschlag), BW (Bodenwasser). Davon
#                haengt ab, ob ein abweichendes Verhaeltnis ein Befund
#                ist: an einem Lysimeter ist die Bilanz gar nicht
#                zulaessig, im Niederschlag schon.
#   VERS_NAME    der Versuchsstandort. Er beantwortet die Frage, die
#                sich bei einem abweichenden Verhaeltnis immer stellt:
#                liegt der Standort am Meer? Dort ist viel Natrium und
#                Chlorid normal, im Binnenland nicht.
A_WASSER_FELDER = ("prob_id", "fluss_kart", "vers_name")


def a_wasser(zugang, prob_ids, verbindung=None) -> dict:
    """Die Standortangaben zu diesen Proben - {PROB_ID: Satz}.

    Findet sich zu einer Probe keine Zeile, fehlt sie im Ergebnis; das
    ist kein Fehler, sondern eine Auskunft. Die Pruefung zeigt dann
    leere Spalten und behandelt die Probe als eine, deren Art niemand
    kennt - und die muss geprueft werden, nicht uebergangen.
    """
    nummern = [wert for wert in (prob_ids or []) if wert is not None]
    if not nummern:
        return {}
    gefunden = {}
    for anfang in range(0, len(nummern), 500):
        haeppchen = nummern[anfang:anfang + 500]
        namen = [f":p{nummer}" for nummer in range(len(haeppchen))]
        bindungen = {f"p{nummer}": wert
                     for nummer, wert in enumerate(haeppchen)}
        for zeile in _zeilen(zugang, f"""
            SELECT prob_id, fluss_kart, vers_name
              FROM a_wasser
             WHERE prob_id IN ({', '.join(namen)})
        """, bindungen, verbindung=verbindung):
            satz = dict(zip(A_WASSER_FELDER, zeile))
            gefunden.setdefault(satz["prob_id"], satz)
    return gefunden


# Welche Serien im LIMS zur Qualitaetspruefung stehen. QP_SERIEN hat
# drei Spalten: SERIE, BEMERKUNG und GEPRUEFT. Ein "X" in GEPRUEFT
# heisst, die Pruefung ist durch; alles andere - leer oder etwas
# anderes - heisst, sie steht noch an.
#
# Die Probenart steht nicht in QP_SERIEN, sondern in SERIEN.PART_ID
# (1 Boden, 2 Pflanze, 3 Wasser, 4 Humus); deshalb der Verbund. Ohne
# ihn stuenden hier auch die Bodenserien, und die Wasserpruefung kann
# sie nicht rechnen.
QP_OFFENE_FELDER = ("serie", "bemerkung", "geprueft", "termin", "status")

# Das Zeichen, das "geprueft" bedeutet.
QP_GEPRUEFT = "X"


def qp_offene_serien(zugang, part_id, verbindung=None) -> list[dict]:
    """Die Serien einer Probenart, deren QP noch offen ist.

    Gelesen wird QP_SERIEN - die Liste, die das LIMS selbst fuehrt.
    Eine Serie, die dort nicht steht, ist nicht in der Pruefung; eine,
    die mit einem X dasteht, ist durch.
    """
    zeilen = _zeilen(zugang, """
        SELECT q.serie, q.bemerkung, q.geprueft, s.termin, s.status
          FROM qp_serien q
          JOIN serien s ON s.serie = q.serie
         WHERE s.part_id = :part_id
           AND (q.geprueft IS NULL
                OR UPPER(TRIM(q.geprueft)) <> :geprueft)
         ORDER BY s.termin, q.serie
    """, {"part_id": part_id, "geprueft": QP_GEPRUEFT},
        verbindung=verbindung)
    return _dicts(zeilen, QP_OFFENE_FELDER)


# Was eine QP-Zeile mitbringt. Der Wert selbst ist MW; MW_ROH steht
# daneben, weil die Nachmessung spaeter wissen muss, was vor der
# Rechnung dastand. Die Schluessel sind nicht Beiwerk: ueber sie geht
# eine Nachmessung zurueck ins LIMS, und ueber PARA_ID mit GEME_ID
# haengt die Bestimmungsgrenze.
# Der Endfaktor und der Verduennungsfaktor stehen mit dabei, weil die
# Nachmessung sie braucht: aus dem eingetippten Wert wird MW ueber den
# Endfaktor, und die Bestimmungsgrenze der Probe wird ueber den
# Verduennungsfaktor auf die Ebene der gemessenen Loesung gebracht.
# Ohne beide liesse sich eine Nachmessung nicht zurueckschreiben.
QP_FELDER = ("prob_id", "probe_nr", "wdh_um", "wdh_me", "serie",
             "um_id", "um_kuerzel", "pm_id", "pm_ver", "para_id",
             "part_id", "gegr_id", "geme_id", "vkd_id", "einh_id",
             "lnr", "mw", "mw_roh", "mw_org", "kommentar",
             "parameter", "pruefmethode", "pm_lsta_id", "einheit",
             "v_faktor", "end_faktor", "stan_id", "standard", "standardtyp")


def qp_ergebnisse(zugang, serie: str, um_ids, verbindung=None) -> list[dict]:
    """Alle Ergebniszeilen einer Serie zu diesen Untersuchungsmethoden.

    Anders als beim Lauf (ergebniszeilen) steht hier *kein* Geraet in
    der Bedingung. Eine QP sieht die Serie als Ganzes an: derselbe
    Parameter kann an zwei Geraeten gemessen worden sein, und beide
    Werte gehoeren in dieselbe Zeile der Pruefung. Ein Filter auf
    GEGR_ID liesse die Haelfte davon verschwinden - und weil die Spalte
    leer sein darf, unter Umstaenden alles.

    Die Spaltennamen der Pruefung sind PRUEFMETHODEN.NAME - so hiessen
    sie auch im CSV-Auszug, mit dem bisher geprueft wurde.
    """
    if not um_ids:
        return []
    namen = [f":um{nummer}" for nummer in range(len(um_ids))]
    bindungen = {f"um{nummer}": wert for nummer, wert in enumerate(um_ids)}
    bindungen["serie"] = serie
    zeilen = _zeilen(zugang, f"""
        SELECT e.prob_id, p.probe_nr, p.wdh_um, p.wdh_me, e.serie,
               e.um_id, u.kuerzel, e.pm_id, e.pm_ver, e.para_id,
               e.part_id, e.gegr_id, e.geme_id, e.vkd_id, e.einh_id,
               e.lnr, e.mw, e.mw_roh, e.mw_org, e.kommentar,
               pa.name, pm.name, pm.lsta_id, ei.einheit,
               e.v_faktor, t.end_faktor,
               e.stan_id, sv.bezeichnung, sv.typ
          FROM ergebnisse e
          JOIN proben p                    ON p.id = e.prob_id
          LEFT JOIN teilproben t           ON t.prob_id = e.prob_id
                                          AND t.um_id = e.um_id
          LEFT JOIN standardverwaltung sv  ON sv.id = e.stan_id
          LEFT JOIN untersuchungsmethode u ON u.id = e.um_id
          LEFT JOIN parameter pa           ON pa.id = e.para_id
          LEFT JOIN pruefmethoden pm       ON pm.id = e.pm_id
                                          AND pm.version = e.pm_ver
          LEFT JOIN einheiten ei           ON ei.id = e.einh_id
         WHERE e.serie = :serie
           AND e.um_id IN ({', '.join(namen)})
         ORDER BY p.probe_nr, p.wdh_um, p.wdh_me, pm.name, e.pm_id, e.pm_ver
    """, bindungen, verbindung=verbindung)
    ergebnisse = _dicts(zeilen, QP_FELDER)
    for zeile in ergebnisse:
        zeile["probe"] = probenschluessel(zeile["probe_nr"], zeile["wdh_um"],
                                          zeile["wdh_me"])
        zeile["pm_freigegeben"] = zeile["pm_lsta_id"] == LSTA_FREIGEGEBEN
        # Ein Standard steht im LIMS als eigene Probe, mit der Nummer
        # davor: "1/NHarz". Zaehler und Name werden hier getrennt -
        # sortiert wird nach dem Namen, nicht nach der Nummer.
        zaehler, name = standard_zerlegen(zeile["probe_nr"])
        zeile["standardzaehler"] = zaehler
        zeile["standardname"] = (str(zeile["standard"] or "").strip()
                                 or name)
    return ergebnisse


# Was eine Standardvorgabe ausmacht: Sollwert und Grenzen je Standard
# und Parameter. Sie gelten fuer die *verrechnete* Probe - also gegen
# MW und nicht gegen MW_ROH.
QP_STANDARDGRENZEN_FELDER = ("stan_id", "para_id", "um_id", "sollwert",
                             "toleranz", "gu", "go", "parameter")


def qp_standardgrenzen(zugang, serie: str, um_ids,
                       verbindung=None) -> list[dict]:
    """Die gepflegten Grenzen der Standards dieser Serie.

    Eingegrenzt auf die Standards, die in der Serie ueberhaupt
    vorkommen, und auf die Untersuchungsmethoden der Pruefung - sonst
    kaeme die halbe Tabelle mit. Welche Zeile zu welchem Messwert
    gehoert, entscheidet (STAN_ID, PARA_ID, UM_ID).
    """
    if not um_ids:
        return []
    namen = [f":um{nummer}" for nummer in range(len(um_ids))]
    bindungen = {f"um{nummer}": wert for nummer, wert in enumerate(um_ids)}
    bindungen["serie"] = serie
    zeilen = _zeilen(zugang, f"""
        SELECT sp.stan_id, sp.para_id, sp.um_id, sp.sollwert, sp.toleranz,
               sp.gu, sp.go, pa.name
          FROM standard_para sp
          LEFT JOIN parameter pa ON pa.id = sp.para_id
         WHERE sp.um_id IN ({', '.join(namen)})
           AND sp.stan_id IN (
                 SELECT DISTINCT e.stan_id FROM ergebnisse e
                  WHERE e.serie = :serie
                    AND e.stan_id IS NOT NULL
                    AND e.um_id IN ({', '.join(namen)}))
         ORDER BY sp.stan_id, pa.name, sp.id
    """, bindungen, verbindung=verbindung)
    return _dicts(zeilen, QP_STANDARDGRENZEN_FELDER)


# Was eine Bestimmungsgrenze ausmacht: der Stand der Kenndaten (VKD_ID)
# und die beiden Arbeitswerte. Geprueft wird, ob fuer einen Parameter an
# einer Geraetemethode ueberall derselbe Stand gilt - stehen zwei
# verschiedene da, rechnet das LIMS zwei verschiedene Grenzen auf
# denselben Parameter, und das faellt sonst niemandem auf.
QP_GRENZEN_FELDER = ("para_id", "geme_id", "parameter", "vkd_id",
                     "nwg_arbeit", "bg_arbeit", "zeilen")


def qp_grenzen(zugang, serie: str, um_ids, verbindung=None) -> list[dict]:
    """Welche Grenzen in dieser Serie je Parameter und Geraetemethode gelten.

    Gelesen wird aus den Ergebniszeilen selbst und nicht aus
    VERFAHRENSKENNDATEN: die Frage ist nicht, welche Grenze gepflegt
    ist, sondern welche an den gebuchten Werten haengt. Zwei Zeilen mit
    verschiedener VKD_ID auf demselben Parameter sind der Befund.
    """
    if not um_ids:
        return []
    namen = [f":um{nummer}" for nummer in range(len(um_ids))]
    bindungen = {f"um{nummer}": wert for nummer, wert in enumerate(um_ids)}
    bindungen["serie"] = serie
    bindungen["lsta"] = LSTA_FREIGEGEBEN
    zeilen = _zeilen(zugang, f"""
        SELECT e.para_id, e.geme_id, MIN(pa.name), e.vkd_id,
               MIN(v.nwg_arbeit), MIN(v.bg_arbeit), COUNT(*)
          FROM ergebnisse e
          LEFT JOIN parameter pa ON pa.id = e.para_id
          LEFT JOIN verfahrenskenndaten v ON v.id = e.vkd_id
                                         AND v.pm_id = e.pm_id
                                         AND v.pm_ver = e.pm_ver
                                         AND v.um_id = e.um_id
                                         AND v.lsta_id = :lsta
         WHERE e.serie = :serie
           AND e.um_id IN ({', '.join(namen)})
         GROUP BY e.para_id, e.geme_id, e.vkd_id
         ORDER BY MIN(pa.name), e.para_id, e.geme_id, e.vkd_id
    """, bindungen, verbindung=verbindung)
    return _dicts(zeilen, QP_GRENZEN_FELDER)


# --------------------------------------------------------------------------
# Das Urteil der Qualitaetspruefung: BEW_TEIL
# --------------------------------------------------------------------------
#
# Sechs Spalten, und jede davon sagt etwas anderes:
#
#   PROB_ID      welche Probe
#   UM_ID        unter welcher Untersuchungsmethode
#   QP_ART_KZL   das Kuerzel der Pruefungsart
#   QP_ART       ihr Name im Klartext
#   BEWERTUNG    das Urteil - "OK"
#   KOMMENTAR    warum, wenn es nicht von selbst aufgeht
#
# So steht eine Stickstoffbilanz darin: PROB_ID 23843, QP_ART_KZL "N",
# BEWERTUNG "OK", KOMMENTAR leer, UM_ID 39, QP_ART "Stickstoffbilanz".
# Die Ionenbilanz geht genauso hinein - eine Zeile je Probe und
# Untersuchungsmethode, denn die Bilanz gilt fuer die Probe und die
# Zeile haengt an der Methode.
#
# Geschrieben wird nach derselben Regel wie ueberall: eine fehlende
# Zeile wird angelegt, eine vorhandene nur dort gefuellt, wo noch
# nichts stand. Ein Urteil, das jemand anders gesetzt hat,
# ueberschreibt dieser Weg nicht.
BEW_TEIL_SCHLUESSEL = ("prob_id", "um_id", "qp_art_kzl")
BEW_TEIL_FELDER = ("prob_id", "um_id", "qp_art_kzl", "qp_art",
                   "bewertung", "kommentar")


def bew_teil_lesen(zugang, prob_ids, qp_art_kzl, verbindung=None) -> list[dict]:
    """Was zu diesen Proben schon in BEW_TEIL steht.

    Gebraucht wird das vor dem Schreiben: die Vorschau soll sagen,
    welche Zeile angelegt und welche nur ergaenzt wird - und wo schon
    ein Urteil steht, das dieser Weg nicht anfasst.
    """
    nummern = [wert for wert in (prob_ids or []) if wert is not None]
    if not nummern:
        return []
    zeilen = []
    # In Oracle 11 sind tausend Werte in einer IN-Liste die Grenze;
    # eine Serie mit dreihundert Proben und zwei Methoden liegt darunter,
    # aber die Haeppchen kosten nichts und die Grenze fiele sonst
    # irgendwann unbemerkt auf.
    for anfang in range(0, len(nummern), 500):
        haeppchen = nummern[anfang:anfang + 500]
        namen = [f":p{nummer}" for nummer in range(len(haeppchen))]
        bindungen = {f"p{nummer}": wert
                     for nummer, wert in enumerate(haeppchen)}
        bindungen["kzl"] = qp_art_kzl
        zeilen += _zeilen(zugang, f"""
            SELECT prob_id, um_id, qp_art_kzl, qp_art, bewertung, kommentar
              FROM bew_teil
             WHERE prob_id IN ({', '.join(namen)})
               AND qp_art_kzl = :kzl
             ORDER BY prob_id, um_id
        """, bindungen, verbindung=verbindung)
    return _dicts(zeilen, BEW_TEIL_FELDER)


# Welche Kommentare zu einer Serie schon in BEW_TEIL stehen. Gebraucht
# wird das beim *Oeffnen* einer Pruefung und nicht erst beim Schreiben:
# hat jemand eine Probe schon einmal kommentiert - in einer frueheren
# Sitzung, an einem anderen Platz, oder im LIMS selbst -, dann soll
# dieser Kommentar hier dastehen und nicht ein zweites Mal getippt
# werden muessen.
#
# Gelesen wird je Probe und Pruefungsart *ein* Kommentar. In BEW_TEIL
# steht er je Untersuchungsmethode, und bei einer Probe mit zwei
# Methoden steht er zweimal - derselbe Text. Steht doch einmal etwas
# anderes da, gilt der erste: eine Pruefung hat einen Kommentar je
# Probe, und zwei zusammenzukleben waere eine Aussage, die niemand
# geschrieben hat.
def bew_teil_kommentare(zugang, prob_ids, kuerzel, verbindung=None) -> dict:
    """{(PROB_ID, QP_ART_KZL): Kommentar} - nur wo einer steht."""
    nummern = [wert for wert in (prob_ids or []) if wert is not None]
    arten = [str(wert) for wert in (kuerzel or []) if str(wert or "").strip()]
    if not nummern or not arten:
        return {}
    gefunden = {}
    for anfang in range(0, len(nummern), 500):
        haeppchen = nummern[anfang:anfang + 500]
        namen = [f":p{nummer}" for nummer in range(len(haeppchen))]
        artnamen = [f":k{nummer}" for nummer in range(len(arten))]
        bindungen = {f"p{nummer}": wert
                     for nummer, wert in enumerate(haeppchen)}
        bindungen.update({f"k{nummer}": wert
                          for nummer, wert in enumerate(arten)})
        for zeile in _zeilen(zugang, f"""
            SELECT prob_id, qp_art_kzl, kommentar, um_id
              FROM bew_teil
             WHERE prob_id IN ({', '.join(namen)})
               AND qp_art_kzl IN ({', '.join(artnamen)})
               AND kommentar IS NOT NULL
             ORDER BY prob_id, qp_art_kzl, um_id
        """, bindungen, verbindung=verbindung):
            prob_id, kzl, kommentar = zeile[0], zeile[1], zeile[2]
            text = str(kommentar or "").strip()
            if not text:
                continue
            gefunden.setdefault((prob_id, str(kzl or "").strip()), text)
    return gefunden


def bew_teil_sql() -> str:
    """Die Anweisung, die eine vorhandene Zeile ergaenzt.

    NVL wie im Export der Ergebnisse: ein vorhandenes Urteil bleibt
    stehen, und nur was leer war, wird gefuellt. Der Schluessel ist
    vollstaendig - Probe, Untersuchungsmethode und Pruefungsart;
    weniger davon koennte mehr als eine Zeile treffen.
    """
    return """
        UPDATE bew_teil SET
               qp_art    = NVL(qp_art,    :qp_art),
               bewertung = NVL(bewertung, :bewertung),
               kommentar = NVL(kommentar, :kommentar)
         WHERE prob_id = :prob_id AND um_id = :um_id
           AND qp_art_kzl = :qp_art_kzl
    """


def bew_teil_anlegen_sql() -> str:
    """Die Anweisung, die eine fehlende Zeile anlegt.

    Angelegt wird nur, was es noch nicht gibt: das WHERE NOT EXISTS
    macht aus zwei Anweisungen eine und schliesst den Fall aus, dass
    zwischen Nachsehen und Schreiben jemand anders dieselbe Zeile
    anlegt.
    """
    return """
        INSERT INTO bew_teil
               (prob_id, um_id, qp_art_kzl, qp_art, bewertung, kommentar)
        SELECT :prob_id, :um_id, :qp_art_kzl, :qp_art, :bewertung,
               :kommentar
          FROM dual
         WHERE NOT EXISTS (SELECT 1 FROM bew_teil
                            WHERE prob_id = :prob_id AND um_id = :um_id
                              AND qp_art_kzl = :qp_art_kzl)
    """


def bew_teil_nachlesen_sql() -> str:
    """Liest die geschriebene Zeile zurueck."""
    return """
        SELECT bewertung, kommentar FROM bew_teil
         WHERE prob_id = :prob_id AND um_id = :um_id
           AND qp_art_kzl = :qp_art_kzl
    """


def bew_teil_satz_pruefen(satz: dict) -> None:
    """Sieht nach, ob ein Satz geschrieben werden darf.

    Geprueft wird hier und nicht erst in der Datenbank: eine
    Fehlermeldung von Oracle nennt eine Spalte, dieser Satz nennt, was
    fehlt.
    """
    for feld in BEW_TEIL_SCHLUESSEL:
        if satz.get(feld) in (None, ""):
            raise ValueError(f"Ohne {feld.upper()} laesst sich keine Zeile "
                             f"in BEW_TEIL ansprechen")
    if not str(satz.get("bewertung") or "").strip():
        raise ValueError("Eine Zeile ohne Bewertung sagt nichts aus")
    fremd = [feld for feld in satz if feld not in BEW_TEIL_FELDER]
    if fremd:
        raise ValueError("BEW_TEIL fuehrt diese Spalten nicht: "
                         + ", ".join(sorted(fremd)))


def bew_teil_schreiben(zugang: Zugang, saetze: list[dict],
                       hinweise=None) -> dict:
    """Schreibt die Urteile - alles oder nichts.

    Je Satz zwei Versuche in dieser Reihenfolge: erst ergaenzen, und
    nur wenn es keine Zeile gab, anlegen. Umgekehrt waere es ein
    Wettlauf mit jedem anderen, der gerade dieselbe Probe bewertet.

    Zurueck kommt, was geschah: `ergaenzt` die Zeilen, die es schon
    gab, `angelegt` die neuen, `unveraendert` die, in denen schon ein
    Urteil stand, und `nicht_uebernommen` die, bei denen nach dem
    Festschreiben nichts davon zu lesen ist.
    """
    for satz in saetze:
        bew_teil_satz_pruefen(satz)
    if not saetze:
        return {"ergaenzt": 0, "angelegt": 0, "unveraendert": [],
                "versucht": 0, "nicht_uebernommen": []}
    verbindung = zugang.verbinden()
    try:
        ergaenzt, angelegt, unveraendert = 0, 0, []
        with verbindung.cursor() as cursor:
            for nummer, satz in enumerate(saetze):
                if hinweise is not None and nummer < len(hinweise):
                    cursor.hinweis = hinweise[nummer]
                vollstaendig = {feld: satz.get(feld)
                                for feld in BEW_TEIL_FELDER}
                cursor.execute(bew_teil_sql(), vollstaendig)
                if cursor.rowcount:
                    ergaenzt += cursor.rowcount
                    continue
                cursor.execute(bew_teil_anlegen_sql(), vollstaendig)
                if cursor.rowcount:
                    angelegt += cursor.rowcount
                else:
                    # Weder ergaenzt noch angelegt: die Zeile gibt es,
                    # und es stand schon etwas darin.
                    unveraendert.append(satz)
        verbindung.commit()
        with verbindung.cursor() as cursor:
            geblieben = _bew_teil_nachpruefen(
                cursor, [satz for satz in saetze
                         if satz not in unveraendert])
    except Exception:
        verbindung.rollback()
        raise
    finally:
        verbindung.close()
    return {"ergaenzt": ergaenzt, "angelegt": angelegt,
            "unveraendert": unveraendert, "versucht": len(saetze),
            "nicht_uebernommen": geblieben}


def _bew_teil_nachpruefen(cursor, saetze) -> list:
    """Liest die geschriebenen Zeilen zurueck - steht das Urteil dort?"""
    geblieben = []
    for satz in saetze:
        cursor.execute(bew_teil_nachlesen_sql(),
                       {feld: satz.get(feld)
                        for feld in BEW_TEIL_SCHLUESSEL})
        zeile = cursor.fetchone()
        if zeile is None:
            geblieben.append(satz)
            continue
        if not _angekommen(zeile[0], satz.get("bewertung")):
            geblieben.append(satz)
    return geblieben


# --------------------------------------------------------------------------
# Die Nachmessung der Qualitaetspruefung
# --------------------------------------------------------------------------
#
# Der eine Weg dieses Moduls, der einen gebuchten Messwert ersetzt - und
# er ist gewollt: eine Nachmessung ist eine zweite Messung derselben
# Probe, und ihr Ergebnis gehoert an die Stelle der ersten. Deshalb kein
# NVL; dafuer ist die Anweisung so eng wie moeglich.
#
# Angefasst werden genau drei Spalten:
#
#   MW_ROH   die Konzentration der gemessenen Loesung
#   MW_N     dieselbe Zahl als Zahl - ein Kleiner-Zeichen kann sie nicht
#            tragen, deshalb steht dort unter der Grenze die Grenze
#   MW       der Gehalt: MW_ROH mal Endfaktor
#
# Drei Spalten, die der TRDF-Rueckweg mitschreibt, bleiben hier
# ausdruecklich unberuehrt: FC8, PSTA_ID und KORREKTUR_FLAG. Sie sagen
# etwas ueber den *Bearbeitungsstand* der Zeile im LIMS, und darueber
# entscheidet das LIMS und nicht dieses Programm - eine Nachmessung
# traegt einen Wert nach, sie setzt keine Zeile auf "bearbeitet".
#
# MW_ORG bleibt ebenfalls unberuehrt, und das aus einem anderen Grund:
# dort steht der Wert der *ersten* Messung, und er ist der, gegen den
# eine Nachmessung verglichen wird. Wer ihn mitueberschreibt, wirft
# genau die Auskunft weg, wegen der nachgemessen wurde.
QP_NACHMESSUNG_SPALTEN = ("MW_ROH", "MW_N", "MW")

QP_NACHMESSUNG_FELDER = ("mw_roh", "mw_n", "mw", "prob_id", "pm_id",
                         "pm_ver", "um_id", "gegr_id")


def qp_nachmessung_sql() -> str:
    """Die Anweisung, die eine nachgemessene Ergebniszeile ersetzt.

    Dieselbe Anweisung fuehrt auch zurueck: sie setzt jede der drei
    Spalten einzeln, und die Sicherung traegt alle drei. Ein eigener
    Rueckweg waere eine siebte Stelle, an der in ERGEBNISSE
    geschrieben wird, und er koennte nicht mehr, als diese kann.

    Die GEGR_ID darf leer sein - in ERGEBNISSE ist sie laut Schema
    nullable. Ein schlichtes `gegr_id = :gegr_id` traefe eine solche
    Zeile nie: in SQL ist NULL mit nichts gleich, auch nicht mit NULL.
    Deshalb der zweite Zweig; die Gleichheit steht zuerst, damit der
    Index weiter benutzt wird.
    """
    return """
        UPDATE ergebnisse SET
               mw_roh = :mw_roh,
               mw_n   = :mw_n,
               mw     = :mw
         WHERE prob_id = :prob_id AND pm_id = :pm_id AND pm_ver = :pm_ver
           AND um_id = :um_id
           AND (gegr_id = :gegr_id OR (gegr_id IS NULL AND :gegr_id IS NULL))
    """


def qp_nachmessung_nachlesen_sql() -> str:
    """Liest die geschriebene Zeile zurueck - steht die Zahl dort?"""
    return """
        SELECT mw_roh, mw_n, mw FROM ergebnisse
         WHERE prob_id = :prob_id AND pm_id = :pm_id AND pm_ver = :pm_ver
           AND um_id = :um_id
           AND (gegr_id = :gegr_id OR (gegr_id IS NULL AND :gegr_id IS NULL))
    """


def qp_nachmessung_satz_pruefen(satz: dict) -> None:
    """Sieht nach, ob ein Satz geschrieben werden darf.

    Der Schluessel muss vollstaendig sein - ohne ihn traefe die
    Anweisung mehr als eine Zeile. Und MW_ROH muss etwas enthalten:
    eine Nachmessung ohne Wert ist keine.
    """
    for feld in ("prob_id", "pm_id", "pm_ver", "um_id"):
        if satz.get(feld) in (None, ""):
            raise ValueError(f"Ohne {feld.upper()} traefe die Anweisung "
                             f"mehr als eine Ergebniszeile")
    if not str(satz.get("mw_roh") or "").strip():
        raise ValueError("Eine Nachmessung ohne Wert ist keine")
    fremd = [feld for feld in satz if feld not in QP_NACHMESSUNG_FELDER]
    if fremd:
        raise ValueError("Diese Spalten gehoeren nicht in den Rueckweg "
                         "der Nachmessung: " + ", ".join(sorted(fremd)))


def qp_nachmessung_schreiben(zugang: Zugang, saetze: list[dict],
                             hinweise=None) -> dict:
    """Schreibt die Nachmessungen - alles oder nichts.

    Eine Transaktion ueber alle Saetze: eine halb geschriebene Serie
    waere schlimmer als eine ungeschriebene, denn niemand saehe, wo
    sie aufgehoert hat.

    Zurueck kommt, was geschah: `geschrieben` die Zahl der getroffenen
    Zeilen, `ohne_zeile` die Saetze, zu denen es keine Ergebniszeile
    gibt (angelegt wird keine - ein Wert ohne Zeile im LIMS gehoert
    nicht von hier aus hinein), und `nicht_uebernommen` die, bei denen
    nach dem Festschreiben noch etwas anderes steht.
    """
    for satz in saetze:
        qp_nachmessung_satz_pruefen(satz)
    if not saetze:
        return {"geschrieben": 0, "ohne_zeile": [], "versucht": 0,
                "nicht_uebernommen": []}
    verbindung = zugang.verbinden()
    try:
        geschrieben, ohne_zeile = 0, []
        with verbindung.cursor() as cursor:
            for nummer, satz in enumerate(saetze):
                if hinweise is not None and nummer < len(hinweise):
                    cursor.hinweis = hinweise[nummer]
                vollstaendig = {feld: satz.get(feld)
                                for feld in QP_NACHMESSUNG_FELDER}
                cursor.execute(qp_nachmessung_sql(), vollstaendig)
                if cursor.rowcount:
                    geschrieben += cursor.rowcount
                else:
                    ohne_zeile.append(satz)
        verbindung.commit()
        with verbindung.cursor() as cursor:
            geblieben = _qp_nachmessung_nachpruefen(
                cursor, [satz for satz in saetze if satz not in ohne_zeile])
    except Exception:
        verbindung.rollback()
        raise
    finally:
        verbindung.close()
    return {"geschrieben": geschrieben, "ohne_zeile": ohne_zeile,
            "versucht": len(saetze), "nicht_uebernommen": geblieben}


def _qp_nachmessung_nachpruefen(cursor, saetze) -> list:
    """Liest die geschriebenen Zeilen zurueck - steht der Wert dort?"""
    geblieben = []
    schluessel = ("prob_id", "pm_id", "pm_ver", "um_id", "gegr_id")
    for satz in saetze:
        cursor.execute(qp_nachmessung_nachlesen_sql(),
                       {feld: satz.get(feld) for feld in schluessel})
        zeile = cursor.fetchone()
        if zeile is None:
            geblieben.append(satz)
            continue
        if not _angekommen(zeile[0], satz.get("mw_roh")):
            geblieben.append(satz)
    return geblieben
