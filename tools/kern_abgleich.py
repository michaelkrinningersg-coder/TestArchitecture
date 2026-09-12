"""Wacht darüber, dass die übernommene Fachschicht unverändert bleibt.

    python tools/kern_abgleich.py                      # gegen die Prüfsummen
    python tools/kern_abgleich.py --pfad /w/testlims   # gegen das Ursprungs-Repo
    python tools/kern_abgleich.py --pfad /w/testlims --uebernehmen

Ohne diese Prüfung wäre „unverändert übernommen" eine Behauptung. Eine
Änderung hier gehört ins Ursprungs-Repo — sonst laufen die Tkinter- und die
Qt6-Oberfläche fachlich auseinander, und genau das soll eine Portierung
nicht.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KERN = ROOT / "labcontrol_qt" / "kern"
LISTE = KERN / "PRUEFSUMMEN.txt"

MODULE = ("config", "dateien", "laufkontext", "lims_db", "protokoll",
          "sitzung", "verschleppung")


def summe(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def schreiben() -> int:
    zeilen = [f"{summe(KERN / f'{name}.py')}  {name}.py" for name in MODULE]
    LISTE.write_text("\n".join(zeilen) + "\n", encoding="ascii")
    print(f"{LISTE.relative_to(ROOT)}: {len(zeilen)} Prüfsummen geschrieben.")
    return 0


def pruefen() -> int:
    if not LISTE.exists():
        print(f"{LISTE} fehlt — mit --uebernehmen anlegen.")
        return 1
    erwartet = {}
    for zeile in LISTE.read_text(encoding="ascii").splitlines():
        if zeile.strip():
            wert, name = zeile.split(maxsplit=1)
            erwartet[name.strip()] = wert
    abweichungen = []
    for name in MODULE:
        datei = KERN / f"{name}.py"
        if not datei.exists():
            abweichungen.append(f"{name}.py fehlt")
        elif summe(datei) != erwartet.get(f"{name}.py"):
            abweichungen.append(f"{name}.py weicht ab")
    if abweichungen:
        print("Die Fachschicht ist nicht mehr die übernommene:")
        for text in abweichungen:
            print(f"  - {text}")
        print("Änderungen gehören ins Ursprungs-Repo und werden von dort "
              "erneut übernommen.")
        return 1
    print(f"{len(MODULE)} Module unverändert.")
    return 0


def uebernehmen(pfad: Path) -> int:
    if not pfad.is_dir():
        print(f"{pfad} ist kein Verzeichnis.")
        return 1
    for name in MODULE:
        quelle = pfad / f"{name}.py"
        if not quelle.exists():
            print(f"{quelle} fehlt.")
            return 1
        shutil.copy2(quelle, KERN / f"{name}.py")
    print(f"{len(MODULE)} Module aus {pfad} übernommen.")
    return schreiben()


def vergleichen(pfad: Path) -> int:
    unterschiede = [name for name in MODULE
                    if not (pfad / f"{name}.py").exists()
                    or summe(pfad / f"{name}.py") != summe(KERN / f"{name}.py")]
    if unterschiede:
        print(f"Abweichung gegenüber {pfad}: {', '.join(unterschiede)}")
        print("Mit --uebernehmen nachziehen.")
        return 1
    print(f"{len(MODULE)} Module stimmen mit {pfad} überein.")
    return 0


def main(argv: list[str] | None = None) -> int:
    zerleger = argparse.ArgumentParser(description="Fachschicht abgleichen")
    zerleger.add_argument("--pfad", type=Path,
                          help="Ursprungs-Repo, gegen das verglichen wird")
    zerleger.add_argument("--uebernehmen", action="store_true",
                          help="Module von dort neu übernehmen")
    argumente = zerleger.parse_args(argv)

    if argumente.uebernehmen:
        if argumente.pfad is None:
            print("--uebernehmen braucht --pfad.")
            return 1
        return uebernehmen(argumente.pfad)
    if argumente.pfad is not None:
        return vergleichen(argumente.pfad)
    return pruefen()


if __name__ == "__main__":
    raise SystemExit(main())
