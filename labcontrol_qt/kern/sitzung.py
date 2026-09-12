# -*- coding: utf-8 -*-
"""Den Stand einer Bearbeitung sichern und wiederfinden.

Eine Serie zu pruefen ist Handarbeit: Werte von Hand aus- und
einschliessen, Messlinien waehlen, Spalten zuordnen, einzelne Parameter
umsetzen. Eine halbe Stunde davon war bisher weg, sobald das Fenster
zuging - im Programm stand alles, auf der Platte nichts.

Hier wird genau dieser Stand gehalten: eine Datei je Geraet,
Untersuchungsmethode und Serie, im Ordner `saves` neben dem Programm.
Gespeichert wird, was ein Mensch entschieden hat, nicht was sich daraus
rechnen laesst - die Bewertung entsteht beim Oeffnen ohnehin neu, und
eine gespeicherte Zahl waere spaetestens dann falsch, wenn sich die
Verfahrenskenndaten geaendert haben.

Was hineingeht:

  * die Laufdatei selbst - Spalten und Zeilen, so wie sie eingelesen
    wurden; damit laesst sich ein Stand oeffnen, ohne dass die
    Originaldatei noch dasein muss,
  * die Entscheidungen von Hand - welcher Wert trotz Kuerzel gilt und
    welcher trotz sauberer Messung draussen bleibt,
  * die Wahl der Messlinie, wo mehrere in Frage kommen,
  * die Spaltenzuordnung dieses Laufs samt Einwaagespalte,
  * die Schalter, an denen Zahlen haengen (Blindwertabzug),
  * welche Reiter in einem eigenen Fenster standen - wer zwei
    Bildschirme eingerichtet hat, soll sie beim naechsten Oeffnen
    wiederfinden,
  * ein Protokoll der Umsetzvorgaenge - die stehen laengst in der
    Datenbank, aber wer spaeter fragt, was mit dem Lauf geschah, findet
    es hier.

Was *nicht* hineingeht, sind die Stammdaten aus dem LIMS -
Ergebniszeilen, Verfahrenskenndaten, Standards. Sie werden beim Oeffnen
frisch geholt: eine eingefrorene Obergrenze waere spaetestens dann
falsch, wenn jemand sie gepflegt hat, und die Bewertung soll gegen den
heutigen Stand laufen, nicht gegen den von vorgestern.

Geschrieben wird gepacktes JSON (.json.gz). Der Inhalt bleibt Klartext -
wer hineinsehen will, entpackt ihn mit jedem Werkzeug -, aber ein Lauf
mit dreihundert Proben schrumpft von 64 auf 14 Kilobyte, und auf einem
Netzlaufwerk mit hunderten Staenden ist das der Unterschied zwischen
unauffaellig und auffaellig.

Die Kennung steht im Dateinamen: Serie, Untersuchungsmethode, Station
und der Name der Laufdatei. So kommt die Liste im Reiter Saves ohne das
Oeffnen einer einzigen Datei aus, und das Aufraeumen ebenso.
"""
import datetime
import gzip
import json
import os
import re
import tempfile

# Der Ordner neben dem Programm. Ein Stand ist keine Einstellung - er
# gehoert einer Serie und wird wieder weggeworfen.
ORDNER = "saves"

# Wie oft von selbst gesichert wird, in Sekunden. Null schaltet es ab;
# beim Schliessen des Fensters wird trotzdem gesichert.
VORGABE_TAKT = 30

# Wie lange ein Stand aufgehoben wird, in Monaten. Null hebt alles auf.
VORGABE_MONATE = 3

# Ein Monat, grob gerechnet: fuer "aelter als drei Monate" braucht es
# keinen Kalender.
TAGE_JE_MONAT = 30

ENDUNG = ".json.gz"


def _teil(wert) -> str:
    sauber = re.sub(r"[^A-Za-z0-9_.-]+", "_",
                    str(wert if wert is not None else "")).strip("_")
    return sauber or "ohne"


# Was Windows in einem Dateinamen nicht duldet. Alles andere bleibt
# stehen: der Name der Laufdatei steht im Dateinamen und wird von dort
# gelesen - er soll aussehen wie im Explorer.
VERBOTEN = re.compile(r'[\\/:*?"<>|]+')


