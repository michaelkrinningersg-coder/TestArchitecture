# Welche Datenbank für ein neues LIMS — und was die Oracle-Lizenz wirklich kostet

## Kurz

| | Antwort |
|---|---|
| **Bei Oracle bleiben, Lizenz sparen** | Oracle Database Free: **12 GB harte Grenze**, 2 GB RAM, 2 Kerne. Reicht je nach Durchsatz 1–11 Jahre, dann `ORA-12954`. |
| **Ganz neue Datenbank** | **PostgreSQL.** Kostet nichts, hat keine Grenzen — und euer SQL läuft dort: **38 von 38 Anweisungen**, gemessen. |
| **SQLite** | Nur für einen Arbeitsplatz. Für eine geteilte Ablage nein ([Begründung](sqlite-neues-lims.md)). |
| **„Notlösung": alte DB, neue Oberfläche** | **Das ist keine Notlösung, das ist der beste erste Schritt** — und der einzige, der *heute* keine neue Lizenz kostet. |

## 1. Die Lizenzfrage, konkret

### Oracle Database Free — die Zahlen

Aus Oracles eigener Dokumentation, wörtlich:

> „Oracle AI Database Free limits itself automatically to **two cores** for
> processing."
>
> „The maximum amount of user data in Oracle AI Database Free cannot exceed
> **12 GB**." — bei Überschreitung: `ORA-12954`
>
> „The maximum amount of RAM for Oracle AI Database Free cannot exceed
> **2 GB**, even if more is available."
>
> „Oracle AI Database Free restricts itself to only **one installation per
> logical environment**." — bei Verstoß: `ORA-00442`

> **Was ich nicht belegen konnte:** die Nutzungsbedingungen selbst. Oracles
> Seiten dazu antworten hier mit *403 Forbidden*. Die Vorgängerfassung
> (Express Edition) war ausdrücklich auch produktiv erlaubt; **das gehört vor
> einer Entscheidung selbst nachgelesen**, nicht von mir übernommen.

### Was 12 GB bei euch bedeuten

Gerechnet aus der gemessenen Zeilenbreite (319 Byte + 58 Byte Index, siehe
[sqlite-neues-lims.md](sqlite-neues-lims.md)) und 6 000 Zeilen je Lauf:

| Läufe/Woche | Zeilen/Jahr | `ERGEBNISSE`/Jahr | 12 GB reichen für |
|---|---:|---:|---|
| 5 | 1,56 Mio | 0,55 GB | 22 Jahre — mit den übrigen 26 Tabellen grob **11** |
| 10 | 3,12 Mio | 1,10 GB | 11 Jahre — grob **5** |
| 20 | 6,24 Mio | 2,19 GB | 5 Jahre — grob **3** |
| 50 | 15,60 Mio | 5,48 GB | 2 Jahre — grob **1** |

Zwei Dinge dazu:

* **Es ist eine Wand, keine Bremse.** Bei 12 GB verweigert die Datenbank das
  Schreiben (`ORA-12954`). Wer dort ankommt, hat kein Leistungsproblem, sondern
  steht still — und muss dann migrieren, mit Zeitdruck.
* **Die 20 Jahre Geschichte passen vermutlich nicht mit hinein.** Bei 5 Läufen
  pro Woche wären das rund 11 GB — die Grenze ist damit schon erreicht, bevor
  ein neuer Wert geschrieben ist. Bei 20 Läufen sind es 44 GB.

Dazu 2 GB RAM: die Ankerabfrage bliebe schnell (sie liest über den Index wenige
Seiten), aber alles, was viel liest, verliert den Puffer — genau der Unterschied
zwischen „warm" und „kalt", den ich gemessen habe (1,6 ms gegen 33,8 ms für die
Serienliste).

**Fazit:** Oracle Free ist eine ehrliche Option für ein Werkzeug, aber nicht
für die Datenbank eines Labors, das zwanzig Jahre aufhebt.

### Der billigste Oracle-Weg ist gar kein neuer

