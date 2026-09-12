# Ein neues LIMS auf SQLite — wie groß, wie schnell, wo die Grenze liegt

Vier Fragen, in der Reihenfolge, in der sie kamen: Wie groß darf die Datei
werden? Wie viele Zeilen verträgt `ERGEBNISSE`? Was kostet der Zugriff, und
welche Bedingungen kann man erzwingen? Und: gehen Benutzerrechte wirklich
nicht — auch später nicht? Dazu PostgreSQL: wie man es aufsetzt, und ob es
einfach auf `G:` liegen darf.

Alles unten ist **gemessen**, nicht geschätzt. Werkzeug:
[`bench/sqlite_lims.py`](../bench/sqlite_lims.py). Es baut nicht *Beispieldaten*
nach, sondern die **Gestalt** der echten Tabelle: 81 Spalten wie `ERGEBNISSE`,
davon die 27, die in `lims_db.py` namentlich vorkommen, mit ihren echten Namen
und Typen; die übrigen 54 als Füllung in plausiblen Typen, damit die Zeile so
breit ist wie die echte. Dazu die sechs Tabellen, die die Ankerabfrage
dazujoint, und die Abfragen selbst wörtlich aus `lims_db.py` — nur `:name` auf
`?` umgestellt.

Ein Lauf ist wie bei euch **300 Proben × 20 Parameter = 6 000 Zeilen**; die
Zahl steht in `lims_db.py` als Begründung für `EXPORT_BUENDEL`.

> Gemessen auf 4 Kernen, 16 GB RAM, lokaler SSD, SQLite 3.45.1, Python 3.11.
> **Eine lokale SSD.** Was auf `G:` passiert, steht in Abschnitt 5 — und ist
> der eigentliche Punkt.

## Kurz

| Frage | Antwort |
|---|---|
| Wie groß kann die Datei werden? | 281 TB laut SQLite; euer Betrieb landet nach 15 Jahren bei **8–80 GB**. Größe ist nicht die Grenze. |
| Wie viele Zeilen in `ERGEBNISSE`? | Gemessen bis 25 Mio. Die Ankerabfrage bleibt dabei **gleich schnell**. |
| Zugriffsgeschwindigkeit? | Mit Index **28 ms** für einen Lauf — bei 60 000 wie bei 25 Mio Zeilen. Ohne Index wächst sie mit der Tabelle. |
| Constraints? | NOT NULL, CHECK, UNIQUE, PK, FK, generierte Spalten, Trigger, Fensterfunktionen: alles da. Zwei Schalter muss man kennen. |
| Benutzerrechte? | **Nein. Und auch später nicht** — das ist Bauart, keine Lücke. |
| PostgreSQL auf `G:`? | **Nein**, so funktioniert es nicht. Es braucht eine Maschine, die läuft. |

## 1. Wie groß kann die Datei werden

### Die Obergrenze

SQLite sagt es selbst: „An SQLite database is limited in size to 281 terabytes
(2⁴⁸ bytes, 256 tibibytes)." Das gilt bei der größten Seitengröße; in der
Voreinstellung ist weniger:

| | |
|---|---|
| `PRAGMA page_size` (Standard) | 4 096 Byte |
| `PRAGMA max_page_count` | 4 294 967 294 |
| ergibt | **16 TiB** |
| mit `page_size = 65536` | **256 TiB** |

Das sind bei der unten gemessenen Zeilenbreite rund **47 Milliarden Zeilen**.
Die Grenze ist also nicht SQLite, sondern was ein Rechner in einer Datei
sichern, prüfen und über ein Netz schieben kann.

### Was eine Zeile wirklich kostet

Dieselbe Tabelle, 1 Million Zeilen, drei Füllgrade — weil eine 81-spaltige
Tabelle in der Praxis nie ganz gefüllt ist und man die Bandbreite kennen soll:

| Füllung | belegte Spalten | Datei ohne Index | Byte/Zeile |
|---|---:|---:|---:|
| `nur_kern` | 27 von 81 | 158,6 MB | **167** |
| `mittel` | 45 von 81 | 302,6 MB | **319** |
| `voll` | 81 von 81 | 490,1 MB | **516** |