def _dateiteil(wert) -> str:
    sauber = VERBOTEN.sub("_", str(wert if wert is not None else "")).strip()
    # Der Trenner der Kennung darf im Namen nicht vorkommen, sonst
    # zerfaellt er beim Lesen an der falschen Stelle.
    return sauber.replace("~", "-") or "ohne"


def schluessel(um_id, stat_id, serie, datei) -> tuple:
    """Eine Kennung je Laufdatei - mehrere Staende je Serie sind moeglich."""
    return (um_id, stat_id, str(serie or ""), str(datei or ""))


def dateiname(kennung) -> str:
    um_id, stat_id, serie, datei = kennung
    return (f"{_teil(serie)}~um{_teil(um_id)}~stat{_teil(stat_id)}"
            f"~{_dateiteil(datei)}{ENDUNG}")


def kennung_aus_dateiname(name: str):
    """Liest die Kennung zurueck - ohne die Datei zu oeffnen.

    Damit kommen die Liste im Reiter Saves und das Aufraeumen ohne das
    Entpacken hunderter Staende aus.
    """
    if not name.endswith(ENDUNG):
        return None
    teile = name[:-len(ENDUNG)].split("~")
    if len(teile) != 4:
        return None
    serie, um_text, stat_text, datei = teile
    if not um_text.startswith("um") or not stat_text.startswith("stat"):
        return None
    return schluessel(_zahl_oder_text(um_text[2:]),
                      _zahl_oder_text(stat_text[4:]), serie, datei)


def _zahl_oder_text(text: str):
    try:
        return int(text)
    except (TypeError, ValueError):
        return text


# --------------------------------------------------------------------------
# Schluessel, die keine Zeichenkette sind
# --------------------------------------------------------------------------
#
# Im Programm sind es Tupel - (Zeile, Messlinie) und (Zeile, PM_ID,
# PM_VER). JSON kennt als Schluessel nur Text, deshalb werden sie mit
# einem senkrechten Strich zusammengesetzt und beim Lesen wieder
# auseinandergenommen. Ein Wert, der sich nicht in eine Zahl
# zurueckverwandeln laesst, wird uebergangen: lieber ein Stand ohne
# diesen einen Eintrag als gar keiner.

TRENNER = "|"


def schluesseltext(teile) -> str:
    return TRENNER.join(str(teil) for teil in teile)


def schluessel_lesen(text: str, laenge: int):
    teile = str(text).split(TRENNER)
    if len(teile) != laenge:
        return None
    try:
        return tuple(int(teil) for teil in teile)
    except (TypeError, ValueError):
        return None


def _tupelschluessel(rohe: dict, laenge: int) -> dict:
    gelesen = {}
    for text, wert in (rohe or {}).items():
        marke = schluessel_lesen(text, laenge)
        if marke is not None:
            gelesen[marke] = wert
    return gelesen


