# Herkunft dieser Module

Wörtlich übernommen aus **michaelkrinningersg-coder/testlims**,
Commit `f163391a1c9a86098742d19ee488611cab6dbc9c` vom 2026-09-12.

| Datei | Zeilen | Rolle |
|---|---|---|
| `lims_db.py` | 4810 | Datenbankzugriff und Fachlogik |
| `laufkontext.py` | 3368 | Momentaufnahme der LIMS-Stammdaten für einen Lauf |
| `config.py` | 449 | Pfade und zuletzt benutzte Eingaben |
| `sitzung.py` | 406 | Sitzungsstände |
| `protokoll.py` | 376 | Protokollbuch der Änderungen |
| `verschleppung.py` | 225 | Verschleppungsrechnung |
| `dateien.py` | 188 | Messdateien einlesen |

Zusammen 9 822 Zeilen. **Hier wird nichts geändert.** Die Qt6-Oberfläche
sitzt auf derselben Fachschicht wie das Tkinter-Original; jede Änderung
gehört ins Ursprungs-Repo und wird von dort erneut übernommen. Sonst laufen
die beiden Oberflächen fachlich auseinander, und genau das soll eine
Portierung nicht.

Aktualisieren:

```bash
python tools/kern_abgleich.py --pfad /pfad/zu/testlims --uebernehmen
```

## Prüfsummen

`PRUEFSUMMEN.txt` hält den Stand der Übernahme fest. `tools/kern_abgleich.py`
prüft ohne Argumente dagegen, mit `--pfad` gegen ein danebenliegendes
Ursprungs-Repo. Der Windows-Workflow prüft vor jedem Bau.
