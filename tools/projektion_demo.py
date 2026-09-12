"""Schritt 2 und 3 vorgeführt: schlanke Sicht daneben, eigene Tabellen dazu.

Gezeigt wird der Mechanismus, auf dem Variante 1 steht — und zwar *laufend*,
nicht als Behauptung:

**Schritt 2** — Das Bestandsschema (``limsadmin``) bleibt unberührt. Daneben
entsteht ein eigenes Schema (``neues_lims``) mit **Sichten gleichen Namens**.
Weil LabControls SQL die Tabellen **ohne Schemapräfix** anspricht — 117 Stellen,
0 mit Präfix, und der Quelltext sagt es selbst: „sie liegen im Schema des
angemeldeten Benutzers oder sind ueber Synonyme erreichbar" —, liest dieselbe
Abfrage **ohne eine Zeile Änderung** die Sicht statt der Tabelle.

**Schritt 3** — Die eigenen Tabellen des neuen LIMS (Prüfpfad, Freigabe)
liegen im selben Schema, mit Rechten je Tabelle und einem Prüfpfad, der nur
wachsen kann.

> Vorgeführt auf **PostgreSQL 16**, weil hier kein Oracle 11.2 steht. Der
> Mechanismus ist derselbe; nur die Namen unterscheiden sich:
>
> | | PostgreSQL | Oracle |
> |---|---|---|
> | Schema anlegen | ``CREATE SCHEMA`` | ``CREATE USER`` (ein Schema *ist* ein Benutzer) |
> | Auflösungsreihenfolge | ``SET search_path = neues_lims`` | ``ALTER SESSION SET CURRENT_SCHEMA = neues_lims`` |
> | Fehler auslösen | ``RAISE EXCEPTION`` | ``RAISE_APPLICATION_ERROR`` |
> | Zeitstempel | ``timestamptz`` | ``TIMESTAMP WITH TIME ZONE`` |
> | Zähler | ``GENERATED … AS IDENTITY`` | dito (ab 12c) bzw. Sequenz + Trigger |

Aufruf (braucht einen laufenden PostgreSQL und ``pg8000``)::

    python tools/projektion_demo.py
"""

from __future__ import annotations

import sys

try:
    import pg8000.dbapi
except ImportError:                                   # pragma: no cover
    print("pg8000 fehlt: pip install pg8000")
    raise SystemExit(1)

# Die 27 Spalten, die LabControl in ERGEBNISSE wirklich anspricht — ausgezählt
# von tools/spaltenbedarf.py und unabhängig gegengeprüft.
GEBRAUCHT = [
    "prob_id", "serie", "um_id", "pm_id", "pm_ver", "para_id", "einh_id",
    "einh_ver_id", "vkd_id", "part_id", "gegr_id", "geme_id", "stan_id",
    "lnr", "mw", "mw_roh", "mw_org", "mw_n", "kommentar", "anwender",
    "v_faktor", "fc7", "fc8", "fc9", "datum_zeit", "psta_id",
    "korrektur_flag",
]
# Die echte Tabelle hat 81 Spalten. Die übrigen 54 kenne ich nicht; für die
# Vorführung stehen Platzhalter, damit die Breite stimmt.
UEBRIG = [f"rest{i:02d}" for i in range(1, 81 - len(GEBRAUCHT) + 1)]


def verbinden(benutzer="postgres", passwort="probe", datenbank="demo_lims"):
    return pg8000.dbapi.Connection(benutzer, host="127.0.0.1", port=5432,
                                   database=datenbank, password=passwort)


def satz(cursor, sql, werte=None):
    cursor.execute(sql, werte or ())
    return cursor.fetchall()


def versuch(cursor, verbindung, sql, werte=None) -> str:
    """Führt aus und gibt zurück, was passiert ist — Erfolg oder Fehlertext."""
    try:
        cursor.execute(sql, werte or ())
        verbindung.commit()
        return "durchgelassen"
    except Exception as fehler:
        verbindung.rollback()
        text = str(fehler)
        for schluessel in ("'M': '", '"M": "'):
            if schluessel in text:
                text = text.split(schluessel, 1)[1].split(schluessel[-1])[0]
                break
        return text[:78]


def aufbauen(v, z) -> None:
    """Das Bestandsschema — so, wie es heute ist, und unberührt bleibt."""
    z.execute("DROP SCHEMA IF EXISTS limsadmin CASCADE")
    z.execute("DROP SCHEMA IF EXISTS neues_lims CASCADE")
    z.execute("CREATE SCHEMA limsadmin")
    spalten = ", ".join(
        [f"{n} {'real' if n.startswith('mw') or n == 'v_faktor' else 'text' if n in ('serie', 'kommentar', 'anwender', 'fc7', 'fc8', 'fc9', 'datum_zeit', 'korrektur_flag') else 'int'}"
         for n in GEBRAUCHT] + [f"{n} text" for n in UEBRIG])
    z.execute(f"CREATE TABLE limsadmin.ergebnisse ({spalten})")
    z.execute("CREATE TABLE limsadmin.proben (id int PRIMARY KEY, "
              "probe_nr text, wdh_um int, wdh_me int, bemerkung text, "
              "rest text)")
    z.execute("INSERT INTO limsadmin.proben VALUES (1,'2026P0000001',0,0,NULL,'x')")
    werte = ", ".join(["1", "'2026W052'"] + ["1"] * 5 + ["1", "1", "1", "1", "1"]
                      + ["1", "1.5", "1.5", "1.5", "1.5", "NULL", "'mk'",
                         "1.0", "'1'", "'J'", "NULL", "'2026-04-01'", "1",
                         "NULL"] + ["'x'"] * len(UEBRIG))
    z.execute(f"INSERT INTO limsadmin.ergebnisse VALUES ({werte})")
    v.commit()


