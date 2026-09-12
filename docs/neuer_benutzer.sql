-- Neuen Benutzer anlegen — und ihm dieselben Rechte geben
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
