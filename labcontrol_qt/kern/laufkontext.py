"""
LabControl - Laufkontext
========================

Die Momentaufnahme der LIMS-Stammdaten, gegen die ein Lauf geprueft wird.

Warum eine Momentaufnahme und kein wiederholtes Fragen
------------------------------------------------------
Wuerde jede Pruefung ihre Referenzwerte einzeln aus dem LIMS ziehen, koennte
sich ein Wert mitten in der Bearbeitung aendern - jemand pflegt eine
Kalibrierung nach, waehrend eine Serie durchgegangen wird. Dann pruefen die
ersten Proben gegen andere Werte als die letzten, und niemand merkt es. Ein
einmal geladener Kontext bindet den ganzen Lauf an einen konsistenten Stand.

Der zweite Grund ist die Nachweisbarkeit: mit den Ergebnissen laesst sich
festhalten, gegen welchen Stand bewertet wurde. Aus dem LIMS allein ist das
spaeter nicht mehr zu rekonstruieren, wenn die Stammdaten inzwischen
gepflegt wurden. Dafuer gibt es den CSV-Export.

Der dritte Grund ist die Testbarkeit: die Pruefmodule bekommen spaeter den
Kontext und das Raster der Laufdatei als schlichte Datenstrukturen herein -
sie brauchen dann weder Oracle noch ein Fenster.

Wichtig: der Kontext ist eine Momentaufnahme *fuer einen Lauf*, kein
Zwischenspeicher. Er wird bei jedem Oeffnen einer Laufdatei frisch geladen.
Die exportierte CSV ist ein Beleg, keine Eingabe fuer den naechsten Lauf -
ein Abbild, das ueber Laeufe hinweg liegen bleibt, veraltet unbemerkt.

Was hier nachgebaut wird
------------------------
Im LIMS sammelt bisher RELAQS_SOURCE / RELAQS_WORK (View V_RELAQS_WORK)
dieselben Angaben zusammen. Das Altsystem loest LabControl ab; die Felder
sind bewusst daran angelehnt, damit sich beide Seiten vergleichen lassen,
solange noch beide laufen.

Enthalten sind bisher:
  * die Pruefzuordnung des Geraets (GERAETE_PRUEFUNGEN_ANHANG),
  * die Ergebniszeilen des Laufs samt Faktoren, Einheit und Grenzen,
  * die fuer die Untersuchungsmethode zugelassenen Standards,
  * die Messlinien (PM_WELLEN), ueber die sich Spalten der Laufdatei einem
    Parameter zuordnen lassen.
"""

from __future__ import annotations

import datetime as dt
import decimal
import re

import lims_db


class Laufkontext:
    """Alles, was ein Lauf an Stammdaten braucht - zu einem Zeitpunkt gezogen."""

    def __init__(self, alias, benutzer, serie, methode, um_id, geraet, stat_id,
                 pruefungen, ergebnisse=None, standards=None, wellen=None,
                 bearbeiter="", erkennung=None, zuordnung=None,
                 umanhang=None, ohne_kenndaten=False, feststoff=None):
        # Die Einstellungen der Feststoffverbrennung - je Geraet.
        self.feststoff = feststoff or Feststoff()
        # Nicht eingestellt, sondern festgestellt: zum Lauf gibt es keine
        # einzige freigegebene Kenndatenzeile.
        self.alias = alias
        self.benutzer = benutzer
        self.bearbeiter = bearbeiter
        self.serie = serie
        self.methode = methode
        self.um_id = um_id
        self.geraet = geraet
        self.stat_id = stat_id
        self.pruefungen = pruefungen
        self.ergebnisse = ergebnisse or []
        self.standards = standards or []
        self.wellen = wellen or []
        # Was UM_ANHANG fuer die Parameter dieses Laufs zulaesst - daraus
        # ergibt sich, auf welche Stationen sich umsetzen laesst.
        self.umanhang = umanhang or []
        # Wie die Spalten der Laufdatei einer Pruefmethode zugeordnet
        # werden - je Geraet eingestellt, siehe ERKENNUNGEN.
        self.erkennung = erkennung or ERKENNUNG_PM_CODE
        # Von Hand gesetzte Zuordnung: Spaltenkopf -> "PM_ID/PM_VER".
        # Sie schlaegt jede automatische Erkennung - wer sie eintraegt,
        # weiss mehr als jede Namensregel.
        self.zuordnung = dict(zuordnung or {})
        # Fuer dieses Geraet sind keine Verfahrenskenndaten gepflegt -
        # Einstellung je Geraet. Dann fehlen Nachweis-, Bestimmungs- und
        # Obergrenze nicht, sie gibt es hier einfach nicht.
        self.ohne_kenndaten = bool(ohne_kenndaten)
        self.geladen_am = dt.datetime.now()
        # Verzeichnisse ueber die Listen - siehe _verzeichnis().
        self._verzeichnisse = {}

    # ------------------------------------------------------------ Nachschlagen
    #
    # Ein Lauf hat schnell sechstausend Ergebniszeilen, und jede Zelle der
    # Auswertung fragt nach genau einer davon. Die Liste dafuer jedes Mal
    # zu durchsuchen ist quadratisch: bei doppelt so vielen Proben dauert
    # es viermal so lange - gemessen 0,3 s bei hundert Proben und 5,3 s
    # bei vierhundert, und das bei jedem Klick erneut. Deshalb wird einmal
    # ein Verzeichnis gebaut und danach nur noch nachgeschlagen.
    #
    # Gebaut wird beim ersten Zugriff, nicht im Konstruktor: die
    # Pruefungen haengen Zeilen an, nachdem der Kontext steht. Aendert
    # sich die Liste - eine andere Liste oder eine andere Laenge -, baut
    # sich das Verzeichnis neu.

    def _verzeichnis(self, name: str, quelle: list, bauen):
        marke = (id(quelle), len(quelle))
        gemerkt = self._verzeichnisse.get(name)
        if gemerkt is None or gemerkt[0] != marke:
            self._verzeichnisse[name] = (marke, bauen())
        return self._verzeichnisse[name][1]

    @property
    def _nach_probe(self) -> dict:
        """Je Probe ihre Ergebniszeilen, in der Reihenfolge der Liste."""
        def bauen():
            nach_probe = {}
            for zeile in self.ergebnisse:
                nach_probe.setdefault(zeile["probe"], []).append(zeile)
            return nach_probe
        return self._verzeichnis("probe", self.ergebnisse, bauen)

    @property
    def _nach_messstelle(self) -> dict:
        """(Probe, PM_ID, PM_VER) -> Ergebniszeile; die erste gilt."""
        def bauen():
            nach_stelle = {}
            for zeile in self.ergebnisse:
                nach_stelle.setdefault(
                    (zeile["probe"], zeile["pm_id"], zeile["pm_ver"]), zeile)
            return nach_stelle
        return self._verzeichnis("messstelle", self.ergebnisse, bauen)

    @property
    def _nach_standard(self) -> dict:
        """(STAN_ID, PARA_ID) -> Standardzeile; die erste gilt."""
        def bauen():
            nach_standard = {}
            for eintrag in self.standards:
                nach_standard.setdefault(
                    (eintrag["stan_id"], eintrag["para_id"]), eintrag)
            return nach_standard
        return self._verzeichnis("standard", self.standards, bauen)

    # ------------------------------------------------------------ Ableitungen
    @property
    def laufende(self) -> list[dict]:
        """Die Pruefungen, die tatsaechlich durchgefuehrt werden."""
        return lims_db.laufende_pruefungen(self.pruefungen)

    @property
    def probenschluessel(self) -> list[str]:
        """Die Proben des Laufs, in der Reihenfolge der Ergebniszeilen.

        Das ist die Liste, gegen die eine Bezeichnung aus der Laufdatei
        abgeglichen wird - Wiederholungen stehen darin als eigene Eintraege
        ("2026P00554 2 1"), so wie sie auch in der Datei stehen.
        """
        return list(self._nach_probe)

    def endfaktor_gerechnet(self, probe: str):
        """Der Endfaktor, mit dem gerechnet wird - fehlt einer, die Eins.

        Nicht jede Untersuchungsmethode fuehrt eine Einwaage: an der
        pH-LF-Titration steht in TEILPROBEN nichts, und dort ist auch
        nichts umzurechnen. Ein fehlender Faktor darf deshalb kein
        Ergebnis vom Export ausschliessen - die Eins laesst den Messwert,
        wie er ist, und genau das ist die richtige Rechnung. Dass sie
        ergaenzt wurde, steht im Hinweis; `endfaktor` sagt weiterhin die
        Wahrheit ueber die Pflege.
        """
        faktor = self.endfaktor(probe)
        return ENDFAKTOR_EINS if faktor is None else faktor

    def endfaktor(self, probe: str):
        """Der Endfaktor einer Probe aus TEILPROBEN.

        Er haengt an PROB_ID und UM_ID, gilt also fuer alle Parameter einer
        Probe - deshalb reicht die erste Ergebniszeile, die einen fuehrt.
        Die Ergebniszeilen sind bereits auf Serie, Untersuchungsmethode und
        Station eingegrenzt; die Bedingung steht damit schon.

        Steht in END_FAKTOR nichts, gilt FAKTOR * FAKTOR_WGH - und eine
        leere Spalte darin gilt als 1. Das LIMS pflegt die drei nicht
        ueberall; wo nichts steht, ist nichts umzurechnen, und der
        Endfaktor 1 laesst den Messwert, wie er ist.
        """
        for zeile in self._nach_probe.get(probe, ()):
            wert = lims_db.als_zahl(zeile["end_faktor"])
            if wert is not None:
                return wert
            if zeile.get("endfaktor_soll") is not None:
                return zeile["endfaktor_soll"]
        return None

    def probenart(self, probe: str):
        """Die PART_ID einer Probe - sie steht in jeder ihrer Zeilen.

        Gebraucht wird sie fuer die Solleinwaage: Boden wird anders
        eingewogen als Pflanze. Die erste Ergebniszeile reicht; die
        Probenart haengt an der Probe, nicht am Parameter.
        """
        for zeile in self._nach_probe.get(probe, ()):
            if zeile.get("part_id") is not None:
                return zeile["part_id"]
        return None

    def grenzen_fuer(self, probe: str, pm_id, pm_ver) -> dict | None:
        """Nachweis-, Bestimmungs- und Obergrenze einer Messstelle.

        Sie haengen an der Ergebniszeile, die beim Laden schon mit den
        freigegebenen Verfahrenskenndaten verbunden wurde. Passte dort
        keine oder mehr als eine, stehen sie auf None - dann wird nicht
        bewertet statt gegen eine geratene Grenze zu pruefen.
        """
        zeile = self.ergebnis(probe, pm_id, pm_ver)
        if zeile is None:
            return None
        grenzen = {"nwg": lims_db.als_zahl(zeile["nwg"]),
                   "bg": lims_db.als_zahl(zeile["bg"]),
                   "ogrenze": lims_db.als_zahl(zeile["ogrenze"])}
        # Bei einer Feststoffverbrennung kommt die Obergrenze aus den
        # Optionen: sie haengt an der Kalibrierung des Ofens, nicht an
        # den Verfahrenskenndaten - und die kennen den Bezug auf einen
        # Bruchteil der Solleinwaage nicht.
        eigene = self.feststoff.ogrenze_erlaubt(zeile.get("part_id"),
                                                zeile.get("parameter"))
        if eigene is not None:
            grenzen["ogrenze"] = eigene
            grenzen["ogrenze_herkunft"] = self.feststoff.ogrenzentext(
                zeile.get("part_id"), zeile.get("parameter"))
        return grenzen

    def ergebnis(self, probe: str, pm_id, pm_ver) -> dict | None:
        """Die Ergebniszeile einer Probe zu einer bestimmten Pruefmethode."""
        return self._nach_messstelle.get((probe, pm_id, pm_ver))

    @property
    def standardnamen(self) -> dict:
        """Bezeichnung -> Standardkopf. Der Abgleich ist bewusst exakt.

        Die Kontrollstandards heissen K26MS, K26MSHg, K26MSUpdate ... - der
        eine Name ist Anfang des anderen. Wer hier auf "enthalten" pruefte,
        ordnete vier Laufzeilen demselben Standard zu.
        """
        def bauen():
            namen = {}
            for eintrag in self.standards:
                name = str(eintrag["bezeichnung"] or "").strip()
                if name:
                    namen.setdefault(name, eintrag)
            return namen
        return self._verzeichnis("standardnamen", self.standards, bauen)

    def standardgrenzen(self, stan_id, para_id) -> dict | None:
        """Sollwert und Grenzen eines Standards fuer einen Parameter.

        Die Regel steht in standardbereich() - sie gilt auch in der
        Qualitaetspruefung, und zwei Rechnungen fuer dieselbe Vorgabe
        waeren zwei Antworten.
        """
        return standardbereich(self._nach_standard.get((stan_id, para_id)))

    def sollwert(self, stan_id, para_id):
        """Der gepflegte Sollwert eines Standards - auch ohne Grenzen.

        `standardgrenzen` gibt nichts zurueck, wo kein Bereich gepflegt
        ist; fuer die Wiederfindung reicht aber der Sollwert allein.
        """
        eintrag = self._nach_standard.get((stan_id, para_id))
        return eintrag["sollwert_zahl"] if eintrag else None

    def parameter_von(self, standard) -> list[dict]:
        """Die gepflegten Parameterzeilen eines Standards."""
        return [e for e in self.standards if e["stan_id"] == standard]

    def geladen_text(self) -> str:
        return self.geladen_am.strftime("%d.%m.%Y %H:%M:%S")

    def zusammenfassung(self) -> str:
        return ((("ohne Verfahrenskenndaten - " if self.ohne_kenndaten
                  else "")
                 + f"{len(self.laufende)} von {len(self.pruefungen)} "
                   f"Pruefungen ")
                + f"laufen an diesem Geraet - {len(self.probenschluessel)} Proben, "
                f"{len(self.ergebnisse)} Ergebniszeilen, "
                f"{len(self.standardnamen)} Standards, "
                f"{len(self.wellen)} Messlinien - "
                f"Stand {self.geladen_text()} aus {self.alias}")

    # ------------------------------------------------------------------- Beleg
    def csv_zeilen(self) -> list[list]:
        """Der Kontext als Zeilen fuer den Beleg.

        Bewusst mit Kopfblock: die Datei dokumentiert, gegen welchen Stand
        geprueft wurde, und wird von Menschen gelesen - nicht wieder
        eingelesen.
        """
        zeilen = [
            ["LabControl - Laufkontext"],
            ["geladen am", self.geladen_text()],
            ["Datenbank", self.alias],
            ["angemeldet als", self.benutzer],
            ["Bearbeiter", self.bearbeiter],
            ["Serie", self.serie],
            ["Untersuchungsmethode", self.methode],
            ["UM_ID", self.um_id],
            ["Geraet", self.geraet],
            ["STAT_ID / GEGR_ID", self.stat_id],
            [],
            ["Pruefungen"],
            ["Nr.", "Pruefung", "Status", "laeuft am Geraet"],
        ]
        for pruefung in self.pruefungen:
            zeilen.append([pruefung["gepr_id"], pruefung["pruefung"],
                           pruefung["status"],
                           "ja" if pruefung["laeuft"] else "nein"])
        if not self.pruefungen:
            zeilen.append(["(keine Pruefung zugeordnet)"])

        zeilen += [[], ["Standards"], list(STANDARD_SPALTEN)]
        zeilen += standard_zeilen(self)
        zeilen += [[], ["Proben"], list(PROBEN_SPALTEN)]
        zeilen += proben_zeilen(self)
        return zeilen

    def als_csv(self) -> bytes:
        return lims_db.csv_bytes(self.csv_zeilen())

    def dateiname(self) -> str:
        stempel = self.geladen_am.strftime("%Y%m%d_%H%M%S")
        serie = "".join(z for z in str(self.serie) if z.isalnum()) or "lauf"
        return f"laufkontext_{serie}_{stempel}.csv"


def lade(zugang, serie, methode, um_id, geraet, stat_id,
         bearbeiter="", erkennung=None, zuordnung=None,
         feststoff=None) -> Laufkontext:
    """Zieht den Kontext eines Laufs in einem Zug aus dem LIMS.

    Alle Abfragen laufen ueber dieselbe Verbindung - damit stammt der ganze
    Kontext aus einer Sitzung und nicht aus fuenf zeitlich verteilten.

    Findet sich zum ganzen Lauf keine freigegebene Kenndatenzeile, ist
    das kein Befund, sondern eine Feststellung: nicht jedes Verfahren
    fuehrt Kenndaten - die pH-LF-Titration etwa. Die Zeilen bekommen dann
    keine Grenzen und keinen Vorwurf, und alles, was ohne Grenzen zu
    pruefen ist, wird weiter geprueft.
    """
    verbindung = zugang.verbinden()
    try:
        pruefungen = lims_db.pruefungen_fuer_station(zugang, stat_id,
                                                     verbindung=verbindung)
        ergebnisse = lims_db.ergebniszeilen(zugang, serie, um_id, stat_id,
                                            verbindung=verbindung)
        kenndaten = lims_db.kenndaten_fuer_lauf(zugang, serie, um_id,
                                                stat_id,
                                                verbindung=verbindung)
        # Der Umweg zum Geraet: VERFAHRENSKENNDATEN fuehrt GERA_ID,
        # die Ergebniszeile GEME_ID - was zusammengehoert, sagt
        # GERAETE_ANHANG.
        geraete = (lims_db.geraete_anhang(zugang, verbindung=verbindung)
                   if kenndaten else set())
        lims_db.kenndaten_zuordnen(ergebnisse, kenndaten, geraete)
        standards = lims_db.standards_fuer_lauf(zugang, serie, um_id,
                                                stat_id, verbindung=verbindung)
        wellen = lims_db.wellen_fuer_lauf(zugang, serie, um_id, stat_id,
                                          verbindung=verbindung)
        umanhang = lims_db.geraetewahl_fuer_lauf(zugang, serie, um_id,
                                                 stat_id,
                                                 verbindung=verbindung)
        einheiten_zuordnen(wellen, ergebnisse)
    finally:
        verbindung.close()
    return Laufkontext(
        alias=zugang.alias, benutzer=zugang.benutzer, serie=serie,
        methode=methode, um_id=um_id, geraet=geraet, stat_id=stat_id,
        pruefungen=pruefungen, ergebnisse=ergebnisse, standards=standards,
        wellen=wellen, bearbeiter=bearbeiter, erkennung=erkennung,
        zuordnung=zuordnung, umanhang=umanhang,
        ohne_kenndaten=not kenndaten, feststoff=feststoff)


# ==========================================================================
# Abgleich der Laufdatei gegen den Kontext
# ==========================================================================
#
# Reine Funktionen: sie bekommen den Kontext und das Raster der Laufdatei und
# geben Tabellen zurueck. Kein Oracle, kein Fenster - so laesst sich jede
# Zuordnungsregel mit einer Handvoll Zeilen pruefen.

# ==========================================================================
# Wie eine Spalte der Laufdatei zu ihrer Pruefmethode findet
# ==========================================================================
#
# Der Normalfall ist PM_WELLEN: dort steht zu jeder Messlinie der PM_CODE,
# und der ist genau der Spaltenkopf des Geraets. Nicht jedes Geraet ist
# aber so gepflegt - manche Laufdateien tragen Koepfe, die in PM_WELLEN
# gar nicht vorkommen. Dann bleibt nur der Name: der Kopf "ff" steckt im
# Methodennamen "FFIC5.1", und der Kopf "NaNages" ist der Anfang von
# "NaNagesIC5.1".
#
# Welcher Weg gilt, haengt am Geraet und ist deshalb eine Einstellung -
# geraten wird nicht. Und in allen Faellen gilt: passt mehr als eine
# Pruefmethode auf einen Kopf, wird die Spalte nicht zugeordnet. Eine
# geratene Zuordnung schriebe Messwerte in die falsche Ergebniszeile, und
# das faellt niemandem auf.

