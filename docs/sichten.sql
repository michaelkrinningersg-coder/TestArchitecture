-- Schlanke Sicht auf das LIMS — erzeugt von tools/spaltenbedarf.py
--
-- Eine Sicht je Tabelle mit genau den Spalten, die LabControl
-- anspricht: 27 Tabellen, 168 Spalten.
--
-- KEIN DROP, KEIN ALTER. Das Bestandssystem bleibt unberuehrt; diese
-- Sichten liegen daneben. Anzulegen in einem EIGENEN Schema
-- (hier: NEUES_LIMS), nicht im Schema des LIMS.
--
-- Vor dem Ausfuehren pruefen: die Spaltenliste stammt aus einer
-- Textauswertung des Quelltexts und ist eine Untergrenze. Was der
-- Code zur Laufzeit zusammensetzt, kann fehlen.

-- Welche Spalten es wirklich gibt, sagt Oracle selbst:
--   SELECT table_name, column_name, data_type, data_length,
--          data_precision, data_scale, nullable
--     FROM all_tab_columns
--    WHERE owner = 'LIMSADMIN'
--      AND table_name IN ('A_WASSER', 'ANWENDERSTAMMDATEN', 'BEW_TEIL', 'EINHEITEN', 'ERGEBNISSE', 'GERAETE_ANHANG', 'GERAETE_PRUEFUNGEN', 'GERAETE_PRUEFUNGEN_ANHANG', 'PARAMETER', 'PM_WELLEN', 'PROBEN', 'PROBENART', 'PRUEFMETHODEN', 'QP_SERIEN', 'ROHWERTPARAMETER', 'S_FAHRPLAN', 'SERIEN', 'SERIEN_MW_ANHANG', 'STANDARD_PARA', 'STANDARDVERWALTUNG', 'STATIONEN', 'TEILPROBEN', 'TEILPROBEN_ANHANG', 'UM_ANHANG', 'UM_ROHWERTE', 'UNTERSUCHUNGSMETHODE', 'VERFAHRENSKENNDATEN')
--    ORDER BY table_name, column_id;

-- A_WASSER: 3 von 55 Spalten
CREATE OR REPLACE VIEW v_a_wasser AS
  SELECT fluss_kart,
         prob_id,
         vers_name
    FROM limsadmin.a_wasser;
-- GRANT SELECT ON v_a_wasser TO labor_lesen;

-- ANWENDERSTAMMDATEN: 1 von 13 Spalten
CREATE OR REPLACE VIEW v_anwenderstammdaten AS
  SELECT username
    FROM limsadmin.anwenderstammdaten;
-- GRANT SELECT ON v_anwenderstammdaten TO labor_lesen;

-- BEW_TEIL: 6 von 6 Spalten
CREATE OR REPLACE VIEW v_bew_teil AS
  SELECT bewertung,
         kommentar,
         prob_id,
         qp_art,
         qp_art_kzl,
         um_id
    FROM limsadmin.bew_teil;
-- GRANT SELECT ON v_bew_teil TO labor_lesen;

-- EINHEITEN: 2 von 7 Spalten
CREATE OR REPLACE VIEW v_einheiten AS
  SELECT einheit,
         id
    FROM limsadmin.einheiten;
-- GRANT SELECT ON v_einheiten TO labor_lesen;

-- ERGEBNISSE: 27 von 81 Spalten
CREATE OR REPLACE VIEW v_ergebnisse AS
  SELECT anwender,
         datum_zeit,
         einh_id,
         einh_ver_id,
         fc7,
         fc8,
         fc9,
         gegr_id,
         geme_id,
         kommentar,
         korrektur_flag,
         lnr,
         mw,
         mw_n,
         mw_org,
         mw_roh,
         para_id,
         part_id,
         pm_id,
         pm_ver,
         prob_id,
         psta_id,
         serie,
         stan_id,
         um_id,
         v_faktor,
         vkd_id
    FROM limsadmin.ergebnisse;
