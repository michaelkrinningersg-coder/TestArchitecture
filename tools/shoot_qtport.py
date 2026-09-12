"""Bildschirmfotos der Qt6-Portierung.

    xvfb-run -a -s "-screen 0 1600x1000x24" python tools/shoot_qtport.py

Läuft gegen die Demoquelle: keine Datenbank, kein VPN, kein Netzlaufwerk.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "screenshots"
GROESSE = (1440, 860)


def main() -> int:
    from labcontrol_qt.app import Anwendung, anwendung_bauen
    from labcontrol_qt.arbeit import abwarten
    from labcontrol_qt.hauptfenster import Hauptfenster
    from labcontrol_qt.kern import config
    from labcontrol_qt.messfenster import Messfenster
    from labcontrol_qt.quelle import DemoQuelle

    OUT.mkdir(parents=True, exist_ok=True)
    programm = anwendung_bauen()

    def ruhen(runden: int = 6) -> None:
        for _ in range(runden):
            programm.processEvents()
            abwarten(1_500)
            programm.processEvents()

    def schuss(widget, name: str, groesse=None) -> None:
        if groesse:
            widget.resize(*groesse)
        ruhen(2)
        ziel = OUT / f"port_{name}.png"
        widget.grab().save(str(ziel))
        print(f"  {ziel.relative_to(ROOT)} · {widget.width()}×{widget.height()}")

    with tempfile.TemporaryDirectory() as ordner:
        konfig = config.Config(runtime_dir=ordner)
        konfig.set("benutzer", "LABOR")
        quelle = DemoQuelle(Path(ordner) / "stationen")

        # 1 — Anmeldung
        anwendung = Anwendung(konfig)
        anwendung.resize(*GROESSE)
        anwendung.show()
        schuss(anwendung, "anmeldung")

        # 2 — Bearbeiten, Auswahl über die Übersicht
        fenster = Hauptfenster(quelle, konfig)
        fenster.resize(*GROESSE)
        fenster.show()
        ruhen()
        schuss(fenster, "bearbeiten_leer")

        reiter = fenster.bearbeiten
        reiter._uebersicht_gewaehlt(0)
        ruhen()
        auswahl = reiter.auswahl()
        quelle.messdateien_anlegen(auswahl["geraet"], [auswahl["serie"]])
        reiter._dateien_laden()
        ruhen()
        schuss(fenster, "bearbeiten")

        # 3 — Messfenster
        pfad = os.path.join(reiter.dateien_ordner, reiter.dateien_gezeigt[0])
        mess = Messfenster(pfad, auswahl, quelle, fenster)
        mess.resize(1380, 820)
        mess.show()
        ruhen()
        schuss(mess, "laufdatei")

        mess.reiter.setCurrentIndex(1)
        ruhen()
        schuss(mess, "laufkontext")

        mess.reiter.setCurrentIndex(2)
        ruhen(2)
        schuss(mess, "offen")

        mess.close()
        fenster.close()
        anwendung.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
