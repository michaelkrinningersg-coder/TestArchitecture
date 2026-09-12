# Zwei Varianten für ein neues LIMS — geprüft

## Kurz

| | Antwort |
|---|---|
| **Variante 1**: neues LIMS, Oracle behalten | **Ja — und von allen Wegen der stärkste.** |
| **Variante 1a**: unnötige Tabellen und Spalten droppen | **Nein.** Es ist nicht euer Schema — Begründung unten. |
| **Variante 2**: SQLite, die Oberfläche als Rechtesystem | Als *Bedienkonzept* richtig. Als *Rechtesystem* nicht haltbar — ein Satz entscheidet das. |
| „In `ERGEBNISSE` bräuchte man viel weniger Spalten" | **Gemessen: 27 von 81.** Du liegst richtig. |

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
   baut, ersetzt das mit — und genau das ist die 17025-Frage aus
   [sqlite-neues-lims.md](sqlite-neues-lims.md), nur von der anderen Seite.

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
3. **17025 und Aufbewahrung.** In den Tabellen stehen Aufzeichnungen, die
   aufzubewahren sind. `DROP TABLE` ist deren Vernichtung, und ein
   `DROP COLUMN` auf `ERGEBNISSE` ist an der Stelle nicht rückholbar. Das ist
   kein technisches, sondern ein Nachweisproblem.
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

Für eine 17025-Nachweisführung ist die Frage ohnehin nicht „würde jemand das
tun?", sondern „zeigen Sie mir die Maßnahme". *„Unsere Oberfläche hat den Knopf
nicht"* ist keine.

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

* Die Rechte kommen von Oracle, nicht von der Oberfläche — das ist der Punkt,
  an dem SQLite ausscheidet und an dem 17025 entschieden wird.
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
   Bestand. Danach einmal gegen die Sichten lesen und prüfen, ob LabControl
   damit auskommt — das ist der Beweis, dass 2,8 % reichen.
3. **Die eigenen Tabellen dazu**: Prüfpfad, Freigaben, was das neue LIMS
   selbst besitzt. Mit `GRANT` je Tabelle, wie in
   [sqlite-neues-lims.md](sqlite-neues-lims.md#7-postgresql) vorgeführt — in
   Oracle heißt es genauso.
4. **Erst dann die Oberfläche**, und erst dann die Frage Tkinter oder Qt 6 —
   die hängt am Oracle-Update, nicht am Datenmodell.