ERKENNUNG_PM_CODE = "pm_code"
ERKENNUNG_KOPF = "kopf"
ERKENNUNG_PARAMETER = "parameter"
ERKENNUNGEN = (
    (ERKENNUNG_PM_CODE, "ueber PM_WELLEN / PM_CODE",
     "Der Spaltenkopf entspricht genau dem PM_CODE einer Messlinie - der "
     "Normalfall, etwa \u201e208Pb (mp_KED-H2)\u201c."),
    (ERKENNUNG_KOPF, "Parameter ueber Spaltenueberschrift erkennen",
     "Der Spaltenkopf steckt im Namen der Pruefmethode: \u201eff\u201c "
     "findet FFIC5.1. Nur wenn genau eine Pruefmethode passt."),
    (ERKENNUNG_PARAMETER, "Parametername den Spaltenueberschriften zuweisen",
     "Der Spaltenkopf ist der Parametername oder der Anfang des "
     "Methodennamens: \u201eNaNages\u201c findet NaNagesIC5.1. Nur wenn "
     "genau eine Pruefmethode passt."),
)


def erkennungsname(schluessel) -> str:
    """Die Beschriftung einer Erkennungsart."""
    for kennung, name, _ in ERKENNUNGEN:
        if kennung == schluessel:
            return name
    return ERKENNUNGEN[0][1]


# Anhaengsel, die ein Geraet an den Spaltenkopf haengt, ohne dass sie zum
# Parameter gehoeren: das ICP-MS schreibt "75As (mp_KED-H2) - Value", in
# PM_WELLEN steht "75As (mp_KED-H2)". Als Liste, weil weitere Endungen
# ("- RSD" und dergleichen) absehbar sind.
SPALTENKOPF_ANHAENGSEL = (" - Value",)


def spaltenkopf(text) -> str:
    """Der Spaltenkopf, wie er mit PM_CODE verglichen wird.

    Abgeschnitten wird nur am Ende: mitten im Namen ist "- Value" Teil der
    Bezeichnung und darf nicht verschwinden. Dieselbe Regel gilt fuer beide
    Seiten des Vergleichs - falls das Anhaengsel doch einmal in PM_CODE
    gepflegt ist, passt es trotzdem zusammen.
    """
    gekuerzt = str(text or "").strip()
    for anhaengsel in SPALTENKOPF_ANHAENGSEL:
        if gekuerzt.endswith(anhaengsel):
            gekuerzt = gekuerzt[:-len(anhaengsel)].strip()
    return gekuerzt


# Wiederholungszaehler am Ende einer Probennummer: "2026P00557 4 1".
WDH_ZAEHLER = re.compile(r"\s\d+\s+\d+$")


def probenanzeige(bezeichnung) -> str:
    """Die Probennummer, wie sie angezeigt wird - immer mit beiden Zaehlern.

    Das LIMS laesst " 1 1" weg, solange nichts wiederholt wurde. In einer
    Liste stehen dann "2026P00557" und "2026P00557 4 1" untereinander und
    sehen aus wie zweierlei Dinge. Ausgeschrieben ist auf einen Blick zu
    sehen, dass die erste die erste Messung derselben Probe ist.
    """
    text = str(bezeichnung or "").strip()
    if not text or WDH_ZAEHLER.search(text):
        return text
    return f"{text} 1 1"


def probenbasis(bezeichnung) -> str:
    """Die Probennummer ohne die Wiederholungszaehler.

    Danach wird gruppiert: alle Messungen derselben Probe gehoeren
    zusammen, gleich als wievielte Wiederholung sie gelaufen sind.
    """
    return WDH_ZAEHLER.sub("", str(bezeichnung or "").strip()).strip()


def standardbereich(eintrag) -> dict | None:
    """Sollwert und Grenzen aus einer Zeile der Standardpflege.

    Genommen wird das gepflegte Paar GU/GO. Fehlt es, wird der Bereich
    aus Sollwert und Toleranz gebildet - die Toleranz gibt die
    prozentual erlaubte Abweichung an. Bleibt auch das leer, gibt es
    keinen Bereich und damit keine Bewertung; geraten wird nichts.

    Der Bereich gilt fuer die *verrechnete* Probe - also gegen den
    Gehalt und nicht gegen die Konzentration der Loesung.

    Steht `sollwert_zahl` nicht schon als Zahl da (die
    Qualitaetspruefung liest STANDARD_PARA selbst), wird der Text
    gelesen: in der Pflege steht er mit Komma.
    """
    if not eintrag:
        return None
    soll = eintrag.get("sollwert_zahl")
    if soll is None:
        soll = lims_db.als_zahl(eintrag.get("sollwert"))
    unten = eintrag.get("gu_zahl")
    if unten is None:
        unten = lims_db.als_zahl(eintrag.get("gu"))
    oben = eintrag.get("go_zahl")
    if oben is None:
        oben = lims_db.als_zahl(eintrag.get("go"))
    if unten is None and oben is None and soll is not None \
            and eintrag.get("toleranz") is not None:
        spanne = soll * lims_db.als_zahl(eintrag["toleranz"]) / 100
        unten, oben = soll - abs(spanne), soll + abs(spanne)
    if unten is None and oben is None:
        return None
    return {"unten": unten, "soll": soll, "oben": oben,
            "quelle": eintrag}


KONTROLLSTANDARD = "Kontrollstandard"
BLINDWERTSTANDARD = "Blindwertstandard"
# "Standardmaterialien" fasst Haus- und Referenzstandardmaterial zusammen -
# beide werden gleich behandelt, deshalb teilen sie sich ein Blatt. Die
# Schreibweise schwankt (mit und ohne "-material"), darum eine Liste.
STANDARDMATERIALIEN = ("Hausstandardmaterial", "Referenzstandardmaterial",
                       "Referenzstandard", "Standardmaterial")
# In der Spalte "Art" steht die kurze Form: "Hausstandardmaterial" und
# "Referenzstandardmaterial" machen die Spalte breit, und unterschieden
# werden sie in der Auswertung ohnehin nicht.
STANDARDMATERIAL_KURZ = "Standardmaterial"


def _typ_passt(typ, erlaubt) -> bool:
    """Tolerant verglichen, damit eine abweichende Schreibweise nichts frisst."""
    return str(typ or "").strip().upper() in {e.upper() for e in erlaubt}


def ist_kontrollstandard(typ) -> bool:
    return _typ_passt(typ, (KONTROLLSTANDARD,))


def ist_standardmaterial(typ) -> bool:
    return _typ_passt(typ, STANDARDMATERIALIEN)


def ist_blindwertstandard(typ) -> bool:
    return _typ_passt(typ, (BLINDWERTSTANDARD,))


def artkuerzel(typ) -> str:
    """Die kurze Form fuer die Spalte "Art"."""
    return STANDARDMATERIAL_KURZ if ist_standardmaterial(typ) else typ


PROBE = "Probe"
STANDARD = "Standard"
UNBEKANNT = "nicht zugeordnet"


def probenspalte(laufdatei, kontext) -> int:
    """Welche Spalte der Laufdatei traegt die Probenbezeichnung?

    Statt die erste Spalte zu setzen, wird die Spalte mit den meisten
    Treffern gegen Proben und Standards genommen. Jedes Geraeteformat legt
    die Bezeichnung woanders hin; eine feste Position waere die erste
    Annahme, die beim naechsten Geraet bricht. Bei Gleichstand gewinnt die
    linke Spalte.
    """
    proben = set(kontext.probenschluessel)
    namen = kontext.standardnamen
    beste, bester_wert = 0, -1
    for nummer in range(laufdatei.spaltenzahl):
        treffer = 0
        for zeile in laufdatei.zeilen:
            if nummer >= len(zeile):
                continue
            text = str(zeile[nummer] or "").strip()
            if not text:
                continue
            if text in proben or lims_db.standard_zerlegen(text)[1] in namen:
                treffer += 1
        if treffer > bester_wert:
            beste, bester_wert = nummer, treffer
    return beste


def zuordnen(kontext, laufdatei) -> list[dict]:
    """Ordnet jede Zeile der Laufdatei einer Probe oder einem Standard zu.

    Was sich nicht zuordnen laesst - "Dummy", "Blank", "x" -, bleibt mit der
    Art "nicht zugeordnet" stehen und faellt aus jeder Auswertung heraus.
    Es wird aber angezeigt: eine Zeile, die klammheimlich verschwindet, ist
    genau die, nach der spaeter jemand sucht.

    Ebenfalls nicht ausgewertet werden Proben ohne Ergebniszeile im LIMS -
    fuer sie ist in diesem Lauf nichts beauftragt.

    Ein Standard bleibt ein Standard, auch wenn er zusaetzlich als Probe
    gefuehrt wird. Das LIMS legt Standards eine Probennummer an, damit ihre
    Messwerte in ERGEBNISSE landen koennen - "1/NHarz" steht deshalb in
    PROBEN *und* in STANDARDVERWALTUNG. Wer zuerst auf die Probenliste
    sieht, nennt ein Hausstandardmaterial "Probe" und bewertet es spaeter
    nach den falschen Regeln. Deshalb entscheidet die
    Standardverwaltung, und der Probenschluessel wird trotzdem gemerkt -
    ohne ihn liesse sich die Ergebniszeile nicht finden.
    """
    proben = set(kontext.probenschluessel)
    namen = kontext.standardnamen
    spalte = probenspalte(laufdatei, kontext)
    eintraege = []
    for nummer, zeile in enumerate(laufdatei.zeilen, start=1):
        text = (str(zeile[spalte]).strip() if spalte < len(zeile)
                and zeile[spalte] is not None else "")
        wiederholung, name = lims_db.standard_zerlegen(text)
        standard = namen.get(name)
        # Der Schluessel ist die vollstaendige Bezeichnung, mit Praefix:
        # das LIMS fuehrt "1/NHarz" und "2/NHarz" als eigene Proben. Auf
        # den gekuerzten Namen auszuweichen wuerde beide auf dieselbe
        # Ergebniszeile zeigen lassen.
        eintrag = {"zeile": nummer, "bezeichnung": text, "art": UNBEKANNT,
                   "probe": text if text in proben else "",
                   "stan_id": None, "wiederholung": None, "standard": "",
                   "typ": ""}
        if standard is not None and _praefix_stimmt(standard["typ"],
                                                    wiederholung):
            eintrag.update({"art": STANDARD, "stan_id": standard["stan_id"],
                            "wiederholung": wiederholung,
                            "standard": name, "typ": standard["typ"]})
        elif eintrag["probe"]:
            eintrag["art"] = PROBE
        eintraege.append(eintrag)
    return eintraege


# Standardmaterialien, Referenzstandards und Blindwertstandards werden im
# Lauf immer mit vorangestellter Nummer geschrieben - "1/NHarz". Ohne
# dieses Praefix ist die Zeile nicht die geplante Messung dieses
# Standards, sondern etwas anderes, das zufaellig so heisst. Sie wird dann
# nicht ausgewertet, statt als Standard gezaehlt zu werden.
PRAEFIXPFLICHT = STANDARDMATERIALIEN + (BLINDWERTSTANDARD,)


def _praefix_stimmt(typ, wiederholung) -> bool:
    return not _typ_passt(typ, PRAEFIXPFLICHT) or wiederholung is not None


def ausgewertete(eintraege: list[dict]) -> list[dict]:
    """Nur die Zeilen, die in die Auswertung eingehen."""
    return [e for e in eintraege if e["art"] != UNBEKANNT]


class Matrix:
    """Proben in den Zeilen, Messlinien in den Spalten, Pruefmethode in der Zelle."""

    def __init__(self, wellen, proben, zellen, ohne_spalte):
        self.wellen = wellen                # zugeordnete PM_WELLEN-Zeilen
        self.proben = proben                # Probenbezeichnungen (Zeilen)
        self.zellen = zellen                # {(probe, pmwe_id): Pruefmethode}
        self.ohne_spalte = ohne_spalte      # Spalten der Datei ohne Messlinie

    @property
    def gruppen(self) -> list[list[dict]]:
        return methodengruppen(self.wellen)

    @property
    def spalten(self) -> list[str]:
        # Eine Spalte je Element, wie in den anderen Reitern. Ohne Einheit:
        # in der Zelle steht der Name der Pruefmethode, keine Zahl.
        return ["Probe"] + gruppennamen(self.gruppen, mit_einheit=False)

    def zeilen(self) -> list[list]:
        return [[probenanzeige(probe)]
                + [self._zelle(probe, gruppe) for gruppe in self.gruppen]
                for probe in self.proben]

    def _zelle(self, probe, gruppe) -> str:
        for welle in gruppe:
            treffer = self.zellen.get((probe, welle["pmwe_id"]), "")
            if treffer:
                return treffer
        return ""

    def zusammenfassung(self) -> str:
        return (f"{len(self.proben)} Proben x {len(self.wellen)} Messlinien "
                f"- {len(self.zellen)} zugeordnete Pruefmethoden")


def beschriftung(welle: dict) -> str:
    """Spaltenkopf einer Messlinie.

    Der PM_CODE ist der ganze Spaltenkopf der Geraetedatei
    ("206Pb (mp_KED-H2)") und als Ueberschrift zu lang - zwei
    Bleilinien nebeneinander waeren nicht mehr zu unterscheiden. Genommen
    wird deshalb der Kurzname der Linie, sonst die Wellenlaenge, und erst
    zuletzt der Code.
    """
    name = str(welle["parameter"] or "").strip()
    linie = (str(welle["kurzname"] or "").strip()
             or str(welle["wellenlaenge"] or "").strip()
             or str(welle["pm_code"] or "").strip())
    if name and linie and name != linie:
        return f"{name} ({linie})"
    return name or linie or f"PM_WELLEN {welle['pmwe_id']}"


# Welche der beiden Einheiten an die Ueberschrift gehoert: die der
# gemessenen Loesung (EINH_ID) oder die des Gehalts (EINH_VER_ID). Wo mit
# dem Endfaktor gerechnet wurde, steht in der Zelle ein Gehalt - und der
# traegt eine andere Einheit als die Konzentration, aus der er stammt.
EINHEIT_LOESUNG = "einheit"
EINHEIT_GEHALT = "einheit_ver"


def kurzbeschriftungen(wellen: list[dict], mit_einheit=False,
                       einheit=EINHEIT_LOESUNG) -> list[str]:
    """Der Parametername je Messlinie, die Linie nur bei Doppelung.

    Dieselbe Ueberlegung wie bei lims_db.eindeutige_bezeichnungen: so kurz
    wie moeglich, aber nie mehrdeutig.

    `mit_einheit` haengt die Einheit an - "Cdges [ug/l]". Sie steht ueberall
    da, wo eine Konzentration in der Zelle steht: eine Zahl ohne Einheit
    laesst sich nicht gegen eine Grenze halten. `einheit` waehlt, welche:
    die der Loesung oder die des mit dem Endfaktor gerechneten Gehalts.
    """
    namen = [str(w["parameter"] or "").strip() for w in wellen]
    mehrfach = {name for name in namen if namen.count(name) > 1}
    kurz = [beschriftung(welle) if not name or name in mehrfach else name
            for name, welle in zip(namen, wellen)]
    if not mit_einheit:
        return kurz
    return [f"{name} [{welle.get(einheit)}]" if welle.get(einheit) else name
            for name, welle in zip(kurz, wellen)]


def einheiten_zuordnen(wellen: list[dict], ergebnisse: list[dict]) -> list[dict]:
    """Haengt jeder Messlinie die Einheit ihrer Ergebniszeilen an.

    PM_WELLEN fuehrt keine Einheit - sie steht in ERGEBNISSE.EINH_ID und
    kommt ueber EINHEITEN.EINHEIT. Zugeordnet wird ueber PM_ID und PM_VER;
    mehrere Linien derselben Pruefmethode teilen sich damit die Einheit,
    was richtig ist: es ist dieselbe Groesse, nur eine andere Linie.

    Dazu die verrechnete Einheit aus EINH_VER_ID: sobald der Endfaktor
    angewandt ist, steht in der Zelle kein Konzentrationswert der Loesung
    mehr, sondern ein Gehalt - und der traegt eine andere Einheit
    (ug/l wird zu mg/kg). Beide Einheiten haengen deshalb an der Linie.
    """
    nach_methode: dict = {}
    for zeile in ergebnisse:
        schluessel = (zeile["pm_id"], zeile["pm_ver"])
        eintrag = nach_methode.setdefault(schluessel, {})
        for feld in (EINHEIT_LOESUNG, EINHEIT_GEHALT):
            if zeile.get(feld) and not eintrag.get(feld):
                eintrag[feld] = str(zeile[feld]).strip()
    for welle in wellen:
        gefunden = nach_methode.get((welle["pm_id"], welle["pm_ver"]), {})
        for feld in (EINHEIT_LOESUNG, EINHEIT_GEHALT):
            welle[feld] = gefunden.get(feld, "")
    return wellen


def _vergleichbar(text) -> str:
    """Text ohne Leerzeichen und ohne Gross-/Kleinschreibung.

    Die Koepfe der Geraete und die Namen im LIMS stimmen selten bis aufs
    Zeichen ueberein - "NaNages" und "nanages " sollen dasselbe treffen.
    """
    return "".join(str(text or "").split()).upper()


def _methoden(kontext) -> list[dict]:
    """Die Pruefmethoden dieses Laufs, je einmal.

    Aus den Ergebniszeilen, nicht aus PM_WELLEN: gesucht wird ja gerade
    dann, wenn PM_WELLEN den Spaltenkopf nicht kennt. Beauftragt ist, was
    in ERGEBNISSE steht.
    """
    gesehen, methoden = set(), []
    for zeile in kontext.ergebnisse:
        schluessel = (zeile["pm_id"], zeile["pm_ver"])
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        methoden.append(zeile)
    return methoden


def _passt_zum_kopf(methode: dict, kopf: str, art: str) -> bool:
    """Gehoert diese Pruefmethode zu diesem Spaltenkopf?"""
    name = _vergleichbar(methode["pruefmethode"])
    parameter = _vergleichbar(methode["parameter"])
    gesucht = _vergleichbar(kopf)
    if not gesucht:
        return False
    if art == ERKENNUNG_KOPF:
        # Der Kopf steckt irgendwo im Methodennamen: "ff" in "FFIC5.1".
        return bool(name) and gesucht in name
    # Der Kopf ist der Parametername oder der Anfang des Methodennamens:
    # "NaNages" gehoert zu NaNagesIC5.1.
    return (bool(parameter) and gesucht == parameter) or \
        (bool(name) and name.startswith(gesucht))


def _kopfvarianten(kopf) -> list[str]:
    """Der Spaltenkopf - und derselbe Kopf ohne den Vorsatz des Geraets.

    Der Ionenchromatograph schreibt seine Koepfe als "RS.FF", "RS.NaNages":
    vorn steht, aus welchem Kanal der Wert kommt, dahinter erst der
    Parameter. Gegen "FFIC5.1" gehalten passt "RS.FF" nirgends - "FF"
    schon. Abgeschnitten wird deshalb bis zum letzten Punkt, und zwar erst
    im zweiten Anlauf: passt der ganze Kopf, gilt der ganze Kopf.
    """
    name = str(kopf or "").strip()
    varianten = [name]
    rest = name.rsplit(".", 1)[-1].strip() if "." in name else ""
    if rest and rest not in varianten:
        varianten.append(rest)
    return varianten


def _treffer(methoden: list, kopf: str, art: str):
    """Die eine Pruefmethode zu diesem Kopf - oder None.

    Passt keine oder passt mehr als eine, bleibt die Spalte draussen: eine
    geratene Zuordnung schriebe Messwerte in die falsche Ergebniszeile.
    """
    for variante in _kopfvarianten(kopf):
        passend = [m for m in methoden if _passt_zum_kopf(m, variante, art)]
        if len(passend) == 1:
            return passend[0]
    return None


def methodenschluessel(methode: dict) -> str:
    """Wie eine Pruefmethode in der Einstellungsdatei steht: "100/2"."""
    return f"{methode['pm_id']}/{methode['pm_ver']}"


def methodenliste(kontext) -> list[dict]:
    """Die Pruefmethoden des Laufs mit Schluessel und Beschriftung.

    Das ist die Auswahl, aus der von Hand zugeordnet wird - und zwar aus
    ERGEBNISSE: was dort nicht steht, laesst sich ohnehin nicht schreiben.

    Gesperrte Pruefmethoden bleiben draussen: sie stehen zwar in
    ERGEBNISSE, sind aber nicht mehr gueltig, und eine Spalte auf sie zu
    legen hiesse, gegen einen abgelaufenen Stand zu messen.
    """
    liste, gesehen = [], set()
    for methode in _methoden(kontext):
        if methode.get("pm_freigegeben") is False:
            continue
        schluessel = methodenschluessel(methode)
        gesehen.add(schluessel)
        liste.append({"schluessel": schluessel,
                      "beschriftung": _methodentext(methode, schluessel),
                      "methode": methode, "gegr_id": methode.get("gegr_id"),
                      "station": "", "gebucht": True})
    # Dazu, was dieselben Parameter an anderen Stationen bedeuten wuerden:
    # UM_ANHANG fuehrt sie, und wer eine davon waehlt, will den Lauf
    # dorthin umsetzen - die Pruefmethode wechselt ja nicht allein.
    for zeile in _andere_stationen(kontext):
        schluessel = methodenschluessel(zeile)
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        station = str(zeile.get("station") or "").strip() \
            or f"GEGR_ID {zeile.get('gegr_id')}"
        liste.append({"schluessel": schluessel,
                      "beschriftung": f"{_methodentext(zeile, schluessel)} "
                                      f"\u2192 umsetzen auf {station}",
                      "methode": zeile, "gegr_id": zeile.get("gegr_id"),
                      "station": station, "gebucht": False})
    return sorted(liste, key=lambda e: (not e["gebucht"],
                                        e["beschriftung"].upper()))