class Stand:
    """Was an einer Serie von Hand entschieden wurde."""

    def __init__(self, kennung, kopf=None, laufdatei=None,
                 entscheidungen=None, linienwahl=None, zuordnung=None,
                 einwaage_spalte="", schalter=None, umsetzungen=None,
                 abgetrennt=None):
        self.kennung = tuple(kennung)
        # Die eingelesene Laufdatei: Pfad, Spalten, Zeilen und wie sie
        # gelesen wurde. Damit steht ein Stand fuer sich - die
        # Originaldatei muss nicht mehr dasein.
        self.laufdatei = dict(laufdatei or {})
        # Woher der Stand kommt - Laufdatei, Bearbeiter, Zeitpunkt.
        self.kopf = dict(kopf or {})
        self.entscheidungen = dict(entscheidungen or {})
        self.linienwahl = dict(linienwahl or {})
        # Spaltenkopf -> (PM_ID, PM_VER)
        self.zuordnung = dict(zuordnung or {})
        self.einwaage_spalte = str(einwaage_spalte or "")
        self.schalter = dict(schalter or {})
        self.umsetzungen = list(umsetzungen or [])
        # Die Reiter, die in einem eigenen Fenster standen - fuer den
        # zweiten Bildschirm.
        self.abgetrennt = [str(name) for name in (abgetrennt or [])]

    @property
    def leer(self) -> bool:
        """Ist hier nichts, was sich wiederherstellen liesse?

        Die Laufdatei allein macht keinen Stand: sie liegt ja auch im
        Original vor. Erst eine Entscheidung von Hand ist einer.
        """
        return not (self.entscheidungen or self.linienwahl or self.zuordnung
                    or self.einwaage_spalte or self.umsetzungen
                    or self.abgetrennt or any(self.schalter.values()))

    @property
    def datei(self) -> str:
        """Der Name der Laufdatei, aus der dieser Stand kam."""
        return str(self.kopf.get("datei") or self.kennung[3])

    def zusammenfassung(self) -> str:
        teile = []
        for anzahl, eins, viele in (
                (len(self.entscheidungen), "Entscheidung", "Entscheidungen"),
                (len(self.linienwahl), "Linienwahl", "Linienwahlen"),
                (len(self.zuordnung), "Spalte", "Spalten"),
                (len(self.umsetzungen), "Umsetzung", "Umsetzungen")):
            if anzahl:
                teile.append(f"{anzahl} {eins if anzahl == 1 else viele}")
        if self.abgetrennt:
            teile.append(f"{len(self.abgetrennt)} eigene(s) Fenster")
        an = [name for name, wert in sorted(self.schalter.items()) if wert]
        if an:
            teile.append("Schalter: " + ", ".join(an))
        return ", ".join(teile) or "nichts entschieden"

    def als_json(self) -> dict:
        um_id, stat_id, serie, datei = self.kennung
        return {
            "kennung": {"um_id": um_id, "stat_id": stat_id, "serie": serie,
                        "datei": datei},
            "kopf": self.kopf,
            "laufdatei": self.laufdatei,
            "entscheidungen": {schluesseltext(marke): wert
                               for marke, wert in self.entscheidungen.items()},
            "linienwahl": {schluesseltext(marke): wert
                           for marke, wert in self.linienwahl.items()},
            "zuordnung": {kopf: list(wert)
                          for kopf, wert in self.zuordnung.items()},
            "einwaage_spalte": self.einwaage_spalte,
            "schalter": self.schalter,
            "umsetzungen": self.umsetzungen,
            "abgetrennt": self.abgetrennt,
        }

    @classmethod
    def aus_json(cls, inhalt: dict):
        kennung = inhalt.get("kennung") or {}
        zuordnung = {}
        for kopf, wert in (inhalt.get("zuordnung") or {}).items():
            if isinstance(wert, (list, tuple)) and len(wert) == 2:
                zuordnung[kopf] = tuple(wert)
        return cls(
            schluessel(kennung.get("um_id"), kennung.get("stat_id"),
                       kennung.get("serie"), kennung.get("datei")),
            kopf=inhalt.get("kopf"),
            laufdatei=inhalt.get("laufdatei"),
            entscheidungen=_tupelschluessel(inhalt.get("entscheidungen"), 2),
            linienwahl=_tupelschluessel(inhalt.get("linienwahl"), 3),
            zuordnung=zuordnung,
            einwaage_spalte=inhalt.get("einwaage_spalte"),
            schalter=inhalt.get("schalter"),
            umsetzungen=inhalt.get("umsetzungen"),
            abgetrennt=inhalt.get("abgetrennt"))


# --------------------------------------------------------------------------
# Die Laufdatei hin und zurueck
# --------------------------------------------------------------------------
#
# dateien.Laufdatei ist ein schlichtes Objekt - Pfad, Spalten, Zeilen und
# wie gelesen wurde. Alles andere (Name, Zeilenzahl, Herkunft) rechnet
# sich daraus. Deshalb genuegt es, diese fuenf Angaben abzulegen; auch
# eine xlsx ist nach dem Einlesen nur noch Text in Listen.

def datei_als_json(laufdatei) -> dict:
    return {"pfad": getattr(laufdatei, "pfad", ""),
            "spalten": [str(kopf) for kopf in laufdatei.spalten],
            "zeilen": [[str(wert) for wert in zeile]
                       for zeile in laufdatei.zeilen],
            "kodierung": getattr(laufdatei, "kodierung", ""),
            "trennzeichen": getattr(laufdatei, "trennzeichen", ""),
            "abgeschnitten": bool(getattr(laufdatei, "abgeschnitten", False))}