-- GRANT SELECT ON v_ergebnisse TO labor_lesen;

-- GERAETE_ANHANG: 2 von 2 Spalten
CREATE OR REPLACE VIEW v_geraete_anhang AS
  SELECT geme_id,
         gera_id
    FROM limsadmin.geraete_anhang;
-- GRANT SELECT ON v_geraete_anhang TO labor_lesen;

-- GERAETE_PRUEFUNGEN: 3 von 7 Spalten
CREATE OR REPLACE VIEW v_geraete_pruefungen AS
  SELECT id,
         pruefung,
         status
    FROM limsadmin.geraete_pruefungen;
-- GRANT SELECT ON v_geraete_pruefungen TO labor_lesen;

-- GERAETE_PRUEFUNGEN_ANHANG: 2 von 2 Spalten
CREATE OR REPLACE VIEW v_geraete_pruefungen_anhang AS
  SELECT gegr_id,
         gepr_id
    FROM limsadmin.geraete_pruefungen_anhang;
-- GRANT SELECT ON v_geraete_pruefungen_anhang TO labor_lesen;

-- PARAMETER: 2 von 26 Spalten
CREATE OR REPLACE VIEW v_parameter AS
  SELECT id,
         name
    FROM limsadmin.parameter;
-- GRANT SELECT ON v_parameter TO labor_lesen;

-- PM_WELLEN: 11 von 13 Spalten
CREATE OR REPLACE VIEW v_pm_wellen AS
  SELECT hfa_code,
         id,
         kurzname,
         ogrenze,
         pm_code,
         pm_id,
         pm_ver,
         rsdabs,
         rsdproz,
         ugrenze,
         wellenlaenge
    FROM limsadmin.pm_wellen;
-- GRANT SELECT ON v_pm_wellen TO labor_lesen;

-- PROBEN: 5 von 51 Spalten
CREATE OR REPLACE VIEW v_proben AS
  SELECT bemerkung,
         id,
         probe_nr,
         wdh_me,
         wdh_um
    FROM limsadmin.proben;
-- GRANT SELECT ON v_proben TO labor_lesen;

-- PROBENART: 2 von 2 Spalten
CREATE OR REPLACE VIEW v_probenart AS
  SELECT art,
         id
    FROM limsadmin.probenart;
-- GRANT SELECT ON v_probenart TO labor_lesen;

-- PRUEFMETHODEN: 11 von 38 Spalten
CREATE OR REPLACE VIEW v_pruefmethoden AS
  SELECT format,
         formel,
         formelkuerzel,
         gegr_id,
         geme_id,
         id,
         kurzname,
         lsta_id,
         name,
         para_id,
         version
    FROM limsadmin.pruefmethoden;
-- GRANT SELECT ON v_pruefmethoden TO labor_lesen;

-- QP_SERIEN: 3 von 3 Spalten
CREATE OR REPLACE VIEW v_qp_serien AS
  SELECT bemerkung,
         geprueft,
         serie
    FROM limsadmin.qp_serien;
-- GRANT SELECT ON v_qp_serien TO labor_lesen;

-- ROHWERTPARAMETER: 5 von 6 Spalten
CREATE OR REPLACE VIEW v_rohwertparameter AS
  SELECT formelkuerzel,
         id,
         kuerzel,
         name,
         sort
    FROM limsadmin.rohwertparameter;
-- GRANT SELECT ON v_rohwertparameter TO labor_lesen;

-- S_FAHRPLAN: 1 von 14 Spalten
CREATE OR REPLACE VIEW v_s_fahrplan AS
  SELECT serie
    FROM limsadmin.s_fahrplan;
-- GRANT SELECT ON v_s_fahrplan TO labor_lesen;

-- SERIEN: 4 von 7 Spalten
CREATE OR REPLACE VIEW v_serien AS
  SELECT part_id,
         serie,
         status,
         termin
    FROM limsadmin.serien;
-- GRANT SELECT ON v_serien TO labor_lesen;