def _methodentext(methode: dict, schluessel: str) -> str:
    name = str(methode.get("pruefmethode") or "").strip()
    parameter = str(methode.get("parameter") or "").strip()
    beschriftung = " - ".join(teil for teil in (parameter, name) if teil)
    return f"{beschriftung} ({schluessel})" if beschriftung else schluessel


def _andere_stationen(kontext) -> list[dict]:
    """Die UM_ANHANG-Zeilen der Parameter des Laufs an anderen Stationen."""
    return [zeile for zeile in getattr(kontext, "umanhang", [])
            if zeile.get("gegr_id") is not None
            and zeile.get("gegr_id") != kontext.stat_id]


def vorschlag(kontext, laufdatei, art=None) -> dict:
    """Ein Vorschlag, welche Spalte zu welcher Pruefmethode gehoert.

    Fuer den Knopf im Reiter Zuordnung: er fuellt die Auswahlfelder mit
    dem, was sich ueber die Namen finden laesst - unabhaengig davon,
    welche Erkennung eingestellt ist. Angesehen und bestaetigt wird von
    Hand, gespeichert wird erst danach.

    Ohne `art` werden beide Namensregeln der Reihe nach versucht: erst der
    Parametername/Methodenanfang, dann der im Methodennamen enthaltene
    Kopf. Eine Pruefmethode wird nur einmal vergeben.
    """
    methoden = _methoden(kontext)
    arten = (art,) if art else (ERKENNUNG_PARAMETER, ERKENNUNG_KOPF)
    gefunden, vergeben = {}, set()
    for kopf in laufdatei.spalten:
        name = spaltenkopf(kopf)
        if not name or name in gefunden:
            continue
        methode = None
        for kennung in arten:
            methode = _treffer(methoden, name, kennung)
            if methode is not None:
                break
        if methode is None:
            continue
        schluessel = methodenschluessel(methode)
        if schluessel in vergeben:
            continue
        vergeben.add(schluessel)
        gefunden[name] = schluessel
    return gefunden


def _welle_aus_methode(methode: dict, kopf: str, nummer: int) -> dict:
    """Baut zu einer Pruefmethode die Messlinie, die PM_WELLEN nicht hat.

    Alles Weitere - Verdichtung, Bewertung, Export - arbeitet mit
    Messlinien. Damit eine ueber den Namen gefundene Spalte denselben Weg
    geht, bekommt sie hier eine: ohne Konzentrationsbereich, denn den
    kennt nur PM_WELLEN, und mit einer negativen Kennung, an der sich
    ablesen laesst, dass sie nicht aus der Tabelle stammt.
    """
    return {"pmwe_id": -(nummer + 1), "pm_id": methode["pm_id"],
            "pm_ver": methode["pm_ver"], "pm_code": kopf, "kurzname": "",
            "wellenlaenge": "", "hfa_code": "", "ugrenze": None,
            "ogrenze": None, "rsdproz": None, "rsdabs": None,
            "para_id": methode["para_id"],
            "pruefmethode": methode["pruefmethode"],
            "parameter": methode["parameter"],
            "einheit": methode.get("einheit", ""),
            "einheit_ver": methode.get("einheit_ver", "")}


def messlinien(kontext, laufdatei) -> list[dict]:
    """Die Spalten der Laufdatei, die zu einer Pruefmethode gehoeren.

    Welcher Weg gilt, steht im Kontext (Einstellung je Geraet):

      * ueber PM_CODE - der Spaltenkopf entspricht *genau* einer Messlinie
        aus PM_WELLEN. Genau, nicht enthalten: die Koepfe heissen "206Pb
        (mp_KED-H2)", "207Pb ...", "208Pb ..." - eine Suche nach
        Teilstrings traefe drei Spalten auf einmal, und ein kurzer Code
        wie "Co" auch noch die Spalte "Sample List - Comment".
      * ueber den Namen - fuer Geraete, deren Koepfe in PM_WELLEN nicht
        gepflegt sind. Dann wird der Kopf gegen die Namen der
        Pruefmethoden gehalten; passt genau eine, gilt sie.

    Passt mehr als eine Pruefmethode, bleibt die Spalte draussen: eine
    geratene Zuordnung schriebe Messwerte in die falsche Ergebniszeile.

    Von Hand zugeordnet duerfen mehrere Spalten auf dieselbe
    Pruefmethode zeigen - so wie PM_WELLEN mehrere Messlinien je Methode
    fuehrt. Welche davon in einer Zeile gilt, entscheidet danach der
    Konzentrationsbereich oder die Wahl im Reiter
    Konzentrationsumschaltung.
    """
    methoden = _methoden(kontext)
    nach_schluessel = {methodenschluessel(m): m for m in methoden}
    automatisch = kontext.erkennung in (ERKENNUNG_KOPF, ERKENNUNG_PARAMETER)
    wellen, vergeben = [], set()
    for kopf in laufdatei.spalten:
        name = spaltenkopf(kopf)
        # 1. Von Hand zugeordnet - das gilt immer.
        von_hand = nach_schluessel.get(kontext.zuordnung.get(name))
        gewaehlt = von_hand
        if gewaehlt is None and automatisch:
            # 2. Sonst ueber den Namen, wenn genau eine Methode passt.
            gewaehlt = _treffer(methoden, name, kontext.erkennung)
        if gewaehlt is None:
            continue
        schluessel = (gewaehlt["pm_id"], gewaehlt["pm_ver"])
        # Von Hand darf dieselbe Pruefmethode mehrfach vergeben werden:
        # eine Methode kann mehrere Messlinien haben, und dann gehoeren
        # ihr auch mehrere Spalten der Laufdatei - genau wie in
        # PM_WELLEN. Ueber die Namen erkannt bleibt es bei einer: dort
        # waere die zweite geraten.
        if schluessel in vergeben and von_hand is None:
            continue
        vergeben.add(schluessel)
        wellen.append(_welle_aus_methode(gewaehlt, name, len(wellen)))
    if not automatisch and not kontext.zuordnung:
        # Der Normalfall: ueber PM_WELLEN.
        koepfe = {spaltenkopf(kopf) for kopf in laufdatei.spalten}
        return [w for w in kontext.wellen
                if spaltenkopf(w["pm_code"]) in koepfe]
    if not automatisch:
        # Von Hand zugeordnete Koepfe *und* die Messlinien aus PM_WELLEN:
        # eine Handzuordnung ergaenzt den Normalfall, sie ersetzt ihn
        # nicht - sonst verschwaende ein einziger Eintrag alle anderen
        # Spalten.
        belegt = {(w["pm_id"], w["pm_ver"]) for w in wellen}
        koepfe = {spaltenkopf(kopf) for kopf in laufdatei.spalten}
        wellen += [w for w in kontext.wellen
                   if spaltenkopf(w["pm_code"]) in koepfe
                   and (w["pm_id"], w["pm_ver"]) not in belegt]
    return wellen


def zuordnungsgrund(kontext, laufdatei) -> str:
    """Warum keine Spalte der Laufdatei zu einer Pruefmethode gefunden wurde.

    Ein leerer Reiter Messung sagt nur, dass nichts da ist - nicht, an
    welcher Stelle es fehlt. Und es sind mehrere Stellen moeglich: keine
    Ergebniszeile im LIMS, keine Messlinie in PM_WELLEN, oder Koepfe, die
    weder auf einen PM_CODE noch auf einen Methodennamen passen. Ohne
    diese Unterscheidung sucht man an der falschen.

    Kommt "" zurueck, liegt es nicht an der Zuordnung.
    """
    if messlinien(kontext, laufdatei):
        return ""
    koepfe = [spaltenkopf(kopf) for kopf in laufdatei.spalten]
    if not kontext.ergebnisse:
        return ("Zu Serie, Untersuchungsmethode und Station steht keine "
                "Zeile in ERGEBNISSE - fuer diesen Lauf ist im LIMS nichts "
                "beauftragt.")
    if kontext.erkennung in (ERKENNUNG_KOPF, ERKENNUNG_PARAMETER):
        namen = [_methodentext(m, f"{m['pm_id']}/{m['pm_ver']}")
                 for m in _methoden(kontext)]
        return (f"Kein Spaltenkopf passt auf genau eine Pruefmethode "
                f"({kontext.erkennung}). Koepfe: {_kurzliste(koepfe)}; "
                f"Pruefmethoden: {_kurzliste(namen)}. Im Reiter Zuordnung "
                f"lassen sich die Spalten von Hand zuordnen.")
    if not kontext.wellen:
        return ("PM_WELLEN fuehrt zu den Pruefmethoden dieses Laufs keine "
                "Messlinie - dann kann der Normalfall (Spaltenkopf gleich "
                "PM_CODE) nichts finden. Fuer dieses Geraet gehoert die "
                "Parametererkennung in den Optionen auf den Namen, oder "
                "die Spalten werden im Reiter Zuordnung von Hand "
                "zugeordnet.")
    codes = [spaltenkopf(w["pm_code"]) for w in kontext.wellen]
    return (f"Kein Spaltenkopf der Laufdatei entspricht einem PM_CODE aus "
            f"PM_WELLEN, auch nicht ohne \u201e - Value\u201c. Koepfe: "
            f"{_kurzliste(koepfe)}; PM_CODE: {_kurzliste(codes)}.")


def _kurzliste(namen, hoechstens=6) -> str:
    """Ein paar Namen in einer Zeile - der Rest waere nicht mehr zu lesen."""
    sichtbar = [str(name).strip() for name in namen if str(name).strip()]
    if not sichtbar:
        return "(keine)"
    text = ", ".join(sichtbar[:hoechstens])
    return text + (f" ... (+{len(sichtbar) - hoechstens})"
                   if len(sichtbar) > hoechstens else "")


def matrix(kontext, laufdatei) -> Matrix:
    """Spannt Proben gegen Parameter auf.

    Spalten sind die Spalten der Laufdatei, die sich einer Pruefmethode
    zuordnen liessen - wie, entscheidet die Einstellung des Geraets, siehe
    `messlinien`.

    Zeilen sind die Proben der Laufdatei, fuer die es Ergebniszeilen gibt.
    In der Zelle steht der Name der Pruefmethode aus PM_ID und PM_VER.
    """
    wellen = messlinien(kontext, laufdatei)
    erkannt = {spaltenkopf(w["pm_code"]) for w in wellen}
    # Nicht erkannte Koepfe bleiben ungekuerzt stehen - sie sollen sich in
    # der Datei wiederfinden lassen.
    ohne_spalte = [str(kopf).strip() for kopf in laufdatei.spalten
                   if spaltenkopf(kopf) not in erkannt]

    proben, gesehen = [], set()
    for eintrag in zuordnen(kontext, laufdatei):
        if eintrag["art"] != PROBE or eintrag["probe"] in gesehen:
            continue
        gesehen.add(eintrag["probe"])
        proben.append(eintrag["probe"])

    zellen = {}
    for probe in proben:
        for welle in wellen:
            treffer = kontext.ergebnis(probe, welle["pm_id"], welle["pm_ver"])
            if treffer is None:
                continue
            name = str(treffer["pruefmethode"] or welle["pruefmethode"]
                       or "").strip()
            if name:
                zellen[(probe, welle["pmwe_id"])] = name
    # Proben ohne eine einzige Zelle sind in diesem Lauf nicht beauftragt.
    proben = [p for p in proben
              if any((p, w["pmwe_id"]) in zellen for w in wellen)]
    return Matrix(wellen, proben, zellen, ohne_spalte)


# ------------------------------------------------------------ Tabellenaufbau

STANDARD_SPALTEN = ("STAN_ID", "Nummer", "Bezeichnung", "Typ", "Parameter",
                    "Test", "Sollwert", "Toleranz %", "GU", "GO", "QC_GU",
                    "QC_GO", "PR_GU", "PR_GO", "Linie")


def standard_zeilen(kontext) -> list[list]:
    return [[e["stan_id"], e["nummer"], e["bezeichnung"], e["typ"],
             e["parameter"], e["test"], e["sollwert"], e["toleranz"],
             e["gu"], e["go"], e["qc_gu"], e["qc_go"], e["pr_gu"], e["pr_go"],
             e["linie"]] for e in kontext.standards]


PROBEN_SPALTEN = ("Probe", "PROB_ID", "Probenart", "Parameter", "Pruefmethode",
                  "PM_ID", "PM_VER", "Einheit", "Messwert", "Faktor",
                  "Faktor WGH", "Endfaktor", "Endfaktor Soll", "Endfaktor-Pruefung",
                  "NWG", "BG", "Obergrenze", "VKD_ID", "Hinweis")

# Zwei Ueberschriften sind laenger als ihre Spalte breit sein muss. Gekuerzt
# angezeigt, ausgeschrieben beim Ueberfahren - der Platz gehoert den Zahlen.
PROBEN_UEBERSCHRIFTEN = tuple(
    {"Endfaktor Soll": "EF soll",
     "Endfaktor-Pruefung": "EF P"}.get(name, name) for name in PROBEN_SPALTEN)

# Die Spalten, in denen ein Faktor steht. Vier Nachkommastellen reichen zum
# Lesen; der genaue Wert steht im Hinweis.
FAKTORSPALTEN = ("Faktor", "Faktor WGH", "Endfaktor", "Endfaktor Soll")
NACHKOMMA_FAKTOR = 4


def faktortext(wert) -> str:
    """Ein Faktor mit hoechstens vier Nachkommastellen, ohne Nullen am Ende.

    "3" liest sich besser als "3,0000", und "1,5" besser als "1,5000" -
    die Stellen sagen hier nichts, sie stehen nur im Weg.
    """
    zahl = lims_db.als_zahl(wert)
    if zahl is None:
        return lims_db.als_text(wert)
    text = zahltext(zahl, NACHKOMMA_FAKTOR)
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text or "0"


def _messwerttext(wert) -> str:
    """Der Messwert wie in der Datenbankabfrage - gestufte Stellenzahl."""
    zahl = lims_db.als_zahl(wert)
    return (zahltext_gestuft(zahl) if zahl is not None
            else lims_db.als_text(wert))


def proben_zeilen(kontext) -> list[list]:
    return [[probenanzeige(e["probe"]), e["prob_id"], e["probenart"],
             e["parameter"],
             e["pruefmethode"], e["pm_id"], e["pm_ver"], e["einheit"],
             _messwerttext(e["mw"]), faktortext(e["faktor"]),
             faktortext(e["faktor_wgh"]), faktortext(e["end_faktor"]),
             faktortext(e["endfaktor_soll"]), e["endfaktor_status"],
             e["nwg"], e["bg"], e["ogrenze"], e["vkd_gefunden"],
             e["kenndaten_hinweis"]] for e in kontext.ergebnisse]


# Welche Spalten gerundet angezeigt werden und woher ihr genauer Wert kommt.
PROBEN_GENAU = {"Messwert": "mw", "Faktor": "faktor",
                "Faktor WGH": "faktor_wgh", "Endfaktor": "end_faktor",
                "Endfaktor Soll": "endfaktor_soll"}


def proben_hinweis(kontext):
    """Zu jeder gerundeten Zelle des Reiters Proben ihr genauer Wert."""
    stellen = [PROBEN_GENAU.get(name) for name in PROBEN_SPALTEN]

    def hinweis(zeile: int, spalte: int) -> str:
        if not 0 <= zeile < len(kontext.ergebnisse):
            return ""
        if not 0 <= spalte < len(stellen) or stellen[spalte] is None:
            return ""
        wert = kontext.ergebnisse[zeile][stellen[spalte]]
        return "" if wert is None else f"genau: {lims_db.als_text(wert)}"
    return hinweis


# ==========================================================================
# Welche Messlinie ein Element liefert
# ==========================================================================
#
# Misst ein Geraet denselben Parameter auf mehreren Linien - zwei
# Calciumlinien etwa -, gehoeren sie derselben Pruefmethode und damit
# derselben Ergebniszeile. In jeder Auswertung steht deshalb je Element
# eine Spalte; welche Linie sie fuellt, entscheidet sich hier.
#
# Die Regel liegt bewusst hier und nicht in der Bewertung: sie beschreibt
# den Aufbau des Laufs, nicht seine Beurteilung - und Anzeige wie Bewertung
# muessen dieselbe Linie nehmen, sonst zeigte der eine Reiter etwas
# anderes als der andere schreibt.

def methodengruppen(wellen: list[dict]) -> list[list[dict]]:
    """Die Messlinien, nach Pruefmethode gebuendelt.

    Eine Pruefmethode ist eine Ergebniszeile und damit ein Element - egal
    auf wie vielen Linien es gemessen wurde. In der Auswertung steht
    deshalb je Gruppe eine Spalte: "Calcium", nicht "Ca315" und "Ca393"
    nebeneinander. Welche Linie den Wert liefert, entscheidet der
    Konzentrationsbereich - oder die Hand.

    Die Reihenfolge folgt dem ersten Auftreten, damit die Spalten stehen
    bleiben, wenn sich eine Auswahl aendert.
    """
    gruppen: dict[tuple, list] = {}
    for welle in wellen:
        gruppen.setdefault((welle["pm_id"], welle["pm_ver"]), []).append(welle)
    return list(gruppen.values())


def gruppenname(gruppe: list[dict], mit_einheit=True,
                einheit=EINHEIT_LOESUNG) -> str:
    """Die Ueberschrift einer Gruppe: der Parametername, meist mit Einheit."""
    return kurzbeschriftungen([gruppe[0]], mit_einheit=mit_einheit,
                              einheit=einheit)[0]


def gruppennamen(gruppen: list[list[dict]], mit_einheit=True,
                 einheit=EINHEIT_LOESUNG) -> list[str]:
    """Die Ueberschriften aller Gruppen, garantiert unterscheidbar.

    Zwei Pruefmethoden koennen denselben Parameter messen. Als Spaltenkopf
    waeren sie dann nicht auseinanderzuhalten - und eine Treeview braucht
    ohnehin je Spalte eine eigene Kennung.
    """
    namen = [gruppenname(g, mit_einheit, einheit) for g in gruppen]
    mehrfach = {name for name in namen if namen.count(name) > 1}
    return [f"{name} (PM {g[0]['pm_id']})" if name in mehrfach else name
            for name, g in zip(namen, gruppen)]


# Wo der einmal gelesene Bereich einer Linie liegt. Er wird bei jeder
# Zeile und jeder Neubewertung gebraucht - bei dreihundert Zeilen ist das
# fuenfzigtausend mal derselbe Text in dieselbe Zahl.
_BEREICH = "_bereich_gelesen"


def _bereich(welle) -> tuple:
    """Der Konzentrationsbereich einer Messlinie aus PM_WELLEN.

    Einmal gelesen, dann gemerkt: PM_WELLEN steht fuer den ganzen Lauf
    fest, und das Umwandeln kostete mehr als die Auswertung selbst.
    """
    gelesen = welle.get(_BEREICH)
    if gelesen is None:
        gelesen = (lims_db.als_zahl(welle.get("ugrenze")),
                   lims_db.als_zahl(welle.get("ogrenze")))
        welle[_BEREICH] = gelesen
    return gelesen


def im_bereich(welle, wert) -> bool:
    """Liegt der Wert im Konzentrationsbereich dieser Messlinie?

    Eine offene Grenze - UGRENZE oder OGRENZE leer - begrenzt nicht: die
    Linie gilt dann nach unten beziehungsweise oben unbeschraenkt.
    """
    unten, oben = _bereich(welle)
    if unten is not None and wert < unten:
        return False
    return not (oben is not None and wert > oben)


