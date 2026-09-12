"""Hält die Versionsnummern in Modul und Windows-Ressource beisammen.

    python packaging/check_version.py

Läuft im Workflow vor dem Bauen: sonst steht in den Dateieigenschaften der EXE
irgendwann eine andere Version als in der Anwendung.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from labcontrol import __version__  # noqa: E402


def main() -> int:
    resource = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    expected = tuple(int(part) for part in __version__.split(".")) + (0,)
    problems = []

    for field in ("filevers", "prodvers"):
        found = re.search(rf"{field}=\((\d+), (\d+), (\d+), (\d+)\)", resource)
        if not found or tuple(int(g) for g in found.groups()) != expected:
            problems.append(f"{field} ist {found.groups() if found else 'nicht gesetzt'},"
                            f" erwartet {expected}")

    for field in ("FileVersion", "ProductVersion"):
        found = re.search(rf"StringStruct\('{field}', '([^']+)'\)", resource)
        wanted = ".".join(str(part) for part in expected)
        if not found or found.group(1) != wanted:
            problems.append(f"{field} ist {found.group(1) if found else 'leer'},"
                            f" erwartet {wanted}")

    if problems:
        print(f"Version {__version__} passt nicht zu packaging/version_info.txt:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"Version {__version__} stimmt in Modul und Windows-Ressource überein.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