Das ist der Punkt, der leicht untergeht: **ihr habt schon eine Oracle-Lizenz,
denn das LIMS läuft.** Ein eigenes Schema in derselben Instanz ist keine neue
Datenbank — es ist ein Benutzer mehr in einer bezahlten.

Ob das wirklich nichts kostet, hängt an eurem Lizenzmodell, und das kann ich
von hier nicht sehen:

| Modell | ein zusätzliches Schema | zusätzliche Benutzer |
|---|---|---|
| **Processor** (nach Prozessorkernen) | kostet nichts | kostet nichts |
| **Named User Plus** (nach benannten Benutzern) | kostet nichts | **kann kosten** — NUP zählt Personen |

Das ist eine Frage an die Stelle, die den Oracle-Vertrag hält, und sie ist vor
allem anderen zu klären: **fällt sie auf „Processor", ist Variante 1 aus
[varianten.md](varianten.md) kostenlos** — und damit der günstigste Weg
überhaupt.

## 2. Ganz neue Datenbank: PostgreSQL

### Euer SQL läuft dort — 38 von 38

Derselbe Versuch wie damals gegen SQLite, diesmal gegen PostgreSQL 16 und
diesmal gegen ein Schema, das der Sache nahekommt: die 27 Tabellen mit den 168
Spalten aus [`tools/spaltenbedarf.py`](../tools/spaltenbedarf.py), nicht
geraten. Werkzeug: [`tools/pg_dialekt.py`](../tools/pg_dialekt.py).

```
== PostgreSQL 16.13
   Schema gebaut: 27 Tabellen, 168 Spalten

   SELECT/UPDATE/INSERT-Zeichenketten: 60
   davon Fragmente (f-String-Lücken): 22
   vollständige Anweisungen: 38
   davon lauffähig:          38
   Fehler:                   0

   benutzte Übersetzungsgriffe:
     NVL→COALESCE             4×
     FROM dual weg            1×
```

**Null Fehler.** Nötig waren vier `NVL`→`COALESCE` und ein gestrichenes
`FROM dual`. `ROWNUM` kam unter den vollständigen Anweisungen gar nicht vor —
alle fünf Stellen stecken in Fragmenten, die der Code zur Laufzeit
zusammensetzt. Und `REGEXP_LIKE` bliebe unverändert stehen, weil PostgreSQL es
ab 15 kennt.

> Die Zahlen unterscheiden sich von den 30 im SQLite-Versuch, weil dort nur
> `SELECT` geprüft wurde und hier auch `UPDATE` und `INSERT`. Beide Läufe sagen
> dasselbe: das Dialektproblem ist klein und lokal.

### Was ihr dafür bekommt

Alles gemessen, Einzelheiten in [sqlite-neues-lims.md](sqlite-neues-lims.md)
und [varianten.md](varianten.md):

| | |
|---|---|
| Grenzen | keine 12 GB, keine 2 GB RAM, keine 2 Kerne |
| Kosten | **keine** — PostgreSQL-Lizenz, auch kommerziell |
| Mehrere Schreiber | zweiter Schreiber auf anderer Zeile: **durchgelassen nach 2,5 ms** |
| Rechte | je Tabelle **und je Spalte**: `GRANT UPDATE (mw_roh, mw)` |
| Prüfpfad, der nur wächst | `INSERT` ja, `UPDATE`/`DELETE`/`TRUNCATE` abgewiesen — auch für den Eigentümer |
| Lesen | 6 000 Zeilen in 35 ms (SQLite: 28 ms) |
| Schreiben | 6 000 Zeilen als *eine* Anweisung: 181 ms |

### Was es wirklich kostet: Betrieb, nicht Geld

Das ist die ehrliche Rechnung. Kein Lizenzposten, aber drei laufende Pflichten:

1. **Eine Maschine, die läuft.** Server, VM oder ein Rechner, der ohnehin
   durchläuft. Das Datenverzeichnis gehört dem Dienst allein — es kann nicht
   auf `G:` liegen.
2. **`pg_hba.conf` und eine Sicherung.** Zwei Zeilen und ein `pg_dump` im
   Aufgabenplaner. Ein Nachmittag.