def zustaendige_linie(linien: list[dict], wert):
    """Welche Messlinie gilt fuer diesen Wert?

    Misst ein Geraet denselben Parameter auf mehreren Linien - zwei
    Calciumlinien etwa -, gehoeren sie derselben Pruefmethode und damit
    derselben Ergebniszeile. Welche zustaendig ist, entscheidet der
    Konzentrationsbereich aus PM_WELLEN: jede Linie gilt in dem Bereich,
    fuer den sie kalibriert ist.

    Liegt der Wert genau auf der Grenze zwischen zwei Bereichen, gewinnt
    der obere. Sonst faenge die Zuordnung an der Nahtstelle an zu
    schwanken, je nachdem wie gerundet wurde.

    Liegt der Wert ausserhalb aller Bereiche, gilt die naechstgelegene
    Linie. Der Bereich waehlt *zwischen* Linien aus, er verwirft keine
    Werte - sonst fiele jeder Blindwert nahe null heraus, und ob ein Wert
    ueberhaupt im Arbeitsbereich liegt, sagen die Verfahrenskenndaten
    (Kuerzel O), nicht PM_WELLEN.

    Gibt es nur eine Linie, ist sie zustaendig - dann braucht es keinen
    Bereich.
    """
    if len(linien) == 1:
        return linien[0]
    if wert is None:
        return None
    bewertet = [(_abstand(w, wert), _rangordnung(w), w) for w in linien]
    naechste = min(e[0] for e in bewertet)
    kandidaten = [e for e in bewertet if e[0] == naechste]
    # Decken mehrere Bereiche den Wert ab, gewinnt der hoehere - erst nach
    # der unteren Grenze, dann nach der oberen.
    hoechster = max(e[1] for e in kandidaten)
    gleichauf = [e for e in kandidaten if e[1] == hoechster]
    # Zwei Linien mit genau demselben Bereich sind nicht zu unterscheiden.
    # Das ist ein Pflegefehler, kein Grenzfall - dann lieber keine
    # Zustaendigkeit als eine nach Listenreihenfolge.
    return gleichauf[0][2] if len(gleichauf) == 1 else None


def _abstand(welle, wert):
    """Wie weit liegt der Wert vom Bereich dieser Linie? Null, wenn darin."""
    unten, oben = _bereich(welle)
    if unten is not None and wert < unten:
        return unten - wert
    if oben is not None and wert > oben:
        return wert - oben
    return decimal.Decimal(0)


def _rangordnung(welle) -> tuple:
    """Wie hoch der Bereich einer Linie liegt - untere Grenze, dann obere.

    Damit laesst sich "der hoehere Bereich" vergleichen. Eine fehlende
    untere Grenze zaehlt als ganz unten, eine fehlende obere als ganz
    oben: eine Linie ohne Untergrenze faengt bei null an, eine ohne
    Obergrenze reicht beliebig weit hinauf.
    """
    unten, oben = _bereich(welle)
    return (unten if unten is not None else decimal.Decimal("-Infinity"),
            oben if oben is not None else decimal.Decimal("Infinity"))



def zustaendig_in_zeile(gruppe: list[dict], zahlen: dict, zeilennummer,
                        linienwahl=None):
    """Die Linie, die in dieser Zeile fuer dieses Element gilt.

    `zahlen` bildet PMWE_ID auf den Messwert der Linie ab. Beurteilt wird
    jede Linie an *ihrem eigenen* Wert: zustaendig ist die, deren Wert in
    ihren eigenen Bereich faellt.

    Von Hand gewaehlt schlaegt den Bereich - wer umschaltet, hat einen
    Grund, den die Bereiche nicht kennen. Kaeme sonst mehr als eine Linie
    in Frage oder keine, gibt es keine Zustaendigkeit: geraten wird nicht.
    """
    if len(gruppe) == 1:
        return gruppe[0]
    wahl = (linienwahl or {}).get((zeilennummer, gruppe[0]["pm_id"],
                                   gruppe[0]["pm_ver"]))
    if wahl is not None:
        gewaehlt = [w for w in gruppe if w["pmwe_id"] == wahl]
        if gewaehlt:
            return gewaehlt[0]
    eigen = [w for w in gruppe
             if zustaendige_linie(gruppe, zahlen.get(w["pmwe_id"])) is w]
    if len(eigen) == 1:
        return eigen[0]
    if not eigen:
        # Keine Linie faellt in ihren eigenen Bereich. Hat nur eine
        # ueberhaupt eine Zahl geliefert - die andere "N/A" -, dann ist es
        # diese: eine gemessene Zahl ist mehr wert als eine Kalibrierung,
        # die knapp danebenliegt. Leer bleibt die Zelle erst, wenn keine
        # Linie eine Zahl hat.
        eigen = [w for w in gruppe if zahlen.get(w["pmwe_id"]) is not None]
        if len(eigen) == 1:
            return eigen[0]
    if not eigen:
        return None
    # Mehrere Linien kommen in Frage - etwa weil sich ihre Messwerte nur
    # wenig unterscheiden und jede in ihren eigenen Bereich faellt
    # (Ca 2-1000 misst 2,5 - Ca 0-2 misst 1,9). Dann gilt der hoehere
    # Bereich: er ist fuer diese Groessenordnung kalibriert, der untere
    # endet hier gerade.
    hoechster = max(_rangordnung(w) for w in eigen)
    treffer = [w for w in eigen if _rangordnung(w) == hoechster]
    return treffer[0] if len(treffer) == 1 else None


def verdichten(wellen: list[dict], zeilen: list[dict], linienwahl=None):
    """Macht aus den Messlinien je Element eine Spalte.

    Zurueck kommen die Stellvertreterlinien und die Zeilen mit je einem
    Wert pro Element. Gibt es keine zustaendige Linie, bleibt der Platz
    leer und traegt den Grund - eine Linie zu greifen, nur damit die
    Spalte gefuellt ist, waere die falsche Zahl.
    """
    gruppen = methodengruppen(wellen)
    stelle = {w["pmwe_id"]: nummer for nummer, w in enumerate(wellen)}
    verdichtet = []
    for eintrag in zeilen:
        zahlen = {w["pmwe_id"]: eintrag["werte"][stelle[w["pmwe_id"]]]["zahl"]
                  for w in wellen}
        werte = []
        for gruppe in gruppen:
            welle = zustaendig_in_zeile(gruppe, zahlen, eintrag["zeile"],
                                        linienwahl)
            # Welche Linie den Wert geliefert hat, bleibt am Wert
            # haengen: nur damit laesst sich eine Zelle spaeter wieder
            # ansprechen - etwa um sie von Hand auszuschliessen.
            werte.append(
                dict(eintrag["werte"][stelle[welle["pmwe_id"]]],
                     pmwe_id=welle["pmwe_id"])
                if welle is not None else
                {"roh": "", "zahl": None, "rechnung": "", "pmwe_id": None,
                 "grund": "keine Linie dieser Pruefmethode ist "
                          "eindeutig zustaendig"})
        verdichtet.append(dict(eintrag, werte=werte))
    return [g[0] for g in gruppen], verdichtet


# ==========================================================================
# Messwerte aus der Laufdatei
# ==========================================================================
#
# Dieselben Achsen wie die Matrix - Messlinien in den Spalten, Zeilen der
# Laufdatei in den Zeilen -, aber mit den gemessenen Zahlen statt der
# Pruefmethode. Die Zahlen kommen aus der Datei, nicht aus dem LIMS: sie
# sind noch nicht dort. Der Kontext bestimmt nur, *welche* Zeilen und
# Spalten ueberhaupt dazugehoeren.

# Womit gerechnet wird, wenn TEILPROBEN keinen Endfaktor fuehrt: die Eins
# laesst den Messwert, wie er ist.
ENDFAKTOR_EINS = decimal.Decimal(1)

# Die Probenarten des LIMS (PROBENART.ID). Eingewogen und verbrannt werden
# Boden, Pflanze und Humus; Wasser steht hier nur der Vollstaendigkeit
# halber, es wird nicht eingewogen.
PROBENARTEN = ((1, "Boden"), (2, "Pflanze"), (3, "Wasser"), (4, "Humus"))
PROBENARTNAMEN = dict(PROBENARTEN)

# In diesen Schritten wird eingewogen, in Milligramm. Sie stehen im
# Reiter Verduennungen: liegt ein Wert ueber der Obergrenze, wird nicht
# verduennt, sondern weniger eingewogen.
EINWAAGE_STUFEN = (20, 50, 100, 200, 500)

# Was ueblicherweise eingewogen wird, in Gramm - je Probenart.
SOLLEINWAAGE_VORGABE = {1: "1,0", 2: "0,070", 4: "0,070"}
# Auf welchen Bruchteil der Solleinwaage sich die Kalibrierung bezieht:
# bei Boden auf ein Achtel, bei Pflanze und Humus auf das Doppelte.
BEZUGSFAKTOR_VORGABE = {1: "8", 2: "0,5", 4: "0,5"}
# Wieviel ueber der zurueckgerechneten Obergrenze noch durchgeht, in
# Prozent. Die Obergrenze entsteht hier aus zwei Rechenschritten - Bezug
# auf die Solleinwaage und dann auf die tatsaechliche Einwaage -, und eine
# Probe knapp darueber deswegen zu verwerfen waere zu streng.
#
# Sie gilt *nur* fuer die Feststoffverbrennung. Ueberall sonst ist die
# Obergrenze aus den Verfahrenskenndaten die Grenze, und ein Wert
# darueber liegt ausserhalb des Arbeitsbereichs - da gibt es nichts zu
# tolerieren.
OGRENZE_TOLERANZ = decimal.Decimal("20")

# Was eine Einwaage in Gramm bedeutet, je nach Einheit der Spalte.
EINWAAGE_EINHEITEN = (("mg", decimal.Decimal(1000)),
                      ("g", decimal.Decimal(1)))

# Gramm in Milligramm - die Solleinwaage wird in Gramm gepflegt, die
# Einwaagestufen sind Milligramm.
MILLIGRAMM = decimal.Decimal(1000)


class Feststoff:
    """Die Einstellungen einer Feststoffverbrennung - je Geraet.

    Bei einer Verbrennung wird die Probe eingewogen, nicht verduennt. Wer
    mehr einwiegt, misst empfindlicher: die halbe Solleinwaage bedeutet
    doppelt so hohe Nachweis- und Bestimmungsgrenzen, und ebenso eine
    doppelt so hohe Obergrenze - der Ofen sieht dieselbe absolute Menge,
    nur bezogen auf weniger Probe.

    Der Faktor ist deshalb Solleinwaage durch tatsaechliche Einwaage. Er
    verhaelt sich zu den Grenzen wie ein Verduennungsfaktor - aber *nicht*
    zum Messwert: das Geraet gibt den Gehalt der Probe schon richtig aus.
    Deshalb stehen in Ergebnis und ErgVFak dieselben Zahlen.
    """

    def __init__(self, aktiv=False, spalte="", einheit="mg", soll=None,
                 ogrenzen=None, toleranz=None):
        self.aktiv = bool(aktiv)
        # Die Spalte der Laufdatei mit der Einwaage - zugewiesen wie die
        # Spalte mit dem Verduennungsfaktor.
        self.spalte = str(spalte or "").strip()
        self.einheit = einheit if einheit in dict(EINWAAGE_EINHEITEN) else "mg"
        # {PART_ID: Solleinwaage in Gramm}
        self.soll = {int(art): decimal.Decimal(str(wert))
                     for art, wert in (soll or {}).items()
                     if _zahl_brauchbar(wert)}
        # {(PART_ID, Parameter): (Wert der Kalibrierung, Bezugsfaktor)}
        self.ogrenzen = dict(ogrenzen or {})
        # Wieviel Prozent ueber der zurueckgerechneten Obergrenze noch
        # durchgehen.
        self.toleranz = (OGRENZE_TOLERANZ if toleranz is None
                         else decimal.Decimal(str(toleranz)))

    @property
    def teiler(self):
        """Womit die Spalte in Gramm umzurechnen ist."""
        return dict(EINWAAGE_EINHEITEN)[self.einheit]

    def gleicht(self, anderer) -> bool:
        """Stehen hier dieselben Einstellungen wie dort?

        Gebraucht, wenn die Optionen gespeichert werden: nur bei einer
        echten Aenderung wird ein offener Lauf neu gerechnet.
        """
        if anderer is None:
            return False
        return (self.aktiv == anderer.aktiv
                and self.spalte == anderer.spalte
                and self.einheit == anderer.einheit
                and self.soll == anderer.soll
                and self.ogrenzen == anderer.ogrenzen
                and self.toleranz == anderer.toleranz)

    def solleinwaage(self, part_id):
        """Die Solleinwaage dieser Probenart in Gramm - oder None."""
        try:
            return self.soll.get(int(part_id))
        except (TypeError, ValueError):
            return None

    def solleinwaage_mg(self, part_id):
        """Die Solleinwaage dieser Probenart in Milligramm.

        Gepflegt wird sie in Gramm, und der Vorschlag im Reiter
        Neueinwaagen rechnet in Milligramm - die Stufen sind 20 bis 500
        mg. Die Einheit der *Spalte* hat damit nichts zu tun: sie sagt
        nur, wie die Laufdatei ihre Einwaage schreibt. Sie hier
        einzurechnen war ein Fehler - bei einer Spalte in Gramm kam als
        Solleinwaage 1 mg heraus und damit nie ein Vorschlag.
        """
        soll = self.solleinwaage(part_id)
        return None if soll is None else soll * MILLIGRAMM

    def faktor(self, part_id, einwaage):
        """Solleinwaage durch tatsaechliche Einwaage.

        `einwaage` ist die Zahl aus der Laufdatei, in der eingestellten
        Einheit. Fehlt eine der beiden Zahlen, kommt None zurueck - dann
        wird nicht gerechnet, statt eine Eins zu erfinden, die eine
        Aussage waere.
        """
        soll = self.solleinwaage(part_id)
        if soll is None or einwaage is None or einwaage <= 0:
            return None
        return soll / (decimal.Decimal(einwaage) / self.teiler)

    def ogrenze(self, part_id, parameter):
        """Die Obergrenze bei Solleinwaage - Wert durch Bezugsfaktor.

        Kalibriert wird nicht mit der Solleinwaage, sondern mit einem
        Bruchteil davon: steht in der Pflege 41,0 bei Bezugsfaktor 10,
        gilt bei voller Solleinwaage 4,10.

        Ohne Haken gilt nichts davon: dann bleibt es bei der Obergrenze
        aus den Verfahrenskenndaten, auch wenn hier noch Eintraege von
        einem frueheren Versuch stehen.
        """
        if not self.aktiv:
            return None
        eintrag = self.ogrenzen.get((_ganzzahl(part_id),
                                     str(parameter or "").strip()))
        if not eintrag:
            return None
        wert, bezug = eintrag
        if wert is None or not bezug:
            return None
        return decimal.Decimal(str(wert)) / decimal.Decimal(str(bezug))

    def ogrenzentext(self, part_id, parameter) -> str:
        """Wie die Obergrenze zustande kommt - Zahl fuer Zahl.

        Zum Nachrechnen im Hinweis: zwischen dem, was in den Optionen
        steht, und der Grenze dieser Probe liegen drei Schritte, und wer
        einen davon anzweifelt, soll ihn sehen.
        """
        eintrag = self.ogrenzen.get((_ganzzahl(part_id),
                                     str(parameter or "").strip()))
        if not self.aktiv or not eintrag:
            return ""
        wert, bezug = eintrag
        return (f"Obergrenze aus den Optionen: {_knapp(wert)} / "
                f"{_knapp(bezug)} (Bezug) = "
                f"{_knapp(self.ogrenze(part_id, parameter))} bei "
                f"Solleinwaage, mit {_knapp(self.toleranz)}% Toleranz "
                f"{_knapp(self.ogrenze_erlaubt(part_id, parameter))}")

    def ogrenze_erlaubt(self, part_id, parameter):
        """Die Obergrenze samt Toleranz - bis hierher gilt ein Wert noch.

        Zwei Rechenschritte liegen dazwischen (Bezug auf die
        Solleinwaage, dann auf die Einwaage dieser Probe); eine Probe
        knapp darueber deswegen zu verwerfen waere zu streng.
        """
        grenze = self.ogrenze(part_id, parameter)
        if grenze is None:
            return None
        return grenze * (1 + self.toleranz / 100)


def _knapp(wert) -> str:
    """Eine Zahl ohne angehaengte Nullen - fuer Rechenwege im Hinweis.

    Dort stehen gepflegte Zahlen (41,1 / 10 / 20%), und "41,100 / 10,000"
    liest sich wie eine Genauigkeit, die niemand eingegeben hat.
    """
    if wert is None:
        return ""
    gekuerzt = decimal.Decimal(wert).normalize()
    if gekuerzt == gekuerzt.to_integral_value():
        gekuerzt = gekuerzt.quantize(decimal.Decimal(1))
    return format(gekuerzt, "f").replace(".", ",")


def _ganzzahl(wert):
    try:
        return int(wert)
    except (TypeError, ValueError):
        return None


def _zahl_brauchbar(wert) -> bool:
    try:
        decimal.Decimal(str(wert))
    except (decimal.InvalidOperation, TypeError, ValueError):
        return False
    return True

NACHKOMMA_ANZEIGE = 3       # so viele Stellen stehen in der Zelle
NACHKOMMA_HINWEIS = 5       # so viele zeigt der Hinweis beim Ueberfahren


def zahltext(wert, stellen: int) -> str:
    """Formatiert eine Zahl mit fester Stellenzahl und Dezimalkomma.

    'f' statt der Vorgabe, damit sehr kleine Werte nicht in die
    Exponentialschreibweise kippen - "1E-7" liest im Labor niemand gern.
    """
    if wert is None:
        return ""
    schritt = decimal.Decimal(1).scaleb(-stellen)
    gerundet = wert.quantize(schritt, rounding=decimal.ROUND_HALF_UP)
    return format(gerundet, "f").replace(".", ",")


# Kleine Zahlen brauchen mehr Stellen: bei 0,0007 sagt die dritte
# Nachkommastelle nichts mehr, waehrend 12,345 mit fuenf Stellen nur
# unruhig aussieht. Deshalb wird die Stellenzahl gestaffelt.
NACHKOMMA_KLEIN = ((decimal.Decimal("0.01"), 5),
                   (decimal.Decimal("0.1"), 4))


def zahltext_gestuft(wert, stellen: int = NACHKOMMA_ANZEIGE) -> str:
    """Wie zahltext, aber mit mehr Stellen fuer kleine Werte."""
    if wert is None:
        return ""
    betrag = abs(wert)
    for grenze, mehr in NACHKOMMA_KLEIN:
        if betrag < grenze:
            return zahltext(wert, max(stellen, mehr))
    return zahltext(wert, stellen)


# Die festen Spalten links, vor den Messlinien. Der Verduennungsfaktor
# steht dabei, weil sich ein Messwert ohne ihn nicht beurteilen laesst -
# er gehoert neben den Wert, nicht in einen anderen Reiter.
# "V" statt "Verduennungsfaktor": ausgeschrieben ist die Spalte breiter
# als ihr Inhalt und schiebt die Elemente nach rechts.
KOPFSPALTEN = ("Zeile", "Bezeichnung", "Art", "V")


class Messung:
    """Gemessene Werte, aufgespannt wie die Matrix."""

    # Die Kopfspalten tragen keine Messwerte.
    VORSPANN = len(KOPFSPALTEN)

    def __init__(self, wellen, zeilen, ohne_spalte, verduennung=None):
        self.wellen = wellen
        self.zeilen = zeilen            # je Zeile der Laufdatei ein Eintrag
        self.ohne_spalte = ohne_spalte
        self.verduennung = verduennung  # Verduennung oder None

    @property
    def spalten(self) -> list[str]:
        """Die Kennungen der Spalten - je Messlinie eine eindeutige."""
        return list(KOPFSPALTEN) + [beschriftung(w) for w in self.wellen]

    @property
    def ueberschriften(self) -> list[str]:
        """Was ueber den Spalten steht: der Parametername allein.

        "Cdges" liest sich besser als "Cdges (111Cd (mp_KED-H2))". Nur wo
        derselbe Parameter mehrfach gemessen wird - drei Bleilinien etwa -,
        kommt die Linie dazu, sonst staenden dort drei gleiche Ueberschriften
        und niemand wuesste, welche Spalte welche ist.
        """
        return list(KOPFSPALTEN) + kurzbeschriftungen(self.wellen,
                                                     mit_einheit=True)

    def tabelle(self) -> list[list]:
        """Die Zellen, wie sie angezeigt werden - auf drei Stellen gerundet."""
        return [[eintrag["zeile"], eintrag["bezeichnung"], eintrag["art"],
                 eintrag["verduennung"]]
                + [zahltext(wert["zahl"], NACHKOMMA_ANZEIGE)
                   if wert["zahl"] is not None else wert["roh"]
                   for wert in eintrag["werte"]]
                for eintrag in self.zeilen]

    def hinweis(self, zeile: int, spalte: int) -> str:
        """Der genauere Wert beim Ueberfahren einer Zelle.

        Die Anzeige ist gerundet; ohne den genauen Wert liesse sich spaeter
        nicht nachvollziehen, warum ein Ergebnis eine Grenze gerissen hat.
        Mitgezeigt wird auch der Rohtext aus der Datei - er ist die
        eigentliche Wahrheit, alles andere ist schon Umrechnung.
        """
        if not 0 <= zeile < len(self.zeilen):
            return ""
        nummer = spalte - self.VORSPANN
        if not 0 <= nummer < len(self.wellen):
            return ""
        return _hinweistext(self.zeilen[zeile]["werte"][nummer])