Die drei Indizes, die die Abfragen brauchen — `(serie, um_id, gegr_id)`,
`(prob_id, pm_id, pm_ver, um_id, gegr_id)`, `(serie)` — kosten **55,7 MB je
Million**, also 58 Byte je Zeile, unabhängig von der Füllung. Mit Index also
rund **375 Byte je Zeile** im mittleren Fall.

### Hochrechnung auf euren Betrieb

6 000 Zeilen je Lauf, 52 Wochen, 15 Jahre, 375 Byte je Zeile:

| Läufe/Woche | Zeilen/Jahr | nach 15 Jahren | Datei (mittel) | Datei (voll) |
|---|---:|---:|---:|---:|
| 5 | 1,56 Mio | 23 Mio | **8,2 GB** | 12,5 GB |
| 10 | 3,12 Mio | 47 Mio | **16,4 GB** | 25,0 GB |
| 20 | 6,24 Mio | 94 Mio | **32,9 GB** | 50,1 GB |
| 50 | 15,60 Mio | 234 Mio | **82,2 GB** | 125,2 GB |

Auch der schlimmste Fall liegt um drei Größenordnungen unter der Obergrenze.
**Die Datenmenge ist kein Argument gegen SQLite — sie war es nie.**

## 2. Wie viele Zeilen verträgt `ERGEBNISSE`

Gemessen mit `mittel`-Füllung, jede Abfrage mehrfach, Median der Läufe:

| | 1 Mio | 10 Mio | 25 Mio |
|---|---:|---:|---:|
| Datei ohne Index | 302,6 MB | 3 038,6 MB | 7 598,7 MB |
| Datei mit Index | 358,3 MB | 3 608,2 MB | 9 060,4 MB |
| **Ankerabfrage, mit Index** | **28,0 ms** | **28,2 ms** | **27,4 ms** |
| Ankerabfrage, ohne Index | 137,7 ms | 1 317,7 ms | 3 206,2 ms |
| Serienliste (`DISTINCT serie`) | 0,2 ms | 1,6 ms | 3,4 ms |
| `COUNT(*)` | 3,7 ms | 41,9 ms | 95,6 ms |
| Export: 6 000 `UPDATE` | 26,3 ms | 26,6 ms | 26,0 ms |

Die eine Zahl, auf die es ankommt, ist die dritte Zeile: **die Ankerabfrage
bleibt gleich schnell.** 28 ms bei 60 000 Zeilen, 28 ms bei 25 Millionen. Das
ist kein Glück, sondern die Bauart eines B-Baum-Index: die Datenbank sucht sich
über `(serie, um_id, gegr_id)` an die 6 000 Zeilen des Laufs und rührt den Rest
nicht an. Was dabei überhaupt noch Zeit kostet, ist nicht SQLite, sondern
Python, das 6 000 × 31 Werte in Tupel packt.

Die Zeile darunter zeigt, was ohne Index passiert: von 1 auf 10 Millionen
wird dieselbe Abfrage fast genau **zehnmal** langsamer (137,7 → 1 317,7 ms),
weil die Datenbank jedes Mal die ganze Tabelle liest. Bei 10 Millionen Zeilen
ist sie damit **47-mal langsamer als mit Index**. Das ist der Unterschied
zwischen „wird nie langsamer" und „wird mit jedem Jahr langsamer", und er
kostet nichts als drei `CREATE INDEX`.

### Ein Befund zu einem eurer Kommentare

In `lims_db.serien_historisch` steht:

> „Das ist die teure Haelfte: ERGEBNISSE ist die groesste Tabelle des LIMS, und
> ohne Index auf SERIE liest die Datenbank sie ganz."

Das stimmt — und die Umkehrung stimmt auch. **Mit** einem Index auf `serie`
braucht dieselbe Abfrage bei 10 Millionen Zeilen **1,6 ms** für 512 Serien,
weil SQLite von einem Wert zum nächsten springt statt zu lesen. Die
Vorsichtsmaßnahme im Startbildschirm — die teure Hälfte getrennt laden, damit
die laufenden Serien nicht darauf warten — bliebe richtig, würde aber nichts
mehr abfangen müssen.

Wenn ihr im Oracle-LIMS gelegentlich lange auf die Serienliste wartet, lohnt
ein Blick, ob `ERGEBNISSE.SERIE` dort einen Index hat. Das ist unabhängig von
jeder SQLite-Frage.

### Und wenn nichts im Arbeitsspeicher liegt

