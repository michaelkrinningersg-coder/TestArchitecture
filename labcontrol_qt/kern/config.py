"""
LabControl - Konfiguration, Pfade & Einstellungen
=================================================

Folgt dem Muster aus LabDoku Desktop / MessKomplize: runtime_dir erkennt
automatisch, ob die Anwendung als .exe (PyInstaller) oder als Python-Skript
laeuft, und legt alles *neben* die Anwendung - portabel und ohne
Installation.

Wo die Einstellungen liegen
---------------------------
Im Unterordner "einstellungen" neben dem Programm. Damit zieht die
Einstellungsdatei mit, wenn der Ordner kopiert oder verschoben wird, und
zwei Staende auf zwei Laufwerken kommen sich nicht ins Gehege.

Laesst sich dort nicht schreiben - die exe liegt auf einem
schreibgeschuetzten Netzlaufwerk -, weicht die Datei in den
Benutzerordner aus, statt still verlorenzugehen. Welcher Ort tatsaechlich
gilt, steht im Reiter "Optionen"; nur zu raten, wo die eigenen
Einstellungen liegen, ist schlimmer als ein zweiter moeglicher Ort.

Anders als LabDoku braucht dieses Werkzeug keine eigene Datenablage - die
Daten liegen in der Oracle-Datenbank. Gespeichert werden die zuletzt
benutzten Eingaben und die Einstellungen, die je Geraet gelten.

Das Passwort wird bewusst NICHT gespeichert. Es lebt nur im Arbeitsspeicher,
solange die Anwendung laeuft.
"""

import json
import os
import sys

import sitzung
import verschleppung

EINSTELLUNGEN_ORDNER = "einstellungen"
# Bis Anfang 2026 lagen die Einstellungen in "backup" - der Name stammte von
# LabDoku und sagte nicht, was drin steht. Beim ersten Start nach der
# Umbenennung wird der alte Stand noch gelesen und beim naechsten Speichern
# an den neuen Ort geschrieben, damit niemand seine Eintraege neu tippt.
ALTER_ORDNER = "backup"