def _mit_abzug(wert: dict, eintragung) -> dict:
    """Setzt den um den Blindwert bereinigten Wert in die Zelle.

    Die Rechnung selbst steht in blindwert.py und ist schon geschehen -
    hier wird nur uebernommen, was dort herauskam, damit das Blatt
    dieselbe Zahl zeigt wie Ergebnis und ErgVFak.
    """
    if eintragung is None or wert.get("zahl") is None:
        return wert
    zahl, mittel, anzahl = eintragung
    return dict(wert, zahl=zahl, blindwert=mittel, blindwert_anzahl=anzahl)


def _hinweistext(wert: dict) -> str:
    """Der genauere Wert samt Herkunft - fuer jede gerundete Anzeige derselbe.

    Steht eine Rechnung dabei, wird sie gezeigt statt des Rohtexts: wer
    eine umgerechnete Konzentration bewertet, muss sehen, wie sie zustande
    kam, sonst bleibt ein falscher Faktor unentdeckt.
    """
    if wert["zahl"] is None:
        return wert.get("grund") or wert["roh"]
    genau = zahltext(wert["zahl"], NACHKOMMA_HINWEIS)
    teile = [genau]
    if wert.get("rechnung"):
        teile.append(wert["rechnung"])
    elif wert["roh"].strip() != genau:
        teile.append(f"in der Datei: {wert['roh']}")
    # Wo ein Sollwert gepflegt ist, steht die Wiederfindung dabei - in
    # beiden Ansichten, damit der Umschalter keine Zahl verbirgt.
    gefunden = Standardblatt.wiederfindung(wert)
    if gefunden is not None:
        teile.append(f"Sollwert {zahltext(wert['soll'], NACHKOMMA_HINWEIS)}"
                     f" - Wiederfindung {zahltext(gefunden, 1)} %")
    # Steht hier ein korrigierter Wert, gehoert die Rechnung dazu: sonst
    # stuende im Blatt eine andere Zahl als in der Datei, ohne Grund.
    if wert.get("blindwert") is not None:
        anzahl = wert.get("blindwert_anzahl", 0)
        teile.append(
            f"Blindwert abgezogen: "
            f"{zahltext(wert['blindwert'], NACHKOMMA_HINWEIS)} "
            f"(Mittel aus {anzahl} Blindwert"
            f"{'en' if anzahl != 1 else ''})")
    return "\n".join(teile)


def _spaltennummern(laufdatei, wellen) -> list[int]:
    """Zu jeder Messlinie die Spalte der Laufdatei."""
    nach_kopf = {}
    for nummer, kopf in enumerate(laufdatei.spalten):
        nach_kopf.setdefault(spaltenkopf(kopf), nummer)
    return [nach_kopf[spaltenkopf(w["pm_code"])] for w in wellen]


def _wert(zeile, nummer: int) -> dict:
    roh = str(zeile[nummer]).strip() if nummer < len(zeile) \
        and zeile[nummer] is not None else ""
    return {"roh": roh, "zahl": lims_db.als_zahl(roh)}


def messung(kontext, laufdatei, verduennung_spalte="") -> Messung:
    """Die Messwerte des Laufs, auf das Beauftragte eingegrenzt.

    Zeilen sind die Zeilen der Laufdatei in ihrer Reihenfolge - Proben und
    Standards, nichts sonst. Was sich nicht zuordnen liess, faellt heraus;
    die Reihenfolge bleibt, weil sie im Lauf etwas bedeutet (eine
    Wiederholung steht hinter ihrer Probe, ein Blindwert vor einem Block).

    Spalten sind die Messlinien, die auch in ERGEBNISSE vorkommen - der
    Zuschnitt der Matrix. Was das Geraet zusaetzlich misst, ist in diesem
    Lauf nicht beauftragt und gehoert nicht in die Auswertung.
    """
    raster = matrix(kontext, laufdatei)
    nummern = _spaltennummern(laufdatei, raster.wellen)
    faktoren = verduennung(kontext, laufdatei, verduennung_spalte)
    nach_zeile = {z["zeile"]: z["roh"] for z in faktoren.zeilen}
    # Warum ein Faktor fehlt, gehoert an die Zelle: dort wird er gebraucht.
    gruende = {z["zeile"]: z.get("grund", "") for z in faktoren.zeilen}
    zeilen = []
    for eintrag in ausgewertete(zuordnen(kontext, laufdatei)):
        datei_zeile = laufdatei.zeilen[eintrag["zeile"] - 1]
        zeilen.append({
            "zeile": eintrag["zeile"],
            "bezeichnung": (probenanzeige(eintrag["bezeichnung"])
                            if eintrag["art"] == PROBE
                            else eintrag["bezeichnung"]),
            "art": artkuerzel(eintrag["typ"]) or eintrag["art"],
            "probe": eintrag["probe"],
            "standard": eintrag["standard"],
            "stan_id": eintrag["stan_id"],
            "wiederholung": eintrag["wiederholung"],
            "verduennung": nach_zeile.get(eintrag["zeile"], ""),
            "verduennung_grund": gruende.get(eintrag["zeile"], ""),
            "werte": [_wert(datei_zeile, nummer) for nummer in nummern],
        })
    return Messung(raster.wellen, zeilen, raster.ohne_spalte, faktoren)


# ==========================================================================
# Verduennungsfaktor
# ==========================================================================
#
# Er steht bei manchen Geraeten in der Laufdatei, bei anderen nicht - und
# wo er steht, heisst die Spalte je Geraet anders ("Sample List - Total
# Dilution Factor" beim ICP-MS). Deshalb ist der Spaltenname eine
# Einstellung je Geraet und keine Konstante. Bleibt sie leer, kommt der
# Faktor nicht aus der Datei und muss anders erfasst werden.

class Verduennung:
    """Der Verduennungsfaktor je Zeile der Laufdatei."""

    def __init__(self, spaltenname, spaltennummer, zeilen, feststoff=False):
        self.spaltenname = spaltenname
        self.spaltennummer = spaltennummer
        self.zeilen = zeilen
        self.genau = True           # exakt getroffen oder nachsichtig?
        self.koepfe = []            # die Spaltenkoepfe der Laufdatei
        # Bei einer Feststoffverbrennung steht in der Spalte die Einwaage
        # und der Faktor wird daraus gerechnet - die Tabelle zeigt dann
        # beides.
        self.feststoff = bool(feststoff)

    @property
    def automatisch(self) -> bool:
        """Kein Spaltenname eingestellt - der Faktor kommt nicht aus der Datei."""
        return not self.spaltenname

    @property
    def gefunden(self) -> bool:
        return self.spaltennummer is not None

    @property
    def spalten(self) -> list[str]:
        if self.feststoff:
            return ["Zeile", "Bezeichnung", "Art", "Probenart", "Einwaage",
                    "Solleinwaage", "Faktor"]
        return ["Zeile", "Bezeichnung", "Art", "Verduennungsfaktor"]

    def tabelle(self) -> list[list]:
        if self.feststoff:
            return [[z["zeile"], z["bezeichnung"], z["art"],
                     z.get("probenart", ""), z.get("einwaage", ""),
                     z.get("soll", ""), z["roh"]] for z in self.zeilen]
        return [[z["zeile"], z["bezeichnung"], z["art"], z["roh"]]
                for z in self.zeilen]

    def zusammenfassung(self) -> str:
        if self.feststoff:
            return self._feststofftext()
        if self.automatisch:
            return ("Kein Spaltenname eingestellt - es gilt durchgehend "
                    "Faktor 1, also unverduennt. Der Name steht im Reiter "
                    "Optionen des Startbildschirms.")
        if not self.gefunden:
            # Die vorhandenen Koepfe dazusagen: so laesst sich der Eintrag
            # in den Optionen ohne Suchen richtigstellen.
            return (f"Die Spalte \u201e{self.spaltenname}\u201c steht nicht "
                    f"in dieser Laufdatei - es gilt Faktor 1. Vorhanden "
                    f"sind: {' | '.join(self.koepfe[:8])}"
                    f"{' ...' if len(self.koepfe) > 8 else ''}")
        text = (f"Spalte \u201e{self.spaltenname}\u201c - "
                f"{len(self.zeilen)} Zeilen")
        if not self.genau:
            text += (" (nachsichtig getroffen - Gross-/Kleinschreibung oder "
                     "Leerzeichen weichen ab)")
        if all(z["zahl"] == decimal.Decimal(1) for z in self.zeilen):
            text += " - durchgehend Faktor 1"
        return text

    def _feststofftext(self) -> str:
        """Was bei einer Feststoffverbrennung oben steht."""
        if not self.spaltenname:
            return ("Feststoffverbrennung, aber keine Spalte mit der "
                    "Einwaage eingestellt - es gilt durchgehend Faktor 1. "
                    "Der Spaltenname steht in den Geraeteoptionen.")
        if not self.gefunden:
            return (f"Die Spalte \u201e{self.spaltenname}\u201c mit der "
                    f"Einwaage steht nicht in dieser Laufdatei - es gilt "
                    f"Faktor 1. Vorhanden sind: "
                    f"{' | '.join(self.koepfe[:8])}"
                    f"{' ...' if len(self.koepfe) > 8 else ''}")
        ohne = [z for z in self.zeilen if not z.get("soll")]
        gewogen = [z for z in self.zeilen
                   if z.get("soll") and not z.get("einwaage")]
        text = (f"Einwaage aus \u201e{self.spaltenname}\u201c - Faktor ist "
                f"Solleinwaage durch Einwaage; er hebt Nachweis-, "
                f"Bestimmungs- und Obergrenze, den Messwert laesst er "
                f"unberuehrt ({len(self.zeilen)} Zeilen)")
        if not self.genau:
            text += (" (nachsichtig getroffen - Gross-/Kleinschreibung oder "
                     "Leerzeichen weichen ab)")
        if ohne:
            text += (f" - fuer {len(ohne)} Zeilen fehlt die Solleinwaage "
                     f"ihrer Probenart, dort gilt Faktor 1")
        if gewogen:
            # Das ist der schwerere Fall: ohne Einwaage sind die Grenzen
            # dieser Probe unbekannt, und der Wert steht trotzdem da.
            text += (f" - {len(gewogen)} Zeilen ohne Einwaage: "
                     + ", ".join(z["bezeichnung"] for z in gewogen[:5])
                     + (" ..." if len(gewogen) > 5 else ""))
        return text


def _spalte_finden(laufdatei, name: str):
    """Sucht die Spalte zum eingestellten Namen.

    Zuerst genau, dann nachsichtig: ohne Ruecksicht auf Gross- und
    Kleinschreibung und auf doppelte Leerzeichen. Ein aus dem Geraet
    kopierter Name unterscheidet sich sonst an einer Stelle, die niemand
    sieht, und der Faktor bliebe stumm weg.
    """
    gesucht = spaltenkopf(name)
    koepfe = [spaltenkopf(kopf) for kopf in laufdatei.spalten]
    if gesucht in koepfe:
        return koepfe.index(gesucht), True
    entschaerft = " ".join(gesucht.lower().split())
    for stelle, kopf in enumerate(koepfe):
        if " ".join(kopf.lower().split()) == entschaerft:
            return stelle, False
    return None, True


def verduennung(kontext, laufdatei, spaltenname: str) -> Verduennung:
    """Liest den Verduennungsfaktor aus der eingestellten Spalte.

    Gesucht wird wie bei den Messlinien nach dem gekuerzten Spaltenkopf,
    damit ein angehaengtes " - Value" nicht stoert.

    Liefert das Geraet keinen Faktor - keine Spalte eingestellt, oder ein
    leeres Feld -, gilt 1: dann wurde nicht verduennt. Das ist der
    Normalfall bei vielen Geraeten und kein Mangel.

    Bei einer Feststoffverbrennung steht in der Spalte keine Verduennung,
    sondern die Einwaage; der Faktor wird daraus gerechnet.
    """
    if getattr(kontext, "feststoff", None) is not None \
            and kontext.feststoff.aktiv:
        return _einwaage(kontext, laufdatei)
    name = str(spaltenname or "").strip()
    nummer, genau = (None, True)
    if name:
        nummer, genau = _spalte_finden(laufdatei, name)
    zeilen = []
    for eintrag in ausgewertete(zuordnen(kontext, laufdatei)):
        datei_zeile = laufdatei.zeilen[eintrag["zeile"] - 1]
        wert = _wert(datei_zeile, nummer) if nummer is not None \
            else {"roh": "", "zahl": None}
        if wert["zahl"] is None:
            # Kein Faktor heisst nicht verduennt.
            wert = {"roh": wert["roh"] or "1", "zahl": decimal.Decimal(1)}
        zeilen.append({"zeile": eintrag["zeile"],
                       "bezeichnung": (probenanzeige(eintrag["bezeichnung"])
                                       if eintrag["art"] == PROBE
                                       else eintrag["bezeichnung"]),
                       "art": artkuerzel(eintrag["typ"]) or eintrag["art"],
                       **wert})
    blatt = Verduennung(name, nummer, zeilen)
    blatt.genau = genau
    blatt.koepfe = [str(k).strip() for k in laufdatei.spalten]
    return blatt


def _einwaage(kontext, laufdatei) -> Verduennung:
    """Der Faktor einer Feststoffverbrennung: Solleinwaage durch Einwaage.

    Fehlt die Einwaage oder ist fuer die Probenart keine Solleinwaage
    gepflegt, gilt 1 - und dass es so ist, steht in der Zeile und in der
    Zusammenfassung. Geraten wird nichts: eine erfundene Einwaage
    verschoebe jede Grenze dieser Probe.
    """
    einstellung = kontext.feststoff
    name = einstellung.spalte
    nummer, genau = _spalte_finden(laufdatei, name) if name else (None, True)
    eins = decimal.Decimal(1)
    zeilen = []
    for eintrag in ausgewertete(zuordnen(kontext, laufdatei)):
        datei_zeile = laufdatei.zeilen[eintrag["zeile"] - 1]
        gewogen = (_wert(datei_zeile, nummer) if nummer is not None
                   else {"roh": "", "zahl": None})
        art = kontext.probenart(eintrag["probe"]) if eintrag["probe"] else None
        soll = einstellung.solleinwaage(art)
        faktor = einstellung.faktor(art, gewogen["zahl"])
        # Warum es keinen Faktor gibt, ist zweierlei: fuer diese
        # Probenart ist keine Solleinwaage gepflegt (dann ist an der
        # Zeile nichts auszusetzen), oder die Einwaage fehlt in der
        # Laufdatei - und dann sind die Grenzen dieser Probe unbekannt.
        grund = ""
        if faktor is None and soll is not None:
            grund = ("keine Einwaage in der Laufdatei - gerechnet mit "
                     "Faktor 1")
        elif faktor is None and eintrag["probe"]:
            grund = (f"keine Solleinwaage fuer die Probenart "
                     f"{PROBENARTNAMEN.get(_ganzzahl(art), art)} - "
                     f"gerechnet mit Faktor 1")
        zeilen.append({
            "grund": grund,
            "zeile": eintrag["zeile"],
            "bezeichnung": (probenanzeige(eintrag["bezeichnung"])
                            if eintrag["art"] == PROBE
                            else eintrag["bezeichnung"]),
            "art": artkuerzel(eintrag["typ"]) or eintrag["art"],
            "probenart": PROBENARTNAMEN.get(_ganzzahl(art), ""),
            "einwaage": gewogen["roh"],
            "soll": (f"{zahltext(soll * einstellung.teiler, 3)} "
                     f"{einstellung.einheit}" if soll is not None else ""),
            "zahl": eins if faktor is None else faktor,
            "roh": ("1" if faktor is None else zahltext(faktor, 4)),
        })
    blatt = Verduennung(name, nummer, zeilen, feststoff=True)
    blatt.genau = genau
    blatt.koepfe = [str(k).strip() for k in laufdatei.spalten]
    return blatt


# ==========================================================================
# Was im LIMS steht
# ==========================================================================
#
# Dieselben Zeilen und Spalten wie die Messung, aber mit dem Wert aus
# ERGEBNISSE.MW_ROH statt dem aus der Laufdatei. Nebeneinandergelegt zeigt
# sich, was schon gebucht ist und was noch nicht - und wo beide
# auseinanderlaufen.
#
# MW_ROH, nicht MW: der Rohwert ist das, was das Geraet geliefert hat, und
# damit das Gegenstueck zur Laufdatei. MW ist bereits mit Faktoren
# verrechnet und liesse sich nicht unmittelbar vergleichen.

class Datenbankabfrage:
    """Der gebuchte Rohwert je Probe und Messlinie."""

    VORSPANN = Messung.VORSPANN

    def __init__(self, wellen, zeilen, ohne_entsprechung=(),
                 kontrollstandards=0):
        # Je Element eine Spalte: ERGEBNISSE fuehrt ohnehin eine Zeile je
        # Pruefmethode, nicht je Messlinie - drei Bleilinien nebeneinander
        # zeigten dreimal denselben Wert.
        self.wellen = wellen
        self.zeilen = zeilen
        # Was nicht gezeigt wird, wird gezaehlt: eine stillschweigend
        # weggelassene Zeile ist genau die, nach der jemand sucht.
        self.ohne_entsprechung = list(ohne_entsprechung)
        self.kontrollstandards = kontrollstandards

    # Statt des Verduennungsfaktors steht hier die Probennummer aus dem
    # LIMS: sie sagt, welche Zeile ueberhaupt eine Entsprechung hat.
    KOPF = KOPFSPALTEN[:3] + ("PROB_ID",)

    @property
    def gruppen(self) -> list[list[dict]]:
        return methodengruppen(self.wellen)

    @property
    def spalten(self) -> list[str]:
        return list(self.KOPF) + gruppennamen(self.gruppen, mit_einheit=False)

    @property
    def ueberschriften(self) -> list[str]:
        # Nur der Parametername: hier steht, was im LIMS gebucht ist, und
        # das LIMS kennt die Messlinie nicht.
        return self.spalten

    def tabelle(self) -> list[list]:
        return [[z["zeile"], z["bezeichnung"], z["art"], z["prob_id"]]
                + list(z["werte"]) for z in self.zeilen]

    def zusammenfassung(self) -> str:
        gebucht = sum(1 for z in self.zeilen for w in z["werte"] if w)
        moeglich = len(self.zeilen) * len(self.gruppen)
        text = (f"{gebucht} von {moeglich} Rohwerten stehen im LIMS "
                f"(ERGEBNISSE.MW_ROH)")
        if self.kontrollstandards:
            text += (f" - {self.kontrollstandards} Kontrollstandardzeilen "
                     f"bleiben draussen, sie haben keine Ergebniszeile")
        if self.ohne_entsprechung:
            namen = self.ohne_entsprechung
            text += (f" - {len(namen)} Zeilen ohne Entsprechung im LIMS: "
                     f"{', '.join(namen[:6])}"
                     f"{' ...' if len(namen) > 6 else ''}")
        return text + "."


