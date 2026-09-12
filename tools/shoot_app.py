"""Nimmt die Bildschirmfotos der Qt6-Anwendung auf.

    xvfb-run -a -s "-screen 0 1500x900x24" python tools/shoot_app.py

Fenster und Dialoge werden aufgebaut, gezeigt und abfotografiert — ohne
Benutzer, aber mit echtem Zeichnen auf einem X-Server.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "screenshots"
SIZE = (1440, 820)


def main() -> int:
    from core.model import Status
    from labcontrol.app import build_application, open_storage
    from labcontrol.audit_view import AuditDialog
    from labcontrol.dialogs import DecisionDialog, SampleDialog
    from labcontrol.domain import ReleaseState
    from labcontrol.main_window import MainWindow

    OUT.mkdir(parents=True, exist_ok=True)
    application = build_application()

    with tempfile.TemporaryDirectory() as folder:
        storage = open_storage(Path(folder) / "shots.sqlite3", 2_000)
        window = MainWindow(storage, persist_state=False)
        window.resize(*SIZE)
        window.show()
        application.processEvents()

        def shoot(widget, name: str, size: tuple[int, int] | None = None) -> None:
            if size is not None:
                widget.resize(*size)
            application.processEvents()
            application.processEvents()
            target = OUT / f"app_{name}.png"
            widget.grab().save(str(target))
            print(f"  {target.relative_to(ROOT)} · {widget.width()}×{widget.height()}")

        shoot(window, "uebersicht")

        window.search.setText("Cadmium")
        window.ampel_filter.setCurrentIndex(3)      # gesperrt
        application.processEvents()
        shoot(window, "gefiltert")
        window.reset_filters()
        application.processEvents()

        # Eine Probe außerhalb der Toleranz für den Entscheidungsdialog
        blocked = next(record for record in storage.query()
                       if record.ampel() is Status.FAIL
                       and record.state is ReleaseState.OPEN)
        window.select_sample(blocked.sample_id)
        application.processEvents()
        shoot(window, "auswahl")

        decision = DecisionDialog(window, record=blocked)
        decision.reason.setPlainText(
            "Wiederholungsmessung bestätigt die Überschreitung. Charge gesperrt, "
            "Kunde ist informiert, Ursachenanalyse läuft.")
        decision.block.setChecked(True)
        decision.show()
        shoot(decision, "entscheidung", (600, 430))
        decision.close()

        storage.decide(blocked, ReleaseState.BLOCKED,
                       "Wiederholungsmessung bestätigt die Überschreitung.",
                       actor="m.krinninger")
        audit = AuditDialog(storage, window)
        audit.resize(1080, 520)
        audit.show()
        shoot(audit, "pruefpfad", (1080, 520))
        audit.close()

        editor = SampleDialog(window, record=blocked,
                              suggestions={column: storage.distinct(column)
                                           for column in ("matrix", "analyte",
                                                          "analyst", "unit")})
        editor.show()
        shoot(editor, "bearbeiten", (460, 470))
        editor.close()

        window.close()
        storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
