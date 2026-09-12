# Zwei Varianten für ein neues LIMS — geprüft

## Kurz

| | Antwort |
|---|---|
| **Variante 1**: neues LIMS, Oracle behalten | **Ja — und von allen Wegen der stärkste.** |
| **Variante 1a**: unnötige Tabellen und Spalten droppen | **Nein.** Es ist nicht euer Schema — Begründung unten. |
| **Variante 2**: SQLite, die Oberfläche als Rechtesystem | Als *Bedienkonzept* richtig. Als *Rechtesystem* trägt es technisch nicht — ob das hier reicht, ist eine Abwägung, keine Vorschrift. |
| „In `ERGEBNISSE` bräuchte man viel weniger Spalten" | **Gemessen: 27 von 81.** Du liegst richtig. |

> **Berichtigt am 12.09.2026.** Frühere Fassungen dieses und der anderen
> Dokumente haben mit einer „17025-Nachweisführung" argumentiert. Das war
> **meine Annahme, nicht euer Stand** — die NW-FVA ist nicht nach ISO/IEC 17025
> akkreditiert, und im Quelltext kommt 17025 an keiner Stelle vor; ich habe es
> nachgesehen. Nachgeprüft habe ich auch, was stattdessen dasteht: eine
> **Qualitätsprüfung** aus 24 Modulen und 13 028 Zeilen (Bilanzen für N, C, P,
> S und NaCl, Kontrollproben, Wiederholungen, Toleranzen, Nachmessungen), die
> ihr Urteil samt Kommentar in `BEW_TEIL` schreibt — und ein `protokoll.py`,
> das jede verändernde Anweisung mitschreibt, mit dieser Begründung:
>
> > „Ein Export schreibt in die Ergebnisse eines Labors, das seine Zahlen
> > zwanzig Jahre aufhebt. Wenn spaeter jemand fragt, woher ein Wert kommt, ist
> > die Antwort ‚LabControl hat ihn geschrieben' keine Auskunft."
>
> Darauf stützen sich die Empfehlungen jetzt. **Was sich dadurch geändert hat:**
> aus „steht nicht zur Wahl" ist „ist eure Abwägung" geworden, und die
> vollständige Rechtemaschinerie in Schritt 3 ist eine Möglichkeit statt einer
> Pflicht. **Was sich nicht geändert hat:** die Messungen, die 27 von 456
> Tabellen, das Argument gegen das Löschen im Fremdschema — und der harte Grund
> gegen eine geteilte SQLite-Datei im Netz, denn eine beschädigte Datei ist
> kein Nachweisproblem, sondern Datenverlust.

## Der Befund, der alles davor rückt: das LIMS ist ein Produkt

Beim Durchzählen der 456 Tabellen fiel etwas auf, das die Frage nach dem
Löschen beantwortet, bevor man über Oracle-Syntax redet. **171 Tabellen mit
1 684 Spalten** gehören überhaupt nicht zu euren Labordaten:

| Gruppe | Beispiele | was die Namen sagen |
|---|---|---|
| Lizenz | `BL_LICENSE_INFO_SUMMARY`, `BL_LICENSE_USAGE`, `BLUAD_EULA_CONFIRMATION` | **lizenzierte Software mit Lizenzbedingungen** |
| Masken | `BL_MASKS`, `BL_MASKFIELDS`, `BL_MASKBLOCKS`, `BL_MASKBUTTONS`, `BL_VIEWS`, `BL_VIEWFIELDS` | die Bildschirmmasken **stehen in Tabellen** |
| Eigener Katalog | `BL_DB_TABLES`, `BL_DB_COLUMNS`, `BL_DB_RELATIONS`, `BL_DB_TABLE_DESIGN` | das Produkt führt **sein eigenes Verzeichnis jeder Tabelle, Spalte und Beziehung** |
| Trigger | `BL_TRIGGER`, `BL_TRIGGER_CD` | Trigger werden aus diesen Angaben erzeugt |
| Rechte | `BLUAD_USERS`, `BLUAD_GROUPS`, `BLUAD_PRIVILEGES`, `BLUAD_V_OBJ_TAB_USER_PRIVS` | ein **Benutzer- und Rechtesystem** über Oracle |
| Protokoll | `BL_LOG_ACTION`, `BL_LOG_SQL_COMMANDS` | Änderungsprotokoll |
| Web | `BLUAD_SESSIONS`, `BLUAD_FAILED_WEB_LOGINS`, `BLUAD_V_THIS_WEB_USER` | eine **Weboberfläche mit Anmeldungen** |

> Das ist aus den Namen gelesen, nicht aus einem Handbuch — ich habe das
> Produkt nicht vor mir. Aber `EULA_CONFIRMATION` neben `LICENSE_USAGE` neben
> `BL_DB_COLUMNS` lässt kaum eine andere Deutung zu.