def datenbankwerte(kontext, laufdatei, verduennung_spalte="") -> Datenbankabfrage:
    """Legt neben jede Zeile der Messung, was dazu im LIMS gebucht ist.

    Gezeigt wird, was drueben eine Entsprechung hat: Proben,
    Standardmaterialien und Blindwertstandards, und von ihnen nur die
    Zeilen mit einer Ergebniszeile im LIMS. Kontrollstandards bleiben
    draussen - das LIMS fuehrt sie nicht in ERGEBNISSE, ihre Zeile waere
    immer leer und sagte nichts.

    Was wegfaellt, wird gezaehlt und in der Zusammenfassung genannt: eine
    Zeile, die klammheimlich verschwindet, ist genau die, nach der spaeter
    jemand sucht.
    """
    werte = messung(kontext, laufdatei, verduennung_spalte)
    gruppen = methodengruppen(werte.wellen)
    zeilen, ohne, kontrollstandards = [], [], 0
    for eintrag in werte.zeilen:
        if ist_kontrollstandard(eintrag["art"]):
            kontrollstandards += 1
            continue
        probe = eintrag["probe"]
        ergebnisse = []
        prob_id = ""
        for gruppe in gruppen:
            welle = gruppe[0]
            treffer = (kontext.ergebnis(probe, welle["pm_id"], welle["pm_ver"])
                       if probe else None)
            if treffer is None:
                ergebnisse.append("")
                continue
            prob_id = prob_id or treffer["prob_id"]
            # Als Zahl gesetzt, nicht als Rohtext: das LIMS speichert
            # MW_ROH als Text, und fuenfzehn Stellen nebeneinander sind
            # nicht zu lesen. Bleibt es unlesbar, steht der Text da.
            zahl = lims_db.als_zahl(treffer["mw_roh"])
            ergebnisse.append(zahltext_gestuft(zahl) if zahl is not None
                              else lims_db.als_text(treffer["mw_roh"]))
        if not prob_id:
            ohne.append(eintrag["bezeichnung"])
            continue
        zeilen.append({"zeile": eintrag["zeile"],
                       "bezeichnung": eintrag["bezeichnung"],
                       "art": eintrag["art"], "prob_id": prob_id,
                       "werte": ergebnisse})
    return Datenbankabfrage(werte.wellen, zeilen, ohne, kontrollstandards)


# ==========================================================================
# Kontrollstandards
# ==========================================================================
#
# Der Ausschnitt aus der Messung, an dem sich zuerst zeigt, ob ein Lauf
# taugt. Deshalb ein eigener Reiter: zwischen hundert Probenzeilen gehen
# die paar Kontrollstandards unter, und genau sie will man nebeneinander
# sehen.

class Standardblatt:
    """Standards eines Typs mit ihren Werten je Element.

    Gruppiert nach Standard, weil man einen Standard ueber seine
    Wiederholungen hinweg liest - nicht die Zeilen der Reihe nach.
    """

    VORSPANN = 3

    def __init__(self, wellen, gruppen, bezeichnung, rechnungstext="",
                 einheit=EINHEIT_LOESUNG):
        self.wellen = wellen
        self.gruppen = gruppen      # [(Name, [Zeilen]), ...] in Lauf-Reihenfolge
        self.bezeichnung = bezeichnung
        self.rechnungstext = rechnungstext
        # Welche Einheit ueber den Zahlen steht - mit dem Endfaktor wird
        # aus der Konzentration ein Gehalt.
        self.einheit = einheit
        # Untereinander statt nebeneinander: so ist jede Tabelle so breit
        # wie das Fenster und zeigt alle Parameter. Eine halbe
        # Fensterbreite reicht dafuer bei einem Dutzend Elementen nicht.
        self.einspaltig = True
        # Statt des Gehalts die Wiederfindung in Prozent - dieselben
        # Zahlen, anders gelesen: gegen ein Zertifikat vergleicht man
        # nicht 39,2 mit 40,0, sondern liest 98,0 %.
        self.prozent = False

    @property
    def kopf(self) -> tuple:
        # Die Zeilennummer der Laufdatei steht vorn: an ihr laesst sich
        # eine Auffaelligkeit im Lauf wiederfinden - die laufende Nummer
        # sagt nur, das wievielte Mal dieser Standard gemessen wurde.
        return ("Zeile", "Nr.", self.bezeichnung)

    @property
    def zeilen(self) -> list[dict]:
        return [zeile for _, zeilen in self.gruppen for zeile in zeilen]

    @property
    def spalten(self) -> list[str]:
        return list(self.kopf) + [beschriftung(w) for w in self.wellen]

    @property
    def ueberschriften(self) -> list[str]:
        if self.prozent:
            return list(self.kopf) + [
                f"{name} [%]" for name in
                kurzbeschriftungen(self.wellen, mit_einheit=False)]
        return list(self.kopf) + kurzbeschriftungen(self.wellen,
                                                    mit_einheit=True,
                                                    einheit=self.einheit)

    @staticmethod
    def wiederfindung(wert: dict):
        """Gemessen durch Sollwert, in Prozent - oder None.

        Ohne gepflegten Sollwert und bei einem Sollwert von null gibt es
        keine: ein Blindwert hat keine Wiederfindung, er hat einen
        Betrag.
        """
        soll = wert.get("soll")
        if wert.get("zahl") is None or soll is None or not soll:
            return None
        return wert["zahl"] / soll * 100

    def _zelle(self, wert: dict) -> str:
        if self.prozent:
            gefunden = self.wiederfindung(wert)
            return "" if gefunden is None else zahltext(gefunden, 1)
        if wert["zahl"] is None:
            return wert.get("roh", "")
        return zahltext(wert["zahl"], NACHKOMMA_ANZEIGE)

    def tabelle(self, zeilen=None) -> list[list]:
        """Die Zellen - gerundet wie im Reiter Messung, damit es dasselbe ist."""
        return [[zeile["zeile"], zeile["nummer"], zeile["bezeichnung"]]
                + [self._zelle(wert) for wert in zeile["werte"]]
                for zeile in (self.zeilen if zeilen is None else zeilen)]

    def haelften(self) -> tuple[list, list]:
        """Teilt die Standards auf zwei Spalten auf.

        Erst ab zwei verschiedenen Standards: bei einem einzigen waere die
        rechte Haelfte leer und die linke unnoetig schmal. Geteilt wird
        nach Standards, nicht nach Zeilen - die Wiederholungen eines
        Standards gehoeren zusammen.

        Zurzeit stehen alle Blaetter einspaltig: in einer halben
        Fensterbreite sind ein Dutzend Elemente nicht mehr zu sehen, und
        genau darum geht es hier. Die Teilung bleibt trotzdem stehen -
        sie kostet nichts und waere bei wenigen Elementen wieder
        brauchbar.
        """
        if self.einspaltig or len(self.gruppen) < 2:
            return list(self.gruppen), []
        mitte = (len(self.gruppen) + 1) // 2
        return list(self.gruppen[:mitte]), list(self.gruppen[mitte:])

    def hinweis_fuer(self, zeilen):
        """Baut die Hinweisfunktion fuer eine Teiltabelle.

        Je Haelfte eine eigene: beide zaehlen ihre Zeilen ab null, eine
        gemeinsame Funktion zeigte rechts die Werte von links.
        """
        def hinweis(zeile: int, spalte: int) -> str:
            if not 0 <= zeile < len(zeilen):
                return ""
            nummer = spalte - self.VORSPANN
            if not 0 <= nummer < len(self.wellen):
                return ""
            return _hinweistext(zeilen[zeile]["werte"][nummer])
        return hinweis

    def zusammenfassung(self) -> str:
        if not self.gruppen:
            return (f"Im Lauf steht kein {self.bezeichnung} - "
                    f"es gibt nichts zu vergleichen.")
        namen = ", ".join(f"{name} ({len(zeilen)}x)"
                          for name, zeilen in self.gruppen)
        text = (f"{len(self.gruppen)} {self.bezeichnung}(s), "
                f"{len(self.zeilen)} Zeilen: {namen}")
        if self.prozent:
            return text + " - Wiederfindung in Prozent des Sollwerts"
        if self.rechnungstext:
            text += f" - {self.rechnungstext}"
        return text


def _gruppieren(zeilen: list[dict]) -> list[tuple]:
    """Je Standard seine Zeilen, mit laufender Nummer in Lauf-Reihenfolge.

    Gruppiert wird nach dem Namen aus der Standardverwaltung, nicht nach
    der Bezeichnung in der Laufdatei: "1/BlindMWN1.1" und "2/BlindMWN1.1"
    sind derselbe Standard, zweimal gemessen - als zwei Gruppen waeren sie
    nicht zu vergleichen, und genau darum geht es hier.

    Die laufende Nummer kommt aus dem Praefix, wo es eins gibt; sonst wird
    gezaehlt. Kontrollstandards tragen kein Praefix.
    """
    gruppen: dict[str, list] = {}
    for eintrag in zeilen:
        name = eintrag.get("standard") or eintrag["bezeichnung"]
        gleiche = gruppen.setdefault(name, [])
        gleiche.append(dict(eintrag, bezeichnung=name,
                            nummer=eintrag.get("wiederholung")
                            or len(gleiche) + 1))
    return list(gruppen.items())


def _standardblatt(kontext, laufdatei, verduennung_spalte, passt,
                   bezeichnung, mit_endfaktor, verrechnet=True,
                   linienwahl=None, basis=None,
                   korrigiert=None) -> Standardblatt:
    """Ein Blatt je Standardtyp - die Bloecke unterscheiden sich nur im Filter.

    Grundlage ist die Konzentration *einschliesslich* Verduennungsfaktor,
    also der Wert aus ErgVFak: das LIMS bildet MW aus MW_ROH, und MW_ROH
    ist die Konzentration der gemessenen Loesung. Mit `mit_endfaktor` wird
    darauf der Endfaktor aus TEILPROBEN angewandt.

    `korrigiert` sind die um den Blindwert bereinigten Werte, gestellt
    von der Bewertung: {(Laufzeile, pmwe_id): (Wert, Blindwert, Anzahl)}.
    Ohne sie stuende im Blatt der gemessene Wert, waehrend Ergebnis und
    ErgVFak den korrigierten zeigen - zwei Zahlen fuer dieselbe Zelle.
    Wo nichts abgezogen wurde, steht auch nichts drin: der
    Kontrollstandard prueft das Geraet, nicht die Aufarbeitung, und
    bleibt damit von allein unberuehrt.
    """
    korrigiert = korrigiert or {}
    # `basis` ist dieselbe Aufstellung, schon gerechnet: mehrere Reiter
    # bauen auf ihr auf, und sie haengt an nichts, was ein Klick aendert.
    werte = basis or ergebnisse(kontext, laufdatei, verduennung_spalte,
                                verrechnet, mit_faktor=True)
    # Je Element eine Spalte, wie in Messung und Ergebnis - sonst staende
    # hier ein VK oder ein Vergleich fuer eine Linie, die gar nicht gilt.
    wellen, verdichtete = verdichten(werte.wellen, werte.zeilen, linienwahl)
    zeilen = []
    for eintrag in verdichtete:
        if not passt(eintrag["art"]):
            continue
        # Erst den Abzug, dann den Endfaktor - in dieser Reihenfolge
        # rechnet auch die Zelle.
        if korrigiert:
            eintrag = dict(eintrag, werte=[
                _mit_abzug(wert, korrigiert.get((eintrag["zeile"],
                                                 wert.get("pmwe_id"))))
                for wert in eintrag["werte"]])
        if mit_endfaktor:
            faktor = kontext.endfaktor(eintrag["probe"])
            eintrag = dict(eintrag, werte=[_mit_endfaktor(wert, faktor)
                                           for wert in eintrag["werte"]])
        # Der Sollwert gehoert an die Zelle: aus ihm wird die
        # Wiederfindung, und der Hinweis nennt ihn ohnehin.
        eintrag = dict(eintrag, werte=[
            dict(wert, soll=kontext.sollwert(eintrag.get("stan_id"),
                                             welle["para_id"]))
            for wert, welle in zip(eintrag["werte"], wellen)])
        zeilen.append(eintrag)
    hinweis = ("Konzentrationen mit dem Endfaktor aus TEILPROBEN in Gehalte "
               "umgerechnet" if mit_endfaktor else "")
    # Mit dem Endfaktor steht in der Zelle ein Gehalt: dann gilt die
    # Einheit aus EINH_VER_ID, nicht die der gemessenen Loesung.
    einheit = EINHEIT_GEHALT if mit_endfaktor else EINHEIT_LOESUNG
    return Standardblatt(wellen, _gruppieren(zeilen), bezeichnung, hinweis,
                         einheit)


def kontrollstandards(kontext, laufdatei, verduennung_spalte="",
                      verrechnet=True, linienwahl=None,
                      basis=None) -> Standardblatt:
    """Die Kontrollstandards des Laufs.

    Dieselben Zeilen und Werte wie im Reiter Messung - hier nur enger
    zugeschnitten. Die laufende Nummer zaehlt, das wievielte Mal dieser
    Standard in der Laufdatei vorkommt; Kontrollstandards tragen anders als
    Blindwert- und Referenzstandards kein Praefix, aus dem sich das ablesen
    liesse.
    """
    # Ohne `korrigiert`: von einem Kontrollstandard wird kein Blindwert
    # abgezogen. Er prueft das Geraet, nicht die Aufarbeitung - und ein
    # Abzug wuerde genau die Abweichung verstecken, die er zeigen soll.
    return _standardblatt(kontext, laufdatei, verduennung_spalte,
                          ist_kontrollstandard, KONTROLLSTANDARD, False,
                          verrechnet, linienwahl, basis)


def standardmaterialien(kontext, laufdatei, verduennung_spalte="",
                        verrechnet=True, linienwahl=None,
                        basis=None, korrigiert=None) -> Standardblatt:
    """Haus- und Referenzstandardmaterialien, mit dem Endfaktor verrechnet.

    Der Endfaktor stammt aus TEILPROBEN und ist schon beim Laden des
    Kontexts ueber PROB_ID und UM_ID an die Ergebniszeilen gebunden; die
    Ergebniszeilen selbst sind auf Serie, Untersuchungsmethode und Station
    eingegrenzt. Die Einschraenkung steht also bereits, es muss nichts
    nachgeschlagen werden.

    Gerechnet wird auf dem Messwert aus der Laufdatei - so, wie das LIMS
    MW aus MW_ROH bildet. Fehlt der Endfaktor, bleibt die Zelle leer: eine
    Konzentration ohne ihren Faktor waere eine falsche Zahl.
    """
    return _standardblatt(kontext, laufdatei, verduennung_spalte,
                          ist_standardmaterial, "Standardmaterial", True,
                          verrechnet, linienwahl, basis, korrigiert)


def blindwertstandards(kontext, laufdatei, verduennung_spalte="",
                       verrechnet=True, linienwahl=None,
                       basis=None, korrigiert=None) -> Standardblatt:
    """Die Blindwertstandards - wie die Standardmaterialien gerechnet."""
    return _standardblatt(kontext, laufdatei, verduennung_spalte,
                          ist_blindwertstandard, BLINDWERTSTANDARD, True,
                          verrechnet, linienwahl, basis, korrigiert)


def _mit_endfaktor(wert: dict, faktor) -> dict:
    """Rechnet einen Messwert mit dem Endfaktor.

    Ohne Messwert bleibt die Zelle leer und traegt den Grund - unter einer
    Ueberschrift, die eine verrechnete Konzentration verspricht, saehe der
    ungerechnete Messwert aus wie ein Ergebnis.

    Fehlt der Endfaktor, wird mit 1 gerechnet: nicht jede
    Untersuchungsmethode fuehrt eine Einwaage, und wo nichts steht, ist
    nichts umzurechnen. Die Rechnung sagt, dass die Eins ergaenzt wurde -
    ein leeres Feld sagte nur, dass etwas fehlt, und das stimmt hier
    nicht.
    """
    if wert["zahl"] is None:
        return dict(wert, roh="", rechnung="",
                    grund=wert["roh"] or "kein Messwert")
    if faktor is None:
        return dict(wert, rechnung=f"{wert['roh']} * 1 (kein Endfaktor in "
                                   f"TEILPROBEN, als 1 genommen)", grund="")
    return dict(wert, zahl=wert["zahl"] * faktor,
                rechnung=f"{wert['roh']} * {faktor} (Endfaktor)", grund="")


# ==========================================================================
# Ergebnis
# ==========================================================================
#
# Dieselbe Aufstellung wie die Messung, aber mit der Konzentration der
# unverduennten Probe. Die Geraete geben die Konzentration der gemessenen
# Loesung aus, schon mit dem Verduennungsfaktor multipliziert - ein
# Messwert von 40 bei Faktor 4 gehoert als 10 in die Auswertung.
#
# Ob ein Geraet so rechnet, ist eine Einstellung je Geraet: manche liefern
# den unverrechneten Wert, dann waere ein Teilen durch den Faktor schlicht
# falsch.


class Ergebnis:
    """Konzentrationen - mit oder ohne Verduennungsfaktor."""

    KOPF = KOPFSPALTEN
    VORSPANN = len(KOPF)

    def __init__(self, wellen, zeilen, operation, mit_faktor):
        self.wellen = wellen
        self.zeilen = zeilen
        self.operation = operation      # "teilen", "mal" oder "nichts"
        self.mit_faktor = mit_faktor

    @property
    def spalten(self) -> list[str]:
        return list(self.KOPF) + [beschriftung(w) for w in self.wellen]

    @property
    def ueberschriften(self) -> list[str]:
        return list(self.KOPF) + kurzbeschriftungen(self.wellen,
                                                    mit_einheit=True)

    def tabelle(self) -> list[list]:
        return [[z["zeile"], z["bezeichnung"], z["art"], z["verduennung"]]
                + [zahltext(wert["zahl"], NACHKOMMA_ANZEIGE)
                   for wert in z["werte"]]
                for z in self.zeilen]

    def hinweis(self, zeile: int, spalte: int) -> str:
        """Zeigt die Rechnung, nicht nur das Ergebnis.

        Wer eine Konzentration bewertet, muss nachvollziehen koennen, wie
        sie zustande kam - sonst bleibt eine Abweichung im Faktor
        unentdeckt.
        """
        if not 0 <= zeile < len(self.zeilen):
            return ""
        nummer = spalte - self.VORSPANN
        if not 0 <= nummer < len(self.wellen):
            return ""
        return _hinweistext(self.zeilen[zeile]["werte"][nummer])

    def zusammenfassung(self) -> str:
        gerechnet = sum(1 for z in self.zeilen for w in z["werte"]
                        if w["zahl"] is not None)
        moeglich = len(self.zeilen) * len(self.wellen)
        if self.operation == "nichts":
            grund = ("die Laufdatei liefert die Konzentration bereits mit dem "
                     "Verduennungsfaktor" if self.mit_faktor else
                     "die Laufdatei liefert die Konzentration ohne den "
                     "Verduennungsfaktor")
            return (f"{gerechnet} von {moeglich} Werten unveraendert aus der "
                    f"Laufdatei - {grund}.")
        wie = ("mit dem Verduennungsfaktor multipliziert"
               if self.operation == "mal"
               else "durch den Verduennungsfaktor zurueckgerechnet")
        ohne = [z["bezeichnung"] for z in self.zeilen
                if lims_db.als_zahl(z["verduennung"]) in (None, 0)]
        text = f"{gerechnet} von {moeglich} Werten {wie}"
        if ohne:
            text += (f" - {len(ohne)} Zeilen ohne brauchbaren Faktor bleiben "
                     f"leer: {', '.join(ohne[:6])}"
                     f"{' ...' if len(ohne) > 6 else ''}")
        return text + "."


def rechenweg(mit_faktor: bool, verrechnet: bool, feststoff=False) -> str:
    """Was mit dem Messwert geschehen muss.

    Vier Faelle, zwei Fragen: soll die Konzentration den Verduennungsfaktor
    enthalten, und enthaelt der Wert aus der Laufdatei ihn schon? Stimmen
    beide ueberein, ist nichts zu tun.

    Bei einer Feststoffverbrennung ist nie etwas zu tun: der Faktor kommt
    aus der Einwaage und gehoert zu den Grenzen, nicht zum Messwert. Das
    Geraet gibt den Gehalt der Probe schon richtig aus - deshalb stehen in
    Ergebnis und ErgVFak dieselben Zahlen.
    """
    if feststoff:
        return "nichts"
    if mit_faktor == verrechnet:
        return "nichts"
    return "mal" if mit_faktor else "teilen"


