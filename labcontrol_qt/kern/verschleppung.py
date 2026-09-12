"""
LabControl - Verdacht auf Verschleppung
=======================================

Ein Autosampler saugt aus einem Gefaess ins naechste. Wo eine sehr
konzentrierte Probe gemessen wurde, haengt am Schlauch, an der Nadel und
in der Kammer noch etwas davon - die naechste Probe bekommt einen Teil
davon ab. Verschleppung (englisch carry-over) ist kein Rechenfehler und
kein Kuerzel: sie steht nicht in den Kenndaten und faellt bei der
einzelnen Probe nicht auf. Sie faellt nur im *Nachbarschaftsverhaeltnis*
auf - hoher Wert, unmittelbar danach ein sehr viel niedrigerer.

Der Verdacht faellt, wenn drei Dinge zusammenkommen: ein Wert liegt um
mehr als das Schwellenverhaeltnis ueber dem der naechsten Laufzeile
derselben Messlinie, dieser hohe Wert liegt ueber einem Fuenftel der
Obergrenze, und der niedrige traegt mindestens das Dreifache der
Bestimmungsgrenze. Dann werden beide markiert. Mehr behauptet das
Programm nicht - ob wirklich verschleppt wurde, sagt erst die
Wiederholung. Es geht darum, das Paar zu finden, das man sich ansehen
sollte; deshalb wird auch der hohe Wert markiert und nicht nur der
verdaechtige darunter.

Das Verhaeltnis allein fand zu viel. Zwei Werte tief im unteren
Arbeitsbereich liegen leicht tausendfach auseinander, ohne dass am
Schlauch etwas gehangen haette - und dicht an der Bestimmungsgrenze
streut die Messung selbst um ein Vielfaches, sodass das Verhaeltnis
dorthin gar nichts sagt.

Warum beide Richtungen zaehlen koennten und hier trotzdem nur eine
zaehlt: verschleppt wird nach vorn, in die *folgende* Probe. Ein
niedriger Wert vor einem hohen ist unauffaellig.

Gearbeitet wird auf der Reihenfolge der Laufdatei, nicht auf der
Probennummer: verschleppt wird in der Reihenfolge, in der das Geraet
gemessen hat. Eine Luecke unterbricht die Kette - fehlt der Wert der
Zwischenzeile, war die uebernaechste nicht "unmittelbar danach", und ein
Verdacht ins Blaue hilft niemandem.

Blindwerte und Kontrollstandards zaehlen mit. Gerade der Blindwert nach
einer hohen Probe ist der klassische Nachweis: was dort ankommt, kam aus
dem Gefaess davor.
"""

import decimal

# Um wie viel hoeher der erste Wert sein muss. Hundert ist ein
# vorsichtiger Anfang: bei Faktor zehn faende man in jedem Lauf
# Dutzende Paare, und ein Verdacht, der immer anschlaegt, wird nicht
# mehr gelesen.
VORGABE_SCHWELLE = 100

# Weniger als das Doppelte waere kein Verdacht mehr, sondern eine
# Aufzaehlung aller Nachbarn.
KLEINSTE_SCHWELLE = 2

# Zwei Bedingungen an die Hoehe der Werte selbst - das Verhaeltnis
# allein reicht nicht.
#
# Der hohe Wert muss ueberhaupt hoch sein: zwei Werte tief im unteren
# Arbeitsbereich koennen tausendfach auseinanderliegen, ohne dass am
# Schlauch etwas haengt. Gefordert wird ein Fuenftel der Obergrenze -
# darunter ist die Probe fuer den Autosampler unauffaellig.
#
# Und der niedrige Wert muss ueberhaupt eine Zahl sein: dicht an der
# Bestimmungsgrenze streut die Messung selbst um ein Vielfaches, und ein
# Verhaeltnis zu einem solchen Wert sagt nichts. Gefordert wird das
# Dreifache der Bestimmungsgrenze.
HOCH_ANTEIL = decimal.Decimal(5)        # Obergrenze geteilt durch fuenf
TIEF_BG_FAKTOR = decimal.Decimal(3)     # Bestimmungsgrenze mal drei


def schwelle_lesen(wert, vorgabe=VORGABE_SCHWELLE) -> decimal.Decimal:
    """Macht aus dem Feld eine Zahl - ein Vertipper aendert nichts.

    Ein zu kleines Verhaeltnis waere schlimmer als ein zu grosses: die
    Markierung stuende dann ueberall und saehe nach nichts mehr aus.
    """
    try:
        zahl = decimal.Decimal(str(wert).replace(",", ".").strip())
    except (ArithmeticError, ValueError, AttributeError, TypeError):
        return decimal.Decimal(vorgabe)
    if zahl < KLEINSTE_SCHWELLE:
        return decimal.Decimal(vorgabe)
    return zahl


def _zelle(bewertet, eintrag, gruppe):
    """Die Zelle dieser Probe fuer diesen Parameter - oder nichts.

    Genommen wird die zustaendige Messlinie: bei drei Bleilinien
    vergleicht man sonst 206Pb mit 208Pb, und der Sprung dazwischen
    waere kein Verdacht, sondern ein Missverstaendnis.
    """
    welle = bewertet.gewaehlt(eintrag["zeile"], gruppe)
    if welle is None:
        return None
    zelle = bewertet.zellen.get((eintrag["zeile"], welle["pmwe_id"]))
    if zelle is None or zelle.keine_zahl or zelle.probenwert is None:
        return None
    return zelle