3. **Jemand ist zuständig.** Updates, Neustart, Platz. Bei SQLite braucht das
   niemand — das ist der Preis für ein echtes Rechtesystem.

Punkt 3 ist die eigentliche Entscheidung, nicht Punkt 1 und 2.

### Und die 32-bit-Rechner? Kein Hindernis

Nachgesehen über die PyPI-Schnittstelle: `psycopg` und `psycopg2-binary` haben
**kein** `win32`-Rad. Aber **`pg8000` ist reines Python** (`any`-Rad) und läuft
damit überall, `pyodbc` hat ein `win32`-Rad. Alle Messungen oben liefen über
`pg8000`. Anders als bei Qt 6 ist 32 Bit hier **kein** Stopp.

## 3. SQLite: nur für einen Arbeitsplatz

Unverändert die Antwort aus [sqlite-neues-lims.md](sqlite-neues-lims.md): für
ein LIMS, auf das mehrere Arbeitsplätze schreiben, nein — nicht wegen der
Größe (die trägt es dreifach), sondern weil die Datei dann im Netz liegen müsste
und dort laut SQLites eigener Dokumentation eine **beschädigte Datei** möglich
ist. Messwerte sind teuer; eine verbrauchte Probe misst man nicht nach.

Für ein Werkzeug, das einer allein benutzt — Auswertung, Vorbereitung, eigene
Zwischenstände — bleibt es genau richtig.

## 4. Die „Notlösung" ist der beste erste Schritt

Du nennst es Notlösung. Ich würde es anders nennen, und zwar aus fünf Gründen:

