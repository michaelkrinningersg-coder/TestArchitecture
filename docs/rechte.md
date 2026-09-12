# Rechte für Benutzer — an den vollen Tabellen

Der Weg, wenn alles bleibt, wie es ist, und nur die Oberfläche neu wird. Er
braucht **drei Bausteine, und zwei davon habt ihr schon.**

Vorgeführt mit [`tools/rechte_demo.py`](../tools/rechte_demo.py) auf
PostgreSQL 16; das fertige Oracle-Skript liegt als
[`docs/rechte.sql`](rechte.sql) daneben, 55 Anweisungen.

## Kurz

| | |
|---|---|
| Eigene Konten je Person | **habt ihr schon** — `oracledb.connect(user=…, password=…)` |
| Rollen tragen die Rechte | vier Stück, aus eurem Code abgeleitet |
| Rechte **je Spalte** | dafür braucht es **keine Sicht** — die Tabelle bleibt voll, das Recht wird schmal |
| Was der DBA tun muss | die 55 Anweisungen **einmal** |
| Was danach ihr tut | `GRANT rolle TO person;` — eine Zeile je Mensch |

## 1. Der Baustein, den ihr schon habt

`lims_db.Zugang` verbindet mit dem Konto der Person, nicht mit einem
Sammelkonto:

```python
oracledb.connect(user=self.benutzer, password=self.passwort, dsn=…)
```

und im Docstring steht: „Das Passwort lebt nur hier im Arbeitsspeicher und wird
nirgends gespeichert oder protokolliert."

**Damit ist die Voraussetzung für alles Folgende erfüllt.** Oracle weiß bei
jeder Anweisung, wer sie abgesetzt hat; `USER` in der Datenbank *ist* die
Person. Ihr müsst also **kein eigenes Benutzermanagement bauen** — weder
Passworttabelle noch Rollenverwaltung in der Anwendung. Das wäre die Arbeit,
die ihr sparen könnt.

Nebenwirkung, die gratis kommt: ein Protokoll- oder Prüfpfadeintrag kann `USER`
als Standardwert nehmen, und dann kann die Anwendung nicht lügen, wer
geschrieben hat.

## 2. Rollen tragen die Rechte, nicht die Personen

Der Unterschied ist praktisch, nicht theoretisch: ohne Rollen ist jede neue
Kollegin ein Dutzend `GRANT`-Anweisungen, und nach zwei Jahren weiß niemand
mehr, wer was darf und warum. Mit Rollen ist „darf bearbeiten" **eine Zeile**.

Vier Rollen, und der Schnitt ist nicht erfunden — er steht in eurem Code:

| Rolle | darf | abgeleitet aus |
|---|---|---|
| `labor_lesen` | alle **27** Tabellen lesen | was `lims_db.py` abfragt |
| `labor_bearbeiten` | Messwerte, Bemerkung, Stationswechsel | `ERGEBNISSE`, `PROBEN`, `TEILPROBEN_ANHANG`, `SERIEN_MW_ANHANG` |
| `labor_qp` | das Urteil der Qualitätsprüfung | `BEW_TEIL` |
| `labor_stammdaten` | Sollwerte und Grenzen pflegen | `STANDARD_PARA` |

Jede Arbeitsrolle erbt das Lesen (`GRANT labor_lesen TO labor_bearbeiten;`), und
Personen bekommen nur die Rolle:

```sql
GRANT labor_lesen      TO anna;
GRANT labor_bearbeiten TO bernd;
GRANT labor_qp         TO clara;
GRANT labor_stammdaten TO dora;
```

## 3. Rechte je Spalte — dafür braucht es keine Sicht

Das ist der Teil, der zu deiner Entscheidung passt. **Die Tabellen bleiben
voll. Begrenzt wird das Recht, nicht die Tabelle.** Ausgezählt aus
`lims_db.py` — jede Spalte, die dort je links von einem `SET` steht oder in
einer `INSERT`-Spaltenliste:

