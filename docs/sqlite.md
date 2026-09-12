# Wäre SQLite für das LIMS möglich?

Analyse vom 12.09.2026 gegen den Quelltext von
[testlims](https://github.com/michaelkrinningersg-coder/testlims).

## Kurz vorweg: Qt6 und SQLite

Ja, und zwar auf zwei Wegen. PySide6 bringt eigene SQL-Treiber mit —
nachgesehen, nicht angenommen:

```
QPSQL, QSQLITE, QOCI, QMIMER, QIBASE, QODBC, QMARIADB, QMYSQL
```

`QSQLITE` ist dabei; `QSqlDatabase`/`QSqlQuery`/`QSqlTableModel` arbeiten damit
direkt gegen eine Tabellenansicht. Der zweite Weg ist `sqlite3` aus der
Standardbibliothek, ganz ohne Qt-Beteiligung — und den benutzt
[`labcontrol/`](../labcontrol) in diesem Repo: PySide6-Oberfläche, SQLite als
Ablage, 38 Tests, fertige Windows-EXE. Der Beleg läuft also schon.

Nebenbei: `QOCI` ist Qts Oracle-Treiber. Er hilft hier nicht — auch er braucht
die Oracle-Client-Bibliotheken und damit dieselbe 32-bit-Frage wie in
[portierung.md](portierung.md).

## Das Wichtigste: euer SQL ist praktisch dialektfrei

Aus `lims_db.py` geholt und geprüft:

| | |
|---|---|
| SELECT-Zeichenketten insgesamt | 50 |
| davon vollständige Anweisungen | 30 |
| **davon lauffähig auf SQLite** | **30** |
| Fragmente (setzt der Code zur Laufzeit zusammen) | 20 |

Nötig war genau eine Ersetzung: **`NVL(a, b)` → `COALESCE(a, b)`** (15 Stellen),
dazu ein `FROM dual`, das in SQLite entfällt.

### Nachtrag: zwei Oracle-Eigenheiten in den Fragmenten

Der erste Durchgang oben prüft nur die 30 *vollständigen* Anweisungen. Die
20 Fragmente sind damit ungeprüft geblieben, und genau dort steckt der Rest.
Nachgesehen über den ganzen Quelltext, nicht nur über fertige Abfragen:

| Eigenheit | Stellen in `lims_db.py` | wo |
|---|---:|---|
| `NVL(` | 16 | verteilt |
| `ROWNUM <= :grenze` | 5 | Zeilengrenzen, zur Laufzeit angehängt |
| `REGEXP_LIKE(serie, '^[0-9]{4}')` | 1 | `JAHR_REGEL`, in zwei Abfragen eingesetzt |
| `FROM dual` | 1 | `INSERT … SELECT … WHERE NOT EXISTS` |

Beide sind mechanisch zu ersetzen, und beide sitzen schon an der richtigen
Stelle — in *einer* Datei:

* `ROWNUM <= :grenze` → `LIMIT :grenze`. Euer Kommentar sagt selbst, warum es
  dasteht: „Oracle 11.2 kennt kein FETCH FIRST". SQLite und PostgreSQL kennen
  `LIMIT`.
* `REGEXP_LIKE(serie, '^[0-9]{4}')` → in SQLite
  `substr(serie,1,4) GLOB '[0-9][0-9][0-9][0-9]'`, ohne Erweiterung. In
  PostgreSQL ab 15 heißt die Funktion `regexp_like` und kann wörtlich
  stehenbleiben.

Was wirklich **nicht** vorkommt — geprüft über alle `.py` des Repos, und
`DECODE(`-Treffer waren durchweg Pythons `bytes.decode`:

`SYSDATE` · `TO_DATE` · `TO_CHAR` · `TO_NUMBER` · `TRUNC(` · Sequenzen
(`.NEXTVAL`) · `MERGE INTO` · `CONNECT BY` · `(+)`-Outer-Joins · analytische
Funktionen (`OVER(...)`) · `LISTAGG` · `DECODE` · `NVL2` · `FETCH FIRST`

Das bleibt ungewöhnlich sauber für gewachsenen Oracle-Code: vier Eigenheiten
in 23 Stellen einer Datei. **Praktischer Rat, unabhängig von jeder
Datenbankfrage:** schreibt künftig `COALESCE` statt `NVL` — das versteht
Oracle genauso, und damit ist euer SQL ohne Nacharbeit portabel.

## Die eigentliche Frage hat vier Lesarten

### A — Das LIMS selbst auf SQLite umstellen: **nein**

Nicht wegen der Datenmenge. `ERGEBNISSE` ist laut Kommentar „die größte
Tabelle des LIMS", ein einzelner Lauf hat rund 6 000 Ergebniszeilen, über die
Jahre also Millionen — das trägt SQLite ohne Weiteres.

Die Gründe sind andere, und sie stehen alle im Code:

* **LabControl ist Mitschreiber, nicht nur Leser.** Gefunden wurden
  `UPDATE` auf `ERGEBNISSE`, `PROBEN`, `SERIEN_MW_ANHANG`,
  `TEILPROBEN_ANHANG`, `BEW_TEIL`, `STANDARD_PARA`, dazu `INSERT` in
  `BEW_TEIL` und `SERIEN_MW_ANHANG` und ein `DELETE`. Proben und Serien legt
  LabControl nicht an — die kommen von anderswo. Es ist ein geteiltes System
  mit mehreren Schreibern, und `RELAQS_SOURCE`/`RELAQS_WORK` ist ein weiteres
  daran.
* **SQLite hat einen Schreiber zur Zeit.** Bei mehreren Bearbeitern heißt das
  Warten und „database is locked" — auch mit WAL.
* **Die Datei müsste ins Netz.** Damit mehrere darauf zugreifen, läge sie auf
  `G:`. SQLite über SMB ist für Mehrbenutzerbetrieb ausdrücklich nicht
  empfohlen: die Sperren hängen an Datei-Locks, die Netzdateisysteme nicht
  zuverlässig umsetzen — im schlechten Fall eine beschädigte Datei statt einer
  Fehlermeldung. WAL funktioniert über SMB gar nicht, weil es gemeinsamen
  Speicher braucht.
* **Keine Benutzerrechte.** Oracle unterscheidet Lese- und Schreibrecht je
  Tabelle und Benutzer; euer README verlangt für Änderungen ausdrücklich
  `UPDATE`-Recht. SQLite kennt keine Benutzer: wer die Datei lesen darf, darf
  alles ändern — auch ein Urteil der Qualitätsprüfung in `BEW_TEIL`, und zwar
  ohne Spur. Warum das zählt, sagt euer eigenes `protokoll.py`: „Ein Export
  schreibt in die Ergebnisse eines Labors, das seine Zahlen zwanzig Jahre
  aufhebt."

### B — SQLite als lesender Spiegel der Stammdaten: möglich, aber nur bei Bedarf

Stammdaten ändern sich selten: `PARAMETER`, `PRUEFMETHODEN`, `EINHEITEN`,
`VERFAHRENSKENNDATEN`, `STANDARDVERWALTUNG`. Die ließen sich lokal spiegeln —
Nutzen wären Arbeiten ohne VPN und schnellere Auswertungen.

Kosten: ein Abgleich, der schiefgehen kann, und die Frage „gegen welchen Stand
wurde bewertet". Die zweite löst der **Laufkontext** heute schon sauber — er
ist genau dafür gebaut. Ich würde das erst angehen, wenn Wartezeiten wirklich
stören; vorher ist es Aufwand ohne Anlass.

### C — SQLite für LabControls *eigene* Daten: **ja, der lohnende Teil**

Heute liegen neben der exe:

| Ablage | Form | Aus dem Code |
|---|---|---|
| `saves/` | gepacktes JSON je Lauf | „auf einem Netzlaufwerk mit hunderten Ständen" |
| `protokoll/` | Textdateien, angehängt | jede Änderung an der Datenbank |
| `einstellungen/` | JSON plus `backup/` | zuletzt benutzte Eingaben |

Die Kennung eines Saves steckt im **Dateinamen** — ausdrücklich, damit die
Liste im Reiter *Saves* auskommt, ohne jede Datei zu öffnen. Genau das ist eine
Aufgabe für einen Index. Mit SQLite:

* der Prüfpfad schreibt **atomar** statt anzuhängen — kein halber Eintrag,
  wenn das Netz mitten im Schreiben abbricht
* Saves sind abfragbar, ohne die Kennung in den Dateinamen zu kodieren
* Aufräumen ist ein `DELETE` statt eines Verzeichnisdurchlaufs
* ein Rückgabewert statt „Datei geschrieben, hoffentlich vollständig"

**Mit einer Bedingung:** je Arbeitsplatz eine **lokale** Datei
(`%LOCALAPPDATA%`), nicht eine gemeinsame auf `G:`. Sonst holt man sich genau
die Falle aus A. Was geteilt werden muss, gehört weiter ins LIMS.

### D — SQLite als Entwicklungs- und Testdatenbank: **ja, und billig**

Der Befund oben macht das leicht: 30 von 30 vollständigen Abfragen laufen nach
einer Ersetzung. Ein Schemanachbau der 27 wirklich berührten Tabellen — nur Struktur, keine echten
Daten — würde bringen:

* Entwickeln und Testen ohne VPN und ohne Oracle
* die CI prüft **echtes SQL** statt nachgebauter Antworten. Heute stünde in
  einem Test „`methoden_fuer_serie` gibt Paare zurück"; damit stünde dort, dass
  der Join über `TEILPROBEN` und `UNTERSUCHUNGSMETHODE` das Richtige liefert.
* für die Qt6-Portierung: die `DemoQuelle` könnte gegen dieses SQLite laufen
  statt gegen erfundene Listen — dieselbe Naht, mehr Aussagekraft

Das ist der Vorschlag, den ich zuerst umsetzen würde.

## Wenn es ein neues Projekt wird

Für etwas Neues neben dem LIMS — ein Werkzeug, das seine eigenen Daten hält —
ist **Qt 6 + SQLite genau richtig**, und
[`labcontrol/`](../labcontrol) in diesem Repo ist die fertige Vorlage:
PySide6, `sqlite3`, fortschreibender Prüfpfad, CSV-Export, Windows-EXE per
Action, 38 Tests ohne Fenster.

Die Grenze, an der es kippt, ist nicht die Datenmenge, sondern das
**gleichzeitige Schreiben**: sobald zwei Leute zur selben Zeit in dieselbe
Ablage schreiben müssen, ist PostgreSQL die Antwort. Weil euer SQL ohnehin
dialektfrei ist, kostet dieser Wechsel später wenig — vorausgesetzt, es bleibt
dialektfrei.