def schritt2(v, z) -> None:
    print("== Schritt 2: die Sicht daneben, nichts am Bestand")
    breit = satz(z, "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema='limsadmin' AND table_name='ergebnisse'")
    print(f"   limsadmin.ergebnisse hat {breit[0][0]} Spalten (wie im echten LIMS)")

    z.execute("CREATE SCHEMA neues_lims")
    # Eine Sicht mit GLEICHEM Namen, nur den gebrauchten Spalten.
    z.execute("CREATE VIEW neues_lims.ergebnisse AS SELECT "
              + ", ".join(GEBRAUCHT) + " FROM limsadmin.ergebnisse")
    z.execute("CREATE VIEW neues_lims.proben AS SELECT id, probe_nr, wdh_um, "
              "wdh_me, bemerkung FROM limsadmin.proben")
    v.commit()
    schmal = satz(z, "SELECT count(*) FROM information_schema.columns "
                     "WHERE table_schema='neues_lims' AND table_name='ergebnisse'")
    print(f"   neues_lims.ergebnisse hat {schmal[0][0]} Spalten "
          f"({schmal[0][0]}/{breit[0][0]} = "
          f"{schmal[0][0] / breit[0][0] * 100:.0f} %)")
    print("   Am Bestand geändert: nichts — CREATE VIEW, kein DROP, kein ALTER")

    # Die Abfrage aus lims_db.py, ohne Schemapräfix — wie alle 117 dort.
    abfrage = ("SELECT e.prob_id, p.probe_nr, e.serie, e.mw, e.mw_roh "
               "FROM ergebnisse e JOIN proben p ON p.id = e.prob_id "
               "WHERE e.serie = %s")
    print()
    print("   Dieselbe Abfrage, zweimal, Wort für Wort gleich:")
    for schema in ("limsadmin", "neues_lims"):
        z.execute(f"SET search_path = {schema}")
        zeilen = satz(z, abfrage, ("2026W052",))
        z.execute("SELECT count(*) FROM (SELECT * FROM ergebnisse) t")
        breite = satz(z, "SELECT count(*) FROM information_schema.columns "
                         f"WHERE table_schema='{schema}' "
                         "AND table_name='ergebnisse'")[0][0]
        print(f"     search_path={schema:<11} -> {zeilen} "
              f"(SELECT * liefert {breite} Spalten)")
    print("   Ohne eine Zeile Änderung im Code. Genau das sagt euer eigener")
    print("   Kommentar: „ohne Schemapraefix … im Schema des angemeldeten")
    print("   Benutzers oder ueber Synonyme erreichbar\".")

    # Rechte: die Rolle darf die Sicht lesen, die Tabelle darunter nicht.
    z.execute("SET search_path = public")
    for sql in ("DROP ROLE IF EXISTS neu_lesen",
                "CREATE ROLE neu_lesen LOGIN PASSWORD 'probe'",
                "GRANT USAGE ON SCHEMA neues_lims TO neu_lesen",
                "GRANT SELECT ON neues_lims.ergebnisse TO neu_lesen"):
        z.execute(sql)
    v.commit()
    print()
    print("   Rolle neu_lesen: nur SELECT auf die Sicht, nichts auf limsadmin")
    f = verbinden("neu_lesen", "probe")
    zf = f.cursor()
    for was, sql in (("Sicht lesen", "SELECT count(*) FROM neues_lims.ergebnisse"),
                     ("Tabelle darunter", "SELECT count(*) FROM limsadmin.ergebnisse"),
                     ("Sicht ändern", "UPDATE neues_lims.ergebnisse SET mw = 9")):
        try:
            zf.execute(sql)
            print(f"     {was:<18} -> {zf.fetchall()[0][0]}")
        except Exception as fehler:
            f.rollback()
            print(f"     {was:<18} -> "
                  f"{versuch(zf, f, sql) if False else str(fehler).split(chr(39) + 'M' + chr(39) + ': ' + chr(39))[-1].split(chr(39))[0][:60]}")
    f.close()