Alle Zahlen oben sind *warm*: die Datei lag schon im Seitencache. Beim ersten
Zugriff nach dem Start ist sie kalt. Derselbe Lauf mit vor jeder Messung
verworfenem Cache:

Dieselben Abfragen bei 10 Mio Zeilen, vor jeder Messung mit verworfenem
Seitencache:

| | warm | ohne Seitencache |
|---|---:|---:|
| **Ankerabfrage, mit Index** | 28,2 ms | **29,7 ms** |
| Serienliste (`DISTINCT serie`) | 1,6 ms | **33,8 ms** |
| `COUNT(*)` | 41,9 ms | 75,5 ms |
| Ankerabfrage, ohne Index | 1 317,7 ms | 3 219,7 ms |
| Export: 6 000 `UPDATE` | 26,6 ms | 26,9 ms |

Das Muster ist klar: **was über einen Index an wenige Seiten geht, merkt den
kalten Cache kaum** (28 → 30 ms). Alles, was viel liest, wird deutlich
langsamer — die Serienliste um das Zwanzigfache.

> **Was diese Zeile nicht belegt.** Diese Maschine ist eine virtuelle; unter ihr
> liegt noch ein Cache des Wirtsystems, den ich nicht leeren kann. Der erste
> Zugriff nach längerer Pause hat einmal **279 ms** für `COUNT(*)` gebraucht,
> statt der 75 ms hier. Die Spalte ist also eine **Untergrenze** — eine echte
> Platte ist schlechter, und ein Netzlaufwerk noch einmal um Größenordnungen.
> Was auf `G:` passiert, kann ich hier **nicht** messen; das ist genau der Punkt,
> der in Abschnitt 5 zählt.


## 3. Constraints — was man erzwingen kann

### Was da ist

Alles einmal angelegt und ausprobiert:

| Bedingung | Ergebnis |
|---|---|
| `NOT NULL` | `NOT NULL constraint failed: ergebnisse.prob_id` |
| `CHECK (mw >= 0)` | `CHECK constraint failed: mw >= 0` |
| `CHECK (status IN (…))` | `CHECK constraint failed: status IN ('offen','geprueft','freigegeben')` |
| zusammengesetzter `PRIMARY KEY` | `UNIQUE constraint failed: ergebnisse.prob_id, ergebnisse.para_id` |
| `FOREIGN KEY` | greift — mit dem Schalter unten |
| generierte Spalte, Trigger, partieller Index, FTS5-Volltext, Fensterfunktionen | alles vorhanden |

Für einen Prüfpfad heißt das: „nur wachsen, nie ändern" lässt sich in der
Datenbank selbst festschreiben — ein `BEFORE UPDATE`-Trigger mit
`RAISE(ABORT, …)` auf der Prüfpfadtabelle, und kein Programmierfehler kann
daran vorbei.

### Zwei Schalter, die man einmal kennen muss

**Fremdschlüssel sind von Haus aus aus.** Nicht „schwach", sondern aus:

```
PRAGMA foreign_keys = 0          <- Voreinstellung
INSERT INTO ergebnisse VALUES (99999, 1.0)   -- es gibt keine Probe 99999
-> durchgelassen, stillschweigend
```

Und der Schalter muss **je Verbindung** und **außerhalb eines Vorgangs**
gesetzt werden — innerhalb einer offenen Transaktion wird er ohne Fehlermeldung
ignoriert:

```
db.execute("INSERT …")                 # öffnet die Transaktion
db.execute("PRAGMA foreign_keys=ON")
db.execute("PRAGMA foreign_keys")  ->  0     # wirkungslos, ohne Hinweis
```

Richtig ist: gleich nach dem Verbinden, vor allem anderen. Dann greift er
(`FOREIGN KEY constraint failed`). Was vorher an Waisen entstanden ist, bleibt
allerdings stehen — `PRAGMA foreign_key_check` zeigt sie.

**Ohne `STRICT` nimmt eine `REAL`-Spalte Text an.** SQLite hat von Haus aus
nur eine Typ-*Neigung*:

```
CREATE TABLE locker (mw REAL, kennung TEXT);
INSERT INTO locker VALUES ('kein Messwert', 42);
SELECT typeof(mw) -> 'text'      -- steht jetzt Text in der Messwertspalte

CREATE TABLE streng (mw REAL, kennung TEXT) STRICT;
INSERT INTO streng VALUES ('kein Messwert', 42);
-> cannot store TEXT value in REAL column streng.mw
```

