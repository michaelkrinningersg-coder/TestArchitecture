# Neuen Benutzer anlegen — vier Schritte

Fertiges Skript: **[`docs/neuer_benutzer.sql`](neuer_benutzer.sql)**.
Vorgeführt mit [`tools/benutzer_anlegen.py`](../tools/benutzer_anlegen.py).

| Schritt | |
|---|---|
| **1. Konto** | `CREATE USER` + Passwort + `GRANT CREATE SESSION` |
| **2. Namen auflösbar machen** | Synonyme — ohne sie nützen die Rechte nichts |
| **3. Rechte übernehmen** | beim Vorbild **auslesen** und `GRANT`s daraus erzeugen |
| **4. Vergleichen** | bis die Liste der Unterschiede in **beiden** Richtungen leer ist |

Schritt 4 ist der, den man weglässt und dann bereut: ein Rechteabgleich, den
niemand geprüft hat, ist eine Vermutung.

## Schritt 1 — Konto und Passwort

```sql
CREATE USER neu IDENTIFIED BY "Anfang.2026!"
  DEFAULT TABLESPACE users
  TEMPORARY TABLESPACE temp
  PROFILE lims_benutzer;

GRANT CREATE SESSION TO neu;
```

Drei Kleinigkeiten, die jede für sich einen halben Tag kosten können:

* **`GRANT CREATE SESSION` nicht vergessen.** Ohne das Recht kommt bei der
  Anmeldung `ORA-01045: user lacks CREATE SESSION privilege`.
* **Kein Kontingent.** Ein LIMS-Benutzer legt keine eigenen Objekte an, also
  braucht er **kein `QUOTA`** und ganz sicher kein `UNLIMITED TABLESPACE`. Wer
  es trotzdem vergibt, erlaubt jedem, in der Datenbank eigene Tabellen
  anzulegen.
* **Passwort in doppelte Anführungszeichen**, wenn es Sonderzeichen enthält —
  und ab 11g sind Passwörter **groß-/kleinschreibungsempfindlich**.

### Die Passwortfalle, und sie trifft euch

Ohne eigenes Profil gilt `DEFAULT`, und das hat es ab Oracle 11g in sich:

| | Wert im `DEFAULT`-Profil ab 11g |
|---|---|
| `PASSWORD_LIFE_TIME` | **180 Tage** (vor 11g: unbegrenzt) |
| `FAILED_LOGIN_ATTEMPTS` | 10 |
| `PASSWORD_LOCK_TIME` | 1 Tag |

Nach einem halben Jahr kommt also `ORA-28001` — **und LabControl kann ein
abgelaufenes Passwort heute nicht wechseln.** Euer Code kennt den Fehler, aber
nur, um nicht noch einmal zu versuchen:

```python
ANMELDEFEHLER = ("ORA-01017", "ORA-28000", "ORA-28001", "ORA-01005")
```

Ein Dialog zum Ändern fehlt. Die Person käme also nicht mehr in die Anwendung
und bräuchte SQL\*Plus oder den DBA. Deshalb:

**Setzt niemals `PASSWORD EXPIRE` auf ein neues Konto**, solange die Oberfläche
das nicht kann. Und legt ein eigenes Profil an:

```sql
CREATE PROFILE lims_benutzer LIMIT
  FAILED_LOGIN_ATTEMPTS 10
  PASSWORD_LOCK_TIME 1/24          -- eine Stunde statt einem Tag
  PASSWORD_LIFE_TIME UNLIMITED;    -- oder ein Wert, den die Oberfläche kann
```

Was heute gilt, sagt die Datenbank selbst:

```sql
SELECT profile, resource_name, limit FROM dba_profiles
 WHERE resource_type = 'PASSWORD' ORDER BY profile, resource_name;
SELECT username, profile, account_status, expiry_date
  FROM dba_users ORDER BY username;
```

### Zwei Dinge, die die neue Oberfläche hier besser machen kann

Das ist eine der Stellen, an denen „manches geht vielleicht besser" ganz
konkret wird — und beide kosten wenige Zeilen:

**1. Passwort ändern.** Jeder darf sein **eigenes** Passwort ohne jedes
Sonderrecht ändern:

```sql
ALTER USER <er_selbst> IDENTIFIED BY "neues_passwort";
```

Ein Menüpunkt, eine Anweisung. Damit braucht niemand mehr den DBA dafür.

**2. Abgelaufenes Passwort beim Anmelden wechseln.** Der Treiber kann das schon
— nachgesehen in `oracledb` 26.0.0:

```
newpassword: a new password for the database user. The new password will take
effect immediately upon a successful connection to the database
```

Also: bei `ORA-28001` nicht abweisen, sondern nach einem neuen Passwort fragen
und mit `oracledb.connect(user=…, password=…, newpassword=…)` verbinden. Damit
ist die Passwortfalle oben erledigt, und ihr könnt die Ablauffrist behalten,
statt sie abzuschalten.

## Schritt 2 — Die Namen müssen auflösbar sein

**Der Schritt, der vergessen wird, und dann sehen die Rechte kaputt aus.** Euer
SQL spricht die Tabellen **117-mal ohne Schema** an, und der Kommentar dazu
sagt, woran das hängt:

