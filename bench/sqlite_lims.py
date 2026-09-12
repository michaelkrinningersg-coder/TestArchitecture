"""Wie groß und wie schnell wird SQLite mit der ERGEBNISSE-Tabelle des LIMS?

Gemessen, nicht geschätzt. Nachgebaut wird die Gestalt der echten Tabelle,
nicht ihr Inhalt: 81 Spalten wie im Original, davon die 27, die in
``lims_db.py`` namentlich vorkommen, mit ihren echten Namen und Typen — der
Rest als Füllung in plausiblen Typen, damit die Zeile so breit ist wie die
echte. Dazu die sechs Tabellen, die die Ankerabfrage dazujoint.

Gestellt werden dann die Abfragen, die LabControl wirklich stellt:

* **anker**      ``ergebniszeilen()`` — ein Lauf, sieben Joins
* **serien**     ``serien_historisch()`` — DISTINCT über die größte Tabelle
* **export**     6 000 Einzel-UPDATE in Bündeln von 500, wie ``EXPORT_BUENDEL``
* **zaehlen**    COUNT(*) als Untergrenze für „einmal alles anfassen"

Aufruf::

    python bench/sqlite_lims.py --zeilen 1000000 --fuellung mittel
    python bench/sqlite_lims.py --zeilen 10000000 --fuellung mittel --kalt
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import statistics
import threading
import subprocess
import sys
import time
from pathlib import Path

# Ein Lauf: dreihundert Proben, zwanzig Elemente — die Zahl steht als
# Begründung für EXPORT_BUENDEL in lims_db.py.
PROBEN_JE_LAUF = 300
PARAMETER_JE_LAUF = 20
ZEILEN_JE_LAUF = PROBEN_JE_LAUF * PARAMETER_JE_LAUF
EXPORT_BUENDEL = 500

# Die Spalten, die in lims_db.py namentlich vorkommen.
KERN = [
    ("prob_id", "INTEGER"), ("serie", "TEXT"), ("um_id", "INTEGER"),
    ("pm_id", "INTEGER"), ("pm_ver", "INTEGER"), ("para_id", "INTEGER"),
    ("einh_id", "INTEGER"), ("einh_ver_id", "INTEGER"), ("vkd_id", "INTEGER"),
    ("part_id", "INTEGER"), ("gegr_id", "INTEGER"), ("geme_id", "INTEGER"),
    ("stan_id", "INTEGER"), ("lnr", "INTEGER"),
    ("mw", "REAL"), ("mw_roh", "REAL"), ("mw_org", "REAL"), ("mw_n", "REAL"),
    ("kommentar", "TEXT"), ("anwender", "TEXT"), ("v_faktor", "REAL"),
    ("fc7", "TEXT"), ("fc8", "TEXT"), ("fc9", "TEXT"),
    ("datum_zeit", "TEXT"), ("psta_id", "INTEGER"), ("korrektur_flag", "TEXT"),
]
# 81 Spalten hat die echte Tabelle; die restlichen 54 sind Füllung.
FUELL_ZAHL = 81 - len(KERN)
FUELLUNG = ([(f"f{i:02d}", "REAL") for i in range(1, 21)]
            + [(f"f{i:02d}", "TEXT") for i in range(21, 41)]
            + [(f"f{i:02d}", "INTEGER") for i in range(41, FUELL_ZAHL + 1)])
SPALTEN = KERN + FUELLUNG
assert len(SPALTEN) == 81, len(SPALTEN)

# Wie viele der 54 Füllspalten belegt sind. „nur_kern" ist die Untergrenze
# (81 Spalten, 54 davon NULL), „voll" die Obergrenze. Eine echte Zeile liegt
# dazwischen, deshalb wird mit „mittel" gerechnet.
FUELLGRADE = {"nur_kern": 0, "mittel": 18, "voll": FUELL_ZAHL}

STAMMTABELLEN = """
CREATE TABLE proben (
  id INTEGER PRIMARY KEY, probe_nr TEXT, wdh_um INTEGER, wdh_me INTEGER,
  bemerkung TEXT);
CREATE TABLE teilproben (
  prob_id INTEGER, um_id INTEGER, faktor REAL, faktor_wgh REAL,
  end_faktor REAL, PRIMARY KEY (prob_id, um_id));