Für ein LIMS gibt es dazu nichts abzuwägen: **`STRICT` an jede Tabelle**, und
`PRAGMA foreign_keys=ON` in die Verbindungsfunktion. Beides einmal, dann nie
wieder.

### Die harten Grenzen

| | |
|---|---:|
| Spalten je Tabelle | 2 000 (nötig: 81) |
| Länge einer SQL-Anweisung | 1 000 000 000 Byte |
| Länge eines Werts | 1 000 000 000 Byte |
| gleichzeitig angehängte Datenbanken (`ATTACH`) | 10 |
| Bindungsparameter je Anweisung | 250 000 |
| Tiefe eines Ausdrucks | 1 000 |

Keine davon ist für ein LIMS in Sichtweite.

## 4. Benutzerrechte: nein — und auch nicht später

Das ist die Frage, an der es hängt, deshalb ausprobiert statt behauptet.

**Es gibt die Sprache nicht:**

```
CREATE USER leser IDENTIFIED BY '…'   -> OperationalError: near "USER": syntax error
GRANT SELECT ON ergebnisse TO leser   -> OperationalError: near "GRANT": syntax error
REVOKE UPDATE ON ergebnisse FROM …    -> OperationalError: near "REVOKE": syntax error
```

**Die Benutzer-Erweiterung ist nicht dabei.** SQLite hat ein optionales Modul
`SQLITE_USER_AUTHENTICATION`. In der Fassung, die Python mitbringt — und die in
jeder EXE landet — ist es nicht einkompiliert:

```
USER_AUTH einkompiliert: False
sqlite3_user_add in Python: False
```

Es würde auch nicht helfen: es entscheidet, ob eine Verbindung die Datei öffnen
darf, nicht wer welche Tabelle ändern darf. Und wer die Datei hat, kann sie mit
einem Programm ohne diese Erweiterung öffnen.

**Was die Anwendung selbst kann, und warum es keine Grenze ist.** Es gibt
`set_authorizer` — ein Rückruf, der Schreibzugriffe in *dieser* Verbindung
verbietet. Das wirkt:

```
UPDATE ergebnisse SET mw = 99 -> not authorized
SELECT mw FROM ergebnisse     -> (42.0,)      # Lesen geht weiter
```

Und derselbe Benutzer, dieselbe Datei, ein zweites Programm ohne diesen
Rückruf:

```
UPDATE ergebnisse SET mw = 99   -> durchgelassen
DROP TABLE ergebnisse           -> durchgelassen
Tabelle noch da: 0
```

Ein `python -c` in drei Zeilen, und die Tabelle ist weg. **Das ist keine
Zugriffskontrolle, das ist eine Höflichkeitsregel.**

Die einzige echte Grenze bleibt das Dateirecht des Betriebssystems, und das
kennt zwei Stufen: lesen darf man alles oder nichts, schreiben darf man alles
oder nichts. Ein „darf Messwerte nachtragen, aber keine Freigabe widerrufen"
gibt es dort nicht.