def get_runtime_dir():
    """Ordner der Anwendung: bei .exe der Ordner der exe, sonst der
    Ordner dieses Skripts."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def ausweichordner():
    """Ort fuer den Fall, dass neben dem Programm nicht geschrieben werden darf."""
    basis = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
             or os.path.expanduser("~"))
    return os.path.join(basis, "LabControl")


# Unter diesem Namen steht, was fuer alle gilt, solange niemand eine
# eigene Einstellung gesetzt hat. Ein Geraeteordner oder ein Reiter kann
# nie so heissen - unter Windows ist der Stern in Dateinamen verboten.
GETEILT = "*"


def benutzerschluessel(name) -> str:
    """Unter welchem Namen die Einstellungen eines Bearbeiters stehen.

    Gross geschrieben, damit "mkrinninger" und "MKrinninger" nicht zwei
    Staende bekommen. Ohne Namen gilt der gemeinsame Stand.
    """
    return str(name or "").strip().upper() or GETEILT


def _zahl(wert) -> bool:
    """Eine Zahl - und nicht etwa ein Haken, den Python als 0/1 lesen wuerde."""
    return isinstance(wert, (int, float)) and not isinstance(wert, bool)


def _je_benutzer(zuordnung) -> bool:
    """Steht in dieser Zuordnung schon etwas je Bearbeiter?

    Der Unterschied ist eindeutig: gemeinsam gespeichert stehen dort
    Zahlen oder Listen (Reiter -> 20, Geraet -> [Reiter]), je Bearbeiter
    steht dort eine weitere Zuordnung.
    """
    return any(isinstance(wert, dict)
               for wert in (zuordnung or {}).values())


# Einstellungen, die nur Namen tragen duerfen: ein Spaltenname, ein
# Erkennungsverfahren, ein "PM_ID/PM_VER". Eine Zahl waere dort eine
# verdorbene Datei und bleibt draussen - bei der Reihenfolge der Reiter
# ist die Zahl dagegen gerade der Inhalt.
NUR_NAMEN = ("verduennung", "parametererkennung", "spaltenzuordnung")


def _brauchbar(wert, tiefe: int = 2, nur_namen: bool = False) -> bool:
    """Taugt dieser Wert als Eintrag einer freien Zuordnung?

    Text, Haken, Zahlen und Listen von Namen - und darueber bis zu zwei
    Ebenen Zuordnung. Zwei, weil die Reiter je Bearbeiter *und* je Geraet
    gespeichert werden (Bearbeiter -> Geraet -> [Reiter]).
    """
    if isinstance(wert, dict):
        return tiefe > 0 and all(isinstance(k, str)
                                 and _brauchbar(v, tiefe - 1, nur_namen)
                                 for k, v in wert.items())
    if isinstance(wert, list):
        # Eine Liste von Namen - so stehen die abgewaehlten Reiter da.
        return all(isinstance(eintrag, str) for eintrag in wert)
    if nur_namen:
        return isinstance(wert, str)
    return isinstance(wert, (str, bool, int, float))


class Config:
    """Haelt die Pfade und die gespeicherten Einstellungen."""

    SETTINGS_FILENAME = "labcontrol_settings.json"

    DEFAULTS = {
        "benutzer": "",
        "alias": "LIMSTEST",
        "bearbeiter": "",
        "tabelle": "ERGEBNISSE",
        "serie": "2024B017",
        # Spalte der Laufdatei, in der der Verduennungsfaktor steht - je
        # Geraeteordner einer. Leer heisst: kommt nicht aus der Laufdatei.
        "verduennung": {},
        # Je Geraeteordner: steht in der Laufdatei die schon mit dem
        # Verduennungsfaktor verrechnete Konzentration? Das ist der
        # Normalfall, deshalb steht hier nur, wo es *nicht* so ist - ein
        # neuer Geraeteordner bekommt damit von allein die uebliche
        # Einstellung, und die Datei bleibt kurz.
        "verduennung_verrechnet": {},
        # Wie hoch eine Tabellenzeile in den Auswertungen steht, als
        # Vielfaches der Schriftgroesse. Haengt an den Augen davor,
        # deshalb einstellbar - und je Bearbeiter gespeichert.
        "zeilenhoehe": 1.7,
        # Stehen die Reiter gruppenweise ueber dem Arbeitsbereich ("oben")
        # oder als Menuband links ("links")?
        "reiterleiste": "oben",
        # Je Reiter eine Ordnungszahl; danach werden sie *innerhalb ihrer
        # Kategorie* sortiert - die Gruppe selbst steht fest. Leer heisst:
        # Anlagereihenfolge.
        "reiterfolge": {},
        # Je Geraeteordner, wie eine Spalte der Laufdatei ihre
        # Pruefmethode findet: ueber PM_WELLEN/PM_CODE (Vorgabe), ueber
        # den Spaltenkopf im Methodennamen oder ueber den Parameternamen.
        # Gespeichert wird nur, wo *nicht* der Normalfall gilt.
        "parametererkennung": {},
        # Wie weit die Serienliste im Startbildschirm zurueckreicht, in
        # Jahren. Gezaehlt wird ueber die Jahreszahl vorn in der
        # Seriennummer: 3 heisst im Jahr 2026 "alles ab 2023". Die Serien,
        # die noch in SERIEN stehen, kommen unabhaengig davon mit.
        "serien_jahre": 3,
        # Auch die Serien zeigen, deren Nummer nicht mit einer
        # vierstelligen Jahreszahl beginnt? Fuer sie greift die
        # Jahresgrenze nicht, und die Abfrage wird langsamer - deshalb von
        # Haus aus aus.
        "serien_ohne_jahr": False,
        # Je Geraeteordner die Reiter, die im Messfenster *nicht*
        # gezeigt werden. Gespeichert wird nur das Abgewaehlte: ein neuer
        # Reiter ist damit von allein sichtbar, und ein neues Geraet
        # zeigt alles.
        "reiter_sichtbar": {},
        # Je Geraeteordner das Zielfenster fuer den Verduennungsvorschlag,
        # in Prozent der Obergrenze: {"unten": 15, "oben": 70}. Gespeichert
        # wird nur, wo es von der Vorgabe abweicht.
        "verduennung_ziel": {},
        # Je Geraeteordner: duerfen auch Verduennungen zwischen den
        # ueblichen Stufen vorgeschlagen werden (7, 8, 12 ...)? Ganze
        # Zahlen bleiben es in jedem Fall. Gespeichert wird nur, wo es
        # *an* ist.
        "verduennung_zwischenstufen": {},
        # Je Geraeteordner: gibt es zwei Kontrollstandards, von denen
        # jeder einen Konzentrationsbereich abdeckt (IC)? Dann wird je
        # Standard geprueft und nur die Proben seines Bereichs bekommen
        # das Kuerzel. Gespeichert wird nur, wo es *an* ist.
        # Wie oft der Sitzungsstand von selbst gesichert wird, in
        # Sekunden. Null schaltet den Takt ab; beim Schliessen des
        # Fensters wird trotzdem gesichert. Gilt fuer alle Geraete.
        # Je Bearbeiter und Geraeteordner: vor dem Export Zeile fuer
        # Zeile zeigen, was geschrieben wird und was nicht. Der eine will
        # das bei jedem Export, der andere kennt seinen Lauf - deshalb je
        # Bearbeiter. Gespeichert wird nur, wo es *an* ist.
        "exportvorschau": {},
        # Je Geraeteordner: den Verdacht auf Verschleppung pruefen, und ab
        # welchem Verhaeltnis. Gespeichert wird nur, wo es *an* ist - bei
        # den meisten Geraeten gibt es das Problem nicht.
        "verschleppung": {},
        "verschleppung_schwelle": {},
        "sitzung_takt": sitzung.VORGABE_TAKT,
        # Wie lange ein Sitzungsstand aufgehoben wird, in Monaten. Beim
        # Start des Programms wird im Hintergrund weggeraeumt, was aelter
        # ist; null hebt alles auf.
        "sitzung_monate": sitzung.VORGABE_MONATE,
        "kontrollstandard_bereiche": {},
        # Je Geraeteordner: fuehrt dieses Geraet Regelkarten fuer seine
        # Kontrollstandards? Geschrieben wird beim Export - die
        # Kontrollstandards gehen selbst nicht ins LIMS und waeren danach
        # sonst verloren. Gespeichert wird nur, wo es *an* ist.
        "regelkarte": {},
        # Je Geraeteordner: darf an diesem Geraet der Blindwert von den
        # Messwerten abgezogen werden? Der Haken erlaubt es nur; ob
        # gerechnet wird, entscheidet der Schalter im Reiter
        # Blindwertstandards. Gespeichert wird nur, wo es *an* ist.
        "blindwertabzug": {},
        # Je Geraeteordner: eine Feststoffverbrennung? Dann kommt der
        # Faktor nicht aus einer Verduennungsspalte, sondern aus der
        # Einwaage - Solleinwaage durch tatsaechliche Einwaage. Er hebt
        # die Grenzen und laesst den Messwert stehen. Gespeichert wird
        # nur, wo es an ist.
        "feststoff": {},
        # Je Geraeteordner die Spalte der Laufdatei mit der Einwaage und
        # deren Einheit ("mg" oder "g").
        "feststoff_spalte": {},
        "feststoff_einheit": {},
        # Je Geraeteordner die Solleinwaagen in Gramm, je Probenart:
        # {"1": "1,0", "2": "0,070", "4": "0,070"}.
        "feststoff_soll": {},
        # Je Geraeteordner die Obergrenzen aus der Kalibrierung, je
        # Probenart und Parameter: Schluessel "<PART_ID>|<Parameter>".
        # Der Wert bezieht sich auf einen Bruchteil der Solleinwaage -
        # welchen, sagt feststoff_bezug. Zwei Zuordnungen statt einer
        # verschachtelten, damit die Einstellungsdatei flach bleibt.
        "feststoff_ogrenze": {},
        "feststoff_bezug": {},
        # Je Geraeteordner, wieviel Prozent ueber der zurueckgerechneten
        # Obergrenze noch durchgehen.
        "feststoff_toleranz": {},
        # Je Geraeteordner die von Hand gesetzte Zuordnung
        # Spaltenkopf -> "PM_ID/PM_VER". Sie schlaegt jede automatische
        # Erkennung und gilt beim naechsten Lauf wieder.
        "spaltenzuordnung": {},
        # Was der Pruefer selbst zu einer Groesse des TRDF-Moduls notiert
        # hat: Formelkuerzel -> Beschreibung. Sie steht in der Legende
        # neben Name und Einheit und geht nirgendwo sonst hin - in die
        # Oracle-Datenbank schon gar nicht. Wer den Text loescht und
        # speichert, ist ihn wieder los.
        "trdf_beschreibung": {},
        # Die Reihenfolge der Spalten je Tabelle des TRDF-Moduls, als
        # Liste von Formelkuerzeln - so, wie der Pruefer sie unter
        # „Info“ zurechtgezogen hat. Was hier nicht steht, behaelt
        # seinen Platz am Ende.
        "trdf_reihenfolge": {},
        # Welche Spalten dabei links stehen bleiben, waehrend der Rest
        # waagerecht laeuft. Ohne Eintrag gilt, womit eine Tabelle
        # anfaengt: Zeile, Probe - und bei den Rohwerten die Variante.
        "trdf_fest": {},
        # Welche Spalten je Tabelle ausgeblendet sind. Ohne Eintrag
        # keine - wer eine Tabelle oeffnet, will erst einmal sehen, was
        # es gibt. Was fest steht, bleibt sichtbar.
        "trdf_versteckt": {},
        # Was in der Kopfzeile steht: das Formelkuerzel („Spalte“, so
        # wie das LIMS rechnet), der Name, das Para-Kuerzel, die
        # Einheit oder die eigene Beschreibung. Dies ist die Wahl fuer
        # alle Spalten.
        "trdf_kopfspalte": "Spalte",
        # Und was davon abweichend fuer einzelne Groessen gilt:
        # Formelkuerzel -> Wahl. Wie die Beschreibung haengt sie an der
        # Groesse und gilt damit in allen drei Tabellen.
        "trdf_kopfspalten": {},
    }

    def __init__(self, runtime_dir=None):
        # runtime_dir dient den Tests; im Betrieb gilt der Ordner der
        # Anwendung.
        self.runtime_dir = runtime_dir or get_runtime_dir()
        self.ordner = os.path.join(self.runtime_dir, EINSTELLUNGEN_ORDNER)
        self.settings_path = os.path.join(self.ordner, self.SETTINGS_FILENAME)
        self.quelle = ""            # aus welcher Datei tatsaechlich gelesen
        self.meldung = ""           # was beim letzten Speichern passiert ist
        self.settings = self._vorgaben()
        self.laden()

    def _vorgaben(self) -> dict:
        """Frische Vorgaben - verschachtelte Werte kopiert, nicht geteilt."""
        return {schluessel: dict(wert) if isinstance(wert, dict) else wert
                for schluessel, wert in self.DEFAULTS.items()}

    def kandidaten(self) -> list[str]:
        """Wo nach der Einstellungsdatei gesucht wird, in dieser Reihenfolge.

        Neben dem Programm zuerst: wer den Ordner kopiert, nimmt seine
        Einstellungen mit. Der alte "backup"-Ordner und der Ausweichort im
        Benutzerprofil kommen danach.
        """
        return [
            self.settings_path,
            os.path.join(self.runtime_dir, ALTER_ORDNER, self.SETTINGS_FILENAME),
            os.path.join(ausweichordner(), self.SETTINGS_FILENAME),
        ]

    # ------------------------------------------------------------------ Lesen
    def laden(self):
        """Liest die Einstellungen aus der ersten Datei, die sich lesen laesst.

        Eine kaputte Einstellungsdatei darf den Start nie verhindern - dann
        gelten die Vorgaben, und der Grund steht in `meldung`.
        """
        for pfad in self.kandidaten():
            if not os.path.isfile(pfad):
                continue
            try:
                with open(pfad, encoding="utf-8") as datei:
                    gespeichert = json.load(datei)
            except (OSError, ValueError) as fehler:
                self.meldung = f"{pfad} liess sich nicht lesen: {fehler}"
                continue
            if not isinstance(gespeichert, dict):
                self.meldung = f"{pfad} enthaelt keine Einstellungen"
                continue
            self._uebernehmen(gespeichert)
            self.quelle = pfad
            return

    def _uebernehmen(self, gespeichert: dict):
        for schluessel, vorgabe in self.DEFAULTS.items():
            wert = gespeichert.get(schluessel)
            if isinstance(vorgabe, dict):
                if isinstance(wert, dict):
                    # Eine leere Vorgabe ist eine freie Zuordnung (Geraet ->
                    # Spaltenname): dort sind die Schluessel nicht vorher
                    # bekannt, sonst kaeme nie etwas an. Sonst gelten nur
                    # die vorgesehenen Schluessel.
                    # Zahlen sind hier ebenso erlaubt wie Text und
                    # Haken: die Reihenfolge der Reiter steht als Zahl.
                    erlaubt = {k: v for k, v in wert.items()
                               if isinstance(k, str)
                               and _brauchbar(
                                   v, nur_namen=schluessel in NUR_NAMEN)
                               and (not vorgabe or k in vorgabe)}
                    self.settings[schluessel] = dict(vorgabe, **erlaubt)
            elif isinstance(vorgabe, (int, float)) and not isinstance(
                    vorgabe, bool):
                # Zahlen kommen als Zahl zurueck - ein Text an dieser
                # Stelle waere eine kaputte Datei und bleibt draussen.
                if _zahl(wert):
                    self.settings[schluessel] = wert
                elif isinstance(wert, dict):
                    # Je Bearbeiter eine Zahl - so steht die Zeilenhoehe
                    # da, sobald jemand eine eigene gespeichert hat.
                    eigene = {name: zahl for name, zahl in wert.items()
                              if isinstance(name, str) and _zahl(zahl)}
                    if eigene:
                        self.settings[schluessel] = eigene
            elif isinstance(wert, str):
                self.settings[schluessel] = wert

    # --------------------------------------------------------------- Schreiben
    def speichern(self) -> bool:
        """Schreibt die Einstellungen und sagt, ob es geklappt hat.

        Zuerst neben das Programm. Ist der Ordner schreibgeschuetzt - die
        exe liegt auf einem Netzlaufwerk -, wird in den Benutzerordner
        ausgewichen: die Einstellungen sind sonst beim naechsten Start weg,
        ohne dass jemand erfaehrt warum.

        Geschrieben wird ueber eine Nebendatei, die anschliessend an ihren
        Platz geschoben wird. Bricht der Vorgang ab, steht dann noch der
        alte, vollstaendige Stand da statt einer halben Datei.
        """
        fehler = []
        for ordner in (self.ordner, ausweichordner()):
            pfad = os.path.join(ordner, self.SETTINGS_FILENAME)
            try:
                os.makedirs(ordner, exist_ok=True)
                vorlaeufig = pfad + ".neu"
                with open(vorlaeufig, "w", encoding="utf-8") as datei:
                    json.dump(self.settings, datei, indent=2,
                              ensure_ascii=False)
                os.replace(vorlaeufig, pfad)
            except OSError as ausnahme:
                fehler.append(f"{ordner}: {ausnahme}")
                continue
            self.settings_path = pfad
            self.quelle = pfad
            self.meldung = (f"Gespeichert in {pfad}" if not fehler else
                            f"Gespeichert in {pfad} - neben dem Programm ging "
                            f"es nicht ({fehler[0]})")
            return True
        self.meldung = ("Die Einstellungen liessen sich nicht speichern: "
                        + "; ".join(fehler))
        return False

    # ------------------------------------------------------------------ Zugriff
    def get(self, schluessel):
        return self.settings.get(schluessel, self.DEFAULTS.get(schluessel))

    def set(self, schluessel, wert):
        self.settings[schluessel] = wert

    # --------------------------------------------------- Je Bearbeiter
    #
    # Zwei Einstellungen gehoeren dem Menschen und nicht dem Rechner: die
    # Reihenfolge der Reiter und welche davon ein Geraet zeigt. Wer sich
    # die Ansicht einrichtet, will sie nicht beim naechsten Anmelden eines
    # Kollegen wiederfinden - und umgekehrt.
    #
    # In der Datei steht deshalb eine Ebene mehr: Bearbeiter -> das, was
    # bisher direkt darunter stand. Ein alter Stand ohne diese Ebene wird
    # weiter gelesen und gilt als gemeinsame Vorgabe fuer alle, die noch
    # nichts Eigenes gespeichert haben.

    def get_je_benutzer(self, schluessel, benutzer):
        """Die Einstellung dieses Bearbeiters - sonst die gemeinsame.

        Zwei Formen: eine Zuordnung (die Reiter je Geraet) und ein
        einzelner Wert (die Zeilenhoehe). Welche gilt, sagt die Vorgabe.
        """
        alles = self.get(schluessel)
        if not isinstance(self.DEFAULTS.get(schluessel), dict):
            # Ein einzelner Wert. Steht dort keine Zuordnung, ist es der
            # alte, gemeinsame Stand.
            if not isinstance(alles, dict):
                return alles
            name = benutzerschluessel(benutzer)
            if name in alles:
                return alles[name]
            return alles.get(GETEILT, self.DEFAULTS.get(schluessel))
        alles = alles or {}
        if not _je_benutzer(alles):
            return dict(alles)              # alter Stand, gilt fuer alle
        eigen = alles.get(benutzerschluessel(benutzer))
        if isinstance(eigen, dict):
            return dict(eigen)
        gemeinsam = alles.get(GETEILT)
        return dict(gemeinsam) if isinstance(gemeinsam, dict) else {}

    def set_je_benutzer(self, schluessel, benutzer, wert):
        """Schreibt die Einstellung dieses Bearbeiters - die anderen bleiben.

        Steht dort noch ein gemeinsamer Stand ohne Benutzerebene, wird er
        zur Vorgabe fuer alle uebrigen: wer nichts Eigenes gespeichert
        hat, sieht weiter, was er bisher sah.
        """
        alt = self.get(schluessel)
        if not isinstance(self.DEFAULTS.get(schluessel), dict):
            neu = dict(alt) if isinstance(alt, dict) else (
                {GETEILT: alt} if alt is not None else {})
        elif _je_benutzer(alt or {}):
            neu = {name: dict(eintrag) for name, eintrag in alt.items()
                   if isinstance(eintrag, dict)}
        else:
            neu = {GETEILT: dict(alt)} if alt else {}
        neu[benutzerschluessel(benutzer)] = wert
        self.set(schluessel, neu)
