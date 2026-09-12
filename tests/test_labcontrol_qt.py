"""Tests der Qt6-Portierung von LabControl.

Alles ohne Bildschirm: die Qt-Teile auf der Offscreen-Plattform, die
Fachschicht ohne Datenbank. Geprüft wird vor allem, dass die Portierung
wirklich auf der übernommenen Fachschicht sitzt und nicht auf einem
Nachbau — und dass die Demoquelle Feld für Feld dieselbe Gestalt liefert
wie die echte.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from labcontrol_qt.kern import config, dateien, laufkontext, lims_db
from labcontrol_qt.quelle import DemoQuelle, nur_zur_serie
from labcontrol_qt.raster import Rastermodell


# -- Die übernommene Fachschicht -------------------------------------------
def test_die_fachschicht_gibt_es_nur_einmal():
    """Sonst hätte jedes Modul zwei Zustände — etwa den Thick-Mode-Merker."""
    import lims_db as blank

    assert lims_db is blank
    assert laufkontext.lims_db is lims_db


def test_die_fachschicht_ist_unveraendert():
    """Wortgleich mit dem Ursprungs-Repo, soweit es hier liegt."""
    original = Path("/home/user/testlims")
    if not original.exists():
        pytest.skip("Ursprungs-Repo liegt in dieser Umgebung nicht daneben")
    hier = Path(__file__).resolve().parent.parent / "labcontrol_qt" / "kern"
    for modul in ("lims_db", "dateien", "laufkontext", "config", "protokoll",
                  "sitzung", "verschleppung"):
        assert (hier / f"{modul}.py").read_bytes() == \
               (original / f"{modul}.py").read_bytes(), modul


def test_laufdatei_wird_vom_original_gelesen():
    """Trennzeichen aus dem Inhalt, nicht aus der Endung.

    Die ICP-MS-Datei heißt .csv und ist Tab-getrennt — genau daran hängt,
    ob die Portierung die Datei so liest wie das Original.
    """
    with tempfile.TemporaryDirectory() as ordner:
        quelle = DemoQuelle(Path(ordner))
        pfade = quelle.messdateien_anlegen("ICPMS Agilent 7900",
                                           quelle.serien()[:1])
        csv_datei = next(pfad for pfad in pfade if pfad.endswith(".csv"))
        lauf = dateien.lies(csv_datei)
        assert lauf.trennzeichen == "\t"
        assert lauf.kodierung.startswith("utf-8")
        assert "Tabulator" in lauf.herkunft()
        assert lauf.spaltenzahl == 6
        assert lauf.zeilenzahl > 20


# -- Die Demoquelle hat dieselbe Gestalt wie die echte ---------------------
@pytest.fixture()
def quelle(tmp_path) -> DemoQuelle:
    return DemoQuelle(tmp_path / "stationen")


def test_methoden_und_geraete_kommen_als_paare(quelle):
    serie = quelle.serien()[0]
    methoden = quelle.methoden(serie)
    assert methoden and all(len(eintrag) == 2 for eintrag in methoden)
    geraete = quelle.geraete(serie, methoden[0][0])
    assert geraete and all(len(eintrag) == 2 for eintrag in geraete)


def test_offene_serien_tragen_die_fuenf_schluessel(quelle):
    offen = quelle.offene_serien()
    assert offen
    for zeile in offen:
        assert set(zeile) == {"serie", "um_id", "kuerzel", "stat_id", "station"}


def test_pruefungen_werden_vom_original_bewertet(quelle):
    """„laeuft" entscheidet ``lims_db.pruefung_laeuft`` — nicht die Demoquelle."""
    pruefungen = quelle.pruefungen(1210)
    for eintrag in pruefungen:
        assert eintrag["laeuft"] is lims_db.pruefung_laeuft(eintrag["status"])
    assert any(p["laeuft"] for p in pruefungen)
    assert any(not p["laeuft"] for p in pruefungen)


def test_dateifilter_zeigt_alle_wenn_keine_passt():
    dateien_ = ["2026W052_lauf1.csv", "export_2026W052.txt", "fremd.csv"]
    assert nur_zur_serie(dateien_, "2026W052") == dateien_[:2]
    # Passt keine, kommen alle — eine leere Liste sähe aus wie ein leerer
    # Ordner und versteckte genau die gesuchte Datei.
    assert nur_zur_serie(dateien_, "2099W001") == dateien_
    assert nur_zur_serie(dateien_, "") == dateien_


# -- Das Raster -------------------------------------------------------------
def test_raster_faerbt_einzelne_zellen():
    """Genau das, was eine ttk.Treeview nicht kann."""
    from PySide6.QtCore import Qt

    modell = Rastermodell()
    modell.fuellen(["Prüfung", "läuft"], [["Blindwert", "ja"],
                                          ["Verschleppung", "nein"]],
                   marken={(0, 1): "gruen", (1, 1): "rot"})
    assert modell.rowCount() == 2
    gruen = modell.data(modell.index(0, 1), Qt.ForegroundRole)
    rot = modell.data(modell.index(1, 1), Qt.ForegroundRole)
    assert gruen.name() == "#15803d"
    assert rot.name() == "#b91c1c"
    # Die Nachbarzelle bleibt ungefärbt — Zeilenfärbung wäre zu grob.
    assert modell.data(modell.index(0, 0), Qt.ForegroundRole) is None


