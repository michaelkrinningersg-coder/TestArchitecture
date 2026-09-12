# Fußballmanager oder Idle-Game mit Qt 6 und SQLite?

**Ja — und für die eine Sorte ist es sogar genau das richtige Werkzeug.**

Beide Gattungen sind im Kern dasselbe wie LabControl: viele Sätze in Tabellen,
eine Rechnung darüber, eine Oberfläche daneben. Was sie zusätzlich brauchen, ist
ein **Takt**. Ob Qt den hält und Python die Rechnung dahinter schafft, ist
messbar — Werkzeug: [`bench/spiel_takt.py`](../bench/spiel_takt.py), gemessen
auf 4 Kernen, PySide6 6.11.2, ohne Bildschirm.

## Gemessen

| | Ergebnis |
|---|---|
| **`QTimer` auf 60 Hz** | Soll 16,67 ms → Median **17,15 ms**, p95 17,19, höchster 17,44. Mittlere Abweichung **0,49 ms** |
| **Idle-Tick über 10 000 Werte** | **0,81 ms** je Tick → 1 239 Ticks/s. Bei 60 Hz sind das **5 % eines Kerns** |
| **30 Tage Abwesenheit** | geschlossen gerechnet: **1,57 ms**. Nachgetickt wären es **26 Stunden** |
| **Ein Fußballspiel, Minute für Minute** | **0,019 ms** |
| **Eine Saison (306 Spiele)** | **6,3 ms** |
| **Zwanzig Saisons Ligahistorie** | **0,13 s** — 48 448 Spiele/s |
| **Spielstand in SQLite** | 4 500 Spieler + 6 120 Spiele = **440 KB**, laden in **0,4 ms** |

## Was daran wichtig ist

**Der Takt ist kein Problem.** Qt hält 60 Hz mit einer halben Millisekunde
Abweichung, und der Spitzenwert liegt bei 17,4 ms — nicht bei 40. Für ein Spiel,
das keine Physik simuliert, ist das mehr als genug; ein Idle-Game käme auch mit
10 Hz aus.

**Rechenzeit ist nicht der Engpass.** Zwanzig Saisons in einer Achtelsekunde
heißt: „neues Spiel, zwanzig Jahre Vorgeschichte erzeugen" ist ein Ladebalken,
der nicht nötig ist. Und ein Idle-Tick über 10 000 Werte kostet 5 % eines Kerns
— es bleibt Luft für alles andere.

**Der eigentliche Stolperstein bei Idle-Games sind die Zahlen, nicht die
Geschwindigkeit.** Nach 30 Tagen bei 60 Hz sind das 155 Millionen Ticks, und
dabei **läuft ein `float` über** — der Bestand landet bei 10^671972. Man rechnet
deshalb im Logarithmus (oder mit Deckel), und Abwesenheit tickt man nicht nach,
sondern integriert sie: 1,57 ms statt 26 Stunden. Das ist der Trick der
Gattung, und er hat mit Qt nichts zu tun.

**Ein Fußballmanager ist in Wahrheit eine Datenbankanwendung.** Kader, Spielplan,
Transfers, Tabelle — Listen mit Filtern und Sortierung, und darüber eine
Rechnung. Genau darin ist Qt stark, und der
[Variantenvergleich](vergleich.md) in diesem Repo hat es gemessen: die
Modellaktualisierung liegt bei **100 000 Zeilen bei 48,7 ms**, und abgefragt
werden immer nur die knapp dreißig sichtbaren Zeilen. Eine Liga hat 10 000
Spieler. Das ist nicht die Größenordnung, in der man nachdenken muss.

**SQLite ist hier der naheliegende Spielstand.** 440 KB für eine volle
Ligahistorie, in 0,4 ms geladen, und ein einzelner Datei-Spielstand, den man
kopieren, mitnehmen und sichern kann. Es ist **ein** Spieler, der schreibt —
also genau der Fall, für den SQLite gebaut ist, und nicht der aus
[sqlite-neues-lims.md](sqlite-neues-lims.md).

## Wo es nicht passt

Die Grenze verläuft nicht bei der Geschwindigkeit, sondern beim **Zeichnen**:

| Vorhaben | Werkzeug |
|---|---|
| Fußballmanager: Tabellen, Kader, Spielplan, Simulation | **Qt 6 Widgets + SQLite** — ideal |
| Idle-Game aus Zahlen, Knöpfen, Fortschrittsbalken | **Qt 6 Widgets + SQLite** |
| Idle-Game mit viel Animation und eigenem Look | **Qt Quick (QML)** — dasselbe Qt 6, aber GPU-Szenengraph statt Widgets |
| Jump'n'Run, Aufbauspiel mit scrollender Karte, Partikel | **nicht Qt** — Godot, oder pygame für Kleines |

Qt Widgets — das, was `labcontrol/` hier benutzt — zeichnet Formulare und
Tabellen, und es sieht aus wie ein Programm des Betriebssystems. Das ist für
einen Manager ein Vorteil: so sehen die Vorbilder aus. Für ein Spiel mit
eigenem Aussehen kämpft man dagegen an; dann ist QML der richtige Teil von Qt.

Und eine Ehrlichkeit zum Schluss: 48 448 Spiele/s gelten für eine schlichte
Chancenrechnung. Wird die Spielsimulation ausführlich — Positionen, 90 × 22
Entscheidungen —, kostet sie in reinem Python irgendwann Sekunden. Das ist dann
kein Grund für ein anderes Werkzeug, sondern für einen Hintergrundfaden; die
Naht dafür steht in
[`labcontrol_qt/arbeit.py`](../labcontrol_qt/arbeit.py) schon.
