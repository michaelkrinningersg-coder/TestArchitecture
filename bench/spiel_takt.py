"""Trägt Qt 6 + SQLite ein Spiel? Fußballmanager und Idle-Game, gemessen.

Beide Gattungen sind im Kern dasselbe wie LabControl: viele Sätze in
Tabellen, eine Rechnung darüber, eine Oberfläche daneben. Was sie
zusätzlich brauchen, ist ein **Takt** — und ob Qt den hält und Python die
Rechnung dahinter schafft, ist messbar statt Glaubensfrage.

Gemessen wird:

* **Takt**      hält ein QTimer 60 Hz, und mit welcher Abweichung?
* **Idle**      wie viele Ticks über 10 000 Werte pro Sekunde?
* **Offline**   der Zuwachs von 30 Tagen, geschlossen gerechnet statt getickt
* **Spiel**     ein Spiel Minute für Minute, eine Saison, zwanzig Saisons
* **Ablage**    Kader und Saison in SQLite: Größe und Ladezeit

Aufruf: ``QT_QPA_PLATFORM=offscreen python bench/spiel_takt.py``
"""

from __future__ import annotations

import json
import math
import os
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TAKT_HZ = 60
TAKT_DAUER_S = 3.0
IDLE_WERTE = 10_000
VEREINE = 18
SPIELTAGE = (VEREINE - 1) * 2
SPIELE_JE_SAISON = SPIELTAGE * VEREINE // 2
SAISONS = 20


def takt_messen() -> dict:
    """Hält ein QTimer 60 Hz — und wie weit weicht er ab?"""
    from PySide6.QtCore import (QCoreApplication, QElapsedTimer, Qt,
                                QTimer)

    programm = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    uhr = QElapsedTimer()
    abstaende: list[float] = []
    letzte = [0]

    def tick():
        jetzt = uhr.nsecsElapsed() / 1e6
        abstaende.append(jetzt - letzte[0])
        letzte[0] = jetzt
        if jetzt >= TAKT_DAUER_S * 1000:
            programm.quit()

    zeitgeber = QTimer()
    zeitgeber.setTimerType(Qt.PreciseTimer)
    zeitgeber.timeout.connect(tick)
    uhr.start()
    zeitgeber.start(round(1000 / TAKT_HZ))
    programm.exec()
    zeitgeber.stop()

    gemessen = abstaende[1:]            # der erste Abstand zählt das Starten mit
    soll = 1000 / TAKT_HZ
    return {"soll_ms": round(soll, 2), "ticks": len(gemessen),
            "median_ms": round(statistics.median(gemessen), 2),
            "p95_ms": round(sorted(gemessen)[int(len(gemessen) * 0.95)], 2),
            "hoechster_ms": round(max(gemessen), 2),
            "abweichung_median_ms": round(
                statistics.median([abs(a - soll) for a in gemessen]), 2)}


def idle_messen() -> dict:
    """Ein Tick über 10 000 Werte: Zuwachs, Deckel, Freischaltung."""
    wuerfel = random.Random(2)
    bestand = [wuerfel.uniform(0, 1000) for _ in range(IDLE_WERTE)]
    rate = [wuerfel.uniform(0.0001, 0.01) for _ in range(IDLE_WERTE)]
    deckel = [b * 1000 + 1 for b in bestand]

    def ein_tick():
        for i in range(IDLE_WERTE):
            neu = bestand[i] * (1 + rate[i])
            bestand[i] = neu if neu < deckel[i] else deckel[i]

    ein_tick()                                    # aufwärmen
    begonnen = time.perf_counter()
    runden = 0
    while time.perf_counter() - begonnen < 1.0:
        ein_tick()
        runden += 1
    dauer = time.perf_counter() - begonnen
    return {"werte": IDLE_WERTE, "ticks_pro_s": round(runden / dauer),
            "ms_je_tick": round(dauer * 1000 / runden, 2)}


def offline_messen() -> dict:
    """30 Tage Abwesenheit: getickt gegen geschlossen gerechnet.

    Der Trick jedes Idle-Games — nicht nachticken, sondern integrieren. Und
    gleich der zweite Befund: über 155 Millionen Ticks läuft ein ``float``
    über. Ein Idle-Game rechnet deshalb im Logarithmus (oder mit Deckel),
    nicht mit dem Bestand selbst.
    """
    wuerfel = random.Random(3)
    bestand = [wuerfel.uniform(1, 1000) for _ in range(IDLE_WERTE)]
    rate = [wuerfel.uniform(0.0001, 0.01) for _ in range(IDLE_WERTE)]
    ticks = 30 * 24 * 3600 * TAKT_HZ          # 30 Tage bei 60 Hz

    # Im Logarithmus, deshalb ohne Überlauf: log10(bestand · (1+r)^ticks).
    begonnen = time.perf_counter()
    geschlossen_log10 = [math.log10(bestand[i])
                         + ticks * math.log10(1 + rate[i])
                         for i in range(IDLE_WERTE)]
    geschlossen_ms = (time.perf_counter() - begonnen) * 1000

    # Läuft ein float dabei über?
    ueberlauf = False
    try:
        bestand[0] * math.exp(math.log1p(rate[0]) * ticks)
    except OverflowError:
        ueberlauf = True

    # Dieselbe Rechnung getickt — nur 1 000 Ticks, dann hochgerechnet.
    probe = list(bestand)
    begonnen = time.perf_counter()
    for _ in range(1000):
        for i in range(IDLE_WERTE):
            probe[i] *= 1 + rate[i]
    je_tick = (time.perf_counter() - begonnen) / 1000
    return {"ticks_fuer_30_tage": ticks,
            "geschlossen_ms": round(geschlossen_ms, 2),
            "getickt_hochgerechnet_h": round(je_tick * ticks / 3600, 1),
            "float_laeuft_ueber": ueberlauf,
            "zehnerpotenzen_nach_30_tagen":
                round(max(geschlossen_log10))}