Zwei Folgerungen, und beide sind wichtiger als jede Syntaxfrage:

1. **Das LIMS baut seine Oberfläche aus seinem eigenen Katalog.** Wer eine
   Spalte aus `ERGEBNISSE` löscht, löscht sie nicht aus `BL_DB_COLUMNS`, nicht
   aus `BL_MASKFIELDS` und nicht aus den daraus erzeugten Triggern. Die
   Bestandsanwendung zeigt dann auf etwas, das es nicht mehr gibt.
2. **Euer Rechtesystem hängt heute an `BLUAD_*`.** Wer eine neue Oberfläche
   baut, ersetzt das mit. Das ist die Frage aus
   [sqlite-neues-lims.md](sqlite-neues-lims.md) von der anderen Seite: was heute
   dafür sorgt, dass nicht jeder alles ändern kann, ist Teil des Produkts —
   nicht Teil eurer Anwendung.

## Variante 1 — Oracle behalten

### Die gute Nachricht: der Bedarf ist winzig

Ausgewertet mit [`tools/spaltenbedarf.py`](../tools/spaltenbedarf.py) — es
liest die SQL-Zeichenketten aus `lims_db.py`, löst die Tabellenkürzel auf und
sammelt, welche Spalten wirklich angesprochen werden:

| | |
|---|---:|
| Tabellen im LIMS | 456 |
| **davon berührt LabControl** | **27** |
| Spalten in diesen 27 Tabellen | 556 |
| **davon gebraucht** | **168 (30 %)** |
| Spalten im ganzen LIMS | 5 976 |
| **davon gebraucht** | **168 = 2,8 %** |

Und Tabelle für Tabelle — deine Vermutung zu `ERGEBNISSE` steht in der ersten
Zeile:

| Tabelle | gebraucht | gesamt | |
|---|---:|---:|---:|
| `ERGEBNISSE` | **27** | 81 | 33 % |
| `TEILPROBEN` | 6 | 59 | 10 % |
| `A_WASSER` | 3 | 55 | 5 % |
| `PROBEN` | 5 | 51 | 10 % |
| `PRUEFMETHODEN` | 11 | 38 | 29 % |
| `VERFAHRENSKENNDATEN` | 13 | 30 | 43 % |
| `UNTERSUCHUNGSMETHODE` | 2 | 29 | 7 % |
| `UM_ANHANG` | 13 | 26 | 50 % |
| `PARAMETER` | 2 | 26 | 8 % |
| `STANDARDVERWALTUNG` | 7 | 22 | 32 % |
| `STANDARD_PARA` | 15 | 17 | 88 % |
| `S_FAHRPLAN` | 1 | 14 | 7 % |
| `ANWENDERSTAMMDATEN` | 1 | 13 | 8 % |
| `PM_WELLEN` | 11 | 13 | 85 % |
| `TEILPROBEN_ANHANG` | 9 | 12 | 75 % |
| `STATIONEN` | 3 | 10 | 30 % |
| `UM_ROHWERTE` | 4 | 10 | 40 % |
| `SERIEN_MW_ANHANG` | 6 | 8 | 75 % |
| `EINHEITEN` · `SERIEN` · `GERAETE_PRUEFUNGEN` · `ROHWERTPARAMETER` · `BEW_TEIL` · `QP_SERIEN` · `PROBENART` · `GERAETE_ANHANG` · `GERAETE_PRUEFUNGEN_ANHANG` | je 2–6 | je 2–7 | |

`ERGEBNISSE` **27 von 81** — genau so, wie du es vermutet hast. Es sind die
27, die im Quelltext namentlich vorkommen; mit einer unabhängigen Gegenprobe
(alle `e.<spalte>` plus alle `SET`-Ziele aus den sechs `UPDATE`-Anweisungen)
kommt dieselbe 27 heraus.

Nebenbei ein Nebenbefund: eure eigene Doku nennt in der Kopfliste **20**
Tabellen — der Code fasst **27** an. Die sieben, die dort fehlen, sind
`A_WASSER`, `S_FAHRPLAN`, `QP_SERIEN`, `ROHWERTPARAMETER`, `UM_ROHWERTE`,
`TEILPROBEN_ANHANG` und `GERAETE_ANHANG`. Für ein neues Datenmodell wäre genau
das die Liste, die man nicht aus der Doku, sondern aus dem Code holt.

> Die Auswertung ist eine **Textauswertung, kein SQL-Parser**, und deshalb eine
> **Untergrenze**: was der Code zur Laufzeit zusammensetzt, kann ihr entgehen.
> Für die Größenordnung — 3 % statt 100 % — ändert das nichts.