| | |
|---|---|
| **Keine neue Lizenz** | Ein Schema in der bestehenden Instanz (unter „Processor" kostenlos, siehe oben) |
| **Keine Migration** | Zwanzig Jahre Geschichte bleiben, wo sie sind. Kein Datenrisiko. |
| **Kann heute anfangen** | Es hängt an keiner Beschaffung und an keiner Entscheidung. |
| **Beweist die 2,8 %** | Sichten anlegen, altes LabControl dagegen anmelden — läuft es, ist bewiesen, dass 27 Tabellen reichen. |
| **Die Oberfläche ist das, was du willst** | Genau das Stück, das den Alltag verbessert, kommt zuerst. |

### Drei Korrekturen zu „alles bleibt beim alten"

**1. Das Benutzermanagement bleibt *nicht* beim alten — es wird einfacher.**
Euer heutiges hängt an `BLUAD_USERS` / `BLUAD_GROUPS` / `BLUAD_PRIVILEGES`,
und das ist Innerei des zugekauften Produkts. Eine neue Oberfläche geht dort
gar nicht durch: sie meldet sich an Oracle an, und dann gelten **Oracles**
Benutzer und `GRANT`. Das ist die Schicht, die unter `BLUAD` sowieso schon
liegt.

Mein Rat: **koppelt die neue Oberfläche nicht an die `BLUAD_*`-Tabellen.** Sie
können sich mit jedem Produkt-Update ändern, und ihr hättet eine Abhängigkeit
an genau der Stelle, an der ihr unabhängig werden wollt. `BLUAD` bleibt
zuständig für die alten Masken; für die neue Oberfläche zählen Oracle-Rollen.

**2. „Es läuft nur über die neue Oberfläche" — hier trägt der Gedanke.**
Bei SQLite hat er nicht getragen, weil jeder an die Datei kommt. Bei Oracle
schon: der Benutzer hat **kein** Dateirecht auf die Daten, nur eine Anmeldung,
und was die darf, entscheidet der Dienst. Deine Oberfläche formt die Arbeit,
Oracle hält den Boden. Genau so soll es sein.

**3. „Alles bleibt" gilt für die Tabellen, nicht für die Rechte.** Die Chance
dieses Schritts ist, Schreibrechte auf das zu begrenzen, was wirklich
geschrieben wird — und das sind **sechs Tabellen**, nicht 456:
`ERGEBNISSE`, `PROBEN`, `STANDARD_PARA`, `TEILPROBEN_ANHANG`, `BEW_TEIL`,
`SERIEN_MW_ANHANG`. Der Rest ist Lesen. Das vollständige `GRANT`-Skript steht
in [varianten.md](varianten.md#das-sql-das-der-dba-einmal-ausführt).

### „Manches geht vielleicht besser" — ja, und das ist messbar

Was eine neue Oberfläche kann, was die alte nicht kann — alles schon in diesem
Repo belegt:

* **Farbe je Zelle statt je Zeile.** Eine `ttk.Treeview` kann nur Zeilen
  färben; ein Qt-Tabellenmodell färbt einzelne Zellen
  ([Test](../tests/test_labcontrol_qt.py)). Für ein Raster aus Parametern ×
  Proben ist das der Unterschied zwischen „lesbar" und „zählen müssen".
* **Listen, die nicht langsamer werden.** Qts Modell fragt nur die sichtbaren
  Zeilen ab: 48,7 ms bei 100 000 Zeilen gegen 2 864 ms bei Tkinter
  ([Vergleich](vergleich.md)).
* **Ein Prüfpfad, der Auskunft gibt** statt angehängter Textdateien — als
  Tabelle, atomar, abfragbar ([Schritt 3](varianten.md#schritt-3-im-einzelnen-die-eigenen-tabellen)).
* **Der Laufkontext als Quelle statt als Nachbau.** Damit fällt `RELAQS` weg —
  vier Tabellen weniger, und es steht schon so in eurem README.

## 5. Der Punkt, an dem beide Wege zusammengehen: die Naht

Das Wichtigste an der ganzen Entscheidung ist, dass du sie **nicht heute
treffen musst** — vorausgesetzt, die neue Oberfläche redet nie direkt mit der
Datenbank, sondern über eine Naht.

Die gibt es in diesem Repo schon: [`labcontrol_qt/quelle.py`](../labcontrol_qt/quelle.py)
mit dem `Datenquelle`-Protokoll und zwei Umsetzungen (`LimsQuelle` gegen
Oracle, `DemoQuelle` ohne Datenbank). Gebaut war sie für die Qt-Portierung; sie
ist genau das, was hier gebraucht wird. Ein Wechsel Oracle → PostgreSQL ist
dann **eine neue Umsetzung des Protokolls**, kein neues Programm.

Damit die Naht hält, drei Regeln beim Schreiben von SQL — alle drei kosten
nichts und sind heute schon fast eingehalten:

| statt | schreib | läuft dann auf |
|---|---|---|
| `NVL(a, b)` | `COALESCE(a, b)` | Oracle, PostgreSQL, SQLite |
| `ROWNUM <= :n` | `LIMIT :n` | PostgreSQL, SQLite (Oracle ab 12c: `FETCH FIRST`) |
| `FROM dual` | weglassen | PostgreSQL, SQLite |
| `REGEXP_LIKE(x, …)` | darf bleiben | Oracle, PostgreSQL ab 15 |

## 6. Empfehlung

**Die beiden Wege sind keine Alternativen, sondern zwei Schritte.**

**Jetzt:** neue Oberfläche auf der bestehenden Oracle, in einem eigenen Schema,
mit Sichten auf die 27 Tabellen und `GRANT` nur auf die sechs, in die wirklich
geschrieben wird. Kostet (unter „Processor") keine Lizenz, keine Migration,
kein Datenrisiko — und liefert genau das, was du haben willst.

**Später, wenn ihr aus Oracle heraus wollt:** PostgreSQL. Nicht Oracle Free —
die 12 GB sind eine Wand, und eure Geschichte passt nicht hinein. Der Wechsel
ist dann billig, weil (a) euer SQL messbar portabel ist, 38 von 38, (b) das
Datenmodell 27 Tabellen hat, nicht 456, und (c) die Oberfläche auf einer Naht
sitzt.

**Was ich als erstes klären würde, noch vor jeder Technik:** ob euer
Oracle-Vertrag nach Prozessoren oder nach benannten Benutzern rechnet. Fällt
die Antwort auf „Processor", ist der erste Schritt kostenlos, und die ganze
Lizenzfrage verliert ihre Dringlichkeit.