| Tabelle | Recht | Spalten | von |
|---|---|---:|---:|
| `ERGEBNISSE` | `UPDATE` | **18** | 81 |
| `PROBEN` | `UPDATE` | **1** (`bemerkung`) | 51 |
| `TEILPROBEN_ANHANG` | `UPDATE` | 2 (`mw`, `mw_old`) | 12 |
| `STANDARD_PARA` | `UPDATE` | 6 | 17 |
| `SERIEN_MW_ANHANG` | `UPDATE` | 1 (`stat_id`) | 8 |
| `SERIEN_MW_ANHANG` | `INSERT`, `DELETE` | ganze Zeile | — |
| `BEW_TEIL` | `UPDATE` | 3 | 6 |
| `BEW_TEIL` | `INSERT` | alle 6 | 6 |

`PROBEN` ist das schönste Beispiel: von 51 Spalten wird **eine** geschrieben.
Ein `GRANT UPDATE (bemerkung) ON limsadmin.proben` und der Rest der Tabelle ist
für die Anwendung schreibgeschützt — ohne dass an der Tabelle etwas geändert
wird.

Und ein Detail, das für sich spricht: die sechs Spalten von `STANDARD_PARA` sind
**wörtlich** die Liste, die schon in eurem Code steht —

```python
STANDARDPARA_PFLEGBAR = ("sollwert", "toleranz", "gu", "go", "qc_gu", "qc_go")
```

Das `GRANT` verschiebt diese Regel nur von dort, wo ein Programmierfehler sie
umgehen kann, dorthin, wo das nicht geht.

## Vorgeführt

```
== Wer darf was
                       anna            bernd           clara           dora
   ---------------------------------------------------------------------------
   Ergebnisse lesen    1               1               1               1
   Messwert schreiben  nein            ja              nein            nein
   Bemerkung an Probe  nein            ja              nein            nein
   QP-Urteil setzen    nein            nein            ja              nein
   Sollwert pflegen    nein            nein            nein            ja

== Rechte je Spalte: dieselbe Tabelle, andere Spalte
   bernd: mw_roh (erlaubt)         -> durchgelassen
   bernd: mw (erlaubt)             -> durchgelassen
   bernd: r01 (nicht erlaubt)      -> permission denied for table ergebnisse
   bernd: proben.bemerkung         -> durchgelassen
   bernd: proben.r01               -> permission denied for table proben
   bernd: standard_para.sollwert   -> permission denied for table standard_para

== Rolle entziehen wirkt sofort
   bernd schreibt                 durchgelassen
   bernd liest                    1
   REVOKE labor_bearbeiten FROM bernd;
   bernd schreibt                 permission denied for table ergebnisse
   bernd liest weiter             1
   GRANT labor_bearbeiten TO bernd;
   bernd schreibt wieder          durchgelassen
```

Kein Neustart, keine Abmeldung: das nächste Statement gilt schon.

## Die Falle, in die man dabei tappt

**Ein Tabellenrecht schlägt das Spaltenrecht.** Gemessen:

```
vorher, r01                        permission denied for table ergebnisse
GRANT UPDATE ON ergebnisse TO labor_bearbeiten;   <- ganze Tabelle
jetzt, r01                         durchgelassen        <- Spaltenrecht wirkungslos
REVOKE UPDATE ON ergebnisse FROM labor_bearbeiten;
nach REVOKE, r01                   permission denied
nach REVOKE, mw_roh                permission denied    <- auch das Spaltenrecht ist weg!
```

Zwei Lehren daraus, und die zweite ist die wichtigere:

1. Wer *irgendwo* noch ein `GRANT UPDATE ON <tabelle>` stehen hat, hat die
   Spaltenrechte umsonst vergeben — das breitere gewinnt.