### Aber: nicht durch Löschen

Fünf Gründe, in der Reihenfolge ihres Gewichts:

1. **Es ist nicht euer Schema.** Siehe oben: lizenziertes Produkt, eigener
   Katalog, daraus erzeugte Masken und Trigger. Ein `ALTER TABLE … DROP COLUMN`
   ist aus Sicht des Herstellers keine Anpassung, sondern ein Eingriff — und
   damit ist die Unterstützung weg, genau in dem Moment, in dem man sie
   bräuchte.
2. **Ihr seid nicht die Einzigen darin.** `BLUAD_FAILED_WEB_LOGINS` und
   `BLUAD_SESSIONS` heißt: es gibt eine Weboberfläche, an der sich Leute
   anmelden. Dazu `AQS_*` (6), `RECH_*` (6), `LABORBUCH_*` (6), `AN_*` (5),
   `KO_*` (5). Was für *deine* Oberfläche unnötig ist, ist woanders die
   Tagesarbeit.
3. **Aufbewahrung.** In den Tabellen stehen Messwerte eines Labors, das seine
   Zahlen nach eigener Angabe **zwanzig Jahre** aufhebt (`protokoll.py`).
   `DROP TABLE` ist deren Vernichtung, und ein `DROP COLUMN` auf `ERGEBNISSE`
   ist an der Stelle nicht rückholbar. Eine verbrauchte Probe misst man nicht
   noch einmal.
4. **Habt ihr die Rechte überhaupt?** Euer README verlangt für Änderungen
   ausdrücklich `UPDATE`-Recht. `DROP`/`ALTER` im LIMS-Schema ist eine andere
   Stufe. Wenn euer Anmeldekonto das kann, ist *das* schon einen Blick wert.
5. **Und dann erst die Oracle-Mechanik.** `ALTER TABLE … DROP COLUMN`
   schreibt **jede Zeile neu** — bei `ERGEBNISSE` mit Millionen Zeilen unter
   einer ausschließenden Sperre, mit entsprechendem Redo und Undo.
   `SET UNUSED COLUMN` ist dagegen sofort fertig, lässt die Daten aber auf der
   Platte stehen (aufgeräumt wird erst mit `DROP UNUSED COLUMNS`). Und eine
   Elterntabelle geht nur mit `CASCADE CONSTRAINTS` weg, wobei Sichten,
   Pakete und Trigger ungültig werden.

### Was stattdessen: projizieren, nicht löschen

**Ein eigenes Schema, und darin Sichten auf das, was du brauchst.** Das gibt
dir dasselbe Ergebnis — „nur die Spalten, die ich brauche" — ohne im Bestand
eine Zeile anzufassen.

Die Datei liegt fertig im Repo: **[`docs/sichten.sql`](sichten.sql)**, erzeugt
aus der Auswertung oben. 27 `CREATE OR REPLACE VIEW`, je Tabelle genau die
gebrauchten Spalten, kein `DROP`, kein `ALTER`. Für `ERGEBNISSE` also:

```sql
-- ERGEBNISSE: 27 von 81 Spalten
CREATE OR REPLACE VIEW v_ergebnisse AS
  SELECT anwender, datum_zeit, einh_id, einh_ver_id, fc7, fc8, fc9,
         gegr_id, geme_id, kommentar, korrektur_flag, lnr, mw, mw_n,
         mw_org, mw_roh, para_id, part_id, pm_id, pm_ver, prob_id,
         psta_id, serie, stan_id, um_id, v_faktor, vkd_id
    FROM limsadmin.ergebnisse;
-- GRANT SELECT ON v_ergebnisse TO labor_lesen;
```

Die echten Typen musst du nicht von mir nehmen — Oracle sagt sie selbst, und
die Abfrage steht im Kopf der Datei:

```sql
SELECT table_name, column_name, data_type, data_length,
       data_precision, data_scale, nullable
  FROM all_tab_columns
 WHERE owner = 'LIMSADMIN' AND table_name IN (…die 27…)
 ORDER BY table_name, column_id;
```

Was du damit gewinnst, und zwar mehr als mit dem Löschen:

* **Die Oberfläche sieht nur, was sie sehen soll** — dasselbe Ziel, erreicht.
* **Rechte je Sicht.** `GRANT SELECT ON v_ergebnisse TO labor_lesen` — genau
  das, was SQLite nicht kann, und ihr hättet es geschenkt.
* **Neue Tabellen in deinem Schema.** Prüfpfad, Freigaben, eigene Stammdaten
  gehören dir, liegen aber in derselben Datenbank — also in derselben
  Sicherung und demselben Vorgang.