def _grenze(zelle, name):
    """Eine gepflegte Grenze der Zelle - oder None.

    Dieselben Grenzen, gegen die auch die Kuerzel O, B und N pruefen:
    schon auf den Probenwert umgerechnet, bei einer Feststoffverbrennung
    also mit der tatsaechlichen Einwaage verschoben.
    """
    grenzen = getattr(zelle, "grenzen", None) or {}
    wert = grenzen.get(name)
    return wert if wert else None


def hoch_genug(zelle) -> bool:
    """Ist der hohe Wert ueberhaupt hoch - ein Fuenftel der Obergrenze?

    Ohne gepflegte Obergrenze laesst sich das nicht sagen; dann bleibt
    es beim Verhaeltnis allein. Behauptet wird nichts.
    """
    ogrenze = _grenze(zelle, "ogrenze")
    if ogrenze is None:
        return True
    return zelle.probenwert > ogrenze / HOCH_ANTEIL


def tief_genug_ueber_bg(zelle) -> bool:
    """Traegt der niedrige Wert ueberhaupt eine belastbare Zahl?

    Unter dem Dreifachen der Bestimmungsgrenze streut die Messung
    selbst um ein Vielfaches - ein Verhaeltnis dazu sagt nichts. Ohne
    gepflegte Bestimmungsgrenze bleibt es beim Verhaeltnis allein.
    """
    bg = _grenze(zelle, "bg")
    if bg is None:
        return True
    return zelle.probenwert >= bg * TIEF_BG_FAKTOR


def verdacht(bewertet, schwelle=VORGABE_SCHWELLE) -> list:
    """Alle Paare, bei denen der Verdacht besteht.

    Je Eintrag: der Parameter (als Nummer der Gruppe), die Laufzeile des
    hohen und die des folgenden niedrigen Werts, beide Zahlen und ihr
    Verhaeltnis. Die Reihenfolge ist die der Laufdatei.

    Drei Bedingungen muessen zusammenkommen: das Verhaeltnis, ein hoher
    Wert ueber einem Fuenftel der Obergrenze und ein niedriger Wert von
    mindestens dem Dreifachen der Bestimmungsgrenze. Das Verhaeltnis
    allein fand zu viel - zwei Werte tief im unteren Arbeitsbereich
    liegen leicht tausendfach auseinander, ohne dass am Schlauch etwas
    haengt.
    """
    grenze = schwelle_lesen(schwelle)
    gefunden = []
    for nummer, gruppe in enumerate(bewertet.gruppen):
        vorher = None
        for eintrag in bewertet.zeilen:
            zelle = _zelle(bewertet, eintrag, gruppe)
            if zelle is None:
                # Eine Luecke unterbricht die Kette: die uebernaechste
                # Zeile ist nicht "unmittelbar danach".
                vorher = None
                continue
            zahl = zelle.probenwert
            if vorher is not None and zahl > 0 \
                    and vorher[1].probenwert >= grenze * zahl \
                    and hoch_genug(vorher[1]) and tief_genug_ueber_bg(zelle):
                gefunden.append({
                    "gruppe": nummer,
                    "hoch_zeile": vorher[0], "tief_zeile": eintrag["zeile"],
                    "hoch": vorher[1].probenwert, "tief": zahl,
                    "verhaeltnis": vorher[1].probenwert / zahl,
                })
            vorher = (eintrag["zeile"], zelle)
    return gefunden


def stellen(gefunden) -> dict:
    """(Laufzeile, Parameternummer) -> Rolle im Paar.

    "hoch" ist die Probe, aus der verschleppt worden sein koennte,
    "tief" die, in der man es sehen wuerde. Beide werden markiert - ein
    Paar erklaert sich nur als Paar.
    """
    marken = {}
    for eintrag in gefunden:
        marken[(eintrag["hoch_zeile"], eintrag["gruppe"])] = "hoch"
        marken[(eintrag["tief_zeile"], eintrag["gruppe"])] = "tief"
    return marken


def texte(gefunden, namen) -> dict:
    """(Laufzeile, Parameternummer) -> Satz fuer den Hinweis."""
    gebaut = {}
    for eintrag in gefunden:
        name = (namen[eintrag["gruppe"]] if eintrag["gruppe"] < len(namen)
                else "")
        verhaeltnis = _kurz(eintrag["verhaeltnis"])
        gebaut[(eintrag["hoch_zeile"], eintrag["gruppe"])] = (
            f"Verschleppung moeglich: {name} liegt hier {verhaeltnis}-mal "
            f"ueber Zeile {eintrag['tief_zeile']} unmittelbar danach.")
        gebaut[(eintrag["tief_zeile"], eintrag["gruppe"])] = (
            f"Verschleppung moeglich: Zeile {eintrag['hoch_zeile']} "
            f"unmittelbar davor liegt {verhaeltnis}-mal hoeher. Ob wirklich "
            f"verschleppt wurde, sagt erst eine Wiederholung.")
    return gebaut


def _kurz(zahl) -> str:
    """Ein Verhaeltnis braucht keine Nachkommastellen."""
    try:
        return str(int(zahl))
    except (ArithmeticError, TypeError, ValueError):
        return str(zahl)


def bericht(gefunden) -> str:
    """Ein Satz fuer die Statuszeile."""
    if not gefunden:
        return "Kein Verdacht auf Verschleppung."
    proben = len({eintrag["tief_zeile"] for eintrag in gefunden})
    return (f"{len(gefunden)} Verdachtsfaelle auf Verschleppung in "
            f"{proben} Proben - lila markiert ist das Paar, nicht der "
            f"Befund.")
