"""Neuen Benutzer anlegen und ihm dieselben Rechte geben wie einem bisherigen.

Vier Schritte, und der vierte ist der, den man nicht auslassen darf:

1. **Anlegen** — Konto, Passwort, Anmelderecht.
2. **Namen auflösbar machen** — das SQL spricht die Tabellen ohne Schema an,
   also braucht der neue Benutzer Synonyme (oder es gibt öffentliche).
3. **Rechte übernehmen** — nicht raten, sondern beim Vorbild auslesen und die
   ``GRANT``-Anweisungen daraus erzeugen.
4. **Vergleichen** — beide Benutzer gegenüberstellen, bis die Liste der
   Unterschiede leer ist. Ein Rechteabgleich, den niemand geprüft hat, ist eine
   Vermutung.

Hier vorgeführt auf PostgreSQL 16 (kein Oracle 11.2 vorhanden): angelegt wird
ein Vorbild mit **direkten** Rechten — so sieht es bei euch heute
wahrscheinlich aus —, dann wird geklont und verglichen. Das Oracle-Skript mit
denselben Schritten schreibt dieses Werkzeug nach ``docs/neuer_benutzer.sql``.

Aufruf::

    python tools/benutzer_anlegen.py
"""

from __future__ import annotations

from pathlib import Path

import pg8000.dbapi

# Derselbe Rechteschnitt wie in tools/rechte_demo.py, ausgezählt aus lims_db.py.
SCHREIBRECHTE = {
    "ergebnisse": ("UPDATE", ["mw", "mw_n", "mw_org", "mw_roh", "kommentar"]),
    "proben": ("UPDATE", ["bemerkung"]),
}
BREITE = {"ergebnisse": 20, "proben": 12}


def verbinden(benutzer="postgres", passwort="probe", datenbank="benutzer_probe"):
    return pg8000.dbapi.Connection(benutzer, host="127.0.0.1", port=5432,
                                   database=datenbank, password=passwort)


def rechte_lesen(z, benutzer: str) -> set[tuple]:
    """Was dieser Benutzer darf — als vergleichbare Menge.

    Genau dafür sind die Katalogtabellen da. In Oracle heißen sie
    DBA_TAB_PRIVS / DBA_COL_PRIVS / DBA_ROLE_PRIVS / DBA_SYS_PRIVS.
    """
    z.execute("""
        SELECT 'TABELLE', table_schema, table_name, privilege_type, ''
          FROM information_schema.table_privileges WHERE grantee = %s
        UNION ALL
        SELECT 'SPALTE', c.table_schema, c.table_name, c.privilege_type,
               c.column_name
          FROM information_schema.column_privileges c
         WHERE c.grantee = %s
           AND NOT EXISTS (SELECT 1 FROM information_schema.table_privileges t
                            WHERE t.grantee = c.grantee
                              AND t.table_schema = c.table_schema
                              AND t.table_name = c.table_name
                              AND t.privilege_type = c.privilege_type)
        UNION ALL
        SELECT 'ROLLE', '', r.rolname, '', ''
          FROM pg_auth_members m
          JOIN pg_roles r ON r.oid = m.roleid
          JOIN pg_roles g ON g.oid = m.member
         WHERE g.rolname = %s
    """, (benutzer, benutzer, benutzer))
    return {tuple(zeile) for zeile in z.fetchall()}


def grants_erzeugen(z, vorbild: str, neu: str) -> list[str]:
    """Liest die Rechte des Vorbilds und erzeugt die Anweisungen für den Neuen.

    Spaltenrechte werden je Tabelle und Recht **gebündelt** — einzeln vergeben
    ergäbe für ERGEBNISSE achtzehn Anweisungen statt einer, und bei Oracle
    hebt jede weitere die vorige nicht auf, aber die Liste wird unlesbar.
    """
    anweisungen = []
    z.execute("""
        SELECT r.rolname FROM pg_auth_members m
          JOIN pg_roles r ON r.oid = m.roleid
          JOIN pg_roles g ON g.oid = m.member
         WHERE g.rolname = %s ORDER BY 1
    """, (vorbild,))
    for (rolle,) in z.fetchall():
        anweisungen.append(f"GRANT {rolle} TO {neu};")

    z.execute("""
        SELECT table_schema, table_name, privilege_type
          FROM information_schema.table_privileges
         WHERE grantee = %s ORDER BY 1, 2, 3
    """, (vorbild,))
    for schema, tabelle, recht in z.fetchall():
        anweisungen.append(f"GRANT {recht} ON {schema}.{tabelle} TO {neu};")

    z.execute("""
        SELECT c.table_schema, c.table_name, c.privilege_type,
               string_agg(c.column_name, ', ' ORDER BY c.column_name)
          FROM information_schema.column_privileges c
         WHERE c.grantee = %s
           AND NOT EXISTS (SELECT 1 FROM information_schema.table_privileges t
                            WHERE t.grantee = c.grantee
                              AND t.table_schema = c.table_schema
                              AND t.table_name = c.table_name
                              AND t.privilege_type = c.privilege_type)
         GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """, (vorbild,))
    for schema, tabelle, recht, spalten in z.fetchall():
        anweisungen.append(f"GRANT {recht} ({spalten}) "
                           f"ON {schema}.{tabelle} TO {neu};")
    return anweisungen