> „Die Tabellen werden ohne Schemapraefix angesprochen; sie liegen im Schema
> des angemeldeten Benutzers oder sind ueber Synonyme erreichbar."

Erst nachsehen, wie es heute gelöst ist:

```sql
SELECT owner, synonym_name, table_owner, table_name
  FROM dba_synonyms
 WHERE table_owner = 'LIMSADMIN'
 ORDER BY owner, synonym_name;
```

* **Es gibt `PUBLIC`-Synonyme** → nichts zu tun, der neue Benutzer sieht sie
  sofort.
* **Jeder Benutzer hat eigene** → dieselben für den Neuen anlegen. Die Abfrage
  in [`neuer_benutzer.sql`](neuer_benutzer.sql) erzeugt die Anweisungen.

Symptom, wenn man es übersieht: `ORA-00942: table or view does not exist`,
obwohl das `GRANT` nachweislich dasteht.

## Schritt 3 — Rechte übernehmen: auslesen, nicht raten

Vier Abfragen, jede liefert fertige `GRANT`-Anweisungen zum Abspeichern und
Ausführen — Rollen, Systemrechte, Tabellenrechte, Spaltenrechte. Die
Spaltenrechte werden mit `LISTAGG` (ab 11.2) **gebündelt**, sonst wird aus den
18 Spalten von `ERGEBNISSE` achtzehn Mal `GRANT`:

```sql
SELECT 'GRANT ' || privilege || ' ('
       || LISTAGG(column_name, ', ') WITHIN GROUP (ORDER BY column_name)
       || ') ON ' || owner || '.' || table_name || ' TO &neu;'
  FROM dba_col_privs
 WHERE grantee = UPPER('&vorbild')
 GROUP BY owner, table_name, privilege;
```

## Schritt 4 — Vergleichen, in beiden Richtungen

`MINUS` über Rollen, Systemrechte, Tabellen- und Spaltenrechte. Beide Listen
müssen leer sein, und **die zweite Richtung ist die wichtigere**: hat der Neue
*mehr* als das Vorbild?

Vorgeführt — ein Vorbild mit **direkten** Rechten ohne Rolle, so wie es
aussieht, wenn über Jahre einzeln vergeben wurde:

```
== Vorbild: frank, mit DIREKTEN Rechten (keine Rolle)
   8 Einträge im Rechteverzeichnis

== Schritt 3: Anweisungen aus dem Katalog erzeugen
   GRANT SELECT ON limsadmin.ergebnisse TO emil;
   GRANT SELECT ON limsadmin.proben TO emil;
   GRANT UPDATE (kommentar, mw, mw_n, mw_org, mw_roh) ON limsadmin.ergebnisse TO emil;
   GRANT UPDATE (bemerkung) ON limsadmin.proben TO emil;

== Schritt 4: vergleichen — beide Richtungen
   fehlt emil:   0  — leer
   emil zu viel: 0  — leer
   => gleiche Rechte.

== Und die Probe aufs Ganze: dasselbe dürfen, dasselbe nicht
   frank  mw_roh=ja   erg.r01=nein bemerkung=ja    proben.r01=nein
   emil   mw_roh=ja   erg.r01=nein bemerkung=ja    proben.r01=nein
```

Die letzten zwei Zeilen sind Schritt 5: **ausprobieren.** Ein Rechteabgleich
gilt erst, wenn die Anwendung damit gearbeitet hat.

## Und danach: einmal eine Rolle, dann ist es zwei Zeilen

Der erste neue Benutzer ist der Anlass, aus den ausgelesenen Rechten **einmal**
eine Rolle zu machen ([`docs/rechte.sql`](rechte.sql)). Danach ist ein neuer
Mensch:

```sql
CREATE USER neu IDENTIFIED BY "…" PROFILE lims_benutzer;
GRANT CREATE SESSION, labor_bearbeiten TO neu;
```

und eine geänderte Zuständigkeit **eine** Zeile:

```sql
REVOKE labor_bearbeiten FROM bernd;
```

Vorgeführt, derselbe Lauf: `GRANT labor_arbeit TO gerda;` — und gerda durfte
sofort schreiben, ohne dass ein einziges Spaltenrecht noch einmal vergeben
wurde.

Mit `WITH ADMIN OPTION` braucht es dafür am Ende nicht einmal mehr den DBA:

```sql
GRANT labor_bearbeiten TO laborleitung WITH ADMIN OPTION;
```

## Was ich nicht geprüft habe

Hier steht **kein Oracle 11.2**. Die Vorführung lief auf PostgreSQL 16, wo
dieselben vier Schritte mit anderen Katalogsichten funktionieren. Die
Oracle-Anweisungen und -Katalogsichten in
[`neuer_benutzer.sql`](neuer_benutzer.sql) sind Standard, aber ungetestet:
**probiert sie mit einem Wegwerf-Benutzer aus**, bevor sie jemand Echtes
treffen. Die Zahlen zum `DEFAULT`-Profil und die Zitate aus der
Treiberdokumentation sind dagegen belegt.