def ergebnisse(kontext, laufdatei, verduennung_spalte="", verrechnet=True,
               mit_faktor=False) -> Ergebnis:
    """Die Konzentrationen, wahlweise mit oder ohne Verduennungsfaktor.

    `verrechnet` sagt, ob das Geraet den Faktor schon eingerechnet hat -
    das ist eine Einstellung je Geraet. `mit_faktor` sagt, was hier
    herauskommen soll.

    Fehlt der Faktor oder ist er null, bleibt die Zelle leer statt den
    ungerechneten Wert zu zeigen: unter einer Ueberschrift, die eine
    Umrechnung verspricht, waere das eine falsche Zahl - und eine falsche
    Zahl ist schlimmer als eine fehlende.
    """
    werte = messung(kontext, laufdatei, verduennung_spalte)
    weg = rechenweg(mit_faktor, verrechnet,
                    getattr(kontext, "feststoff", None) is not None
                    and kontext.feststoff.aktiv)
    zeilen = []
    for eintrag in werte.zeilen:
        faktor = lims_db.als_zahl(eintrag["verduennung"])
        zeilen.append(dict(eintrag, werte=[
            _umgerechnet(wert, faktor, weg, eintrag["verduennung"])
            for wert in eintrag["werte"]]))
    return Ergebnis(werte.wellen, zeilen, weg, mit_faktor)


def _umgerechnet(wert: dict, faktor, weg: str, faktortext: str) -> dict:
    if weg == "nichts":
        return dict(wert, rechnung="", grund="")
    if wert["zahl"] is None:
        return dict(wert, zahl=None, rechnung="",
                    grund=wert["roh"] or "kein Messwert")
    if faktor is None or faktor == 0:
        return dict(wert, zahl=None, rechnung="",
                    grund=(f"kein brauchbarer Verduennungsfaktor "
                           f"({faktortext or 'leer'})"))
    if weg == "mal":
        return dict(wert, zahl=wert["zahl"] * faktor,
                    rechnung=f"{wert['roh']} * {faktortext}", grund="")
    return dict(wert, zahl=wert["zahl"] / faktor,
                rechnung=f"{wert['roh']} / {faktortext}", grund="")


# ==========================================================================
# Wiederholungsproben
# ==========================================================================
#
# Dieselbe Probe mehrfach gemessen - daran zeigt sich die Praezision des
# Laufs. Gruppiert wird nach der Probennummer *ohne* die
# Wiederholungszaehler, sortiert nach WDH_UM und dann WDH_ME: so steht
# jede Messreihe in ihrer Reihenfolge beieinander.
#
# Unter jeder Gruppe steht der Variationskoeffizient je Element - die
# Streuung im Verhaeltnis zum Mittelwert. Erst er macht aus zwei Zahlen
# eine Aussage.

VK_BESCHRIFTUNG = "VK %"

# Ab dem Wievielfachen der Bestimmungsgrenze ein Wert ueberhaupt
# beurteilt wird. Nahe der Bestimmungsgrenze streut jede Messung; ein VK
# von dreissig Prozent sagt dort nichts ueber den Lauf, sondern nur, dass
# man am unteren Ende des Verfahrens misst. Dieselbe Schwelle gilt fuer
# den Vergleich mit dem gebuchten Wert (Reiter Abweichung).
BG_ABSTAND = decimal.Decimal("5")


def variationskoeffizient(werte: list):
    """Streuung im Verhaeltnis zum Mittelwert, in Prozent.

    Mit der Stichproben-Standardabweichung (n-1): die Messungen sind eine
    Stichprobe der moeglichen Wiederholungen, nicht deren Gesamtheit.
    Unter zwei Werten und bei einem Mittelwert von null gibt es keinen VK -
    dann None statt einer Zahl, die nichts bedeutet.
    """
    zahlen = [w for w in werte if w is not None]
    if len(zahlen) < 2:
        return None
    mittel = sum(zahlen) / len(zahlen)
    if mittel == 0:
        return None
    quadrate = sum((z - mittel) ** 2 for z in zahlen)
    streuung = (quadrate / (len(zahlen) - 1)).sqrt()
    return abs(streuung / mittel) * 100


class Wiederholungen:
    """Die Wiederholungsmessungen je Probe, mit ihrem VK."""

    KOPF = ("Zeile", "Probe")
    VORSPANN = len(KOPF)
    # Die beiden Zeilen unter jeder Gruppe.
    MITTEL_BESCHRIFTUNG = "Mittelwert"

    def __init__(self, wellen, gruppen, bg_abstand=None):
        self.wellen = wellen
        self.gruppen = gruppen      # [(Basisnummer, [Zeilen]), ...]
        # Ab dem Wievielfachen der Bestimmungsgrenze beurteilt wird -
        # einstellbar, deshalb nicht die Konstante selbst.
        self.bg_abstand = BG_ABSTAND if bg_abstand is None else bg_abstand

    @property
    def spalten(self) -> list[str]:
        return list(self.KOPF) + [beschriftung(w) for w in self.wellen]

    @property
    def ueberschriften(self) -> list[str]:
        # Gehalte, nicht Konzentrationen: hier ist der Endfaktor schon
        # angewandt, also gilt die Einheit aus EINH_VER_ID.
        return list(self.KOPF) + kurzbeschriftungen(self.wellen,
                                                    mit_einheit=True,
                                                    einheit=EINHEIT_GEHALT)

    def tabelle(self, zeilen) -> list[list]:
        """Die Messungen einer Gruppe, darunter Mittelwert und VK."""
        aufstellung = [[zeile["zeile"], zeile["bezeichnung"]]
                       + [zahltext(wert["zahl"], NACHKOMMA_ANZEIGE)
                          for wert in zeile["werte"]]
                       for zeile in zeilen]
        # Erst der Mittelwert, dann der VK: der VK ist die Streuung *um*
        # ihn, und untereinander gelesen ergibt das die Aussage.
        aufstellung.append(["", self.MITTEL_BESCHRIFTUNG]
                           + [zahltext(self.mittelwert(zeilen, nummer),
                                       NACHKOMMA_ANZEIGE)
                              for nummer in range(len(self.wellen))])
        aufstellung.append(["", VK_BESCHRIFTUNG] + self.vk_texte(zeilen))
        return aufstellung

    @staticmethod
    def gilt(wert: dict) -> bool:
        """Zaehlt dieser Wert mit?

        Von Hand ausgeschlossene Werte stehen weiter in der Tabelle - man
        soll sehen, was es gab -, aber sie gehen weder in den Mittelwert
        noch in den VK ein. Sonst spiegelte die Streuung einen Wert, den
        niemand mehr gelten laesst.
        """
        return not wert.get("ausgeschlossen")

    def zahlen(self, zeilen, nummer) -> list:
        """Die geltenden Messwerte dieses Elements in dieser Gruppe."""
        return [zeile["werte"][nummer]["zahl"] for zeile in zeilen
                if zeile["werte"][nummer]["zahl"] is not None
                and self.gilt(zeile["werte"][nummer])]

    def vk(self, zeilen) -> list:
        """Der VK je Element ueber die Messungen dieser Gruppe."""
        return [variationskoeffizient(self.zahlen(zeilen, nummer))
                for nummer in range(len(self.wellen))]

    def ausreisser(self, zeilen, nummer) -> list[int]:
        """Welche Messungen dieses Elements am weitesten danebenliegen.

        Erst wenn der VK seine Grenze reisst, ist das eine Frage - dann
        aber die entscheidende: welche Messung traegt die Streuung? Ab
        drei Messungen ist es die mit dem groessten Abstand zum
        Mittelwert; bei zweien laesst sich das nicht sagen, dort faellt
        die Wahl auf beide.

        Zurueck kommen die Nummern der Zeilen, nullbasiert innerhalb der
        Gruppe.
        """
        zahlen = [(nr, zeile["werte"][nummer]["zahl"])
                  for nr, zeile in enumerate(zeilen)
                  if zeile["werte"][nummer]["zahl"] is not None
                  and self.gilt(zeile["werte"][nummer])]
        if len(zahlen) < 2:
            return []
        if len(zahlen) == 2:
            return [nr for nr, _ in zahlen]
        mittel = sum(wert for _, wert in zahlen) / len(zahlen)
        weiteste = max(abs(wert - mittel) for _, wert in zahlen)
        return [nr for nr, wert in zahlen if abs(wert - mittel) == weiteste]

    def mittelwert(self, zeilen, nummer):
        """Der Mittelwert dieses Elements ueber die Messungen der Gruppe."""
        zahlen = self.zahlen(zeilen, nummer)
        return sum(zahlen) / len(zahlen) if zahlen else None

    def schwelle(self, zeilen, nummer):
        """Die Schwelle der Gruppe: 5 x BG, auf die Gehalte gerechnet."""
        schwellen = [zeile["werte"][nummer].get("schwelle")
                     for zeile in zeilen
                     if zeile["werte"][nummer].get("schwelle") is not None
                     and self.gilt(zeile["werte"][nummer])]
        return sum(schwellen) / len(schwellen) if schwellen else None

    def beurteilt(self, zeilen, nummer) -> bool:
        """Wird der VK dieses Elements ueberhaupt gegen seine Grenze geprueft?

        Nur oberhalb des Fuenffachen der Bestimmungsgrenze. Darunter
        streut jede Messung, und ein gerissener VK saegt nichts ueber den
        Lauf. Ist die Schwelle nicht bestimmbar - keine
        Bestimmungsgrenze, kein Faktor -, wird geprueft wie bisher:
        lieber eine Warnung zu viel als eine stillschweigend
        unterlassene.
        """
        schwelle = self.schwelle(zeilen, nummer)
        if schwelle is None:
            return True
        mittel = self.mittelwert(zeilen, nummer)
        return mittel is not None and mittel > schwelle

    def schwellentext(self, zeilen, nummer) -> str:
        """Warum hier geprueft wird oder nicht - fuer den Hinweis."""
        schwelle = self.schwelle(zeilen, nummer)
        if schwelle is None:
            gruende = {zeile["werte"][nummer].get("grund", "")
                       for zeile in zeilen} - {""}
            return ("ohne Schwelle geprueft"
                    + (f" ({sorted(gruende)[0]})" if gruende else ""))
        mittel = self.mittelwert(zeilen, nummer)
        wie = "ueber" if self.beurteilt(zeilen, nummer) else "unter"
        return (f"Mittelwert {zahltext_gestuft(mittel)} {wie} "
                f"{zahltext(self.bg_abstand, 0)} x BG = "
                f"{zahltext_gestuft(schwelle)}"
                + ("" if self.beurteilt(zeilen, nummer)
                   else " - nicht beurteilt"))

    def vk_texte(self, zeilen) -> list[str]:
        return [zahltext(wert, NACHKOMMA_ANZEIGE) if wert is not None else ""
                for wert in self.vk(zeilen)]

    def hinweis_fuer(self, zeilen):
        """Je Gruppe eine eigene Hinweisfunktion - jede zaehlt ab null."""
        def hinweis(zeile: int, spalte: int) -> str:
            nummer = spalte - self.VORSPANN
            if not 0 <= nummer < len(self.wellen):
                return ""
            if zeile == len(zeilen):            # die Mittelwertzeile
                mittel = self.mittelwert(zeilen, nummer)
                if mittel is None:
                    return ""
                return (f"{zahltext(mittel, NACHKOMMA_HINWEIS)} aus "
                        f"{len(self.zahlen(zeilen, nummer))} von "
                        f"{len(zeilen)} Messungen")
            if zeile == len(zeilen) + 1:        # die VK-Zeile
                wert = self.vk(zeilen)[nummer]
                if wert is None:
                    return ""
                return (f"{zahltext(wert, NACHKOMMA_HINWEIS)} % "
                        f"ueber {len(self.zahlen(zeilen, nummer))} "
                        f"Messungen | {self.schwellentext(zeilen, nummer)}")
            if not 0 <= zeile < len(zeilen):
                return ""
            wert = zeilen[zeile]["werte"][nummer]
            text = _hinweistext(wert)
            if wert.get("ausgeschlossen"):
                text = ((text + " | ") if text else "") + \
                    "ausgeschlossen - zaehlt nicht mit"
            return text
        return hinweis

    def zusammenfassung(self) -> str:
        if not self.gruppen:
            return ("Im Lauf ist keine Probe mehrfach gemessen worden - "
                    "es gibt keine Wiederholungen zu vergleichen.")
        return (f"{len(self.gruppen)} Proben mehrfach gemessen, "
                f"{sum(len(z) for _, z in self.gruppen)} Messungen insgesamt "
                f"- Gehalte mit dem Endfaktor aus TEILPROBEN verrechnet, "
                f"VK als Stichproben-Streuung (n-1). Beurteilt wird nur, "
                f"wo der Mittelwert ueber dem "
                f"{zahltext(self.bg_abstand, 0)}-fachen der "
                f"Bestimmungsgrenze liegt (BG x "
                f"{zahltext(self.bg_abstand, 0)} x Verduennungsfaktor x "
                f"Endfaktor).")


def wiederholungen(kontext, laufdatei, verduennung_spalte="",
                   verrechnet=True, ab=2, linienwahl=None,
                   basis=None, ausgeschlossen=None,
                   bg_abstand=None) -> Wiederholungen:
    """Die mehrfach gemessenen Proben eines Laufs.

    Gezeigt werden nur Proben mit mindestens zwei Messungen - bei einer
    einzigen gibt es nichts zu vergleichen und keinen VK. Grundlage ist wie
    in den Standardblaettern die Konzentration einschliesslich
    Verduennungsfaktor, darauf der Endfaktor aus TEILPROBEN.

    `ausgeschlossen` sind die Zellen, die im Reiter Ergebnis draussen
    sind, als Menge von (Zeile der Laufdatei, PMWE_ID). Sie stehen weiter
    in der Tabelle - man soll sehen, was es gab -, zaehlen aber weder in
    den Mittelwert noch in den VK.
    """
    draussen = set(ausgeschlossen or ())
    werte = basis or ergebnisse(kontext, laufdatei, verduennung_spalte,
                                verrechnet, mit_faktor=True)
    # Je Element eine Spalte: ein VK ueber eine Linie, die fuer diese Probe
    # gar nicht gilt, sagt nichts.
    wellen, verdichtete = verdichten(werte.wellen, werte.zeilen, linienwahl)
    gruppen: dict[str, list] = {}
    for eintrag in verdichtete:
        if eintrag["art"] != PROBE:
            continue
        faktor = kontext.endfaktor(eintrag["probe"])
        verduennung = lims_db.als_zahl(eintrag["verduennung"])
        werte_mit_schwelle = []
        for nummer, wert in enumerate(eintrag["werte"]):
            gehalt = _mit_endfaktor(wert, faktor)
            gehalt.update(bg_schwelle(kontext, eintrag["probe"],
                                      wellen[nummer], verduennung, faktor,
                                      bg_abstand))
            gehalt["ausgeschlossen"] = (
                (eintrag["zeile"], wert.get("pmwe_id")) in draussen)
            werte_mit_schwelle.append(gehalt)
        gruppen.setdefault(probenbasis(eintrag["bezeichnung"]), []).append(
            dict(eintrag, werte=werte_mit_schwelle,
                 sortierung=_wiederholungszaehler(eintrag["bezeichnung"])))
    mehrfach = [(name, sorted(zeilen, key=lambda z: z["sortierung"]))
                for name, zeilen in gruppen.items() if len(zeilen) >= ab]
    return Wiederholungen(wellen, mehrfach, bg_abstand)


def bg_schwelle(kontext, probe: str, welle: dict, verduennung,
                endfaktor, abstand=None) -> dict:
    """Ab welchem Gehalt eine Wiederholung beurteilt wird - und warum nicht.

    Die Bestimmungsgrenze aus den Verfahrenskenndaten gilt fuer die
    *Probe*. Der Reiter zeigt Gehalte: die gemessene Loesung mal
    Endfaktor, und die Loesung enthaelt schon den Verduennungsfaktor. Auf
    dieselbe Ebene gebracht ist die Schwelle also

        5 x BG x Verduennungsfaktor x Endfaktor

    - genau die Rechnung, die auch aus mw_roh den Gehalt macht. Der
    Vergleich ist damit derselbe wie im Reiter Abweichung, nur eine Ebene
    hoeher gerechnet.
    """
    grenzen = kontext.grenzen_fuer(probe, welle["pm_id"], welle["pm_ver"])
    bestimmungsgrenze = grenzen["bg"] if grenzen else None
    if bestimmungsgrenze is None:
        return {"schwelle": None, "bg": None, "abstand": abstand,
                "grund": "keine Bestimmungsgrenze in den "
                         "Verfahrenskenndaten"}
    if verduennung is None:
        return {"schwelle": None, "bg": bestimmungsgrenze,
                "abstand": abstand,
                "grund": "kein Verduennungsfaktor"}
    # Kein Endfaktor heisst nichts umzurechnen - dann ist er eins, und die
    # Schwelle steht trotzdem.
    endfaktor = ENDFAKTOR_EINS if endfaktor is None else endfaktor
    vielfaches = BG_ABSTAND if abstand is None else abstand
    return {"schwelle": bestimmungsgrenze * vielfaches * verduennung
                        * endfaktor,
            "bg": bestimmungsgrenze, "abstand": vielfaches, "grund": ""}


def _wiederholungszaehler(bezeichnung) -> tuple:
    """(WDH_UM, WDH_ME) aus der angezeigten Probennummer - danach wird sortiert."""
    treffer = WDH_ZAEHLER.search(str(bezeichnung or ""))
    if not treffer:
        return (1, 1)
    um, me = treffer.group(0).split()
    return (int(um), int(me))


# ==========================================================================
# Pruefung: das Gebuchte und das Gemessene nebeneinander
# ==========================================================================
#
# Der Reiter, mit dem sich ein Lauf gegen den Stand im LIMS halten laesst.
# Gezeigt werden die Standardmaterialien und die Wiederholungsproben der
# Serie - jeweils mit dem, was das LIMS schon fuehrt (MW_ROH), und mit dem,
# was dieser Lauf gemessen hat.
#
# Beide Zahlen beziehen sich auf die gemessene Loesung: MW_ROH ist die
# Konzentration vor dem Endfaktor, und der Wert aus der Laufdatei ist
# derselbe (Reiter ErgVFak). Mit dem Endfaktor gerechnet stuende hier ein
# Gehalt neben einer Konzentration, und der Vergleich waere keiner.
#
# Steht im LIMS noch nichts, bleiben die LIMS-Zeilen weg - dann ist der
# Reiter die Vorschau auf das, was gebucht wird.

QUELLE_LIMS = "LIMS"
QUELLE_LAUF = "Lauf"


class Pruefblatt:
    """Standardmaterialien und Wiederholungen, gebucht und gemessen."""

    KOPF = ("Quelle", "Zeile", "Probe")
    VORSPANN = len(KOPF)

    def __init__(self, wellen, gruppen):
        self.wellen = wellen
        self.gruppen = gruppen      # [(Name, [Zeilen]), ...]

    @property
    def spalten(self) -> list[str]:
        return list(self.KOPF) + [beschriftung(w) for w in self.wellen]

    @property
    def ueberschriften(self) -> list[str]:
        # Die Einheit der Loesung: hier steht MW_ROH neben dem Messwert,
        # beide vor dem Endfaktor.
        return list(self.KOPF) + kurzbeschriftungen(self.wellen,
                                                    mit_einheit=True)

    @property
    def zeilen(self) -> list[dict]:
        return [zeile for _, zeilen in self.gruppen for zeile in zeilen]

    def tabelle(self, zeilen=None) -> list[list]:
        return [[zeile["quelle"], zeile["zeile"], zeile["probe"]]
                + [zahltext_gestuft(wert["zahl"]) if wert["zahl"] is not None
                   else wert.get("roh", "")
                   for wert in zeile["werte"]]
                for zeile in (self.zeilen if zeilen is None else zeilen)]

    def hinweis_fuer(self, zeilen):
        def hinweis(zeile: int, spalte: int) -> str:
            nummer = spalte - self.VORSPANN
            if not (0 <= zeile < len(zeilen)
                    and 0 <= nummer < len(self.wellen)):
                return ""
            eintrag = zeilen[zeile]
            text = _hinweistext(eintrag["werte"][nummer])
            herkunft = ("aus ERGEBNISSE.MW_ROH"
                        if eintrag["quelle"] == QUELLE_LIMS
                        else "aus der Laufdatei (mit Verduennungsfaktor)")
            return f"{text}\n{herkunft}" if text else herkunft
        return hinweis

    def zusammenfassung(self) -> str:
        if not self.gruppen:
            return ("Weder ein Standardmaterial noch eine Wiederholungsprobe "
                    "in dieser Serie - es gibt nichts gegenueberzustellen.")
        gebucht = sum(1 for zeile in self.zeilen
                      if zeile["quelle"] == QUELLE_LIMS)
        gemessen = len(self.zeilen) - gebucht
        return (f"{len(self.gruppen)} Gruppen, {gebucht} Zeilen aus dem LIMS "
                f"(MW_ROH) und {gemessen} aus der Laufdatei - beide vor dem "
                f"Endfaktor, also unmittelbar vergleichbar.")