def datei_aus_json(inhalt: dict):
    """Baut die Laufdatei zurueck - ohne die Originaldatei zu brauchen."""
    import dateien                    # erst hier: sitzung soll leicht bleiben
    if not inhalt or not inhalt.get("spalten"):
        return None
    return dateien.Laufdatei(
        inhalt.get("pfad", ""), list(inhalt["spalten"]),
        [list(zeile) for zeile in inhalt.get("zeilen") or []],
        kodierung=inhalt.get("kodierung", ""),
        trennzeichen=inhalt.get("trennzeichen", ""),
        abgeschnitten=bool(inhalt.get("abgeschnitten")))


class Ablage:
    """Die Staende auf der Platte - eine Datei je Serie."""

    def __init__(self, ordner: str):
        self.ordner = ordner
        self.meldung = ""

    def pfad(self, kennung) -> str:
        return os.path.join(self.ordner, dateiname(kennung))

    def lesen(self, kennung):
        return self.lesen_pfad(self.pfad(kennung))

    @staticmethod
    def lesen_pfad(pfad: str):
        try:
            with gzip.open(pfad, "rt", encoding="utf-8") as datei:
                return Stand.aus_json(json.load(datei))
        except (OSError, ValueError, EOFError):
            return None

    def schreiben(self, stand: Stand) -> bool:
        """Legt den Stand ab - erst daneben, dann an seinen Platz.

        Ein halb geschriebener Stand waere schlimmer als ein veralteter:
        beim naechsten Oeffnen liesse er sich nicht lesen, und die
        Handarbeit waere doch weg.
        """
        try:
            os.makedirs(self.ordner, exist_ok=True)
            griff, vorlaeufig = tempfile.mkstemp(dir=self.ordner,
                                                 prefix=".sitzung",
                                                 suffix=".tmp")
            os.close(griff)
            # Gepackt: der Inhalt bleibt Klartext-JSON, wird aber rund
            # fuenfmal kleiner. Kompakt statt eingerueckt - gelesen wird
            # er ohnehin entpackt.
            with gzip.open(vorlaeufig, "wt", encoding="utf-8") as datei:
                json.dump(stand.als_json(), datei, ensure_ascii=False,
                          default=str)
            os.replace(vorlaeufig, self.pfad(stand.kennung))
            return True
        except OSError as fehler:
            self.meldung = str(fehler)
            return False

    def entfernen(self, kennung) -> bool:
        try:
            os.remove(self.pfad(kennung))
            return True
        except OSError:
            return False

    def verzeichnis(self) -> list:
        """Was da ist - ohne eine einzige Datei zu oeffnen.

        Die Kennung steht im Dateinamen, der Zeitpunkt der letzten
        Sicherung im Dateidatum. Fuer die Liste im Reiter Saves und fuer
        das Aufraeumen reicht das; entpackt wird erst, was jemand
        wirklich oeffnet.
        """
        eintraege = []
        try:
            namen = sorted(os.listdir(self.ordner))
        except OSError:
            return eintraege
        for name in namen:
            kennung = kennung_aus_dateiname(name)
            if kennung is None:
                continue
            pfad = os.path.join(self.ordner, name)
            try:
                stand = os.path.getmtime(pfad)
            except OSError:
                continue
            eintraege.append({
                "kennung": kennung, "pfad": pfad,
                "um_id": kennung[0], "stat_id": kennung[1],
                "serie": kennung[2], "datei": kennung[3],
                "gesichert": datetime.datetime.fromtimestamp(stand)})
        return eintraege

    def alle(self) -> list:
        """Jeden Stand vollstaendig - fuer Tests und Sonderfaelle."""
        gelesen = []
        for eintrag in self.verzeichnis():
            stand = self.lesen_pfad(eintrag["pfad"])
            if stand is not None:
                gelesen.append(stand)
        return gelesen

    def aufraeumen(self, monate: int, jetzt=None) -> int:
        """Loescht Staende, die aelter sind als die eingestellte Frist.

        Null hebt alles auf. Gearbeitet wird auf dem Dateidatum - eine
        Frist von Monaten braucht keinen Kalender, und ein Stand, den
        seit drei Monaten niemand angefasst hat, ist erledigt.
        """
        if not monate:
            return 0
        grenze = (jetzt or datetime.datetime.now()) - datetime.timedelta(
            days=monate * TAGE_JE_MONAT)
        entfernt = 0
        for eintrag in self.verzeichnis():
            if eintrag["gesichert"] >= grenze:
                continue
            try:
                os.remove(eintrag["pfad"])
                entfernt += 1
            except OSError:
                continue
        return entfernt