**Kommt das noch?** Nein. Es ist kein fehlendes Merkmal, sondern die Bauart:
SQLite ist eine Bibliothek im Programm, kein Dienst zwischen Benutzer und
Daten. Wer die Datei öffnen kann, *ist* die Datenbank. Ein Rechtesystem
verlangt eine Instanz, die zwischen Benutzer und Datei sitzt und nicht umgangen
werden kann — und genau das ist der Unterschied zu PostgreSQL und Oracle. Für
eine 17025-Nachweisführung („wer durfte was, und woran sieht man das") ist das
der Punkt, an dem die Entscheidung fällt.

## 5. Der eigentliche Engpass: ein Schreiber

SQLite sagt es selbst: „SQLite supports an unlimited number of simultaneous
readers, but it will only allow one writer at any instant in time." Gemessen,
in beiden Journalarten, bei 10 Mio Zeilen:

| | `journal_mode=delete` | `journal_mode=wal` |
|---|---|---|
| zweiter Schreiber, während der erste schreibt | `database is locked` | `database is locked` |
| Leser, während geschrieben wird | durchgelassen | durchgelassen |
| Schreiber, während ein Leser eine Abfrage offen hält | **`database is locked`** | **durchgelassen (2 ms)** |
| ein Export (6 000 `UPDATE`) hält die Sperre | 100 ms | 70 ms |
| zweiter Bearbeiter wartet dabei | 81 ms | 54 ms |

Zwei Dinge daran sind wichtig, und beide widersprechen dem üblichen Ratschlag:

**WAL hebt die Einschreiber-Grenze nicht auf.** Der zweite Schreiber bekommt
`database is locked`, in beiden Arten. Was WAL wirklich bringt, steht in der
dritten Zeile: ein Schreiber muss nicht mehr warten, bis ein Leser fertig ist.
Bei einer Anwendung, die lange Listen anzeigt, ist das viel — aber es ist etwas
anderes als „mehrere können gleichzeitig schreiben".

**Bei euren Mengen wäre es zu verkraften — auf einer lokalen Platte.** 70 ms
Haltedauer heißt: zehn Leute, die jede Minute einen Export fahren, kommen sich
statistisch kaum in die Quere. Die Rechnung kippt an zwei Stellen:

* **Auf `G:` gilt sie nicht.** WAL funktioniert über ein Netzlaufwerk
  überhaupt nicht — SQLite: „All processes using a database must be on the same
  host computer; WAL does not work over a network filesystem. This is because
  WAL requires all processes to share a small amount of memory." Es bleibt die
  Rollback-Journal-Art, also Zeile drei in ihrer schlechten Fassung: jeder
  offene Leser blockiert jeden Schreiber. Und dazu: „file locking logic is
  buggy in many network filesystem implementations (on both Unix and Windows).
  If file locking does not work correctly, two or more clients might try to
  modify the same part of the same database at the same time, **resulting in
  corruption**."
* **Die Regel dazu ist eindeutig.** „A good rule of thumb is to avoid using
  SQLite in situations where the same database will be accessed directly
  (without an intervening application server) and simultaneously from many
  computers over a network."

Ein neues LIMS, auf das mehrere Arbeitsplätze schreiben, ist genau dieser Fall.
Nicht weil die Zahlen nicht reichen — sie reichen —, sondern weil die Datei auf
einem Netzlaufwerk liegen müsste, und dort ist der Ausfall nicht eine
Fehlermeldung, sondern eine beschädigte Datei.

## 6. Wenn LabControl im neuen LIMS aufgeht

> „labcontrol ist schon ein halbes lims"

Das ist keine Übertreibung, das ist nachzählbar:

| | |
|---|---:|
| Module (ohne Tests) | **74** |
| Zeilen Quelltext | **52 813** |
| Testmodule | 63 |
| Zeilen Tests | 33 715 |
| **Tests** | **2 497** |
| berührte LIMS-Tabellen | **27 von 456** |
| Reiter im Messfenster | 13 |

Die letzte Zeile ist die nützlichste Zahl in diesem ganzen Dokument: **27 von
456** — ausgezählt mit [`tools/spaltenbedarf.py`](../tools/spaltenbedarf.py)
aus dem Quelltext, und davon werden **168 von 5 976 Spalten** gebraucht, also
**2,8 %** (Einzelheiten in [varianten.md](varianten.md)). Ein neues LIMS
braucht nicht 456 Tabellen — es braucht die, an denen gearbeitet wird, und die
sind jetzt gezählt
([LIMS-Tabellen.md](https://github.com/michaelkrinningersg-coder/testlims/blob/main/LIMS-Tabellen.md)).
Das ist ein Datenmodell, über das man an einem Tag reden kann, statt über eine
gewachsene Landschaft.

### RELAQS ist schon abgeschrieben

Das steht nicht erst zur Debatte — es steht in eurem eigenen README:

> „Im LIMS trägt bisher `RELAQS_SOURCE` / `RELAQS_WORK` (View `V_RELAQS_WORK`)
> dieselben Angaben zusammen. **Das ist das Altsystem, das LabControl
> ersetzt.**"

Der Reiter *Laufkontext* baut es nach, und die Feldnamen sind absichtlich
angelehnt, „damit sich beide Seiten vergleichen lassen, solange noch beide
laufen". In einem neuen LIMS fällt das „solange noch beide laufen" weg: der
Laufkontext ist dann nicht mehr ein Nachbau zum Vergleichen, sondern **die
Quelle**. Das ist ein Vereinfachungsschritt, kein Zusatzaufwand — vier Tabellen
weniger (`RELAQS_SOURCE`, `RELAQS_WORK`, `RELAQS_USER`, `V_RELAQS_WORK`).

### Was das für die Datenbankfrage bedeutet

**Es verschärft sie.** Heute schreibt LabControl in vier Tabellen und füllt in
`ERGEBNISSE` nur, was leer ist — eine Zeile legt es nie an und löscht es nie,
weil das LIMS sie besitzt. Wenn LabControl *das* LIMS wird, besitzt es die
Zeile von der Probenannahme an, und dann schreibt nicht mehr ein Programm an
den Rändern, sondern **das ganze Labor in dieselbe Ablage**. Genau die Zahl, die
in Abschnitt 5 noch „zu verkraften" war, wächst dabei: mehr Schreiber, längere
Vorgänge, und keine Möglichkeit mehr, die Datei lokal zu halten.

Damit fällt die Entscheidung nicht enger, sondern deutlicher aus: **PostgreSQL.**

Eine Sache wird dabei allerdings *besser*, und sie ist für 17025 wichtig: heute
kann LabControl keinen vollständigen Prüfpfad über eine Ergebniszeile führen,
weil sie ihm nicht gehört — es sieht nur, was leer war, und schreibt hinein. In
einem eigenen Datenmodell gehört die Zeile von Anfang an dazu, und dann ist
„wer hat wann welchen Wert auf welchen geändert, und warum" keine Rekonstruktion
mehr, sondern eine Tabelle. Genau das macht
[`labcontrol/`](../labcontrol) in diesem Repo im Kleinen schon vor.

### Und was noch fehlt

Was LabControl heute *nicht* tut, ist der andere Teil eines LIMS, und der
sollte auf der Liste stehen, bevor geschätzt wird: **Proben und Serien legt es
nicht an** — die kommen von anderswo. Also Auftrags- und Probenannahme,
Probenkennzeichnung und Lagerung, Berichte nach außen, und was daran
kaufmännisch hängt. Das ist die andere Hälfte, und sie ist nicht die kleinere.

## 7. PostgreSQL

### Kann es einfach auf `G:` liegen?

**Nein — und zwar nicht, weil es verboten wäre, sondern weil PostgreSQL nicht
so gebaut ist.** Der Unterschied in einem Satz:

* **SQLite:** jedes Programm öffnet *die Datei*. Ein Netzlaufwerk ist der
  Versuch, das gemeinsame Öffnen über SMB zu machen — und daran hängt das ganze
  Sperrproblem.
* **PostgreSQL:** *ein* Dienst öffnet die Dateien, und zwar exklusiv. Alle
  anderen reden über das Netz mit diesem Dienst (Port 5432), nicht mit der
  Datei.

Damit ist die Frage „auf `G:` legen" beantwortet: das Verzeichnis gehört dem
Dienst allein, und die Arbeitsplätze greifen nie darauf zu. Es braucht also
**eine Maschine, die läuft** — ein Server, eine VM, oder ein vorhandener
Rechner, der ohnehin durchläuft. Das ist die eigentliche Entscheidung, nicht
die Installation.

(Am Rande: PostgreSQL *darf* sein Datenverzeichnis auf NFS haben — „The only
firm requirement for using NFS with PostgreSQL is that the file system is
mounted using the `hard` option" —, aber das ist das Verzeichnis *des Servers*,
nicht eine von allen geteilte Datei. Der Sinn wäre ein anderer: Speicher
auslagern, nicht Zugriff teilen.)

### Aufsetzen — und ob du das kannst

Hier, auf dieser Maschine, waren es zwei Befehle:

```console
$ apt-get install postgresql postgresql-client
$ pg_ctlcluster 16 main start
$ psql -c "SELECT version()"
 PostgreSQL 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1) on x86_64-pc-linux-gnu
```

Danach lief ein Dienst auf Port 5432 mit eigenem Datenverzeichnis
(`/var/lib/postgresql/16/main`). Eine ehrliche Zahl für die Dauer kann ich
nicht nennen — in diesem Abbild lagen die Pakete schon —, aber der Vorgang
selbst ist genau das: installieren, starten, fertig.


Das ist der einfache Teil und unter Windows noch einfacher: der Installer von
EDB ist ein Assistent, der nach einem Passwort für `postgres` und einem Port
fragt und danach einen Windows-Dienst hinterlässt. **Ja, das kannst du.**

Was dabei wirklich Arbeit ist, ist nicht die Installation, sondern die drei
Dinge, die man danach nicht vergessen darf:

1. **`pg_hba.conf`** — wer von wo mit welcher Anmeldung verbinden darf. Zwei
   Zeilen, aber falsch gesetzt lässt sie entweder niemanden oder jeden herein.
2. **Sicherung.** `pg_dump` täglich ist eine Zeile im Aufgabenplaner. Ohne sie
   ist die zentrale Datenbank ein zentraler Verlust.
3. **Jemand ist zuständig.** Ein Dienst, der läuft, muss Updates bekommen und
   beim Neustart des Rechners wieder hochkommen. Bei SQLite gibt es niemanden,
   der zuständig ist, weil es niemanden braucht — das ist der Preis, den ein
   echtes Rechtesystem kostet.

Punkt 3 ist die Entscheidung. Punkt 1 und 2 sind ein Nachmittag.

### Die 32-bit-Frage — hier fällt sie anders aus

Für die Qt6-Portierung war 32 Bit der Stopp
([portierung.md](portierung.md)). Bei der Datenbank ist es das **nicht**,
und das ist eine gute Nachricht. Nachgesehen über die PyPI-Schnittstelle,
nicht geraten:

| Paket | Fassung | `win32`-Rad |
|---|---|---|
| `psycopg` / `psycopg-binary` | 3.3.5 | **nein** |
| `psycopg2-binary` | 2.9.13 | **nein** |
| `pg8000` | 1.31.5 | **reines Python** (`any`) → läuft |
| `pyodbc` | 5.3.0 | **ja** |
| `SQLAlchemy` | 2.0.52 | **ja** |

Der übliche Treiber fällt also weg, aber **`pg8000` ist reines Python** und
braucht deshalb überhaupt kein Rad für eine Architektur. PostgreSQL ist von
euren heutigen 32-bit-Rechnern aus erreichbar — mit Tkinter als Oberfläche
heute, mit Qt 6 später, ohne die Datenbank noch einmal anzufassen.

### Was ihr dafür bekommt

Dieselben Fragen wie oben, an PostgreSQL 16 gestellt:

**Euer SQL, wörtlich gestellt:**

```
REGEXP_LIKE(serie, '^[0-9]{4}')        -> 50        (läuft unverändert!)
COALESCE(mw_roh, mw)                   -> 200000
… ORDER BY prob_id LIMIT 1000          -> 1000
NVL(mw_roh, mw)                        -> ERROR: function nvl(real, real) does not exist
… WHERE ROWNUM <= 10                   -> ERROR: column "rownum" does not exist
```

`regexp_like` läuft ab PostgreSQL 15 **wörtlich** — eure `JAHR_REGEL` müsste
nicht einmal angefasst werden. `NVL` und `ROWNUM` sind genau die zwei Stellen
aus der Tabelle unten.

**Rechte je Tabelle *und je Spalte* — Schritt für Schritt:**

```
Rolle labor_lesen darf verbinden, sonst nichts
  SELECT     -> permission denied for table ergebnisse
  UPDATE     -> permission denied for table ergebnisse

GRANT SELECT ON ergebnisse
  SELECT     -> 200000
  UPDATE     -> permission denied for table ergebnisse
  DELETE     -> permission denied for table ergebnisse
  DROP TABLE -> must be owner of table ergebnisse

GRANT UPDATE (mw_roh, mw) ON ergebnisse     <- je Spalte, nicht je Tabelle
  mw         -> durchgelassen
  mw_roh     -> durchgelassen
  kommentar  -> permission denied for table ergebnisse
```

Die dritte Stufe ist die, die es in SQLite nicht einmal als Begriff gibt:
**„darf Messwerte nachtragen, aber den Kommentar nicht anfassen"** ist ein
`GRANT` mit Spaltenliste. Und ein Prüfpfad, der wirklich nur wachsen kann,
ist es auch:

```
GRANT INSERT, SELECT ON pruefpfad
  INSERT   -> durchgelassen
  SELECT   -> 2
  UPDATE   -> permission denied for table pruefpfad
  DELETE   -> permission denied for table pruefpfad
  TRUNCATE -> permission denied for table pruefpfad
```

Das ist der Unterschied zu Abschnitt 4 in einem Bild: dort reichten drei Zeilen
Python, um die Tabelle zu löschen. Hier ist es der Datenbank verboten, und kein
Programm kann daran vorbei.

**Mehrere Schreiber — die Zeile, um die es geht:**

| | SQLite (WAL) | PostgreSQL 16 |
|---|---|---|
| zweiter Schreiber, andere Zeile | `database is locked` | **durchgelassen nach 2,5 ms** |
| dritter Schreiber, **dieselbe** Zeile | `database is locked` | wartet (Zeilensperre) |
| Leser, während geschrieben wird | sieht den alten Stand | sieht den alten Stand (MVCC) |

PostgreSQL sperrt **die Zeile**, nicht die Datei. Zwei Bearbeiter an zwei
Serien stören sich überhaupt nicht; zwei an derselben Zeile schließen sich aus,
und das ist gewollt.

**Was es kostet — und wo der Preis wirklich liegt.** Alles über `pg8000`,
reines Python, über TCP:

| | SQLite (in-process) | PostgreSQL (pg8000/TCP) |
|---|---:|---:|
| 6 000 Zeilen lesen | 28 ms | **35 ms** |
| Export: 6 000 einzelne `UPDATE` | 26 ms | 2 076 ms |
| dieselben 6 000 in **einer** Anweisung | — | **181 ms** |

Lesen ist praktisch gleich schnell. Beim Schreiben zeigt sich der Preis des
Netzes — aber nicht dort, wo man ihn vermutet: **2 076 ms kommen von 6 000
Rundreisen, nicht von PostgreSQL.** Dieselben 6 000 Sätze als *eine* Anweisung
(`UPDATE … FROM (SELECT unnest(…))`) brauchen 181 ms. Das ist derselbe Gedanke,
der bei euch schon hinter `EXPORT_BUENDEL = 500` steht — nur konsequenter. Wer
mit PostgreSQL arbeitet, schreibt Mengen, nicht Zeilen.


## 8. Empfehlung

**Für ein neues LIMS, auf das mehrere Arbeitsplätze schreiben: PostgreSQL.**
Nicht wegen der Datenmenge — die trägt SQLite dreifach —, sondern wegen der
zwei Dinge, die sich nicht nachrüsten lassen: Rechte je Tabelle und Benutzer,
und mehrere Schreiber ohne geteilte Datei im Netz. Beides steht bei einer
17025-Nachweisführung nicht zur Wahl.

**SQLite bleibt trotzdem auf dem Zettel, an drei Stellen:**

1. **Als Entwicklungs- und Testdatenbank.** 30 von 30 vollständigen Abfragen
   laufen ([sqlite.md](sqlite.md)); die CI prüft dann echtes SQL.
2. **Für die eigenen Daten eines Arbeitsplatzes** — Einstellungen, Zwischen­
   stände, ein lokaler Prüfpfad. Lokal, nicht auf `G:`.
3. **Für alles, was ein Mensch allein bearbeitet.** Ein Werkzeug, das seine
   eigenen Daten hält, braucht keinen Server.

**Und der Weg dahin ist billiger als er klingt**, weil euer SQL fast
dialektfrei ist: vier Eigenheiten in 23 Stellen *einer* Datei
([sqlite.md](sqlite.md#nachtrag-zwei-oracle-eigenheiten-in-den-fragmenten)).
Wer das Datendesign des heutigen LIMS übernimmt, übernimmt keine
Oracle-Abhängigkeit mit — vorausgesetzt, es bleibt so:

| statt | schreib | verstehen |
|---|---|---|
| `NVL(a, b)` | `COALESCE(a, b)` | Oracle, PostgreSQL, SQLite |
| `ROWNUM <= :n` | `LIMIT :n` | PostgreSQL, SQLite (Oracle ab 12c: `FETCH FIRST`) |
| `FROM dual` | weglassen | PostgreSQL, SQLite |
| `REGEXP_LIKE(x, …)` | `regexp_like(x, …)` | Oracle, PostgreSQL ab 15 |

Drei davon kosten nichts und halten die Tür offen.