* **Beide Seiten laufen gleichzeitig.** Das brauchst du beim Umstieg ohnehin;
  euer Laufkontext ist schon genau dafür gebaut („damit sich beide Seiten
  vergleichen lassen, solange noch beide laufen").
* **Rückwärts geht immer.** Eine Sicht wirft man weg, eine gelöschte Spalte
  nicht.

### Der Preis dieser Variante: 32 Bit

Oracle behalten heißt Oracle 11.2 heißt Thick Mode heißt 32-bit-Client heißt
**kein Qt 6** — dieselbe Wand wie in [portierung.md](portierung.md).
**Variante 1 heute heißt also Tkinter**, und nach dem Oracle-Update und dem
Umstieg auf 64 Bit heißt sie Qt 6, ohne dass die Datenbankseite noch einmal
angefasst wird. Das ist kein Argument gegen die Variante — nur der Preis, den
sie hat, und er ist bekannt.

## Variante 2 — SQLite, die Oberfläche als Rechtesystem

### Der Gedanke ist nicht falsch

„Die Oberfläche bietet nur die Möglichkeiten, die sie bietet" ist ein
richtiges und verbreitetes Entwurfsprinzip: die Anwendung führt durch den
Vorgang, und was kein Knopf ist, passiert nicht. Euer heutiges LIMS macht
genau das — `BL_MASKS` bestimmt, was auf dem Schirm möglich ist.

Nur: es macht das **zusätzlich** zu `BLUAD_PRIVILEGES` und zu Oracles eigenen
Rechten, nicht **anstatt**. Und darin liegt der Unterschied.

### Ein Satz entscheidet: die Rechte einer Desktop-Anwendung sind die Rechte des Benutzers

Das ist kein Grundsatz, sondern nachgemessen. Dieselbe SQLite-Datei, alle drei
möglichen Rechtelagen, und ein zweiter Benutzer, der nicht Eigentümer ist:

| Ordner / Datei | Anwendung kann schreiben? | Benutzer kann alles? |
|---|---|---|
| lesbar / lesbar (`755` / `644`) | **nein** — `attempt to write a readonly database` | — |
| lesbar / schreibbar (`755` / `666`) | **nein** — SQLite braucht auch den **Ordner**, es legt Journal und WAL daneben an | — |
| schreibbar / schreibbar (`777` / `666`) | ja | **ja: `UPDATE` und `DROP TABLE`** — Tabelle danach weg |

**Es gibt keine Einstellung, in der die Anwendung schreiben darf und der
Benutzer nicht.** Damit die Anwendung arbeiten kann, muss das Konto, unter dem
sie läuft — also das des Benutzers — Schreibrecht auf Datei *und* Ordner haben.
Und wer das hat, hat es mit jedem Programm.

Der Unterschied zu Oracle in einem Satz: dort hat der Benutzer **kein**
Dateirecht auf die Daten, sondern nur eine Anmeldung, und was die darf,
entscheidet der Dienst. Deshalb greift dort die Schranke, auch wenn jemand
etwas anderes als deine Oberfläche startet.

Und es braucht dafür nichts: kein Adminrecht, kein installiertes Python.
*DB Browser for SQLite* ist eine tragbare EXE. Gemessen hat es drei Zeilen
gebraucht, siehe [sqlite-neues-lims.md](sqlite-neues-lims.md#4-benutzerrechte-nein--und-auch-nicht-später).

Ob das reicht, ist **eure Entscheidung** — eine Vorschrift, die es verbietet,
gibt es bei euch nicht. Was dagegen spricht, ist praktisch: ein Kollege, der in
*DB Browser* „nur schnell etwas richtigstellt", eine Datei, die beim Kopieren
halb geschrieben wird, ein Urteil der Qualitätsprüfung in `BEW_TEIL`, das sich
ändert, ohne dass es jemand sehen kann. Euer eigenes `protokoll.py` nennt den
Grund, warum das zählt: „Wenn spaeter jemand fragt, woher ein Wert kommt, ist
die Antwort ‚LabControl hat ihn geschrieben' keine Auskunft."

### Wo der Gedanke trägt

Nicht verwerfen — richtig einsortieren. Es gibt drei Lagen, in denen er hält:

1. **Wenn der Benutzer die Datei nie hat.** Die Datenbank liegt auf einer
   Maschine, auf deren Dateisystem niemand kommt, und die Anwendung redet mit
   einem kleinen Dienst darauf. Dann ist der Dienst die Schranke — nur hast du
   dann einen Datenbankserver geschrieben, und einen fertigen zu nehmen ist
   weniger Arbeit und leichter zu verteidigen.
2. **Lokal je Arbeitsplatz.** Einstellungen, Zwischenstände, ein eigenes
   Protokoll — da ist es genau richtig, und dort schadet es nichts, dass der
   Benutzer alles darf: es sind seine Daten.
3. **Als Schicht über echten Rechten.** Die Oberfläche formt die Arbeit, die
   Datenbank hält den Boden. So macht es euer LIMS heute, und so würde ich es
   wieder bauen.

Dein **eigenes Admin-Tool** passt in diese Reihe: es ist eine zweite
Oberfläche. Es kann Arbeit sehr angenehm machen — aber es nimmt niemandem den
Weg zur Datei.

## Empfehlung

**Variante 1, in der Form „eigenes Schema neben dem LIMS".** Das ist von allen
bisher betrachteten Wegen der stärkste, und zwar aus vier Gründen:

* Die Rechte kommen von Oracle, nicht von der Oberfläche — und sie kosten
  nichts extra, weil sie schon da sind. Das ist der Punkt, an dem SQLite für
  eine geteilte Ablage ausscheidet.
* Nichts wird gelöscht. Das Bestandssystem bleibt heil, und der Weg zurück
  bleibt offen.
* Der Bedarf ist gemessen **2,8 %** der Datenbank. Das Datenmodell des neuen
  LIMS ist überschaubar, nicht gewachsen.
* Der Übergang funktioniert, weil beide Seiten gleichzeitig laufen dürfen.

Und deine Oberfläche darf dabei trotzdem genau das tun, was du willst: nur
anbieten, was gebraucht wird, Proben durchschleusen, ein eigenes Admin-Tool
daneben. Das ist dann **Bedienung**, und nicht die einzige Schranke.

Wenn es doch eine eigene Datenbank werden soll: **PostgreSQL, nicht SQLite.**
Dann gilt derselbe Satz über die Rechte — nur unter eurer Kontrolle statt
unter der eines Herstellers.

### Reihenfolge, die ich vorschlagen würde

1. **Die 27 Tabellen mit echten Typen holen** — die eine Abfrage auf
   `all_tab_columns` aus dem Kopf von [`docs/sichten.sql`](sichten.sql). Damit
   steht das Datenmodell auf Tatsachen statt auf meiner Textauswertung.
2. **Ein eigenes Schema anlegen und die Sichten hineinlegen.** Nichts am
   Bestand. Danach das heutige LabControl gegen das neue Schema anmelden — das
   ist der Beweis, dass 2,8 % reichen, und er kostet ein paar `CREATE VIEW`.
   Einzelheiten oben unter *Schritt 2 im Einzelnen*.
3. **Die eigenen Tabellen dazu**: Prüfpfad und Freigabe, mit `GRANT` je
   Tabelle und einem Riegel, der auch für den Eigentümer gilt. DDL und Vorführung
   oben unter *Schritt 3 im Einzelnen*.
4. **Erst dann die Oberfläche**, und erst dann die Frage Tkinter oder Qt 6 —
   die hängt am Oracle-Update, nicht am Datenmodell.

## Schritt 2 im Einzelnen: die Sicht daneben

### Warum das ohne eine Zeile Codeänderung geht

Der Grund steht in eurem eigenen Quelltext, und ich habe ihn nachgezählt:

```
Tabellen in FROM/JOIN in lims_db.py:   117 ohne Schemapräfix,  0 mit
```

Und der Kommentar direkt darüber sagt, was das bedeutet:

> „Die Tabellen werden ohne Schemapraefix angesprochen; sie liegen im Schema
> des angemeldeten Benutzers oder sind ueber Synonyme erreichbar."

Daraus folgt der ganze Trick. Ein unqualifiziertes `FROM ergebnisse` löst
Oracle **im Schema des angemeldeten Benutzers** auf. Legt man also ein eigenes
Schema an, in dem ein Objekt namens `ERGEBNISSE` liegt — und zwar eine *Sicht*
auf `LIMSADMIN.ERGEBNISSE` mit nur den 27 gebrauchten Spalten —, dann liest
**dieselbe Abfrage**, Wort für Wort unverändert, die Sicht statt der Tabelle.
Umgeschaltet wird über die Anmeldung oder über eine Zeile:

```sql
ALTER SESSION SET CURRENT_SCHEMA = neues_lims;
```

### Vorgeführt

[`tools/projektion_demo.py`](../tools/projektion_demo.py) baut genau das auf
und lässt dieselbe Abfrage zweimal laufen:

```
== Schritt 2: die Sicht daneben, nichts am Bestand
   limsadmin.ergebnisse hat 81 Spalten (wie im echten LIMS)
   neues_lims.ergebnisse hat 27 Spalten (27/81 = 33 %)
   Am Bestand geändert: nichts — CREATE VIEW, kein DROP, kein ALTER

   Dieselbe Abfrage, zweimal, Wort für Wort gleich:
     search_path=limsadmin   -> ([1, '2026P0000001', '2026W052', 1.5, 1.5],)   (SELECT * liefert 81 Spalten)
     search_path=neues_lims  -> ([1, '2026P0000001', '2026W052', 1.5, 1.5],)   (SELECT * liefert 27 Spalten)

   Rolle neu_lesen: nur SELECT auf die Sicht, nichts auf limsadmin
     Sicht lesen        -> 1
     Tabelle darunter   -> permission denied for schema limsadmin
     Sicht ändern       -> permission denied for view ergebnisse
```

Dieselbe Abfrage, dasselbe Ergebnis — aber `SELECT *` liefert einmal 81 und
einmal 27 Spalten. Und die Rolle, die nur die Sicht darf, kommt an die Tabelle
darunter nicht heran.

> **Vorgeführt auf PostgreSQL 16**, weil hier kein Oracle 11.2 steht. Der
> Mechanismus ist derselbe, nur die Namen unterscheiden sich:
>
> | | PostgreSQL | Oracle |
> |---|---|---|
> | Schema anlegen | `CREATE SCHEMA` | `CREATE USER` — ein Schema *ist* ein Benutzer |
> | Auflösung umschalten | `SET search_path = …` | `ALTER SESSION SET CURRENT_SCHEMA = …` |
> | Fehler auslösen | `RAISE EXCEPTION` | `RAISE_APPLICATION_ERROR` |
> | Zähler | `GENERATED AS IDENTITY` | dito ab 12c — **in 11.2: Sequenz + Trigger** |

### Das SQL, das der DBA einmal ausführt

```sql
-- 1. Das eigene Schema. In Oracle ist ein Schema ein Benutzer.
CREATE USER neues_lims IDENTIFIED BY "…";
GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SYNONYM,
      CREATE SEQUENCE, CREATE TRIGGER TO neues_lims;
ALTER USER neues_lims QUOTA 500M ON <tablespace>;

-- 2. Lesen auf die 27 Tabellen. Direkt an den BENUTZER, mit GRANT OPTION —
--    warum, steht unten.
GRANT SELECT ON limsadmin.ergebnisse TO neues_lims WITH GRANT OPTION;
GRANT SELECT ON limsadmin.proben     TO neues_lims WITH GRANT OPTION;
--    … die übrigen 25 analog, Liste im Kopf von docs/sichten.sql

-- 3. Schreiben NUR dort, wo LabControl wirklich schreibt — sechs Tabellen,
--    ausgezählt aus jeder UPDATE/INSERT/DELETE-Anweisung in lims_db.py:
GRANT UPDATE                 ON limsadmin.ergebnisse        TO neues_lims;
GRANT UPDATE                 ON limsadmin.proben            TO neues_lims;
GRANT UPDATE                 ON limsadmin.standard_para     TO neues_lims;
GRANT UPDATE                 ON limsadmin.teilproben_anhang TO neues_lims;
GRANT UPDATE, INSERT         ON limsadmin.bew_teil          TO neues_lims;
GRANT UPDATE, INSERT, DELETE ON limsadmin.serien_mw_anhang  TO neues_lims;
```

> **Achtung, hier weicht die Doku vom Code ab.** `LIMS-Tabellen.md` sagt
> „Geschrieben wird in genau vier davon". Ausgezählt sind es **sechs**: dazu
> kommen `PROBEN` (die Bemerkung einer Probe, `UPDATE proben SET bemerkung`)
> und `TEILPROBEN_ANHANG` (`UPDATE teilproben_anhang SET mw_old = mw, mw = …`).
> Wer nach der Doku grantet, bekommt beim ersten Bemerkungstext ein
> `ORA-01031: insufficient privileges`.

Danach, angemeldet als `neues_lims`, die fertige Datei:

```sql
@docs/sichten.sql
```

### Drei Oracle-Fallen, die genau hier zuschlagen

**1. `WITH GRANT OPTION` ist nicht optional.** Wer über eine Sicht Rechte
weitergeben will, braucht das Recht auf der Basistabelle *mit* Grant-Option.
Oracle sagt das wörtlich:

> „To grant SELECT on a view to another user, either you must own all of the
> objects underlying the view or you must have been granted the SELECT object
> privilege WITH GRANT OPTION on all of those underlying objects. **This is
> true even if the grantee already has SELECT privileges on those underlying
> objects.**"

Ohne das lässt sich `GRANT SELECT ON v_ergebnisse TO labor_lesen` nicht
ausführen — und man merkt es erst, wenn die Sichten schon stehen.

**2. Rechte über eine Rolle zählen nicht.** Eine Sicht ist ein Objekt mit
Definer-Rechten; zum Übersetzen braucht sie **direkte** Grants auf die
Basistabellen. Wenn euer heutiges Anmeldekonto seine Rechte über eine Rolle
hat — der Normalfall —, kann es damit keine Sicht auf `LIMSADMIN` anlegen.
Oracle hat dazu eine passende Einschränkung: „You can specify WITH GRANT
OPTION only when granting to a user or to PUBLIC, **not when granting to a
role**." Also: direkt an den Benutzer `neues_lims`, nicht an eine Rolle.

**3. Eine Sicht ist keine Mauer, sondern ein Ausschnitt.** Eine einfache
Projektionssicht auf eine Tabelle ist in Oracle **änderbar** — wer `UPDATE` auf
die Sicht hat, schreibt in die Tabelle darunter. Die Schranke ist nicht die
Sicht, sondern das `GRANT`. Deshalb sind Schritt 2 und 3 zwei Schritte: die
Sicht regelt, *was man sieht*, der Grant regelt, *was man darf*.

*Kein* Problem ist dagegen die Geschwindigkeit: Oracle löst einfache Sichten
beim Optimieren in die Abfrage hinein auf (View Merging), die Ausführungs­pläne
sind dieselben. Und wo du die volle Breite brauchst, nimm statt einer Sicht ein
Synonym — `CREATE SYNONYM ergebnisse FOR limsadmin.ergebnisse` — das ist die
zweite Hälfte desselben Satzes aus eurem Kommentar.

### Was Schritt 2 an sich schon beweist

Das ist der eigentliche Wert dieses Schritts: **du kannst ihn ausführen, bevor
du eine Zeile neuen Code schreibst.** Sichten anlegen, das heutige LabControl
gegen das neue Schema anmelden, und einmal normal arbeiten. Läuft es durch,
dann ist bewiesen, dass 27 Tabellen und 168 Spalten reichen — nicht berechnet,
sondern vorgeführt. Läuft etwas nicht, sagt die Fehlermeldung genau, welche
Spalte in meiner Auswertung fehlt (sie ist eine Untergrenze, siehe oben), und
du ergänzt eine Zeile in der Sicht.

Kosten dieses Beweises: ein paar `CREATE VIEW`. Risiko für den Bestand: keins.

## Schritt 3 im Einzelnen: die eigenen Tabellen

Ab hier geht es nicht mehr um das alte LIMS, sondern um das, was das neue
besitzt. Zwei Tabellen sind der Kern, und beide haben eine Eigenschaft, die
`ERGEBNISSE` nicht haben kann: **sie gehören dir**, also darfst du sie so bauen,
dass sie später Auskunft geben.

> **Wie viel davon nötig ist, entscheidest du.** Das Folgende ist die
> vollständige Fassung. Wer weniger will, lässt die Rechte je Spalte und den
> Trigger weg und behält nur die zwei Tabellen mit `NOT NULL` und `CHECK` — das
> ist schon der größte Teil des Nutzens für einen Bruchteil des Aufwands. Die
> vollständige Fassung lohnt sich dort, wo eine Zahl später jemandem gegenüber
> begründet werden muss.

### Der Prüfpfad

```sql
-- Oracle 11.2: Zähler über Sequenz + Trigger, Identity gibt es erst ab 12c.
CREATE SEQUENCE pruefpfad_nr;

CREATE TABLE pruefpfad (
  id           NUMBER(18)    NOT NULL PRIMARY KEY,
  wann         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  db_benutzer  VARCHAR2(30)  DEFAULT USER NOT NULL,   -- wer an der Datenbank
  wer          VARCHAR2(64)  NOT NULL,                -- wer in der Anwendung
  tabelle      VARCHAR2(30)  NOT NULL,
  schluessel   VARCHAR2(200) NOT NULL,                -- welche Zeile
  feld         VARCHAR2(30),
  alt_wert     VARCHAR2(400),
  neu_wert     VARCHAR2(400),
  grund        VARCHAR2(400) NOT NULL,
  CONSTRAINT grund_nicht_leer CHECK (TRIM(grund) IS NOT NULL));

CREATE OR REPLACE TRIGGER pruefpfad_nummer
  BEFORE INSERT ON pruefpfad FOR EACH ROW
BEGIN
  IF :new.id IS NULL THEN
    SELECT pruefpfad_nr.NEXTVAL INTO :new.id FROM dual;
  END IF;
END;
```

Drei Dinge daran sind Absicht:

* **`db_benutzer DEFAULT USER`** — die Anwendung kann nicht lügen, wer
  geschrieben hat. Den Wert setzt die Datenbank, nicht der Aufrufer.
* **`wer` daneben** — weil die Datenbankanmeldung und der Mensch nicht
  dasselbe sind. Beides gehört in die Zeile.
* **`grund NOT NULL` plus `CHECK`** — eine Änderung ohne Begründung ist kein
  Prüfpfadeintrag. Das ist dieselbe Regel, die
  [`labcontrol/`](../labcontrol) hier im Repo schon durchsetzt.

### Die Freigabe

```sql
CREATE TABLE freigabe (
  prob_id      NUMBER        NOT NULL,
  um_id        NUMBER        NOT NULL,
  entscheidung VARCHAR2(16)  NOT NULL
    CONSTRAINT entscheidung_bekannt
    CHECK (entscheidung IN ('freigegeben','gesperrt')),
  wer          VARCHAR2(64)  NOT NULL,
  wann         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  begruendung  VARCHAR2(400) NOT NULL
    CONSTRAINT begruendung_nicht_leer CHECK (TRIM(begruendung) IS NOT NULL),
  CONSTRAINT freigabe_pk PRIMARY KEY (prob_id, um_id, wann));
```

`wann` gehört mit in den Schlüssel: eine Freigabe wird nicht überschrieben,
eine spätere kommt **daneben**. Damit ist die Entscheidungsgeschichte die
Tabelle selbst, und man braucht keinen zweiten Ort dafür.

### Der Riegel — und er hält auch gegen den Eigentümer

Zwei Schichten, und beide sind nötig:

```sql
-- 1. Die Rolle der Anwendung darf nur anhängen und lesen.
CREATE ROLE labor_arbeiten;
GRANT SELECT         ON v_ergebnisse TO labor_arbeiten;
GRANT INSERT, SELECT ON pruefpfad    TO labor_arbeiten;
GRANT INSERT, SELECT ON freigabe     TO labor_arbeiten;
-- kein UPDATE, kein DELETE. Was nicht dasteht, gilt nicht.

-- 2. Und ein Riegel, der auch für den Eigentümer gilt.
CREATE OR REPLACE TRIGGER pruefpfad_unveraenderlich
  BEFORE UPDATE OR DELETE ON pruefpfad
BEGIN
  RAISE_APPLICATION_ERROR(-20001, 'Pruefpfad: nur Anhaengen vorgesehen');
END;
```

Vorgeführt — und die interessante Zeile ist die zweite Gruppe, *der Eigentümer
selbst*:

```
== Schritt 3: die eigenen Tabellen, mit Rechten je Tabelle
   Und jetzt der Eigentümer selbst — der, der alles darf:
     INSERT       -> durchgelassen
     UPDATE       -> Pruefpfad: UPDATE ist nicht vorgesehen
     DELETE       -> Pruefpfad: DELETE ist nicht vorgesehen
     TRUNCATE     -> Pruefpfad: TRUNCATE ist nicht vorgesehen
     Grund leer   -> verletzt CHECK grund_nicht_leer

   Und die Rolle, unter der die Anwendung läuft:
     Prüfpfad schreiben     -> durchgelassen
     Freigabe schreiben     -> durchgelassen
     Freigabe ohne Grund    -> verletzt CHECK begruendung_nicht_leer
     Freigabe erfinden      -> verletzt CHECK entscheidung_bekannt
     Prüfpfad ändern        -> permission denied for table pruefpfad
     Prüfpfad löschen       -> permission denied for table pruefpfad
     Messwert ändern        -> permission denied for view ergebnisse
```

> **Ein Unterschied zu Oracle, den man wissen muss:** `TRUNCATE` ist dort DDL
> und löst **keinen** DML-Trigger aus — die Zeile oben gilt so nur für
> PostgreSQL. In Oracle hindert die Anwendungsrolle daran schon, dass sie die
> Tabelle nicht besitzt (`TRUNCATE` braucht Eigentum oder
> `DROP ANY TABLE`). Wer es auch dem Eigentümer verbauen will, braucht dort
> einen DDL-Trigger auf das Ereignis `TRUNCATE` — oder, einfacher und
> wirksamer: das Schema, das den Prüfpfad besitzt, hat ein Passwort, das im
> Alltag niemand benutzt, und die Anwendung meldet sich als
> `labor_arbeiten` an.

### Was das praktisch ändert

Halte es gegen die Messung aus [Variante 2](#variante-2--sqlite-die-oberfläche-als-rechtesystem):
dort haben drei Zeilen Python die Tabelle gelöscht, und es gab keine
Einstellung, die das verhindert hätte. Hier kann selbst der Eigentümer eine
Prüfpfadzeile nicht ändern, und die Rolle der Anwendung kann nur anhängen.

Der Nutzen ist nicht, etwas vorzeigen zu können — es ist die Antwort auf die
Frage, die in eurem Labor ohnehin irgendwann kommt: *woher kommt dieser Wert,
und wer hat ihn wann geändert?* `db_benutzer DEFAULT USER` liefert dazu die
Angabe, die kein Programm fälschen kann, weil die Datenbank sie setzt.