def aufbauen():
    chef = verbinden(datenbank="postgres")
    chef.autocommit = True
    z = chef.cursor()
    z.execute("DROP DATABASE IF EXISTS benutzer_probe")
    for rolle in ("frank", "emil", "gerda", "labor_arbeit"):
        z.execute(f"DROP ROLE IF EXISTS {rolle}")
    z.execute("CREATE DATABASE benutzer_probe")
    chef.close()

    v = verbinden()
    z = v.cursor()
    z.execute("CREATE SCHEMA limsadmin")
    z.execute("SET search_path = limsadmin")
    for tabelle, breite in BREITE.items():
        _, spalten = SCHREIBRECHTE[tabelle]
        rest = [f"r{i:02d} text" for i in range(1, breite - len(spalten) + 1)]
        z.execute(f"CREATE TABLE {tabelle} ("
                  + ", ".join([f"{s} text" for s in spalten] + rest) + ")")
        z.execute(f"INSERT INTO {tabelle} DEFAULT VALUES")

    # Das Vorbild: ein Benutzer mit DIREKTEN Rechten, ohne Rolle — so sieht
    # es aus, wenn über Jahre einzeln vergeben wurde.
    z.execute("CREATE ROLE frank LOGIN PASSWORD 'probe'")
    z.execute("GRANT USAGE ON SCHEMA limsadmin TO frank")
    for tabelle, (recht, spalten) in SCHREIBRECHTE.items():
        z.execute(f"GRANT SELECT ON {tabelle} TO frank")
        z.execute(f"GRANT {recht} ({', '.join(spalten)}) ON {tabelle} TO frank")
    v.commit()
    return v, z