def _standardtyp(kontext, stan_id):
    """Der Typ eines Standards aus der Standardverwaltung."""
    for eintrag in kontext.standards:
        if eintrag["stan_id"] == stan_id:
            return eintrag["typ"], str(eintrag["bezeichnung"] or "").strip()
    return "", ""


def _pruefgruppen(kontext) -> list[tuple]:
    """Welche Proben in den Reiter gehoeren, gruppenweise.

    Zwei Sorten: die Standardmaterialien der Serie und die Proben, die
    mehr als einmal beauftragt sind (WDH_UM oder WDH_ME ueber eins) -
    diese samt ihrer Erstmessung, sonst fehlte der Bezugspunkt.
    """
    material, wiederholt = {}, {}
    for zeile in kontext.ergebnisse:
        if zeile["stan_id"] is not None:
            typ, name = _standardtyp(kontext, zeile["stan_id"])
            if ist_standardmaterial(typ):
                material.setdefault(name or zeile["probe_nr"], []).append(zeile)
                continue
        wiederholt.setdefault(zeile["probe_nr"], []).append(zeile)
    gruppen = [(name, zeilen, True) for name, zeilen in material.items()]
    for name, zeilen in wiederholt.items():
        if any((z["wdh_um"] or 1) != 1 or (z["wdh_me"] or 1) != 1
               for z in zeilen):
            gruppen.append((name, zeilen, False))
    return gruppen


def _lims_zeile(kontext, probe: str, gruppen: list[list[dict]],
                anzeige: str = "") -> dict:
    """Eine Zeile aus dem LIMS - je Element der gebuchte Rohwert."""
    werte = []
    for gruppe in gruppen:
        treffer = kontext.ergebnis(probe, gruppe[0]["pm_id"],
                                   gruppe[0]["pm_ver"])
        roh = lims_db.als_text(treffer["mw_roh"]) if treffer else ""
        werte.append({"roh": roh, "zahl": lims_db.als_zahl(roh),
                      "rechnung": "", "grund": ""})
    return {"quelle": QUELLE_LIMS, "zeile": "",
            "probe": anzeige or probenanzeige(probe),
            "schluessel": probe, "werte": werte}


def pruefung(kontext, laufdatei, verduennung_spalte="", verrechnet=True,
             linienwahl=None, basis=None) -> Pruefblatt:
    """Stellt das Gebuchte und das Gemessene nebeneinander.

    Die LIMS-Zeilen kommen aus denselben Ergebniszeilen, die der ganze
    Lauf benutzt - sie sind auf Serie, Untersuchungsmethode und Station
    eingegrenzt, die Einschraenkung steht damit schon.

    Eine Probe, die in der Laufdatei zweimal vorkommt, steht auch zweimal
    da: welche der beiden Messungen gilt, entscheidet sich anderswo, hier
    sollen beide zu sehen sein.
    """
    werte = basis or ergebnisse(kontext, laufdatei, verduennung_spalte,
                                verrechnet, mit_faktor=True)
    methoden = methodengruppen(werte.wellen)
    wellen, verdichtet = verdichten(werte.wellen, werte.zeilen, linienwahl)
    nach_probe: dict[str, list] = {}
    for eintrag in verdichtet:
        if eintrag["probe"]:
            nach_probe.setdefault(eintrag["probe"], []).append(eintrag)

    blatt = []
    for name, zeilen, ist_standard in _pruefgruppen(kontext):
        proben, gesehen = [], set()
        for zeile in sorted(zeilen, key=lambda z: (z["wdh_um"] or 1,
                                                   z["wdh_me"] or 1)):
            if zeile["probe"] not in gesehen:
                gesehen.add(zeile["probe"])
                proben.append(zeile["probe"])
        eintraege = []
        for probe in proben:
            # Erst der gebuchte Wert, darunter die Messung dieses Laufs:
            # so stehen die beiden Zahlen, die verglichen werden sollen,
            # untereinander und nicht in zwei Bloecken.
            gebucht = _lims_zeile(kontext, probe, methoden,
                                  probe if ist_standard else "")
            # Steht im LIMS noch nichts, waere die Zeile eine leere
            # Behauptung - dann bleibt nur die Messung dieses Laufs.
            if any(wert["roh"] for wert in gebucht["werte"]):
                eintraege.append(gebucht)
            for gemessen in nach_probe.get(probe, []):
                eintraege.append({
                    "quelle": QUELLE_LAUF, "zeile": gemessen["zeile"],
                    "probe": gemessen["bezeichnung"], "schluessel": probe,
                    "werte": gemessen["werte"]})
        if eintraege:
            blatt.append((name, eintraege))
    return Pruefblatt(wellen, blatt)


# --------------------------------------------------------------------------
# Geraetewechsel: welcher Parameter laesst sich wohin umsetzen
# --------------------------------------------------------------------------
#
# Reine Rechnung auf zwei Listen aus dem LIMS: wo die Parameter des Laufs
# gerade gebucht sind (`lims_db.laufparameter`) und welche Stationen
# UM_ANHANG fuer sie zulaesst (`lims_db.geraetewahl_fuer_lauf`). Was daraus
# wird, entscheidet niemand hier - diese Funktionen sagen nur, was moeglich
# waere und was es kostet.

# Spalten, die zur Station gehoeren und beim Umsetzen mitwandern koennten.
# Sie werden *nicht* geschrieben (der Auftrag nennt GEGR_ID, GEME_ID, PM_ID
# und PM_VER), aber verglichen: weicht die Zieleinheit ab, stuende der
# Messwert danach in einer anderen Einheit da, als er gemessen wurde.
MITWANDERND = (("einh_id", "Einheit"),
               ("einh_ver_id", "Einheit des Gehalts"))


def geraetewahl(jetzt: list[dict], moeglich: list[dict],
                staende=None, geraete=None) -> list[dict]:
    """Je Parameter des Laufs: wo er steht und wohin er koennte.

    Zusammengefuehrt wird ueber Parameter *und* Probenart - UM_ANHANG
    fuehrt beide, und dieselbe Substanz kann in Wasser an einem anderen
    Geraet gemessen werden als in Boden.
    """
    eintraege = []
    staende = list(staende or [])
    geraete = set(geraete or ())
    for zeile in jetzt:
        schluessel = (zeile["para_id"], zeile["part_id"])
        auswahl = [m for m in moeglich
                   if (m["para_id"], m["part_id"]) == schluessel]
        # Zeilen ohne Geraetegruppe sind keine Zielstation - UM_ANHANG
        # fuehrt auch Parameter, die an kein Geraet gebunden sind.
        auswahl = [m for m in auswahl if m["gegr_id"] is not None]
        eintraege.append({
            "para_id": zeile["para_id"], "part_id": zeile["part_id"],
            "parameter": _name(zeile, "parameter", zeile["para_id"]),
            "probenart": str(zeile.get("probenart") or "").strip(),
            "zeilen": zeile.get("zeilen") or 0,
            # Zeilen mit Messwert bleiben stehen; bewegt wird nur, was
            # noch leer ist.
            "mit_wert": zeile.get("mit_wert") or 0,
            "offen": (zeile.get("zeilen") or 0) - (zeile.get("mit_wert") or 0),
            "jetzt": zeile,
            "hier": [m for m in auswahl if m["gegr_id"] == zeile["gegr_id"]],
            "ziele": [m for m in auswahl if m["gegr_id"] != zeile["gegr_id"]],
            "staende": staende,
            # Welche Geraetemethode auf welchem Geraet laeuft - damit
            # entscheidet sich, welcher Stand der Kenndaten zur
            # Zielstation gehoert.
            "geraete": geraete,
        })
    return eintraege


def _name(zeile: dict, feld: str, ersatz) -> str:
    text = str(zeile.get(feld) or "").strip()
    return text or f"ID {ersatz}"


def stationsliste(eintraege: list[dict]) -> list[dict]:
    """Die Stationen, auf die sich etwas umsetzen laesst - je einmal.

    Mit der Zahl der Parameter, die dort hinkoennen: eine Station, die
    nicht alle traegt, ist deshalb nicht falsch - aber man soll es vor dem
    Umsetzen sehen und nicht danach.

    Gezaehlt wird je Parameter, nicht je Zeile aus UM_ANHANG: dort steht
    dieselbe Station zu einem Parameter mehrfach (je Einheit oder
    Bezugssubstanz eine Zeile), und die Auswahlliste nannte deshalb
    doppelt so viele Parameter und Ergebniszeilen, wie umgesetzt werden.
    """
    stationen = {}
    for eintrag in eintraege:
        gesehen = set()
        for ziel in eintrag["ziele"]:
            if ziel["gegr_id"] in gesehen:
                continue
            gesehen.add(ziel["gegr_id"])
            eintrag_station = stationen.setdefault(
                ziel["gegr_id"], {"gegr_id": ziel["gegr_id"],
                                  "station": _name(ziel, "station",
                                                   ziel["gegr_id"]),
                                  "parameter": 0, "zeilen": 0})
            eintrag_station["parameter"] += 1
            eintrag_station["zeilen"] += eintrag["offen"]
    return sorted(stationen.values(), key=lambda s: s["station"].upper())


def wechselplan(eintraege: list[dict], ziel_gegr, para_ids=None) -> dict:
    """Was ein Umsetzen auf diese Station bedeuten wuerde.

    `para_ids` grenzt auf einzelne Parameter ein; ohne sie geht alles mit,
    was gehen kann. Zurueck kommt der Plan, den `lims_db.geraet_umsetzen`
    ausfuehrt, und daneben, was nicht mitgeht und warum - das ist der
    Text, der vor dem Schreiben zur Bestaetigung angezeigt wird.
    """
    schritte, ohne_ziel, bleiben, schon_dort, hinweise = [], [], [], [], []
    gemessen = []
    for eintrag in eintraege:
        if eintrag["jetzt"]["gegr_id"] == ziel_gegr:
            schon_dort.append(eintrag)
            continue
        ziele = [z for z in eintrag["ziele"] if z["gegr_id"] == ziel_gegr]
        if not ziele:
            # Der Grund zuerst: "geht nicht" ist etwas anderes als "soll
            # nicht", und im Bestaetigungstext steht beides nebeneinander.
            ohne_ziel.append(eintrag)
            continue
        if para_ids is not None and eintrag["para_id"] not in para_ids:
            bleiben.append(eintrag)
            continue
        if eintrag["offen"] <= 0:
            # Jede Zeile traegt schon einen Messwert - da ist nichts zu
            # bewegen, und bewegt wuerde auch nichts.
            gemessen.append(eintrag)
            continue
        ziel, uebrige = _zielzeile(ziele, eintrag["jetzt"])
        if uebrige:
            hinweise.append(
                f"{eintrag['parameter']}: {len(ziele)} Zeilen in UM_ANHANG "
                f"fuer diese Station - genommen wird PM_ID {ziel['pm_id']}, "
                f"PM_VER {ziel['pm_ver']}"
                + (f" (Marker {ziel['marker']})" if ziel.get("marker")
                   else "")
                + ", zur Wahl standen noch "
                + ", ".join(f"{z['pm_id']}/{z['pm_ver']}" for z in uebrige)
                + ".")
        staende = eintrag.get("staende") or []
        zeiger, grund = lims_db.kenndaten_zeiger(
            staende, ziel, eintrag["jetzt"].get("vkd_id"),
            eintrag.get("geraete"))
        grenzen_jetzt = _grenzenzeile(staende, eintrag["jetzt"],
                                      eintrag["jetzt"].get("vkd_id"))
        grenzen_neu = _grenzenzeile(staende, ziel,
                                    zeiger if zeiger is not None
                                    else eintrag["jetzt"].get("vkd_id"))
        if grund:
            hinweise.append(f"{eintrag['parameter']}: {grund} - VKD_ID "
                            f"bleibt stehen.")
        for feld, bezeichnung in MITWANDERND:
            hier = eintrag["hier"][0].get(feld) if eintrag["hier"] else None
            if hier is not None and ziel.get(feld) is not None \
                    and hier != ziel.get(feld):
                hinweise.append(
                    f"{eintrag['parameter']}: {bezeichnung} unterscheidet "
                    f"sich ({hier} statt {ziel[feld]}) - sie wird nicht "
                    f"mitgeschrieben.")
        schritte.append({
            "para_id": eintrag["para_id"], "part_id": eintrag["part_id"],
            "parameter": eintrag["parameter"],
            "zeilen": eintrag["offen"], "geblieben": eintrag["mit_wert"],
            "alt_pm_id": eintrag["jetzt"]["pm_id"],
            "alt_pm_ver": eintrag["jetzt"]["pm_ver"],
            "alt_station": eintrag["jetzt"]["gegr_id"],
            "neu_pm_id": ziel["pm_id"], "neu_pm_ver": ziel["pm_ver"],
            "neu_geme": ziel["geme_id"],
            # Der Zeiger auf die Verfahrenskenndaten: die ID der
            # freigegebenen Zeile zur Zielmethode. Passt keine oder
            # passen mehrere, bleibt der alte Zeiger stehen und es wird
            # gesagt - ein falscher waere schlimmer als ein alter.
            "neu_vkd": zeiger,
            "grenzen_jetzt": grenzen_jetzt, "grenzen_neu": grenzen_neu,
            "pruefmethode": _name(ziel, "pruefmethode", ziel["pm_id"]),
            "station": _name(ziel, "station", ziel["gegr_id"]),
        })
    # Umgestellt wird die Serie nur, wenn nichts an der alten Station
    # bleibt: sonst braucht es dort *und* hier eine Zeile. Auch eine
    # einzelne schon gemessene Zeile zaehlt dazu - sie bleibt liegen.
    geblieben = sum(s["geblieben"] for s in schritte) \
        + sum(e["zeilen"] for e in gemessen)
    zurueck = bool(ohne_ziel or bleiben or geblieben)
    return {"schritte": schritte, "ohne_ziel": ohne_ziel, "bleiben": bleiben,
            "schon_dort": schon_dort, "gemessen": gemessen,
            "hinweise": hinweise, "serie_umstellen": not zurueck,
            "geblieben": geblieben,
            "zeilen": sum(s["zeilen"] for s in schritte)}


def _zielzeile(ziele: list[dict], jetzt: dict):
    """Welche UM_ANHANG-Zeile gilt, wenn mehrere zur Station passen.

    Es kommt vor: eine Station fuehrt mehrere Geraetemethoden, und
    PM_VER *ist* die GEME_ID - dann steht derselbe Parameter mehrfach da,
    je Methode einmal. Gewaehlt wird der Reihe nach

      1. dieselbe Pruefmethode wie bisher (nur die Version wechselt mit
         dem Geraet - das ist der Normalfall des Umsetzens),
      2. die Zeile mit gesetztem MARKER,
      3. die hoechste PM_VER, also die neueste Geraetemethode.

    Zurueck kommen die gewaehlte Zeile und die, die uebrig blieben -
    sie gehoeren in den Bestaetigungstext, damit die Wahl nachvollziehbar
    bleibt.
    """
    # Zeilen, die sich in nichts unterscheiden, was beim Umsetzen
    # geschrieben wird, sind keine Wahl - UM_ANHANG fuehrt dieselbe
    # Kombination mehrfach, etwa je Einheit oder Bezugssubstanz.
    ziele = _ohne_doppelte(ziele)
    if len(ziele) == 1:
        return ziele[0], []
    engere = [z for z in ziele if z["pm_id"] == jetzt.get("pm_id")] or ziele
    markiert = [z for z in engere if str(z.get("marker") or "").strip()]
    gewaehlt = sorted(markiert or engere,
                      key=lambda z: (z["pm_ver"] is not None, z["pm_ver"]),
                      reverse=True)[0]
    return gewaehlt, [z for z in ziele if z is not gewaehlt]


GRENZEN = (("nwg_arbeit", "NWG"), ("bg_arbeit", "BG"),
           ("ogrenze", "OG"))


def _grenzenzeile(staende: list[dict], methode: dict, vkd_id):
    """Die Kenndatenzeile zu einer Pruefmethode und einem Stand."""
    for zeile in staende:
        if (zeile["pm_id"] == methode["pm_id"]
                and zeile["pm_ver"] == methode["pm_ver"]
                and zeile["part_id"] == methode["part_id"]
                and zeile["vkd_id"] == vkd_id):
            return zeile
    return None


def grenzentext(zeile) -> str:
    """NWG, BG und Obergrenze in einer Zeile - leer, wenn es sie nicht gibt."""
    if not zeile:
        return ""
    return " / ".join(f"{name} {zahltext_gestuft(lims_db.als_zahl(zeile[feld]))}"
                      for feld, name in GRENZEN
                      if lims_db.als_zahl(zeile[feld]) is not None)


def grenzen_gleich(einer, anderer) -> bool:
    """Aendern sich die Grenzen ueberhaupt?"""
    if not einer or not anderer:
        return False
    return all(lims_db.als_zahl(einer[feld]) == lims_db.als_zahl(anderer[feld])
               for feld, _ in GRENZEN)


def _ohne_doppelte(ziele: list[dict]) -> list[dict]:
    """Je Kombination aus dem, was geschrieben wird, nur eine Zeile."""
    gesehen, eindeutig = set(), []
    for zeile in ziele:
        schluessel = (zeile.get("pm_id"), zeile.get("pm_ver"),
                      zeile.get("gegr_id"), zeile.get("geme_id"))
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        eindeutig.append(zeile)
    return eindeutig


def planbeschreibung(plan: dict, station: str) -> str:
    """Der Text, der vor dem Schreiben zur Bestaetigung angezeigt wird."""
    if not plan["schritte"]:
        return ("Es bleibt nichts umzusetzen: kein Parameter dieses Laufs "
                f"kann auf {station} - oder er steht schon dort.")
    zeilen = [f"Umgesetzt werden {len(plan['schritte'])} Parameter mit "
              f"{plan['zeilen']} Ergebniszeilen auf {station}:"]
    for schritt in plan["schritte"]:
        zeilen.append(f"  • {schritt['parameter']}: PM "
                      f"{schritt['alt_pm_id']}/{schritt['alt_pm_ver']} "
                      f"→ {schritt['neu_pm_id']}/{schritt['neu_pm_ver']} "
                      f"({schritt['pruefmethode']}), {schritt['zeilen']} "
                      f"Zeilen"
                      + (f", {schritt['geblieben']} mit Messwert bleiben "
                         f"stehen" if schritt["geblieben"] else ""))
        jetzt = grenzentext(schritt.get("grenzen_jetzt"))
        neu = grenzentext(schritt.get("grenzen_neu"))
        if neu and not grenzen_gleich(schritt.get("grenzen_jetzt"),
                                      schritt.get("grenzen_neu")):
            zeilen.append(f"      Grenzen: {jetzt or 'keine'} \u2192 {neu}")
        elif not neu:
            zeilen.append("      Grenzen: zur Zielmethode stehen keine "
                          "freigegebenen Kenndaten")
    if plan["schon_dort"]:
        zeilen.append(f"Schon dort: "
                      + ", ".join(e["parameter"] for e in plan["schon_dort"]))
    if plan["ohne_ziel"]:
        zeilen.append("Nicht moeglich (steht so nicht in UM_ANHANG): "
                      + ", ".join(e["parameter"] for e in plan["ohne_ziel"]))
    if plan["bleiben"]:
        zeilen.append("Bleibt auf Wunsch stehen: "
                      + ", ".join(e["parameter"] for e in plan["bleiben"]))
    if plan["gemessen"]:
        zeilen.append("Schon gemessen, bleibt stehen (MW_ROH steht drin): "
                      + ", ".join(e["parameter"] for e in plan["gemessen"]))
    zeilen.append("Mitgeschrieben werden GEGR_ID, GEME_ID, PM_ID, PM_VER "
                  "und VKD_ID - die Verfahrenskenndaten haengen an der "
                  "Pruefmethode.")
    zeilen.append("SERIEN_MW_ANHANG: "
                  + ("die Zeile der Serie wird auf die neue Station "
                     "umgestellt." if plan["serie_umstellen"] else
                     "eine zweite Zeile fuer die neue Station wird angelegt, "
                     "die alte bleibt stehen."))
    zeilen += plan["hinweise"]
    return "\n".join(zeilen)