CREATE TABLE parameter (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE pruefmethoden (
  id INTEGER, version INTEGER, name TEXT, lsta_id INTEGER,
  PRIMARY KEY (id, version));
CREATE TABLE einheiten (id INTEGER PRIMARY KEY, einheit TEXT);
CREATE TABLE probenart (id INTEGER PRIMARY KEY, art TEXT);
"""

# Wörtlich die Abfrage aus lims_db.ergebniszeilen — nur die Bindungsnamen
# sind von :name auf ? umgestellt, sonst nichts.
ANKER = """
SELECT e.prob_id, p.probe_nr, p.wdh_um, p.wdh_me, p.bemerkung,
       e.serie, e.um_id, e.pm_id, e.pm_ver, e.para_id, e.einh_id,
       e.einh_ver_id, e.vkd_id, e.part_id, e.gegr_id, e.geme_id,
       e.stan_id, e.lnr, e.mw, e.mw_roh, e.mw_org, e.kommentar,
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
 WHERE e.serie = ? AND e.um_id = ? AND e.gegr_id = ?
 ORDER BY p.probe_nr, p.wdh_um, p.wdh_me, pa.name, e.pm_id, e.pm_ver
"""

# serien_historisch. REGEXP_LIKE(serie, '^[0-9]{4}') kennt SQLite nicht;
# GLOB auf den ersten vier Zeichen sagt dasselbe und braucht keine
# Erweiterung. Genau diese Stelle ist die Dialektnaht.
SERIEN = """
SELECT DISTINCT serie FROM ergebnisse
 WHERE serie IS NOT NULL AND serie >= ?
   AND substr(serie, 1, 4) GLOB '[0-9][0-9][0-9][0-9]'
 ORDER BY serie DESC
"""

# qp_nachmessung_sql, Schlüssel wörtlich übernommen.
EXPORT_UPDATE = """
UPDATE ergebnisse SET mw_roh = ?, mw_n = ?, mw = ?
 WHERE prob_id = ? AND pm_id = ? AND pm_ver = ? AND um_id = ? AND gegr_id = ?
"""

PARAMETERNAMEN = ["Blei", "Cadmium", "Kupfer", "Zink", "Nickel", "Chrom",
                  "Arsen", "Mangan", "Eisen", "Aluminium", "Calcium",
                  "Magnesium", "Kalium", "Natrium", "Phosphor", "Schwefel",
                  "Bor", "Kobalt", "Molybdaen", "Selen"]


# Über wie viele Jahre die Läufe verteilt werden. Das LIMS läuft seit
# Jahren; die Standardgrenze der Serienliste ist "serie >= '2023'", und die
# muss einen echten Teil der Tabelle treffen, sonst misst die Abfrage nichts.
BETRIEBSJAHRE = list(range(2014, 2027))


def _seriennummer(lauf: int, laeufe: int) -> str:
    """2014W001 … — Jahr plus laufende Nummer, wie die echten Seriennummern."""
    je_jahr = max(1, laeufe // len(BETRIEBSJAHRE))
    jahr = BETRIEBSJAHRE[min(lauf // je_jahr, len(BETRIEBSJAHRE) - 1)]
    return f"{jahr}W{lauf % je_jahr + 1:03d}"


def schema_legen(db: sqlite3.Connection) -> None:
    spalten = ",\n  ".join(f"{name} {typ}" for name, typ in SPALTEN)
    db.executescript(f"CREATE TABLE ergebnisse (\n  {spalten}\n);")
    db.executescript(STAMMTABELLEN)
    db.executemany("INSERT INTO parameter VALUES (?, ?)",
                   list(enumerate(PARAMETERNAMEN, start=1)))
    db.executemany("INSERT INTO pruefmethoden VALUES (?, ?, ?, ?)",
                   [(100 + i, 1, f"Methode {i}", 0) for i in range(1, 41)])
    db.executemany("INSERT INTO einheiten VALUES (?, ?)",
                   [(1, "mg/l"), (2, "mg/kg"), (3, "µg/l"), (4, "%")])
    db.executemany("INSERT INTO probenart VALUES (?, ?)",
                   [(1, "Boden"), (2, "Blatt"), (3, "Nadel"), (4, "Wasser")])
    db.commit()


def fuellen(db: sqlite3.Connection, zeilen: int, fuellgrad: int,
            melden=print) -> dict:
    """Füllt ergebnisse und die Probentabellen. Gibt die Einfügedauer zurück."""
    wuerfel = random.Random(17025)
    platzhalter = ", ".join("?" * len(SPALTEN))
    einfuegen = f"INSERT INTO ergebnisse VALUES ({platzhalter})"
    belegt = fuellgrad
    fuell_werte = []
    for i, (_, typ) in enumerate(FUELLUNG):
        if i >= belegt:
            fuell_werte.append(None)
        elif typ == "REAL":
            fuell_werte.append(round(wuerfel.uniform(0, 1000), 4))
        elif typ == "TEXT":
            fuell_werte.append(wuerfel.choice(["OK", "geprueft", "LIMS",
                                               "A1", "n.b.", "2026-04-01"]))
        else:
            fuell_werte.append(wuerfel.randint(1, 99999))

    laeufe = max(1, zeilen // ZEILEN_JE_LAUF)
    begonnen = time.perf_counter()
    prob_id = 0
    geschrieben = 0
    for lauf in range(laeufe):
        serie = _seriennummer(lauf, laeufe)
        um_id = 10 + lauf % 7
        gegr_id = 200 + lauf % 5
        proben, teile, ergebnisse = [], [], []
        for nummer in range(PROBEN_JE_LAUF):
            prob_id += 1
            proben.append((prob_id, f"{serie[:4]}P{prob_id:07d}", 0, 0, None))
            teile.append((prob_id, um_id, 1.0, 1.0, 1.0))
            for p in range(PARAMETER_JE_LAUF):
                ergebnisse.append((
                    prob_id, serie, um_id, 100 + p, 1, 1 + p, 1, 2,
                    500 + p, 1 + p % 4, gegr_id, 300 + p % 3, None,
                    nummer + 1,
                    round(wuerfel.uniform(0, 500), 4),
                    round(wuerfel.uniform(0, 500), 4), None, None,
                    None, "mkrinninger", 1.0, "1", "J", None,
                    "2026-04-01 08:15:00", 1, None, *fuell_werte))
        db.executemany("INSERT INTO proben VALUES (?, ?, ?, ?, ?)", proben)
        db.executemany("INSERT INTO teilproben VALUES (?, ?, ?, ?, ?)", teile)
        db.executemany(einfuegen, ergebnisse)
        geschrieben += len(ergebnisse)
        if lauf % 200 == 0:
            db.commit()
            melden(f"  {geschrieben:>12,} Zeilen "
                   f"({time.perf_counter() - begonnen:6.1f} s)")
    db.commit()
    dauer = time.perf_counter() - begonnen
    return {"zeilen": geschrieben, "laeufe": laeufe, "einfuegen_s": round(dauer, 1),
            "zeilen_pro_s": round(geschrieben / dauer)}


def _groesse(pfad: Path) -> int:
    gesamt = pfad.stat().st_size
    for anhang in ("-wal", "-shm"):
        neben = Path(str(pfad) + anhang)
        if neben.exists():
            gesamt += neben.stat().st_size
    return gesamt


def cache_leeren() -> bool:
    """Seitencache des Betriebssystems verwerfen — sonst misst man RAM."""
    try:
        subprocess.run(["sync"], check=True)
        Path("/proc/sys/vm/drop_caches").write_text("3\n")
        return True
    except Exception:
        return False


def _messen(aufruf, runden: int, kalt: bool, pfad: Path) -> dict:
    zeiten = []
    for _ in range(runden):
        if kalt:
            cache_leeren()
        begonnen = time.perf_counter()
        ergebnis = aufruf()
        zeiten.append((time.perf_counter() - begonnen) * 1000)
    return {"median_ms": round(statistics.median(zeiten), 1),
            "best_ms": round(min(zeiten), 1),
            "runden": [round(z, 1) for z in zeiten],
            "treffer": ergebnis}


def abfragen_messen(pfad: Path, laeufe: int, kalt: bool,
                    runden: int = 5) -> dict:
    db = sqlite3.connect(pfad)
    db.execute("PRAGMA journal_mode=WAL")
    mitte = laeufe // 2
    serie, um_id, gegr_id = (_seriennummer(mitte, laeufe), 10 + mitte % 7,
                             200 + mitte % 5)

    def anker():
        return len(db.execute(ANKER, (serie, um_id, gegr_id)).fetchall())

    def serien():
        return len(db.execute(SERIEN, ("2023",)).fetchall())

    def zaehlen():
        return db.execute("SELECT COUNT(*) FROM ergebnisse").fetchone()[0]

    ergebnis = {
        "anker": _messen(anker, runden, kalt, pfad),
        "serien": _messen(serien, max(2, runden // 2), kalt, pfad),
        "zaehlen": _messen(zaehlen, 2, kalt, pfad),
    }
    ergebnis["anker_plan"] = "\n".join(
        f"{z[3]}" for z in db.execute("EXPLAIN QUERY PLAN " + ANKER,
                                      (serie, um_id, gegr_id)))
    db.close()
    return ergebnis


def export_messen(pfad: Path, laeufe: int) -> dict:
    """6 000 UPDATE in Bündeln von 500 — ein Export, wie er wirklich läuft."""
    db = sqlite3.connect(pfad)
    db.execute("PRAGMA journal_mode=WAL")
    wuerfel = random.Random(4711)
    mitte = laeufe // 3
    serie, um_id, gegr_id = (_seriennummer(mitte, laeufe), 10 + mitte % 7,
                             200 + mitte % 5)
    schluessel = db.execute(
        "SELECT prob_id, pm_id, pm_ver, um_id, gegr_id FROM ergebnisse "
        "WHERE serie = ? AND um_id = ? AND gegr_id = ?",
        (serie, um_id, gegr_id)).fetchall()
    saetze = [(round(wuerfel.uniform(0, 500), 4),) * 3 + tuple(s)
              for s in schluessel]

    begonnen = time.perf_counter()
    for anfang in range(0, len(saetze), EXPORT_BUENDEL):
        db.executemany(EXPORT_UPDATE, saetze[anfang:anfang + EXPORT_BUENDEL])
    db.commit()
    dauer = (time.perf_counter() - begonnen) * 1000
    db.close()
    return {"zeilen": len(saetze), "gesamt_ms": round(dauer, 1),
            "je_zeile_us": round(dauer * 1000 / max(1, len(saetze)), 1)}


def indizes_legen(pfad: Path) -> dict:
    """Die drei Indizes, die die Abfragen von LabControl brauchen."""
    db = sqlite3.connect(pfad)
    vorher = _groesse(pfad)
    zeiten = {}
    for name, sql in (
        ("lauf", "CREATE INDEX ix_erg_lauf ON ergebnisse (serie, um_id, gegr_id)"),
        ("schluessel", "CREATE INDEX ix_erg_key ON ergebnisse "
                       "(prob_id, pm_id, pm_ver, um_id, gegr_id)"),
        ("serie", "CREATE INDEX ix_erg_serie ON ergebnisse (serie)"),
    ):
        begonnen = time.perf_counter()
        db.execute(sql)
        db.commit()
        zeiten[name] = round(time.perf_counter() - begonnen, 1)
    db.execute("ANALYZE")
    db.commit()
    db.close()
    return {"bauzeit_s": zeiten, "zuwachs_mb": round((_groesse(pfad) - vorher)
                                                     / 1024**2, 1)}


def sperren_messen(pfad: Path, laeufe: int) -> dict:
    """Was passiert, wenn zwei gleichzeitig arbeiten — drei Fälle.

    Der Reihe nach: zwei Schreiber, ein offener Leser gegen einen Schreiber,
    und die Wartezeit, die ein zweiter Bearbeiter bei einem echten Export
    wirklich abbekommt. Jeder Fall in beiden Journalarten, weil „nimm WAL"
    der übliche Rat ist und man sehen soll, was er hilft und was nicht.
    """
    ergebnis = {}
    for modus in ("delete", "wal"):
        umschalten = sqlite3.connect(pfad)
        gesetzt = umschalten.execute(
            f"PRAGMA journal_mode={modus}").fetchone()[0]
        umschalten.close()

        # Fall 1: zwei Schreiber.
        a = sqlite3.connect(pfad, timeout=1.0)
        b = sqlite3.connect(pfad, timeout=1.0)
        a.execute("BEGIN IMMEDIATE")
        a.execute("UPDATE ergebnisse SET kommentar = 'A' WHERE prob_id = 1")
        begonnen = time.perf_counter()
        try:
            b.execute("BEGIN IMMEDIATE")
            b.execute("UPDATE ergebnisse SET kommentar = 'B' WHERE prob_id = 2")
            zweiter = "durchgelassen"
        except sqlite3.OperationalError as fehler:
            zweiter = str(fehler)
        warten_schreiber = round((time.perf_counter() - begonnen) * 1000)
        # Ein Leser darf in beiden Arten mitlesen, solange nicht gerade
        # festgeschrieben wird — das ist nicht WALs Verdienst.
        c = sqlite3.connect(pfad, timeout=1.0)
        try:
            c.execute("SELECT COUNT(*) FROM ergebnisse "
                      "WHERE prob_id = 1").fetchone()
            leser_bei_schreiber = "durchgelassen"
        except sqlite3.OperationalError as fehler:
            leser_bei_schreiber = str(fehler)
        a.rollback()
        for v in (a, b, c):
            v.close()

        # Fall 2: ein Leser hält eine Abfrage offen, dann will jemand
        # schreiben. Hier trennen sich die Journalarten.
        leser = sqlite3.connect(pfad, timeout=1.0)
        schreiber = sqlite3.connect(pfad, timeout=1.0)
        leser.execute("BEGIN")
        leser.execute("SELECT COUNT(*) FROM ergebnisse "
                      "WHERE prob_id < 50").fetchone()
        begonnen = time.perf_counter()
        try:
            schreiber.execute("BEGIN IMMEDIATE")
            schreiber.execute("UPDATE ergebnisse SET kommentar = 'C' "
                              "WHERE prob_id = 3")
            schreiber.commit()
            schreiber_bei_leser = "durchgelassen"
        except sqlite3.OperationalError as fehler:
            schreiber_bei_leser = str(fehler)
        warten_leser = round((time.perf_counter() - begonnen) * 1000)
        leser.rollback()
        leser.close()
        schreiber.close()

        # Fall 3: die echte Wartezeit. A schreibt einen Export, B will auch.
        wuerfel = random.Random(99)
        mitte = laeufe // 2
        serie, um_id, gegr_id = (_seriennummer(mitte, laeufe),
                                 10 + mitte % 7, 200 + mitte % 5)
        a = sqlite3.connect(pfad, timeout=60.0)
        schluessel = a.execute(
            "SELECT prob_id, pm_id, pm_ver, um_id, gegr_id FROM ergebnisse "
            "WHERE serie = ? AND um_id = ? AND gegr_id = ?",
            (serie, um_id, gegr_id)).fetchall()
        saetze = [(round(wuerfel.uniform(0, 500), 4),) * 3 + tuple(s)
                  for s in schluessel]
        a.execute("BEGIN IMMEDIATE")
        gestartet = time.perf_counter()
        for anfang in range(0, len(saetze), EXPORT_BUENDEL):
            a.executemany(EXPORT_UPDATE, saetze[anfang:anfang + EXPORT_BUENDEL])
        # B will genau jetzt anfangen und bekommt die Wartezeit zu spüren.
        # Die Verbindung entsteht im Faden, der sie benutzt — sqlite3 lässt
        # sie sonst nicht durch.
        gemessen = {}

        def zweiter_bearbeiter():
            b = sqlite3.connect(pfad, timeout=90.0)
            los = time.perf_counter()
            b.execute("BEGIN IMMEDIATE")
            b.execute("UPDATE ergebnisse SET kommentar = 'B' WHERE prob_id = 4")
            b.commit()
            gemessen["warten_ms"] = round((time.perf_counter() - los) * 1000)
            b.close()

        faden = threading.Thread(target=zweiter_bearbeiter)
        faden.start()
        time.sleep(0.05)
        a.commit()
        haltedauer = round((time.perf_counter() - gestartet) * 1000)
        faden.join(timeout=120)
        a.close()

        ergebnis[modus] = {
            "journal_mode": gesetzt,
            "zwei_schreiber": zweiter,
            "warten_schreiber_ms": warten_schreiber,
            "leser_waehrend_schreiber": leser_bei_schreiber,
            "schreiber_waehrend_leser": schreiber_bei_leser,
            "warten_schreiber_bei_leser_ms": warten_leser,
            "export_haltedauer_ms": haltedauer,
            "export_zeilen": len(saetze),
            "wartezeit_zweiter_bearbeiter_ms": gemessen.get("warten_ms"),
        }
    return ergebnis


def grenzen() -> dict:
    db = sqlite3.connect(":memory:")
    seite = db.execute("PRAGMA page_size").fetchone()[0]
    seiten = db.execute("PRAGMA max_page_count").fetchone()[0]
    db.close()
    return {"page_size": seite, "max_page_count": seiten,
            "max_db_tib_4k": round(seite * seiten / 1024**4, 1),
            "max_db_tib_64k": round(65536 * seiten / 1024**4, 1),
            "sqlite": sqlite3.sqlite_version}


def main() -> int:
    zerlegen = argparse.ArgumentParser(description=__doc__)
    zerlegen.add_argument("--zeilen", type=int, default=1_000_000)
    zerlegen.add_argument("--fuellung", choices=sorted(FUELLGRADE),
                          default="mittel")
    zerlegen.add_argument("--kalt", action="store_true",
                          help="Seitencache vor jeder Messung verwerfen")
    zerlegen.add_argument("--datenbank", default=None)
    zerlegen.add_argument("--ausgabe", default=None)
    zerlegen.add_argument("--behalten", action="store_true")
    argumente = zerlegen.parse_args()

    pfad = Path(argumente.datenbank or
                f"/tmp/lims_{argumente.zeilen}_{argumente.fuellung}.sqlite3")
    for anhang in ("", "-wal", "-shm"):
        Path(str(pfad) + anhang).unlink(missing_ok=True)

    print(f"== {argumente.zeilen:,} Zeilen, Füllung „{argumente.fuellung}“",
          f"({FUELLGRADE[argumente.fuellung]} von {FUELL_ZAHL} Füllspalten belegt)")
    db = sqlite3.connect(pfad)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    schema_legen(db)
    fuellbericht = fuellen(db, argumente.zeilen, FUELLGRADE[argumente.fuellung])
    db.close()

    roh_mb = round(_groesse(pfad) / 1024**2, 1)
    print(f"   ohne Index: {roh_mb} MB "
          f"({_groesse(pfad) / fuellbericht['zeilen']:.0f} Byte/Zeile)")

    ohne_index = abfragen_messen(pfad, fuellbericht["laeufe"],
                                 argumente.kalt, runden=3)
    print(f"   Anker ohne Index: {ohne_index['anker']['median_ms']} ms")

    index = indizes_legen(pfad)
    mit_mb = round(_groesse(pfad) / 1024**2, 1)
    print(f"   mit Index:  {mit_mb} MB (+{index['zuwachs_mb']} MB)")

    mit_index = abfragen_messen(pfad, fuellbericht["laeufe"], argumente.kalt)
    print(f"   Anker mit Index:  {mit_index['anker']['median_ms']} ms "
          f"({mit_index['anker']['treffer']} Zeilen)")
    print(f"   Serienliste:      {mit_index['serien']['median_ms']} ms "
          f"({mit_index['serien']['treffer']} Serien)")
    print(f"   COUNT(*):         {mit_index['zaehlen']['median_ms']} ms")

    export = export_messen(pfad, fuellbericht["laeufe"])
    print(f"   Export {export['zeilen']} UPDATE: {export['gesamt_ms']} ms")

    sperren = sperren_messen(pfad, fuellbericht["laeufe"])
    for modus, wert in sperren.items():
        print(f"   {modus}: 2. Schreiber -> {wert['zwei_schreiber']!r} "
              f"({wert['warten_schreiber_ms']} ms) | Schreiber bei offenem "
              f"Leser -> {wert['schreiber_waehrend_leser']!r} "
              f"({wert['warten_schreiber_bei_leser_ms']} ms) | Export hält "
              f"{wert['export_haltedauer_ms']} ms, 2. Bearbeiter wartet "
              f"{wert['wartezeit_zweiter_bearbeiter_ms']} ms")

    bericht = {
        "zeilen_soll": argumente.zeilen, "fuellung": argumente.fuellung,
        "kalt": argumente.kalt, "fuellen": fuellbericht,
        "groesse_mb": {"ohne_index": roh_mb, "mit_index": mit_mb,
                       "byte_je_zeile": round(_groesse(pfad)
                                              / fuellbericht["zeilen"], 1)},
        "index": index, "ohne_index": ohne_index, "mit_index": mit_index,
        "export": export, "sperren": sperren, "grenzen": grenzen(),
        "umgebung": {"python": sys.version.split()[0],
                     "kerne": os.cpu_count()},
    }
    if argumente.ausgabe:
        Path(argumente.ausgabe).write_text(
            json.dumps(bericht, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"   -> {argumente.ausgabe}")
    if not argumente.behalten:
        for anhang in ("", "-wal", "-shm"):
            Path(str(pfad) + anhang).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
