-- Rechte fuer das neue LabControl — erzeugt von tools/rechte_demo.py
--
-- Voraussetzung, die schon erfuellt ist: jeder Mensch hat ein eigenes
-- Oracle-Konto. lims_db.Zugang verbindet mit oracledb.connect(user=…,
-- password=…), und das Passwort liegt nur im Arbeitsspeicher.
--
-- Die Tabellen bleiben VOLL. Begrenzt wird ueber das Recht, nicht
-- ueber eine Sicht — deshalb entfaellt auch die WITH-GRANT-OPTION-
-- Frage und das Rollenproblem beim Uebersetzen von Sichten.
--
-- Vom DBA einmal auszufuehren, angemeldet als LIMSADMIN (oder mit
-- GRANT ANY OBJECT PRIVILEGE).

-- 1. Rollen. Vier Schnitte, abgeleitet aus dem Code.
CREATE ROLE labor_lesen;
CREATE ROLE labor_bearbeiten;
CREATE ROLE labor_qp;
CREATE ROLE labor_stammdaten;

-- 2. Lesen: alle Tabellen, die der Code anfasst.
--    Oracle kann SELECT NICHT je Spalte einschraenken —
--    Lesen ist immer die ganze Tabelle.
GRANT SELECT ON limsadmin.a_wasser TO labor_lesen;
GRANT SELECT ON limsadmin.anwenderstammdaten TO labor_lesen;
GRANT SELECT ON limsadmin.bew_teil TO labor_lesen;
GRANT SELECT ON limsadmin.einheiten TO labor_lesen;
GRANT SELECT ON limsadmin.ergebnisse TO labor_lesen;
GRANT SELECT ON limsadmin.geraete_anhang TO labor_lesen;
GRANT SELECT ON limsadmin.geraete_pruefungen TO labor_lesen;
GRANT SELECT ON limsadmin.geraete_pruefungen_anhang TO labor_lesen;
GRANT SELECT ON limsadmin.parameter TO labor_lesen;
GRANT SELECT ON limsadmin.pm_wellen TO labor_lesen;
GRANT SELECT ON limsadmin.proben TO labor_lesen;
GRANT SELECT ON limsadmin.probenart TO labor_lesen;
GRANT SELECT ON limsadmin.pruefmethoden TO labor_lesen;
GRANT SELECT ON limsadmin.qp_serien TO labor_lesen;
GRANT SELECT ON limsadmin.rohwertparameter TO labor_lesen;
GRANT SELECT ON limsadmin.s_fahrplan TO labor_lesen;
GRANT SELECT ON limsadmin.serien TO labor_lesen;
GRANT SELECT ON limsadmin.serien_mw_anhang TO labor_lesen;
GRANT SELECT ON limsadmin.standard_para TO labor_lesen;
GRANT SELECT ON limsadmin.standardverwaltung TO labor_lesen;
GRANT SELECT ON limsadmin.stationen TO labor_lesen;
GRANT SELECT ON limsadmin.teilproben TO labor_lesen;
GRANT SELECT ON limsadmin.teilproben_anhang TO labor_lesen;
GRANT SELECT ON limsadmin.um_anhang TO labor_lesen;
GRANT SELECT ON limsadmin.um_rohwerte TO labor_lesen;
GRANT SELECT ON limsadmin.untersuchungsmethode TO labor_lesen;
GRANT SELECT ON limsadmin.verfahrenskenndaten TO labor_lesen;

-- 3. Jede Arbeitsrolle erbt das Lesen.
GRANT labor_lesen TO labor_bearbeiten;
GRANT labor_lesen TO labor_qp;
GRANT labor_lesen TO labor_stammdaten;

-- 4. Schreiben — je Spalte, ausgezaehlt aus lims_db.py:
--    jede Spalte, die dort je links von einem SET steht
--    oder in einer INSERT-Spaltenliste.
GRANT UPDATE (anwender, datum_zeit, fc7, fc8, fc9, gegr_id, geme_id, kommentar, korrektur_flag, mw, mw_n, mw_org, mw_roh, pm_id, pm_ver, psta_id, v_faktor, vkd_id)
      ON limsadmin.ergebnisse TO labor_bearbeiten;   -- 18 von 81 Spalten
GRANT UPDATE (bemerkung)
      ON limsadmin.proben TO labor_bearbeiten;   -- 1 von 51 Spalten
GRANT UPDATE (mw, mw_old)
      ON limsadmin.teilproben_anhang TO labor_bearbeiten;   -- 2 von 12 Spalten
GRANT UPDATE (stat_id)
      ON limsadmin.serien_mw_anhang TO labor_bearbeiten;   -- 1 von 8 Spalten
GRANT INSERT ON limsadmin.serien_mw_anhang TO labor_bearbeiten;   -- INSERT kopiert die ganze Zeile
GRANT DELETE ON limsadmin.serien_mw_anhang TO labor_bearbeiten;   -- ganze Zeile
GRANT UPDATE (bewertung, kommentar, qp_art)
      ON limsadmin.bew_teil TO labor_qp;   -- 3 von 6 Spalten
GRANT INSERT ON limsadmin.bew_teil TO labor_qp;   -- INSERT kopiert die ganze Zeile
GRANT UPDATE (sollwert, toleranz, gu, go, qc_gu, qc_go)
      ON limsadmin.standard_para TO labor_stammdaten;   -- 6 von 17 Spalten

-- 5. Personen. Ab hier ist 'darf bearbeiten' eine Zeile.
GRANT labor_lesen      TO anna;
GRANT labor_bearbeiten TO bernd;
GRANT labor_qp         TO clara;
GRANT labor_stammdaten TO dora;

-- 6. Damit die Rollen nicht am DBA haengen: wer sie
--    weitergeben darf, bekommt sie mit ADMIN OPTION.
GRANT labor_bearbeiten TO laborleitung WITH ADMIN OPTION;
GRANT labor_qp         TO laborleitung WITH ADMIN OPTION;

-- 7. Entziehen wirkt sofort, ohne Neustart:
-- REVOKE labor_bearbeiten FROM bernd;

-- Nachsehen, was jemand darf:
--   SELECT * FROM user_role_privs;
--   SELECT table_name, column_name, privilege
--     FROM user_col_privs ORDER BY table_name, column_name;
--   SELECT table_name, privilege FROM user_tab_privs;