def _spiel(wuerfel: random.Random, heim: dict, gast: dict) -> tuple[int, int]:
    """Ein Spiel Minute für Minute — Chancen aus der Stärke, Tore daraus."""
    tore = [0, 0]
    staerke = (heim["staerke"] * 1.1, gast["staerke"])
    for _ in range(90):
        for seite in (0, 1):
            if wuerfel.random() < staerke[seite] / 3000:
                if wuerfel.random() < 0.35:
                    tore[seite] += 1
    return tore[0], tore[1]


def spiel_messen() -> dict:
    wuerfel = random.Random(4)
    vereine = [{"id": i, "name": f"Verein {i}",
                "staerke": wuerfel.uniform(40, 90)} for i in range(VEREINE)]

    begonnen = time.perf_counter()
    for _ in range(100):
        _spiel(wuerfel, vereine[0], vereine[1])
    je_spiel_ms = (time.perf_counter() - begonnen) * 10

    begonnen = time.perf_counter()
    ergebnisse = []
    for _ in range(SPIELE_JE_SAISON):
        h, g = wuerfel.sample(vereine, 2)
        ergebnisse.append((h["id"], g["id"], *_spiel(wuerfel, h, g)))
    saison_ms = (time.perf_counter() - begonnen) * 1000

    return {"ms_je_spiel": round(je_spiel_ms, 3),
            "spiele_je_saison": SPIELE_JE_SAISON,
            "saison_ms": round(saison_ms, 1),
            "zwanzig_saisons_s": round(saison_ms * SAISONS / 1000, 2),
            "spiele_pro_s": round(SPIELE_JE_SAISON / (saison_ms / 1000))}


def ablage_messen(pfad: Path) -> dict:
    """Kader und zwanzig Saisons in SQLite: Größe und Ladezeit."""
    for anhang in ("", "-wal", "-shm"):
        Path(str(pfad) + anhang).unlink(missing_ok=True)
    wuerfel = random.Random(5)
    db = sqlite3.connect(pfad)
    db.executescript("""
        CREATE TABLE spieler (
          id INTEGER PRIMARY KEY, verein INTEGER, name TEXT, position TEXT,
          alter_j INTEGER, staerke REAL, vertrag_bis INTEGER, gehalt INTEGER,
          form REAL, fitness REAL);
        CREATE TABLE spiele (
          saison INTEGER, spieltag INTEGER, heim INTEGER, gast INTEGER,
          tore_heim INTEGER, tore_gast INTEGER);
        CREATE INDEX ix_spieler_verein ON spieler (verein);
        CREATE INDEX ix_spiele_saison ON spiele (saison, spieltag);
    """)
    # Zehn Ligen zu achtzehn Vereinen, je fünfundzwanzig Spieler.
    spieler = [(None, v, f"Spieler {i}", "MF", wuerfel.randint(17, 36),
                wuerfel.uniform(30, 95), 2028, wuerfel.randint(500, 90000),
                1.0, 1.0)
               for v in range(VEREINE * 10)
               for i in range(25)
               for _ in (0,)
               for _ in (0,)][:VEREINE * 10 * 25]
    spieler = [(i + 1, *rest[1:]) for i, rest in enumerate(spieler)]
    db.executemany("INSERT INTO spieler VALUES (?,?,?,?,?,?,?,?,?,?)", spieler)
    spiele = [(s, t, wuerfel.randrange(VEREINE), wuerfel.randrange(VEREINE),
               wuerfel.randint(0, 5), wuerfel.randint(0, 5))
              for s in range(SAISONS) for t in range(SPIELTAGE)
              for _ in range(VEREINE // 2)]
    db.executemany("INSERT INTO spiele VALUES (?,?,?,?,?,?)", spiele)
    db.commit()
    db.close()

    groesse_kb = round(pfad.stat().st_size / 1024)
    begonnen = time.perf_counter()
    db = sqlite3.connect(pfad)
    kader = db.execute("SELECT * FROM spieler WHERE verein = 7").fetchall()
    tabelle = db.execute("""
        SELECT heim, SUM(CASE WHEN tore_heim > tore_gast THEN 3
                              WHEN tore_heim = tore_gast THEN 1 ELSE 0 END)
          FROM spiele WHERE saison = 19 GROUP BY heim ORDER BY 2 DESC
    """).fetchall()
    laden_ms = (time.perf_counter() - begonnen) * 1000
    db.close()
    return {"spieler": len(spieler), "spiele": len(spiele),
            "groesse_kb": groesse_kb, "laden_ms": round(laden_ms, 1),
            "kader": len(kader), "tabellenplaetze": len(tabelle)}


def main() -> int:
    bericht = {
        "takt": takt_messen(),
        "idle": idle_messen(),
        "offline": offline_messen(),
        "spiel": spiel_messen(),
        "ablage": ablage_messen(Path("/tmp/manager.sqlite3")),
        "umgebung": {"python": sys.version.split()[0], "kerne": os.cpu_count(),
                     "plattform": os.environ.get("QT_QPA_PLATFORM")},
    }
    print(json.dumps(bericht, indent=2, ensure_ascii=False))
    ziel = Path("bench/spiel_takt.json")
    if ziel.parent.exists():
        ziel.write_text(json.dumps(bericht, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