def oracle_skript(ziel: Path) -> None:
    ziel.write_text(r"""-- Neuen Benutzer anlegen — und ihm dieselben Rechte geben
-- =======================================================
-- Erzeugt von tools/benutzer_anlegen.py.
--
-- NICHT gegen Oracle 11.2 gelaufen — hier steht keines. Die Konstrukte sind
-- Standard, die Katalogsichten auch; probiert es zuerst mit einem
-- Testbenutzer aus, bevor es jemand Echtes trifft.
--
-- Anzumelden als DBA. &vorbild = ein Benutzer, der heute alles darf, was der
-- Neue auch soll. &neu = das neue Konto.


-- ---------------------------------------------------------------- Schritt 1
-- Anlegen. Ein LIMS-Benutzer legt keine eigenen Objekte an, also braucht er
-- KEIN Kontingent — kein QUOTA, kein UNLIMITED TABLESPACE.

CREATE USER &neu IDENTIFIED BY "Anfang.2026!"
  DEFAULT TABLESPACE users
  TEMPORARY TABLESPACE temp
  PROFILE lims_benutzer;          -- siehe Schritt 1b

GRANT CREATE SESSION TO &neu;     -- ohne das: ORA-01045, keine Anmeldung

-- Das Passwort in doppelte Anführungszeichen, wenn es Sonderzeichen hat.
-- Ab 11g sind Passwörter GROSS-/kleinschreibungsempfindlich
-- (SEC_CASE_SENSITIVE_LOGON).


-- --------------------------------------------------------------- Schritt 1b
-- Das Profil. Ohne eigenes Profil gilt DEFAULT, und das hat es ab 11g in
-- sich: PASSWORD_LIFE_TIME 180 Tage, FAILED_LOGIN_ATTEMPTS 10, danach ein
-- Tag Sperre. Nach einem halben Jahr kommt also ORA-28001 — und LabControl
-- kann ein abgelaufenes Passwort heute nicht wechseln.

CREATE PROFILE lims_benutzer LIMIT
  FAILED_LOGIN_ATTEMPTS 10
  PASSWORD_LOCK_TIME 1/24          -- eine Stunde statt einem Tag
  PASSWORD_LIFE_TIME UNLIMITED;    -- oder ein Wert, den die Oberfläche kann

-- Prüfen, was heute gilt:
--   SELECT profile, resource_name, limit FROM dba_profiles
--    WHERE resource_type = 'PASSWORD' ORDER BY profile, resource_name;
--   SELECT username, profile, account_status, expiry_date
--     FROM dba_users WHERE username = UPPER('&vorbild');


-- ---------------------------------------------------------------- Schritt 2
-- Die Namen müssen ohne Schema auflösbar sein: lims_db.py spricht die
-- Tabellen 117-mal unqualifiziert an. Erst nachsehen, wie es heute gelöst
-- ist:

--   SELECT owner, synonym_name, table_owner, table_name
--     FROM dba_synonyms
--    WHERE table_owner = 'LIMSADMIN'
--      AND (owner = 'PUBLIC' OR owner = UPPER('&vorbild'))
--    ORDER BY owner, synonym_name;

-- Fall A — es gibt PUBLIC-Synonyme: nichts zu tun.
-- Fall B — jeder Benutzer hat eigene: dieselben für den Neuen erzeugen.
--          Diese Abfrage schreibt die Anweisungen:

SELECT 'CREATE SYNONYM &neu..' || synonym_name
       || ' FOR ' || table_owner || '.' || table_name || ';'
  FROM dba_synonyms
 WHERE owner = UPPER('&vorbild')
 ORDER BY synonym_name;
-- (Dafür braucht der DBA CREATE ANY SYNONYM, oder der neue Benutzer legt
--  sie selbst an, nachdem er CREATE SYNONYM bekommen hat.)


-- ---------------------------------------------------------------- Schritt 3
-- Rechte übernehmen — ausgelesen, nicht geraten. Jede der vier Abfragen
-- liefert fertige Anweisungen; Ergebnis abspeichern und ausführen.

-- 3a) Rollen
SELECT 'GRANT ' || granted_role || ' TO &neu'
       || CASE WHEN admin_option = 'YES' THEN ' WITH ADMIN OPTION' END || ';'
  FROM dba_role_privs
 WHERE grantee = UPPER('&vorbild')
 ORDER BY granted_role;

-- 3b) Systemrechte (CREATE SESSION steht hier)
SELECT 'GRANT ' || privilege || ' TO &neu'
       || CASE WHEN admin_option = 'YES' THEN ' WITH ADMIN OPTION' END || ';'
  FROM dba_sys_privs
 WHERE grantee = UPPER('&vorbild')
 ORDER BY privilege;

-- 3c) Rechte auf ganze Tabellen
SELECT 'GRANT ' || privilege || ' ON ' || owner || '.' || table_name
       || ' TO &neu'
       || CASE WHEN grantable = 'YES' THEN ' WITH GRANT OPTION' END || ';'
  FROM dba_tab_privs
 WHERE grantee = UPPER('&vorbild')
 ORDER BY owner, table_name, privilege;

-- 3d) Rechte je Spalte — gebündelt, sonst wird jede Spalte ein eigenes GRANT.
--     LISTAGG gibt es ab 11.2.
SELECT 'GRANT ' || privilege || ' ('
       || LISTAGG(column_name, ', ') WITHIN GROUP (ORDER BY column_name)
       || ') ON ' || owner || '.' || table_name || ' TO &neu;'
  FROM dba_col_privs
 WHERE grantee = UPPER('&vorbild')
 GROUP BY owner, table_name, privilege
 ORDER BY owner, table_name, privilege;


-- ---------------------------------------------------------------- Schritt 4
-- VERGLEICHEN. Das ist der Schritt, den man nicht auslässt: was hat das
-- Vorbild, was der Neue nicht hat — und umgekehrt. Beide Listen müssen leer
-- sein.

-- 4a) Was fehlt dem Neuen?
SELECT 'ROLLE'   AS art, granted_role AS was, NULL AS wo, NULL AS spalte
  FROM dba_role_privs WHERE grantee = UPPER('&vorbild')
MINUS
SELECT 'ROLLE', granted_role, NULL, NULL
  FROM dba_role_privs WHERE grantee = UPPER('&neu')
UNION ALL
SELECT 'SYSTEM', privilege, NULL, NULL
  FROM dba_sys_privs WHERE grantee = UPPER('&vorbild')
MINUS
SELECT 'SYSTEM', privilege, NULL, NULL
  FROM dba_sys_privs WHERE grantee = UPPER('&neu')
UNION ALL
SELECT 'TABELLE', privilege, owner || '.' || table_name, NULL
  FROM dba_tab_privs WHERE grantee = UPPER('&vorbild')
MINUS
SELECT 'TABELLE', privilege, owner || '.' || table_name, NULL
  FROM dba_tab_privs WHERE grantee = UPPER('&neu')
UNION ALL
SELECT 'SPALTE', privilege, owner || '.' || table_name, column_name
  FROM dba_col_privs WHERE grantee = UPPER('&vorbild')
MINUS
SELECT 'SPALTE', privilege, owner || '.' || table_name, column_name
  FROM dba_col_privs WHERE grantee = UPPER('&neu');

-- 4b) Und dieselbe Abfrage mit vertauschten Namen: hat der Neue zu viel?
--     Das ist die wichtigere Richtung.


-- ---------------------------------------------------------------- Schritt 5
-- Ausprobieren. Mit LabControl anmelden und eine Serie öffnen. Ein
-- Rechteabgleich gilt erst, wenn die Anwendung damit gearbeitet hat.


-- --------------------------------------------------------------- Und danach
-- Damit der nächste Benutzer nicht wieder ein Skript braucht: die
-- ausgelesenen Rechte EINMAL in eine Rolle packen (siehe docs/rechte.sql).
-- Danach ist ein neuer Mensch zwei Zeilen:
--
--   CREATE USER neu IDENTIFIED BY "…" PROFILE lims_benutzer;
--   GRANT CREATE SESSION, labor_bearbeiten TO neu;
--
-- Und ein Wechsel der Zuständigkeit eine:
--
--   REVOKE labor_bearbeiten FROM bernd;
--
-- Sein eigenes Passwort darf jeder selbst ändern, ohne Sonderrecht:
--   ALTER USER <er_selbst> IDENTIFIED BY "neues_passwort";
""", encoding="utf-8")