def schritt3(v, z) -> None:
    print()
    print("== Schritt 3: die eigenen Tabellen, mit Rechten je Tabelle")
    z.execute("SET search_path = neues_lims")
    z.execute("""
        CREATE TABLE neues_lims.pruefpfad (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          wann        timestamptz  NOT NULL DEFAULT now(),
          db_benutzer text         NOT NULL DEFAULT current_user,
          wer         text         NOT NULL,
          tabelle     text         NOT NULL,
          schluessel  text         NOT NULL,
          feld        text,
          alt_wert    text,
          neu_wert    text,
          grund       text         NOT NULL,
          CONSTRAINT grund_nicht_leer CHECK (length(btrim(grund)) > 0))
    """)
    z.execute("""
        CREATE TABLE neues_lims.freigabe (
          prob_id     int          NOT NULL,
          um_id       int          NOT NULL,
          entscheidung text        NOT NULL
            CHECK (entscheidung IN ('freigegeben','gesperrt')),
          wer         text         NOT NULL,
          wann        timestamptz  NOT NULL DEFAULT now(),
          begruendung text         NOT NULL
            CHECK (length(btrim(begruendung)) > 0),
          PRIMARY KEY (prob_id, um_id, wann))
    """)
    # Der Riegel: kein UPDATE, kein DELETE — auch nicht für den Eigentümer.
    z.execute("""
        CREATE FUNCTION neues_lims.nur_wachsen() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Pruefpfad: % ist nicht vorgesehen', TG_OP;
        END $$ LANGUAGE plpgsql
    """)
    z.execute("CREATE TRIGGER pruefpfad_unveraenderlich "
              "BEFORE UPDATE OR DELETE OR TRUNCATE ON neues_lims.pruefpfad "
              "FOR EACH STATEMENT EXECUTE FUNCTION neues_lims.nur_wachsen()")
    v.commit()

    z.execute("INSERT INTO pruefpfad (wer, tabelle, schluessel, feld, "
              "alt_wert, neu_wert, grund) VALUES "
              "('mkrinninger','ergebnisse','1/100/1/1/1','mw_roh',NULL,"
              "'1.5','Erstmessung')")
    v.commit()
    print("   Prüfpfad angelegt, ein Eintrag drin.")
    print("   Und jetzt der Eigentümer selbst — der, der alles darf:")
    for was, sql in (
        ("INSERT", "INSERT INTO pruefpfad (wer, tabelle, schluessel, grund) "
                   "VALUES ('mk','ergebnisse','2','Nachmessung')"),
        ("UPDATE", "UPDATE pruefpfad SET grund = 'anders' WHERE id = 1"),
        ("DELETE", "DELETE FROM pruefpfad WHERE id = 1"),
        ("TRUNCATE", "TRUNCATE pruefpfad"),
        ("Grund leer", "INSERT INTO pruefpfad (wer, tabelle, schluessel, grund) "
                       "VALUES ('mk','ergebnisse','3','   ')"),
    ):
        print(f"     {was:<12} -> {versuch(z, v, sql)}")

    print()
    print("   Und die Rolle, unter der die Anwendung läuft:")
    for sql in ("DROP ROLE IF EXISTS labor_arbeiten",
                "CREATE ROLE labor_arbeiten LOGIN PASSWORD 'probe'",
                "GRANT USAGE ON SCHEMA neues_lims TO labor_arbeiten",
                "GRANT SELECT ON neues_lims.ergebnisse TO labor_arbeiten",
                "GRANT INSERT, SELECT ON neues_lims.pruefpfad TO labor_arbeiten",
                "GRANT INSERT, SELECT ON neues_lims.freigabe TO labor_arbeiten"):
        z.execute(sql)
    v.commit()
    a = verbinden("labor_arbeiten", "probe")
    za = a.cursor()
    za.execute("SET search_path = neues_lims")
    for was, sql in (
        ("Prüfpfad schreiben", "INSERT INTO pruefpfad (wer, tabelle, "
                               "schluessel, grund) VALUES "
                               "('laborant','ergebnisse','9','Freigabe')"),
        ("Freigabe schreiben", "INSERT INTO freigabe (prob_id, um_id, "
                               "entscheidung, wer, begruendung) VALUES "
                               "(1, 1, 'freigegeben', 'laborant', 'in Toleranz')"),
        ("Freigabe ohne Grund", "INSERT INTO freigabe (prob_id, um_id, "
                                "entscheidung, wer, begruendung) VALUES "
                                "(2, 1, 'freigegeben', 'laborant', '')"),
        ("Freigabe erfinden", "INSERT INTO freigabe (prob_id, um_id, "
                              "entscheidung, wer, begruendung) VALUES "
                              "(3, 1, 'vielleicht', 'laborant', 'unklar')"),
        ("Prüfpfad ändern", "UPDATE pruefpfad SET grund = 'x' WHERE id = 1"),
        ("Prüfpfad löschen", "DELETE FROM pruefpfad"),
        ("Messwert ändern", "UPDATE ergebnisse SET mw = 9"),
    ):
        print(f"     {was:<22} -> {versuch(za, a, sql)}")
    a.close()


def main() -> int:
    chef = verbinden(datenbank="postgres")
    chef.autocommit = True
    z = chef.cursor()
    z.execute("DROP DATABASE IF EXISTS demo_lims")
    z.execute("CREATE DATABASE demo_lims")
    chef.close()

    v = verbinden()
    z = v.cursor()
    aufbauen(v, z)
    schritt2(v, z)
    schritt3(v, z)
    v.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