def test_raster_richtet_zahlenspalten_rechts_aus():
    from PySide6.QtCore import Qt

    modell = Rastermodell()
    modell.fuellen(["Name", "Wert"], [["Blei", "0,012"], ["Cadmium", "<0,01"],
                                      ["Kupfer", "1,3"], ["Nitrat", "48"]])
    rechts = int(Qt.AlignRight | Qt.AlignVCenter)
    links = int(Qt.AlignLeft | Qt.AlignVCenter)
    assert modell.data(modell.index(0, 1), Qt.TextAlignmentRole) == rechts
    assert modell.data(modell.index(0, 0), Qt.TextAlignmentRole) == links


def test_raster_haelt_den_inhalt_fuer_den_export():
    modell = Rastermodell()
    modell.fuellen(["a", "b"], [[1, 2], [3, None]])
    kopf, zeilen = modell.inhalt
    assert kopf == ["a", "b"]
    assert zeilen == [["1", "2"], ["3", ""]]


# -- Die Oberfläche ---------------------------------------------------------
@pytest.fixture(scope="module")
def programm():
    from labcontrol_qt.app import anwendung_bauen

    return anwendung_bauen([])


def _ruhen(programm, runden: int = 8) -> None:
    from labcontrol_qt.arbeit import abwarten

    for _ in range(runden):
        programm.processEvents()
        abwarten(2_000)
        programm.processEvents()


@pytest.fixture()
def fenster(programm, tmp_path):
    from labcontrol_qt.hauptfenster import Hauptfenster

    quelle = DemoQuelle(tmp_path / "stationen")
    haupt = Hauptfenster(quelle, config.Config(runtime_dir=str(tmp_path)))
    haupt.show()
    _ruhen(programm)
    yield haupt, quelle
    haupt.close()


def test_bearbeiten_reiter_laedt_die_stammdaten(fenster, programm):
    haupt, _ = fenster
    reiter = haupt.bearbeiten
    assert reiter.feld_serie.count() > 100
    assert reiter.feld_bearbeiter.count() == 5
    assert reiter.uebersicht.modell.rowCount() > 0
    # Ohne vollständige Auswahl bleibt der Weg zur Datei zu.
    assert not reiter.knopf_datei.isEnabled()


def test_seriensuche_schraenkt_die_liste_ein(fenster, programm):
    haupt, _ = fenster
    reiter = haupt.bearbeiten
    alle = reiter.feld_serie.count()
    reiter.feld_suche.setText(reiter.serien_alle[0][:4] + "W")
    programm.processEvents()
    assert 0 < reiter.feld_serie.count() < alle
    reiter.feld_suche.setText("gibtesnicht")
    programm.processEvents()
    assert reiter.feld_serie.count() == 0


def test_klick_in_die_uebersicht_fuellt_alle_drei(fenster, programm):
    haupt, _ = fenster
    reiter = haupt.bearbeiten
    zeile = reiter.uebersicht_zeilen[0]
    reiter._uebersicht_gewaehlt(0)
    _ruhen(programm)
    gewaehlt = reiter.auswahl()
    assert gewaehlt["serie"] == zeile["serie"]
    assert gewaehlt["um_id"] == zeile["um_id"]
    assert gewaehlt["stat_id"] == zeile["stat_id"]
    assert reiter.vollstaendig()
    assert reiter.knopf_datei.isEnabled()


def test_dateiliste_filtert_auf_die_serie(fenster, programm):
    haupt, quelle = fenster
    reiter = haupt.bearbeiten
    reiter._uebersicht_gewaehlt(0)
    _ruhen(programm)
    gewaehlt = reiter.auswahl()
    quelle.messdateien_anlegen(gewaehlt["geraet"],
                               [gewaehlt["serie"], "2019W001"])
    reiter._dateien_laden()
    _ruhen(programm)
    assert len(reiter.dateien_alle) == 4
    assert len(reiter.dateien_gezeigt) == 2
    assert all(gewaehlt["serie"] in name for name in reiter.dateien_gezeigt)

    reiter.dateibereich.haken.setChecked(False)
    programm.processEvents()
    assert len(reiter.dateien_gezeigt) == 4


def test_messfenster_zeigt_laufdatei_und_pruefzuordnung(fenster, programm):
    from labcontrol_qt.messfenster import Messfenster

    haupt, quelle = fenster
    reiter = haupt.bearbeiten
    reiter._uebersicht_gewaehlt(0)
    _ruhen(programm)
    gewaehlt = reiter.auswahl()
    quelle.messdateien_anlegen(gewaehlt["geraet"], [gewaehlt["serie"]])
    reiter._dateien_laden()
    _ruhen(programm)

    pfad = os.path.join(reiter.dateien_ordner, reiter.dateien_gezeigt[0])
    mess = Messfenster(pfad, gewaehlt, quelle, haupt)
    mess.show()
    _ruhen(programm)

    # Laufdatei: eine Spalte mehr als die Datei — die Zeilennummer davor.
    raster = mess.raster_laufdatei.modell
    assert raster.columnCount() == mess.laufdatei.spaltenzahl + 1
    assert raster.rowCount() == mess.laufdatei.zeilenzahl
    assert raster.data(raster.index(0, 0), 0) == "1"

    # Laufkontext: Kopf und Prüfzuordnung
    kopf = dict(mess.kontextkopf.modell.inhalt[1])
    assert kopf["Serie"] == gewaehlt["serie"]
    assert kopf["Gerät"] == gewaehlt["geraet"]
    assert mess.kontextraster.modell.rowCount() == 6
    mess.close()


def test_selbsttest_laeuft_durch(capsys):
    from labcontrol_qt.app import selbsttest

    assert selbsttest() == 0
    assert "bestanden" in capsys.readouterr().out
