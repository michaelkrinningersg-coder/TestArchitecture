"""Welche Spalten welcher Tabellen braucht LabControl wirklich?

Liest die SQL-Zeichenketten aus ``lims_db.py``, löst die Tabellen-Kürzel auf
(``FROM ergebnisse e`` → ``e`` ist ``ergebnisse``) und sammelt, welche Spalten
angesprochen werden. Ergebnis ist die Liste, aus der sich

* eine schlanke Sicht im Oracle (``CREATE VIEW``) und
* ein Schema für ein neues LIMS

bauen lassen — ohne im Bestand etwas zu löschen.

Das ist eine **Textauswertung, kein Parser.** Sie findet, was als
``kuerzel.spalte`` oder in einer Spaltenliste dasteht; was der Code zur
Laufzeit zusammensetzt, kann sie übersehen. Die Zahlen sind deshalb eine
**Untergrenze** des Bedarfs, und genau so sind sie zu lesen.

Aufruf::

    python tools/spaltenbedarf.py /home/user/testlims
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Wörter, die in SQL vorkommen und keine Spalten sind.
SQL_WOERTER = {
    "select", "from", "where", "and", "or", "not", "in", "is", "null", "as",
    "join", "left", "right", "inner", "outer", "on", "order", "by", "group",
    "having", "distinct", "union", "all", "insert", "into", "values", "update",
    "set", "delete", "exists", "case", "when", "then", "else", "end", "asc",
    "desc", "count", "sum", "min", "max", "avg", "nvl", "coalesce", "trim",
    "upper", "lower", "to_char", "to_date", "rownum", "dual", "regexp_like",
    "between", "like", "cascade", "constraints", "table", "create", "view",
    "with", "over", "partition", "limit", "fetch", "first", "rows", "only",
}

TABELLE_MIT_KUERZEL = re.compile(
    r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)\s+(?!on\b|where\b|set\b|using\b)"
    r"([a-z][a-z0-9_]{0,4})\b", re.I)
TABELLE_OHNE = re.compile(
    r"\b(?:from|join|update|into)\s+([a-z_][a-z0-9_]*)", re.I)
QUALIFIZIERT = re.compile(r"\b([a-z][a-z0-9_]{0,4})\.([a-z_][a-z0-9_]*)\b", re.I)


def sql_texte(quelle: Path) -> list[str]:
    """Alle mehrzeiligen Zeichenketten, die nach SQL aussehen."""
    text = quelle.read_text(encoding="utf-8")
    treffer = re.findall(r'"""(.*?)"""', text, re.S)
    treffer += re.findall(r"'''(.*?)'''", text, re.S)
    treffer += re.findall(r'f?"((?:[^"\\\n]|\\.)*)"', text)
    schluesselwoerter = ("select ", "update ", "insert ", "delete ")
    return [t for t in treffer
            if any(w in t.lower() for w in schluesselwoerter)]


BINDUNG = re.compile(r":([a-z_][a-z0-9_]*)", re.I)
# Stellen, an denen in SQL ein Spaltenname steht und sonst wenig.
SELECT_LISTE = re.compile(r"\bselect\s+(?:distinct\s+)?(.*?)\s+\bfrom\b",
                          re.I | re.S)
SET_LISTE = re.compile(r"\bset\b(.*?)(?:\bwhere\b|$)", re.I | re.S)
INSERT_LISTE = re.compile(r"\binsert\s+into\s+\w+\s*\(([^)]*)\)", re.I | re.S)
VERGLEICH = re.compile(r"\b([a-z_][a-z0-9_]{2,})\s*(?:=|<|>|<=|>=|!=|<>|\bis\b"
                       r"|\bin\b|\blike\b|\bbetween\b)", re.I)
NUR_WORT = re.compile(r"\b([a-z_][a-z0-9_]{1,})\b", re.I)


def _worte(text: str) -> set[str]:
    return {w.lower() for w in NUR_WORT.findall(text or "")}


def auswerten(quelle: Path, bekannte: set[str]) -> dict:
    """Sammelt je Tabelle die angesprochenen Spalten.

    Zwei Wege, und beide bewusst eng: qualifizierte Namen (``e.mw_roh``)
    gehören eindeutig ihrer Tabelle. Unqualifizierte werden nur dort
    aufgesammelt, wo in SQL ein Spaltenname stehen *muss* — in der
    SELECT-Liste, hinter SET, in der INSERT-Spaltenliste und links von einem
    Vergleich — und nur, wenn die Anweisung genau eine bekannte Tabelle
    nennt. Bindungsnamen (``:neu_gegr``) werden ausgeschlossen; sonst
    landeten sie als Spalten in der Liste.
    """
    spalten: dict[str, set[str]] = defaultdict(set)
    tabellen_gesehen: set[str] = set()

    for sql in sql_texte(quelle):
        klein = sql.lower()
        # Nur Namen ausschließen, die *ausschließlich* als Bindung
        # vorkommen. „anwender = NVL(anwender, :anwender)" nennt dieselbe
        # Zeichenfolge als Spalte und als Bindung — wer beides wegwirft,
        # verliert die Spalte.
        bindungen = set()
        for name in {b.lower() for b in BINDUNG.findall(klein)}:
            ohne_doppelpunkt = re.search(rf"(?<![:a-z0-9_]){name}\b", klein)
            if ohne_doppelpunkt is None:
                bindungen.add(name)

        kuerzel: dict[str, str] = {}
        for tabelle, kurz in TABELLE_MIT_KUERZEL.findall(klein):
            if tabelle in bekannte:
                kuerzel[kurz] = tabelle
        namen = [t for t in TABELLE_OHNE.findall(klein) if t in bekannte]
        tabellen_gesehen.update(namen)

        for kurz, spalte in QUALIFIZIERT.findall(klein):
            if (kurz in kuerzel and spalte not in SQL_WOERTER
                    and spalte not in bindungen):
                spalten[kuerzel[kurz]].add(spalte)

        if len(set(namen)) != 1:
            continue
        einzige = namen[0]
        kandidaten: set[str] = set()
        for muster in (SELECT_LISTE, SET_LISTE, INSERT_LISTE):
            for stueck in muster.findall(klein):
                kandidaten |= _worte(stueck)
        kandidaten |= {w.lower() for w in VERGLEICH.findall(klein)}
        for wort in kandidaten:
            if (wort not in SQL_WOERTER and wort not in bekannte
                    and wort not in bindungen and len(wort) > 1):
                spalten[einzige].add(wort)

    return {"spalten": {t: sorted(s) for t, s in sorted(spalten.items())},
            "tabellen": sorted(tabellen_gesehen)}


def tabellenliste(doku: Path) -> dict[str, int]:
    text = doku.read_text(encoding="utf-8")
    return {name.lower(): int(zahl) for name, zahl
            in re.findall(r"^- `([A-Z0-9_$#]+)` \((\d+)\)", text, re.M)}


def sichten_schreiben(bericht: dict, alle: dict[str, int], ziel: Path) -> None:
    """Schreibt die schlanke Sicht als Oracle-DDL — ohne ein DROP.

    Eine Sicht je Tabelle, genau die Spalten, die der Code anspricht. Das ist
    der gefahrlose Weg zu „nur was ich brauche": das Bestandssystem merkt
    nichts davon, und Rechte lassen sich je Sicht vergeben.
    """
    tabellen = bericht["spalten"]
    zeilen = [
        "-- Schlanke Sicht auf das LIMS — erzeugt von tools/spaltenbedarf.py",
        "--",
        "-- Eine Sicht je Tabelle mit genau den Spalten, die LabControl",
        f"-- anspricht: {len(tabellen)} Tabellen, "
        f"{sum(min(len(s), alle.get(t, 0)) for t, s in tabellen.items())} Spalten.",
        "--",
        "-- KEIN DROP, KEIN ALTER. Das Bestandssystem bleibt unberuehrt; diese",
        "-- Sichten liegen daneben. Anzulegen in einem EIGENEN Schema",
        "-- (hier: NEUES_LIMS), nicht im Schema des LIMS.",
        "--",
        "-- Vor dem Ausfuehren pruefen: die Spaltenliste stammt aus einer",
        "-- Textauswertung des Quelltexts und ist eine Untergrenze. Was der",
        "-- Code zur Laufzeit zusammensetzt, kann fehlen.",
        "",
        "-- Welche Spalten es wirklich gibt, sagt Oracle selbst:",
        "--   SELECT table_name, column_name, data_type, data_length,",
        "--          data_precision, data_scale, nullable",
        "--     FROM all_tab_columns",
        "--    WHERE owner = 'LIMSADMIN'",
        "--      AND table_name IN (" + ", ".join(
            f"'{t.upper()}'" for t in sorted(tabellen)) + ")",
        "--    ORDER BY table_name, column_id;",
        "",
    ]
    for tabelle in sorted(tabellen):
        spalten = tabellen[tabelle]
        if not alle.get(tabelle):
            continue
        zeilen.append(f"-- {tabelle.upper()}: {len(spalten)} von "
                      f"{alle[tabelle]} Spalten")
        zeilen.append(f"CREATE OR REPLACE VIEW v_{tabelle} AS")
        zeilen.append("  SELECT " + ("," + chr(10) + "         ").join(spalten))
        zeilen.append(f"    FROM limsadmin.{tabelle};")
        zeilen.append(f"-- GRANT SELECT ON v_{tabelle} TO labor_lesen;")
        zeilen.append("")
    ziel.write_text("\n".join(zeilen), encoding="utf-8")


def main() -> int:
    wurzel = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/user/testlims")
    alle = tabellenliste(wurzel / "LIMS-Tabellen.md")
    bericht = auswerten(wurzel / "lims_db.py", set(alle))

    # Nur Spalten, die es in der Tabelle laut Doku geben kann: die Auswertung
    # fängt sonst Bindungsnamen mit ein. Gefiltert wird über die Spaltenzahl
    # nicht, sondern über Plausibilität — deshalb steht beides da.
    print(f"{'Tabelle':<28}{'genutzt':>8}{'gesamt':>8}  Anteil")
    print("-" * 58)
    summe_genutzt = summe_gesamt = 0
    for tabelle, spalten in bericht["spalten"].items():
        gesamt = alle.get(tabelle, 0)
        if not gesamt:
            continue
        genutzt = min(len(spalten), gesamt)
        summe_genutzt += genutzt
        summe_gesamt += gesamt
        print(f"{tabelle.upper():<28}{genutzt:>8}{gesamt:>8}"
              f"  {genutzt / gesamt * 100:>5.0f} %")
    print("-" * 58)
    print(f"{len(bericht['spalten']):>2} Tabellen von {len(alle)}"
          f"{summe_genutzt:>14}{summe_gesamt:>8}"
          f"  {summe_genutzt / max(1, summe_gesamt) * 100:>5.0f} %")
    spalten_aller = sum(alle.values())
    print(f"\nvon allen {spalten_aller} Spalten der {len(alle)} Tabellen: "
          f"{summe_genutzt / spalten_aller * 100:.1f} %")

    ziel = Path("bench/spaltenbedarf.json")
    if ziel.parent.exists():
        ziel.write_text(json.dumps(bericht, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        print(f"-> {ziel}")
    sichten = Path("docs/sichten.sql")
    if sichten.parent.exists():
        sichten_schreiben(bericht, alle, sichten)
        print(f"-> {sichten}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
