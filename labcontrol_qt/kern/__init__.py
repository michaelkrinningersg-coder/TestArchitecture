"""Unveränderte Fachschicht aus dem Ursprungs-Repo — siehe HERKUNFT.md.

Die Module dort importieren einander unter ihrem blanken Namen
(``import lims_db``), so wie sie im flachen Ursprungs-Repo nebeneinander
liegen. Damit sie hier unverändert bleiben können, legt dieses Paket sein
eigenes Verzeichnis auf den Suchpfad und holt sie dann selbst unter genau
diesen Namen herein.

Wichtig ist die Reihenfolge: erst der Suchpfad, dann der Import. Sonst gäbe
es jedes Modul zweimal — einmal als ``lims_db`` und einmal als
``labcontrol_qt.kern.lims_db`` —, mit je eigenem Zustand. Der Merker für den
nachgeladenen Oracle-Client wäre dann in der einen Fassung gesetzt und in der
anderen nicht.
"""

import sys
from pathlib import Path

_HIER = Path(__file__).resolve().parent
if str(_HIER) not in sys.path:
    sys.path.insert(0, str(_HIER))

import config          # noqa: E402,F401
import dateien         # noqa: E402,F401
import laufkontext     # noqa: E402,F401
import lims_db         # noqa: E402,F401
import protokoll       # noqa: E402,F401
import sitzung         # noqa: E402,F401
import verschleppung   # noqa: E402,F401

__all__ = ["config", "dateien", "laufkontext", "lims_db", "protokoll",
           "sitzung", "verschleppung"]
