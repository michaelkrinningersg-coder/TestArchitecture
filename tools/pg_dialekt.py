"""Läuft das SQL des LIMS auf PostgreSQL? Ausprobiert, nicht geschätzt.

Dasselbe Verfahren wie beim SQLite-Versuch in [docs/sqlite.md], nur gegen
PostgreSQL — und diesmal mit den Spaltenlisten aus ``tools/spaltenbedarf.py``,
also gegen ein Schema, das der Sache nahekommt statt geraten zu sein.

Vorgehen:

1. Schema aus ``bench/spaltenbedarf.json`` bauen — 27 Tabellen mit den 168
   Spalten, die der Code anspricht. Typen aus der Namenskonvention
   (``*_id`` → integer, ``mw*`` → numeric, ``serie`` → text …); wo geraten
   wird, steht es unten in der Ausgabe.
2. Die vollständigen Anweisungen aus ``lims_db.py`` holen.
3. Übersetzen, was Oracle-eigen ist: ``NVL`` → ``COALESCE``,
   ``ROWNUM <= :n`` → ``LIMIT``, ``FROM dual`` weg. ``REGEXP_LIKE`` bleibt —
   PostgreSQL kennt es ab 15.
4. Jede Anweisung ausführen (in einer Transaktion, die zurückgerollt wird)
   und zählen, was durchläuft.

Aufruf::

    python tools/pg_dialekt.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

try:
    import pg8000.dbapi
except ImportError:                                    # pragma: no cover
    print("pg8000 fehlt: pip install pg8000")
    raise SystemExit(1)

QUELLE = Path("/home/user/testlims/lims_db.py")
SPALTENBEDARF = Path("bench/spaltenbedarf.json")


def typ_raten(spalte: str) -> str:
    """Typ aus dem Namen — die Konvention des LIMS ist erfreulich klar."""
    if spalte.endswith(("_id", "_ver", "_nr")) or spalte in ("id", "version",
                                                             "lnr", "status"):
        return "integer"
    if spalte.startswith("mw") or spalte in ("sollwert", "toleranz", "gu", "go",
                                             "qc_gu", "qc_go", "pr_gu", "pr_go",
                                             "faktor", "faktor_wgh",
                                             "end_faktor", "v_faktor",
                                             "nwg_arbeit", "bg_arbeit",
                                             "ugrenze", "ogrenze", "wert"):
        return "numeric"
    return "text"


def schema_bauen(z, spalten: dict[str, list[str]]) -> tuple[int, int]:
    z.execute("DROP SCHEMA IF EXISTS dialekt CASCADE")
    z.execute("CREATE SCHEMA dialekt")
    z.execute("SET search_path = dialekt")
    tabellen = 0
    spaltenzahl = 0
    for tabelle, namen in sorted(spalten.items()):
        felder = ", ".join(f"{n} {typ_raten(n)}" for n in sorted(set(namen)))
        z.execute(f"CREATE TABLE {tabelle} ({felder})")
        tabellen += 1
        spaltenzahl += len(set(namen))
    return tabellen, spaltenzahl


def anweisungen_holen() -> tuple[list[str], list[str]]:
    """Die mehrzeiligen SQL-Zeichenketten, die für sich vollständig sind."""
    text = QUELLE.read_text(encoding="utf-8")
    kandidaten = re.findall(r'"""(.*?)"""', text, re.S)
    vollstaendig, fragmente = [], []
    for sql in kandidaten:
        k = sql.strip()
        if not re.match(r"^(select|update|insert|delete)\b", k, re.I):
            continue
        # Fragmente, die der Code zur Laufzeit zusammensetzt. Ein „{" ist eine
        # f-String-Lücke — die Anweisung ist erst vollständig, wenn Python sie
        # gefüllt hat. Beim ersten SQLite-Versuch habe ich genau das mit
        # „läuft nicht" verwechselt; deshalb steht es hier getrennt.
        # Kleinschreiben vor dem Vergleich: die Anweisungen enden auf
        # „WHERE", nicht auf „where".
        if "{" in k or k.rstrip().lower().endswith(("and", "or", "where",
                                                    "+", ",", "(")):
            fragmente.append(k)
            continue
        vollstaendig.append(k)
    return vollstaendig, fragmente


def uebersetzen(sql: str) -> tuple[str, list[str]]:
    """Oracle → PostgreSQL. Gibt den Text und die benutzten Griffe zurück."""
    griffe = []
    neu = sql
    if re.search(r"\bNVL\s*\(", neu, re.I):
        neu = re.sub(r"\bNVL\s*\(", "COALESCE(", neu, flags=re.I)
        griffe.append("NVL→COALESCE")
    if re.search(r"\bFROM\s+dual\b", neu, re.I):
        neu = re.sub(r"\bFROM\s+dual\b", "", neu, flags=re.I)
        griffe.append("FROM dual weg")
    if re.search(r"\bROWNUM\b", neu, re.I):
        neu = re.sub(r"\s+AND\s+ROWNUM\s*<=\s*:(\w+)", r" LIMIT :\1", neu,
                     flags=re.I)
        neu = re.sub(r"\bWHERE\s+ROWNUM\s*<=\s*:(\w+)", r"LIMIT :\1", neu,
                     flags=re.I)
        griffe.append("ROWNUM→LIMIT")
    if re.search(r"\bREGEXP_LIKE\b", neu, re.I):
        griffe.append("REGEXP_LIKE (bleibt)")
    # Bindungen: :name → NULL, damit die Anweisung ohne Werte übersetzt wird.
    # Geprüft wird die Syntax und die Auflösung der Namen, nicht das Ergebnis.
    neu = re.sub(r"(?<![:\w]):([a-z_]\w*)", "NULL", neu, flags=re.I)
    return neu, griffe


def main() -> int:
    if not SPALTENBEDARF.exists():
        print(f"{SPALTENBEDARF} fehlt — erst tools/spaltenbedarf.py laufen lassen")
        return 1
    spalten = json.load(SPALTENBEDARF.open(encoding="utf-8"))["spalten"]

    v = pg8000.dbapi.Connection("postgres", host="127.0.0.1", port=5432,
                                database="postgres", password="probe")
    v.autocommit = True
    z = v.cursor()
    z.execute("DROP DATABASE IF EXISTS dialekt_probe")
    z.execute("CREATE DATABASE dialekt_probe")
    v.close()

    v = pg8000.dbapi.Connection("postgres", host="127.0.0.1", port=5432,
                                database="dialekt_probe", password="probe")
    z = v.cursor()
    z.execute("SELECT version()")
    print("==", z.fetchone()[0].split(" on ")[0])

    tabellen, spaltenzahl = schema_bauen(z, spalten)
    v.commit()
    print(f"   Schema gebaut: {tabellen} Tabellen, {spaltenzahl} Spalten\n")

    anweisungen, fragmente = anweisungen_holen()
    laeuft = fehler = 0
    griffe_gesamt = Counter()
    probleme = []
    for sql in anweisungen:
        uebersetzt, griffe = uebersetzen(sql)
        griffe_gesamt.update(griffe)
        z.execute("SAVEPOINT s")
        try:
            z.execute(uebersetzt)
            z.execute("RELEASE SAVEPOINT s")
            laeuft += 1
        except Exception as f:
            z.execute("ROLLBACK TO SAVEPOINT s")
            fehler += 1
            text = str(f)
            for schluessel in ("'M': '", '"M": "'):
                if schluessel in text:
                    text = text.split(schluessel, 1)[1].split(schluessel[-1])[0]
                    break
            probleme.append((sql.split("\n")[0][:58], text[:70]))
    v.rollback()
    v.close()

    print(f"   SELECT/UPDATE/INSERT-Zeichenketten: "
          f"{len(anweisungen) + len(fragmente)}")
    print(f"   davon Fragmente (f-String-Lücken): {len(fragmente)}")
    print(f"   vollständige Anweisungen: {len(anweisungen)}")
    print(f"   davon lauffähig:          {laeuft}")
    print(f"   Fehler:                   {fehler}")
    print()
    print("   benutzte Übersetzungsgriffe:")
    for griff, zahl in griffe_gesamt.most_common():
        print(f"     {griff:<24} {zahl}×")
    if probleme:
        print()
        print("   Was nicht lief:")
        for anfang, meldung in probleme:
            print(f"     {anfang}")
            print(f"        -> {meldung}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