2. **`REVOKE` nimmt die Spaltenrechte mit.** Danach muss man die gewünschten
   Spalten neu vergeben. In Oracle ist das nicht anders, und es steht so in der
   Dokumentation:

   > „users … cannot selectively revoke column-specific privileges … the
   > grantor must first revoke the object privilege for all columns of a table
   > or view, and then selectively repeat the grant of the column-specific
   > privileges that the grantor intends to keep in effect."

## Zwei Oracle-Besonderheiten

**1. `SELECT` gibt es nicht je Spalte.** Oracles Dokumentation, wörtlich:

> „You can specify columns only when granting the `INSERT`, `REFERENCES`, or
> `UPDATE` privilege."

Lesen ist also immer die ganze Tabelle. Wenn eine *Spalte nicht lesbar* sein
soll, ist das die einzige Stelle, an der eine Sicht wirklich nötig wäre — aber
das ist ein anderes Problem als das Begrenzen von Änderungen, und für euren Fall
dürfte es keins sein. (PostgreSQL kann `GRANT SELECT (spalte)`; Oracle nicht.)

**2. `WITH ADMIN OPTION` holt den DBA aus dem Alltag.** Wer eine Rolle
weitergeben darf, braucht sie mit dieser Ergänzung:

```sql
GRANT labor_bearbeiten TO laborleitung WITH ADMIN OPTION;
```

Laut Oracle darf sie danach „grant and revoke the role to and from other
users" — also neue Kollegen selbst freischalten, ohne Ticket.

## Was deine Entscheidung einfacher macht

Weil du **keine Sichten** nimmst, fallen zwei Fallen aus
[varianten.md](varianten.md) weg:

* Die `WITH GRANT OPTION`-Regel — sie gilt nur, wenn man Rechte *über eine
  Sicht* weitergibt.
* Die Regel, dass Rechte über eine Rolle beim Übersetzen einer Sicht nicht
  zählen. Für gewöhnliches SQL aus einer Anwendung zählen Rollenrechte normal.

Der Weg über die vollen Tabellen ist also nicht nur der, den du willst — er ist
auch der mit weniger Kanten.

## Was der DBA einmal tut, und was danach ihr tut

**Einmal, vom DBA** (angemeldet als `LIMSADMIN` oder mit
`GRANT ANY OBJECT PRIVILEGE`): die 55 Anweisungen aus
[`docs/rechte.sql`](rechte.sql). Dabei wird **keine Tabelle angefasst** — ein
`GRANT` ändert nur einen Eintrag im Rechteverzeichnis.

**Danach ihr:** eine Zeile je Person. Und mit `WITH ADMIN OPTION` braucht auch
das keinen DBA mehr.

## Zuerst nachsehen, was heute gilt

Das ist der Schritt, den ich vor allem anderen machen würde. Eure Leute haben
heute schon Schreibrechte — die Anwendung arbeitet ja. Die Frage ist nicht „wie
vergebe ich Rechte", sondern **„was haben sie jetzt, und ist das mehr als
nötig?"** Drei Abfragen beantworten das:

```sql
-- Was darf ich selbst?
SELECT * FROM user_role_privs;
SELECT table_name, privilege            FROM user_tab_privs ORDER BY 1;
SELECT table_name, column_name, privilege FROM user_col_privs ORDER BY 1, 2;

-- Und als DBA, für alle: wer hat auf den LIMS-Tabellen was?
SELECT grantee, table_name, privilege
  FROM dba_tab_privs
 WHERE owner = 'LIMSADMIN' AND privilege <> 'SELECT'
 ORDER BY grantee, table_name;
```

Steht dort bei euren Leuten `UPDATE` auf ganzen Tabellen — oder gar auf
Tabellen, die die Anwendung nie schreibt —, dann ist das Ergebnis dieses
Schritts nicht „mehr Rechte vergeben", sondern **weniger**. Und genau dafür sind
die 18, 1, 2, 6, 1 und 3 Spalten oben die Vorlage.