-- SERIEN_MW_ANHANG: 6 von 8 Spalten
CREATE OR REPLACE VIEW v_serien_mw_anhang AS
  SELECT felder,
         flag,
         quellen,
         serie,
         stat_id,
         um_id
    FROM limsadmin.serien_mw_anhang;
-- GRANT SELECT ON v_serien_mw_anhang TO labor_lesen;

-- STANDARD_PARA: 15 von 17 Spalten
CREATE OR REPLACE VIEW v_standard_para AS
  SELECT go,
         gu,
         id,
         linie,
         para_id,
         pr_go,
         pr_gu,
         qc_go,
         qc_gu,
         satz,
         sollwert,
         stan_id,
         test,
         toleranz,
         um_id
    FROM limsadmin.standard_para;
-- GRANT SELECT ON v_standard_para TO labor_lesen;

-- STANDARDVERWALTUNG: 7 von 22 Spalten
CREATE OR REPLACE VIEW v_standardverwaltung AS
  SELECT bezeichnung,
         id,
         mw,
         nummer,
         obere_grenze,
         typ,
         untere_grenze
    FROM limsadmin.standardverwaltung;
-- GRANT SELECT ON v_standardverwaltung TO labor_lesen;

-- STATIONEN: 3 von 10 Spalten
CREATE OR REPLACE VIEW v_stationen AS
  SELECT id,
         sgru_id,
         station
    FROM limsadmin.stationen;
-- GRANT SELECT ON v_stationen TO labor_lesen;

-- TEILPROBEN: 6 von 59 Spalten
CREATE OR REPLACE VIEW v_teilproben AS
  SELECT end_faktor,
         faktor,
         faktor_wgh,
         prob_id,
         serie,
         um_id
    FROM limsadmin.teilproben;
-- GRANT SELECT ON v_teilproben TO labor_lesen;

-- TEILPROBEN_ANHANG: 9 von 12 Spalten
CREATE OR REPLACE VIEW v_teilproben_anhang AS
  SELECT art,
         format,
         formelkuerzel,
         lnr,
         mw,
         mw_old,
         prob_id,
         rohw_id,
         um_id
    FROM limsadmin.teilproben_anhang;
-- GRANT SELECT ON v_teilproben_anhang TO labor_lesen;

-- UM_ANHANG: 13 von 26 Spalten
CREATE OR REPLACE VIEW v_um_anhang AS
  SELECT einh_id,
         einh_ver_id,
         gegr_id,
         geme_id,
         id,
         lnr,
         marker,
         para_id,
         part_id,
         pm_id,
         pm_ver,
         um_id,
         vkd_id
    FROM limsadmin.um_anhang;
-- GRANT SELECT ON v_um_anhang TO labor_lesen;

-- UM_ROHWERTE: 4 von 10 Spalten
CREATE OR REPLACE VIEW v_um_rohwerte AS
  SELECT formelkuerzel,
         lnr,
         rohw_id,
         um_id
    FROM limsadmin.um_rohwerte;
-- GRANT SELECT ON v_um_rohwerte TO labor_lesen;

-- UNTERSUCHUNGSMETHODE: 2 von 29 Spalten
CREATE OR REPLACE VIEW v_untersuchungsmethode AS
  SELECT id,
         kuerzel
    FROM limsadmin.untersuchungsmethode;
-- GRANT SELECT ON v_untersuchungsmethode TO labor_lesen;

-- VERFAHRENSKENNDATEN: 13 von 30 Spalten
CREATE OR REPLACE VIEW v_verfahrenskenndaten AS
  SELECT bg_arbeit,
         einh_id,
         gegr_id,
         geme_id,
         gera_id,
         id,
         lsta_id,
         nwg_arbeit,
         ogrenze,
         part_id,
         pm_id,
         pm_ver,
         um_id
    FROM limsadmin.verfahrenskenndaten;
-- GRANT SELECT ON v_verfahrenskenndaten TO labor_lesen;