def main() -> int:
    v, z = aufbauen()
    print("== Vorbild: frank, mit DIREKTEN Rechten (keine Rolle)")
    vorbild = rechte_lesen(z, "frank")
    print(f"   {len(vorbild)} Einträge im Rechteverzeichnis")

    print()
    print("== Schritt 3: Anweisungen aus dem Katalog erzeugen")
    anweisungen = grants_erzeugen(z, "frank", "emil")
    z.execute("CREATE ROLE emil LOGIN PASSWORD 'probe'")
    z.execute("GRANT USAGE ON SCHEMA limsadmin TO emil")
    for sql in anweisungen:
        print("   " + sql)
        z.execute(sql)
    v.commit()

    print()
    print("== Schritt 4: vergleichen — beide Richtungen")
    neu = rechte_lesen(z, "emil")
    fehlt = vorbild - neu
    zuviel = neu - vorbild
    print(f"   fehlt emil:  {len(fehlt)}  {sorted(fehlt) if fehlt else '— leer'}")
    print(f"   emil zu viel: {len(zuviel)} "
          f"{sorted(zuviel) if zuviel else '— leer'}")
    print("   => " + ("gleiche Rechte." if not fehlt and not zuviel
                      else "NICHT gleich, nachsehen!"))

    print()
    print("== Und die Probe aufs Ganze: dasselbe dürfen, dasselbe nicht")
    for wer in ("frank", "emil"):
        ergebnis = []
        for sql in ("UPDATE ergebnisse SET mw_roh = '1'",
                    "UPDATE ergebnisse SET r01 = 'x'",
                    "UPDATE proben SET bemerkung = 'x'",
                    "UPDATE proben SET r01 = 'x'"):
            # Frische Verbindung je Versuch: eine abgebrochene Transaktion
            # laesst die folgenden Anweisungen sonst ebenfalls scheitern.
            e = verbinden(wer, "probe")
            ez = e.cursor()
            try:
                ez.execute("SET search_path = limsadmin")
                ez.execute(sql)
                ergebnis.append("ja  ")
            except Exception:
                ergebnis.append("nein")
            finally:
                e.rollback()
                e.close()
        print(f"   {wer:<6} mw_roh={ergebnis[0]} erg.r01={ergebnis[1]} "
              f"bemerkung={ergebnis[2]}  proben.r01={ergebnis[3]}")

    print()
    print("== Danach: einmal eine Rolle daraus, dann ist es zwei Zeilen")
    z.execute("CREATE ROLE labor_arbeit")
    z.execute("GRANT USAGE ON SCHEMA limsadmin TO labor_arbeit")
    for sql in grants_erzeugen(z, "frank", "labor_arbeit"):
        z.execute(sql)
    z.execute("CREATE ROLE gerda LOGIN PASSWORD 'probe'")
    z.execute("GRANT labor_arbeit TO gerda")
    v.commit()
    g = verbinden("gerda", "probe")
    gz = g.cursor()
    gz.execute("SET search_path = limsadmin")
    gz.execute("UPDATE ergebnisse SET mw_roh = '1'")
    g.rollback()
    print("   CREATE ROLE gerda LOGIN …;  GRANT labor_arbeit TO gerda;")
    print("   gerda schreibt mw_roh: durchgelassen")
    g.close()
    v.close()

    ziel = Path("docs/neuer_benutzer.sql")
    if ziel.parent.exists():
        oracle_skript(ziel)
        print(f"\n   Oracle-Skript -> {ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
