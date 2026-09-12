"""Rechte für Benutzer — an den vollen Tabellen, ohne Sichten.

Der Weg, wenn alles bleibt, wie es ist, und nur die Oberfläche neu wird. Er
braucht drei Bausteine und sonst nichts:

1. **Jeder Mensch hat sein eigenes Datenbankkonto.** Das habt ihr schon:
   ``lims_db.Zugang`` verbindet mit ``oracledb.connect(user=…, password=…)``,
   und das Passwort „lebt nur hier im Arbeitsspeicher".
2. **Rollen tragen die Rechte**, nicht die Personen. Eine Rolle wird einmal
   eingerichtet; danach ist „darf bearbeiten" eine Zeile je Person.
3. **Rechte je Spalte** statt je Tabelle. Damit braucht es keine Sicht, um zu
   begrenzen, was geändert werden darf — die Tabelle bleibt voll, das Recht
   wird schmal.

Was hier ausprobiert wird, ist genau der Rechteschnitt aus eurem Code:
gezählt wurde, welche Spalte in welcher Tabelle je auf der linken Seite eines
``SET`` steht oder in einer ``INSERT``-Spaltenliste.

> Vorgeführt auf PostgreSQL 16, weil hier kein Oracle 11.2 steht. Die
> ``GRANT``-Anweisungen sind in beiden gleich; die zwei Unterschiede stehen am
> Ende der Ausgabe.

Aufruf::

    python tools/rechte_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pg8000.dbapi

# Ausgezählt aus lims_db.py: was LabControl je schreibt.
SCHREIBRECHTE = {
    "ergebnisse": {"UPDATE": ["anwender", "datum_zeit", "fc7", "fc8", "fc9",
                              "gegr_id", "geme_id", "kommentar",
                              "korrektur_flag", "mw", "mw_n", "mw_org",
                              "mw_roh", "pm_id", "pm_ver", "psta_id",
                              "v_faktor", "vkd_id"]},
    "proben": {"UPDATE": ["bemerkung"]},
    "teilproben_anhang": {"UPDATE": ["mw", "mw_old"]},
    # Die Liste STANDARDPARA_PFLEGBAR aus lims_db.py, wörtlich.
    "standard_para": {"UPDATE": ["sollwert", "toleranz", "gu", "go",
                                 "qc_gu", "qc_go"]},
    "bew_teil": {"UPDATE": ["bewertung", "kommentar", "qp_art"],
                 "INSERT": None},          # INSERT über alle sechs Spalten
    "serien_mw_anhang": {"UPDATE": ["stat_id"], "INSERT": None,
                         "DELETE": None},  # INSERT kopiert die ganze Zeile
}

# Wer darf was. Die Namen sind Vorschläge, der Schnitt kommt aus dem Code.
ROLLEN = {
    "labor_lesen":      [],
    "labor_bearbeiten": ["ergebnisse", "proben", "teilproben_anhang",
                         "serien_mw_anhang"],
    "labor_qp":         ["bew_teil"],
    "labor_stammdaten": ["standard_para"],
}

def _lesetabellen() -> list[str]:
    """Alle Tabellen, die der Code anfasst — aus tools/spaltenbedarf.py."""
    datei = Path("bench/spaltenbedarf.json")
    if datei.exists():
        return sorted(json.load(datei.open(encoding="utf-8"))["spalten"])
    return sorted(SCHREIBRECHTE) + ["parameter", "pruefmethoden"]


LESETABELLEN = _lesetabellen()

BREITE = {"ergebnisse": 81, "proben": 51, "teilproben_anhang": 12,
          "standard_para": 17, "bew_teil": 6, "serien_mw_anhang": 8,
          "parameter": 26, "pruefmethoden": 38}


def verbinden(benutzer="postgres", passwort="probe", datenbank="rechte_probe"):
    v = pg8000.dbapi.Connection(benutzer, host="127.0.0.1", port=5432,
                                database=datenbank, password=passwort)
    return v


def versuch(benutzer: str, sql: str) -> str:
    """Führt als dieser Benutzer aus und sagt, was passiert ist."""
    try:
        v = verbinden(benutzer, "probe")
    except Exception as fehler:
        return f"Anmeldung: {str(fehler)[:50]}"
    z = v.cursor()
    try:
        z.execute("SET search_path = limsadmin")
        z.execute(sql)
        ergebnis = ("durchgelassen" if z.description is None
                    else str(z.fetchone()[0]))
        v.rollback()
        return ergebnis
    except Exception as fehler:
        text = str(fehler)
        for s in ("'M': '", '"M": "'):
            if s in text:
                text = text.split(s, 1)[1].split(s[-1])[0]
                break
        return text[:60]
    finally:
        v.close()


def aufbauen() -> None:
    chef = verbinden(datenbank="postgres")
    chef.autocommit = True
    z = chef.cursor()
    # Erst die Datenbanken früherer Vorführungen weg — solange dort Rechte
    # für eine Rolle stehen, lässt sich die Rolle nicht löschen.
    for datenbank in ("rechte_probe", "demo_lims", "dialekt_probe", "lims"):
        z.execute(f"DROP DATABASE IF EXISTS {datenbank}")
    for rolle in list(ROLLEN) + ["anna", "bernd", "clara", "dora",
                                 "labor_arbeiten", "neu_lesen"]:
        z.execute(f"DROP ROLE IF EXISTS {rolle}")
    z.execute("CREATE DATABASE rechte_probe")
    chef.close()

    v = verbinden()
    z = v.cursor()
    z.execute("CREATE SCHEMA limsadmin")
    z.execute("SET search_path = limsadmin")
    for tabelle in LESETABELLEN:
        breite = BREITE.get(tabelle, 12)
        spalten = sorted({s for rechte in
                          [SCHREIBRECHTE.get(tabelle, {})] for liste in
                          rechte.values() if liste for s in liste})
        rest = [f"r{i:02d} text" for i in range(1, breite - len(spalten) + 1)]
        felder = ", ".join([f"{s} numeric" if s.startswith("mw") or s in
                            ("sollwert", "toleranz", "gu", "go", "qc_gu",
                             "qc_go", "v_faktor", "mw_old")
                            else f"{s} text" for s in spalten] + rest)
        z.execute(f"CREATE TABLE {tabelle} ({felder})")
        z.execute(f"INSERT INTO {tabelle} DEFAULT VALUES")
    v.commit()
    return v, z


def rechte_vergeben(v, z) -> list[str]:
    """Genau die Anweisungen, die auch in Oracle stehen würden."""
    zeilen = []
    for rolle in ROLLEN:
        z.execute(f"CREATE ROLE {rolle}")
        zeilen.append(f"CREATE ROLE {rolle};")
    z.execute("GRANT USAGE ON SCHEMA limsadmin TO labor_lesen")
    zeilen.append("GRANT USAGE ON SCHEMA limsadmin TO labor_lesen;")
    for tabelle in LESETABELLEN:
        z.execute(f"GRANT SELECT ON {tabelle} TO labor_lesen")
    zeilen.append(f"-- Lesen: GRANT SELECT ON <{len(LESETABELLEN)} Tabellen> "
                  f"TO labor_lesen;")
    # Jede Arbeitsrolle erbt das Lesen.
    for rolle in ("labor_bearbeiten", "labor_qp", "labor_stammdaten"):
        z.execute(f"GRANT labor_lesen TO {rolle}")
        zeilen.append(f"GRANT labor_lesen TO {rolle};")
    for rolle, tabellen in ROLLEN.items():
        for tabelle in tabellen:
            for art, spalten in SCHREIBRECHTE[tabelle].items():
                if spalten:
                    liste = ", ".join(spalten)
                    z.execute(f"GRANT {art} ({liste}) ON {tabelle} TO {rolle}")
                    zeilen.append(f"GRANT {art} ({liste})"
                                  f"\n      ON {tabelle} TO {rolle};")
                else:
                    z.execute(f"GRANT {art} ON {tabelle} TO {rolle}")
                    zeilen.append(f"GRANT {art} ON {tabelle} TO {rolle};")
    for person, rolle in (("anna", "labor_lesen"),
                          ("bernd", "labor_bearbeiten"),
                          ("clara", "labor_qp"),
                          ("dora", "labor_stammdaten")):
        z.execute(f"CREATE ROLE {person} LOGIN PASSWORD 'probe'")
        z.execute(f"GRANT {rolle} TO {person}")
        zeilen.append(f"GRANT {rolle} TO {person};")
    v.commit()
    return zeilen


def oracle_skript(ziel: Path) -> int:
    """Schreibt dasselbe Rechtemodell als Oracle-DDL."""
    zeilen = [
        "-- Rechte fuer das neue LabControl — erzeugt von tools/rechte_demo.py",
        "--",
        "-- Voraussetzung, die schon erfuellt ist: jeder Mensch hat ein eigenes",
        "-- Oracle-Konto. lims_db.Zugang verbindet mit oracledb.connect(user=…,",
        "-- password=…), und das Passwort liegt nur im Arbeitsspeicher.",
        "--",
        "-- Die Tabellen bleiben VOLL. Begrenzt wird ueber das Recht, nicht",
        "-- ueber eine Sicht — deshalb entfaellt auch die WITH-GRANT-OPTION-",
        "-- Frage und das Rollenproblem beim Uebersetzen von Sichten.",
        "--",
        "-- Vom DBA einmal auszufuehren, angemeldet als LIMSADMIN (oder mit",
        "-- GRANT ANY OBJECT PRIVILEGE).",
        "",
        "-- 1. Rollen. Vier Schnitte, abgeleitet aus dem Code.",
    ]
    for rolle in ROLLEN:
        zeilen.append(f"CREATE ROLE {rolle};")
    zeilen += ["",
               "-- 2. Lesen: alle Tabellen, die der Code anfasst.",
               "--    Oracle kann SELECT NICHT je Spalte einschraenken —",
               "--    Lesen ist immer die ganze Tabelle."]
    for tabelle in LESETABELLEN:
        zeilen.append(f"GRANT SELECT ON limsadmin.{tabelle} TO labor_lesen;")
    zeilen += ["", "-- 3. Jede Arbeitsrolle erbt das Lesen."]
    for rolle in ("labor_bearbeiten", "labor_qp", "labor_stammdaten"):
        zeilen.append(f"GRANT labor_lesen TO {rolle};")
    zeilen += ["",
               "-- 4. Schreiben — je Spalte, ausgezaehlt aus lims_db.py:",
               "--    jede Spalte, die dort je links von einem SET steht",
               "--    oder in einer INSERT-Spaltenliste."]
    for rolle, tabellen in ROLLEN.items():
        for tabelle in tabellen:
            for art, spalten in SCHREIBRECHTE[tabelle].items():
                if spalten:
                    liste = ", ".join(spalten)
                    zeilen.append(f"GRANT {art} ({liste})")
                    zeilen.append(f"      ON limsadmin.{tabelle} TO {rolle};"
                                  f"   -- {len(spalten)} von "
                                  f"{BREITE.get(tabelle, '?')} Spalten")
                else:
                    grund = ("INSERT kopiert die ganze Zeile"
                             if art == "INSERT" else "ganze Zeile")
                    zeilen.append(f"GRANT {art} ON limsadmin.{tabelle} "
                                  f"TO {rolle};   -- {grund}")
    zeilen += ["",
               "-- 5. Personen. Ab hier ist 'darf bearbeiten' eine Zeile.",
               "GRANT labor_lesen      TO anna;",
               "GRANT labor_bearbeiten TO bernd;",
               "GRANT labor_qp         TO clara;",
               "GRANT labor_stammdaten TO dora;",
               "",
               "-- 6. Damit die Rollen nicht am DBA haengen: wer sie",
               "--    weitergeben darf, bekommt sie mit ADMIN OPTION.",
               "GRANT labor_bearbeiten TO laborleitung WITH ADMIN OPTION;",
               "GRANT labor_qp         TO laborleitung WITH ADMIN OPTION;",
               "",
               "-- 7. Entziehen wirkt sofort, ohne Neustart:",
               "-- REVOKE labor_bearbeiten FROM bernd;",
               "",
               "-- Nachsehen, was jemand darf:",
               "--   SELECT * FROM user_role_privs;",
               "--   SELECT table_name, column_name, privilege",
               "--     FROM user_col_privs ORDER BY table_name, column_name;",
               "--   SELECT table_name, privilege FROM user_tab_privs;",
               ]
    ziel.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return len([z for z in zeilen if z.strip() and not z.startswith("--")])


def main() -> int:
    v, z = aufbauen()
    anweisungen = rechte_vergeben(v, z)

    ziel = Path("docs/rechte.sql")
    anzahl = oracle_skript(ziel) if ziel.parent.exists() else 0
    print(f"== Das Oracle-Skript: {anzahl} Anweisungen -> {ziel}")
    print("   Auszug:")
    for zeile in anweisungen[:6]:
        print("   " + zeile)
    print(f"   … insgesamt {len(anweisungen)} Anweisungen\n")

    print("== Wer darf was")
    proben = [
        ("Ergebnisse lesen", "SELECT count(*) FROM ergebnisse"),
        ("Messwert schreiben", "UPDATE ergebnisse SET mw_roh = 1.5"),
        ("Bemerkung an Probe", "UPDATE proben SET bemerkung = 'x'"),
        ("QP-Urteil setzen", "UPDATE bew_teil SET bewertung = 'OK'"),
        ("Sollwert pflegen", "UPDATE standard_para SET sollwert = 2.0"),
    ]
    kopf = f"   {'':<20}" + "".join(f"{p:<16}" for p in
                                    ("anna", "bernd", "clara", "dora"))
    print(kopf)
    print("   " + "-" * (20 + 64))
    for name, sql in proben:
        zeile = f"   {name:<20}"
        for person in ("anna", "bernd", "clara", "dora"):
            antwort = versuch(person, sql)
            kurz = ("ja" if antwort == "durchgelassen"
                    else antwort if antwort.isdigit()
                    else "nein")
            zeile += f"{kurz:<16}"
        print(zeile)

    print()
    print("== Rechte je Spalte: dieselbe Tabelle, andere Spalte")
    for name, sql in (
        ("mw_roh (erlaubt)", "UPDATE ergebnisse SET mw_roh = 1.5"),
        ("mw (erlaubt)", "UPDATE ergebnisse SET mw = 1.5"),
        ("r01 (nicht erlaubt)", "UPDATE ergebnisse SET r01 = 'x'"),
        ("proben.bemerkung", "UPDATE proben SET bemerkung = 'x'"),
        ("proben.r01", "UPDATE proben SET r01 = 'x'"),
        ("standard_para.sollwert", "UPDATE standard_para SET sollwert = 1"),
        ("standard_para.r01", "UPDATE standard_para SET r01 = 'x'"),
    ):
        print(f"   bernd: {name:<24} -> {versuch('bernd', sql)}")

    print()
    print("== Rolle entziehen wirkt sofort")
    # Das Leserecht bekommt bernd zusätzlich direkt, damit der Entzug der
    # Arbeitsrolle nur das Schreiben trifft und nicht auch das Lesen.
    z.execute("GRANT labor_lesen TO bernd")
    v.commit()
    print(f"   {'bernd schreibt':<30} {versuch('bernd', proben[1][1])}")
    print(f"   {'bernd liest':<30} {versuch('bernd', proben[0][1])}")
    z.execute("REVOKE labor_bearbeiten FROM bernd")
    v.commit()
    print("   REVOKE labor_bearbeiten FROM bernd;")
    print(f"   {'bernd schreibt':<30} {versuch('bernd', proben[1][1])}")
    print(f"   {'bernd liest weiter':<30} {versuch('bernd', proben[0][1])}")
    z.execute("GRANT labor_bearbeiten TO bernd")
    v.commit()
    print("   GRANT labor_bearbeiten TO bernd;")
    print(f"   {'bernd schreibt wieder':<30} {versuch('bernd', proben[1][1])}")
    v.close()

    print()
    print("== Die Falle: ein Tabellenrecht schlaegt das Spaltenrecht")
    v = verbinden()
    z = v.cursor()
    z.execute("SET search_path = limsadmin")
    print(f"   {'vorher, r01':<34} "
          f"{versuch('bernd', 'UPDATE ergebnisse SET r01 = 1')}")
    z.execute("GRANT UPDATE ON ergebnisse TO labor_bearbeiten")
    v.commit()
    print("   GRANT UPDATE ON ergebnisse TO labor_bearbeiten;   <- ganze Tabelle")
    print(f"   {'jetzt, r01':<34} "
          f"{versuch('bernd', 'UPDATE ergebnisse SET r01 = 1')}")
    print("   Das Spaltenrecht wirkt nicht mehr — das breitere gewinnt.")
    z.execute("REVOKE UPDATE ON ergebnisse FROM labor_bearbeiten")
    v.commit()
    print("   REVOKE UPDATE ON ergebnisse FROM labor_bearbeiten;")
    print(f"   {'nach REVOKE, r01':<34} "
          f"{versuch('bernd', 'UPDATE ergebnisse SET r01 = 1')}")
    print(f"   {'nach REVOKE, mw_roh':<34} "
          f"{versuch('bernd', 'UPDATE ergebnisse SET mw_roh = 1.5')}")
    print("   Merken: REVOKE nimmt AUCH die Spaltenrechte mit. Danach die")
    print("   gewuenschten Spalten neu vergeben — in Oracle genauso:")
    print("   „the grantor must first revoke the object privilege for all")
    print("   columns … and then selectively repeat the grant\".")
    v.close()

    print()
    print("== Zwei Unterschiede zu Oracle, die man wissen muss")
    print("   1. SELECT je Spalte gibt es in Oracle NICHT. Die Doku: „You can")
    print("      specify columns only when granting the INSERT, REFERENCES, or")
    print("      UPDATE privilege.\" Lesen ist immer die ganze Tabelle.")
    print("      (PostgreSQL kann GRANT SELECT (spalte) — Oracle nicht.)")
    print("   2. Wer Rollen weitergeben darf, braucht WITH ADMIN OPTION:")
    print("      GRANT labor_bearbeiten TO chefin WITH ADMIN OPTION;")
    print("      Danach vergibt sie die Rolle selbst — ohne DBA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
