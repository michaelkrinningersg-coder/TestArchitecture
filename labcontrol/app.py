"""Start der Anwendung, Ablageort der Datenbank und der Selbsttest für die CI."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from labcontrol import APP_NAME, ORGANISATION, __version__
from labcontrol.storage import Storage

ICON_FILE = Path(__file__).with_name("resources") / "labcontrol.png"


def database_path() -> Path:
    """Wo die Datenbank liegt — unter Windows im Profil des Benutzers."""
    location = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    base = Path(location) if location else Path.home() / f".{APP_NAME.lower()}"
    return base / "labcontrol.sqlite3"


def application_icon() -> QIcon:
    return QIcon(str(ICON_FILE)) if ICON_FILE.exists() else QIcon()


def build_application(argv: list[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    application = QApplication(argv if argv is not None else sys.argv[:1])
    application.setApplicationName(APP_NAME)
    application.setApplicationDisplayName(APP_NAME)
    application.setOrganizationName(ORGANISATION)
    application.setApplicationVersion(__version__)
    application.setWindowIcon(application_icon())
    return application


def open_storage(path: Path | str, rows: int) -> Storage:
    """Öffnet die Ablage und befüllt sie beim ersten Start."""
    storage = Storage(path)
    if storage.is_empty() and rows > 0:
        storage.seed(rows)
    return storage


def selftest(rows: int = 200) -> int:
    """Fährt die Anwendung einmal komplett hoch und wieder herunter.

    Der Windows-Build ruft das mit der fertigen EXE auf: startet sie nicht,
    schlägt der Workflow fehl, statt eine kaputte Datei zu veröffentlichen.
    """
    from labcontrol.audit_view import AuditDialog
    from labcontrol.dialogs import DecisionDialog, SampleDialog
    from labcontrol.domain import ReleaseState
    from labcontrol.export import write_csv
    from labcontrol.main_window import MainWindow

    application = build_application()
    with tempfile.TemporaryDirectory() as folder:
        storage = open_storage(Path(folder) / "selftest.sqlite3", rows)
        window = MainWindow(storage, persist_state=False)
        window.resize(1280, 720)
        window.show()
        application.processEvents()

        checks: list[tuple[str, bool]] = [
            ("Datenbank befüllt", storage.count() == rows),
            ("Tabelle gefüllt", window.model.rowCount() == rows),
        ]

        window.search.setText("Cadmium")
        application.processEvents()
        checks.append(("Filter greift", 0 < window.model.rowCount() < rows))
        window.reset_filters()
        application.processEvents()

        window.select_sample(storage.query()[0].sample_id)
        record = window.selected_record()
        checks.append(("Auswahl möglich", record is not None))

        decided = storage.decide(record, ReleaseState.BLOCKED,
                                 "Selbsttest", actor="selftest")
        checks.append(("Entscheidung gespeichert",
                       decided.state is ReleaseState.BLOCKED))
        checks.append(("Prüfpfad geschrieben",
                       any(entry.actor == "selftest"
                           for entry in storage.audit(record.sample_id))))

        target = Path(folder) / "export.csv"
        checks.append(("CSV geschrieben",
                       write_csv(target, storage.query()) == rows
                       and target.stat().st_size > 0))

        SampleDialog(window, suggestions={}, next_id="P-TEST-000001")
        DecisionDialog(window, record=record)
        AuditDialog(storage, window)
        application.processEvents()
        checks.append(("Dialoge baubar", True))

        window.close()
        storage.close()

    lines = [f"  [{'ok' if passed else 'FEHLER'}] {label}" for label, passed in checks]
    failed = [label for label, passed in checks if not passed]
    lines.append(f"Selbsttest fehlgeschlagen: {', '.join(failed)}" if failed
                 else f"Selbsttest bestanden — {APP_NAME} {__version__}")
    _emit(lines)
    return 1 if failed else 0


def _emit(lines: list[str]) -> None:
    """Gibt den Bericht aus — und auf Wunsch zusätzlich in eine Datei.

    Eine Fensteranwendung hat unter Windows kein stdout: ohne die Datei sähe
    der Workflow nur den Rückgabewert, nicht, was geprüft wurde.
    """
    report = "\n".join(lines)
    print(report)
    target = os.environ.get("LABCONTROL_SELFTEST_LOG")
    if target:
        Path(target).write_text(report + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="labcontrol", description=f"{APP_NAME} — Probenfreigabe mit Prüfpfad")
    parser.add_argument("--version", action="version",
                        version=f"{APP_NAME} {__version__}")
    parser.add_argument("--database", help="Pfad zur Datenbankdatei")
    parser.add_argument("--rows", type=int, default=2_000,
                        help="Proben für die Erstbefüllung (0 = leer starten)")
    parser.add_argument("--selftest", action="store_true",
                        help="Anwendung einmal hochfahren und prüfen, dann beenden")
    parser.add_argument("--screenshot", help="Fenster als PNG sichern und beenden")
    parser.add_argument("--size", help="Fenstergröße erzwingen, etwa 1440x820")
    arguments = parser.parse_args(argv)

    if arguments.selftest:
        return selftest()

    application = build_application()
    storage = open_storage(arguments.database or database_path(), arguments.rows)

    from labcontrol.main_window import MainWindow

    window = MainWindow(storage, persist_state=not arguments.screenshot)
    if arguments.size:
        width, _, height = arguments.size.partition("x")
        window.resize(int(width), int(height))
    window.show()

    if arguments.screenshot:
        application.processEvents()
        window.grab().save(arguments.screenshot)
        print(f"{arguments.screenshot} · {window.model.rowCount()} Zeilen")
        return 0
    return application.exec()
