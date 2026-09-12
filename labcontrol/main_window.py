"""Das Hauptfenster: Filterleiste, Probenliste, Detailspalte, Prüfpfad."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QTableView,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.model import AMPEL_BG, AMPEL_FG, Status, status_of
from labcontrol import APP_NAME, ORGANISATION, __version__
from labcontrol.audit_view import AuditDialog
from labcontrol.dialogs import DecisionDialog, SampleDialog, show_about
from labcontrol.domain import (
    STATE_BG,
    STATE_FG,
    ReleaseState,
    SampleRecord,
    ampel_reason,
)
from labcontrol.export import write_csv
from labcontrol.storage import Storage
from labcontrol.table_model import (
    AMPEL_COLUMN,
    COLUMNS,
    STATE_COLUMN,
    PillDelegate,
    SampleTableModel,
)

AMPEL_CHOICES = (("Alle Bewertungen", None), ("frei", Status.OK),
                 ("prüfen", Status.WARN), ("gesperrt", Status.FAIL))
STATE_CHOICES = (("Alle Freigaben", None), ("offen", ReleaseState.OPEN),
                 ("freigegeben", ReleaseState.RELEASED),
                 ("gesperrt", ReleaseState.BLOCKED))


class MainWindow(QMainWindow):
    """Alles, was die Anwendung kann, hängt an diesem Fenster."""

    def __init__(self, storage: Storage, persist_state: bool = True) -> None:
        super().__init__()
        self.storage = storage
        self.persist_state = persist_state
        self.sort_key = "sample_id"
        self.descending = False

        self.setWindowTitle(f"{APP_NAME} {__version__} — Probenfreigabe")
        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        self._restore_settings()
        self.reload()

    # -- Aufbau -------------------------------------------------------------
    def _icon(self, pixmap: QStyle.StandardPixmap):
        return self.style().standardIcon(pixmap)

    def _build_actions(self) -> None:
        self.action_new = QAction(self._icon(QStyle.SP_FileIcon), "&Neue Probe …", self)
        self.action_new.setShortcut(QKeySequence.New)
        self.action_new.triggered.connect(self.new_sample)

        self.action_edit = QAction(self._icon(QStyle.SP_FileDialogDetailedView),
                                   "Probe &bearbeiten …", self)
        self.action_edit.setShortcut(QKeySequence("Ctrl+E"))
        self.action_edit.triggered.connect(self.edit_sample)

        self.action_decide = QAction(self._icon(QStyle.SP_DialogApplyButton),
                                     "&Freigabe entscheiden …", self)
        self.action_decide.setShortcut(QKeySequence("Ctrl+R"))
        self.action_decide.triggered.connect(self.decide_sample)

        self.action_audit = QAction(self._icon(QStyle.SP_FileDialogContentsView),
                                    "&Prüfpfad …", self)
        self.action_audit.setShortcut(QKeySequence("Ctrl+L"))
        self.action_audit.triggered.connect(self.show_audit)

        self.action_export = QAction(self._icon(QStyle.SP_DialogSaveButton),
                                     "Ansicht als &CSV …", self)
        self.action_export.setShortcut(QKeySequence.Save)
        self.action_export.triggered.connect(self.export_csv)

        self.action_reload = QAction(self._icon(QStyle.SP_BrowserReload),
                                     "&Aktualisieren", self)
        self.action_reload.setShortcut(QKeySequence.Refresh)
        self.action_reload.triggered.connect(self.reload)

        self.action_quit = QAction("&Beenden", self)
        self.action_quit.setShortcut(QKeySequence.Quit)
        self.action_quit.triggered.connect(self.close)

        self.action_about = QAction("Ü&ber LabControl", self)
        self.action_about.triggered.connect(
            lambda: show_about(self, self.storage.path))

    def _build_menu(self) -> None:
        bar = self.menuBar()
        datei = bar.addMenu("&Datei")
        datei.addAction(self.action_new)
        datei.addAction(self.action_edit)
        datei.addSeparator()
        datei.addAction(self.action_export)
        datei.addSeparator()
        datei.addAction(self.action_quit)

        freigabe = bar.addMenu("&Freigabe")
        freigabe.addAction(self.action_decide)
        freigabe.addSeparator()
        freigabe.addAction(self.action_audit)

        ansicht = bar.addMenu("&Ansicht")
        ansicht.addAction(self.action_reload)

        hilfe = bar.addMenu("&Hilfe")
        hilfe.addAction(self.action_about)

    def _build_toolbar(self) -> None:
        bar = self.addToolBar("Werkzeuge")
        bar.setObjectName("werkzeugleiste")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        bar.addAction(self.action_new)
        bar.addAction(self.action_edit)
        bar.addSeparator()
        bar.addAction(self.action_decide)
        bar.addAction(self.action_audit)
        bar.addSeparator()
        bar.addAction(self.action_export)
        bar.addAction(self.action_reload)

    def _build_central(self) -> None:
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suche Probe-ID, Analyt, Matrix, Prüfer …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.reload)

        self.ampel_filter = QComboBox()
        self.ampel_filter.addItems([label for label, _ in AMPEL_CHOICES])
        self.ampel_filter.currentIndexChanged.connect(self.reload)

        self.state_filter = QComboBox()
        self.state_filter.addItems([label for label, _ in STATE_CHOICES])
        self.state_filter.currentIndexChanged.connect(self.reload)

        reset = QPushButton("Filter zurücksetzen")
        reset.clicked.connect(self.reset_filters)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.addWidget(QLabel("Filter:"))
        filters.addWidget(self.search, 1)
        filters.addWidget(self.ampel_filter)
        filters.addWidget(self.state_filter)
        filters.addWidget(reset)

        self.model = SampleTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(False)
        self.table.doubleClicked.connect(self.edit_sample)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        pill = PillDelegate(self.table)
        self.table.setItemDelegateForColumn(AMPEL_COLUMN, pill)
        self.table.setItemDelegateForColumn(STATE_COLUMN, pill)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(True)
        header.sectionClicked.connect(self._on_header_clicked)
        for section, column in enumerate(COLUMNS):
            self.table.setColumnWidth(section, column.width)

        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setMinimumWidth(300)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("aufteilung")
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(8)
        layout.addLayout(filters)
        layout.addWidget(self.splitter, 1)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.table.selectionModel().selectionChanged.connect(self._on_selection)

    def _build_statusbar(self) -> None:
        self.counts = QLabel()
        self.statusBar().addPermanentWidget(self.counts)
        self.statusBar().showMessage("Bereit.")

    # -- Daten --------------------------------------------------------------
    def reload(self) -> None:
        """Holt die Liste neu und hält die Auswahl, wenn es geht."""
        selected = self.selected_record()
        records = self.storage.query(
            text=self.search.text(),
            state=STATE_CHOICES[self.state_filter.currentIndex()][1],
            ampel=AMPEL_CHOICES[self.ampel_filter.currentIndex()][1],
            sort_key=self.sort_key,
            descending=self.descending,
        )
        self.model.set_records(records)
        if selected is not None:
            self.select_sample(selected.sample_id)
        if self.selected_record() is None and records:
            # Mit ausgewählter Zeile starten: die Detailspalte zeigt dann sofort,
            # wozu sie da ist, statt auf den ersten Klick zu warten.
            self.table.selectRow(0)
        self._update_counts(records)
        self._on_selection()

    def _update_counts(self, records: list[SampleRecord]) -> None:
        summary = self.storage.summary(records)
        total = self.storage.count()
        self.counts.setText(
            f"{len(records)} von {total} Proben   ·   "
            f"frei {summary['OK']}  prüfen {summary['WARN']}  "
            f"gesperrt {summary['FAIL']}   ·   "
            f"offen {summary['OPEN']}  freigegeben {summary['RELEASED']}  "
            f"gesperrt {summary['BLOCKED']}")

    def selected_record(self) -> SampleRecord | None:
        rows = self.table.selectionModel().selectedRows() \
            if self.table.selectionModel() else []
        return self.model.record_at(rows[0].row()) if rows else None

    def select_sample(self, sample_id: str) -> None:
        row = self.model.row_of(sample_id)
        if row >= 0:
            self.table.selectRow(row)
            self.table.scrollTo(self.model.index(row, 0),
                                QAbstractItemView.PositionAtCenter)

    def _on_header_clicked(self, section: int) -> None:
        key = self.model.sort_key(section)
        self.descending = not self.descending if key == self.sort_key else False
        self.sort_key = key
        self.table.horizontalHeader().setSortIndicator(
            section, Qt.DescendingOrder if self.descending else Qt.AscendingOrder)
        self.reload()

    def _on_selection(self, *_args: object) -> None:
        record = self.selected_record()
        has = record is not None
        self.action_edit.setEnabled(has)
        self.action_decide.setEnabled(has)
        self.details.setHtml(self._detail_html(record))

    def _detail_html(self, record: SampleRecord | None) -> str:
        if record is None:
            return ("<p style='color:#6a6a6a'>Keine Probe ausgewählt.<br><br>"
                    "Eine Zeile anklicken zeigt hier Messwerte, Bewertung und "
                    "den Prüfpfad dieser Probe.</p>")
        today = date.today()
        status = status_of(record, today)
        rows = [
            ("Matrix", record.matrix),
            ("Analyt", record.analyte),
            ("Messwert", f"{record.value:g} {record.unit}"),
            ("Sollwert", f"{record.target:g} ± {record.tolerance:g}"),
            ("Abweichung", f"{record.deviation_ratio * 100:.0f} % der Toleranz"),
            ("Bewertung", ampel_reason(record, today)),
            ("Eingang", record.received.strftime("%d.%m.%Y")),
            ("Fällig", record.due.strftime("%d.%m.%Y")),
            ("Prüfer", record.analyst),
        ]
        table = "".join(
            f"<tr><td style='color:#6a6a6a;padding-right:10px'>{label}</td>"
            f"<td>{value}</td></tr>" for label, value in rows)

        decision = ""
        if record.decided:
            stamp = record.decided_at.strftime("%d.%m.%Y %H:%M") \
                if record.decided_at else ""
            decision = (
                f"<p style='margin-top:12px'><b>{record.state.label}</b> von "
                f"{record.decided_by}<br><span style='color:#6a6a6a'>{stamp} UTC"
                f"</span><br>{record.decision_note}</p>")

        entries = self.storage.audit(record.sample_id, limit=8)
        journal = "".join(
            f"<tr><td style='color:#6a6a6a;padding-right:10px;white-space:nowrap'>"
            f"{entry.at.strftime('%d.%m. %H:%M')}</td><td>{entry.action}"
            f"<span style='color:#6a6a6a'> · {entry.actor}</span></td></tr>"
            for entry in entries)

        # Qt-Rich-Text kennt kein inline-block: die Plaketten stehen deshalb in
        # einer Tabellenzeile, deren Zellen den Hintergrund tragen.
        badges = (
            f"<table cellspacing='0' cellpadding='5'><tr>"
            f"<td bgcolor='{AMPEL_BG[status]}'><b>"
            f"<font color='{AMPEL_FG[status]}'>Bewertung: {status.label}</font>"
            f"</b></td><td width='6'></td>"
            f"<td bgcolor='{STATE_BG[record.state]}'>"
            f"<font color='{STATE_FG[record.state]}'>{record.state.label}</font>"
            f"</td></tr></table>")
        return f"""
        <div style="font-family:sans-serif">
          <h3 style="margin:0 0 2px 0">{record.sample_id}</h3>
          {badges}
          <table style="margin-top:12px">{table}</table>
          {decision}
          <h4 style="margin:16px 0 4px 0">Prüfpfad</h4>
          <table>{journal or "<tr><td style='color:#6a6a6a'>noch nichts</td></tr>"}</table>
        </div>"""

    # -- Aktionen -----------------------------------------------------------
    def reset_filters(self) -> None:
        self.search.clear()
        self.ampel_filter.setCurrentIndex(0)
        self.state_filter.setCurrentIndex(0)

    def _suggestions(self) -> dict[str, list[str]]:
        return {column: self.storage.distinct(column)
                for column in ("matrix", "analyte", "analyst", "unit")}

    def _next_sample_id(self) -> str:
        return f"P-{date.today().year}-{self.storage.count() + 1:06d}"

    def new_sample(self) -> None:
        dialog = SampleDialog(self, suggestions=self._suggestions(),
                              next_id=self._next_sample_id())
        if dialog.exec() != SampleDialog.Accepted:
            return
        try:
            record = self.storage.create(dialog.record())
        except ValueError as error:
            QMessageBox.warning(self, "Nicht gespeichert", str(error))
            return
        self.reload()
        self.select_sample(record.sample_id)
        self.statusBar().showMessage(f"{record.sample_id} angelegt.", 6000)

    def edit_sample(self) -> None:
        record = self.selected_record()
        if record is None:
            return
        dialog = SampleDialog(self, record=record, suggestions=self._suggestions())
        if dialog.exec() != SampleDialog.Accepted:
            return
        updated = self.storage.update(record, dialog.record())
        self.reload()
        self.select_sample(updated.sample_id)
        self.statusBar().showMessage(f"{updated.sample_id} geändert.", 6000)

    def decide_sample(self) -> None:
        record = self.selected_record()
        if record is None:
            return
        dialog = DecisionDialog(self, record=record)
        if dialog.exec() != DecisionDialog.Accepted:
            return
        state, note = dialog.decision()
        try:
            decided = self.storage.decide(record, state, note)
        except ValueError as error:
            QMessageBox.warning(self, "Nicht gespeichert", str(error))
            return
        self.reload()
        self.select_sample(decided.sample_id)
        self.statusBar().showMessage(
            f"{decided.sample_id} {decided.state.label} von {decided.decided_by}.",
            8000)

    def show_audit(self) -> None:
        record = self.selected_record()
        AuditDialog(self.storage, self,
                    sample_id=record.sample_id if record else "").exec()

    def export_csv(self) -> None:
        suggestion = str(Path.home() / f"labcontrol-{date.today().isoformat()}.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Ansicht als CSV speichern", suggestion, "CSV-Datei (*.csv)")
        if not path:
            return
        records = self.storage.query(
            text=self.search.text(),
            state=STATE_CHOICES[self.state_filter.currentIndex()][1],
            ampel=AMPEL_CHOICES[self.ampel_filter.currentIndex()][1],
            sort_key=self.sort_key, descending=self.descending)
        try:
            written = write_csv(path, records)
        except OSError as error:
            QMessageBox.warning(self, "Nicht geschrieben", str(error))
            return
        self.statusBar().showMessage(f"{written} Zeilen nach {path} geschrieben.",
                                     8000)

    # -- Fensterzustand -----------------------------------------------------
    def _settings(self) -> QSettings:
        return QSettings(ORGANISATION, APP_NAME)

    def _restore_settings(self) -> None:
        """Holt Fenstergröße und Aufteilung zurück.

        Der Selbsttest läuft mit ``persist_state=False``: er darf dem Benutzer
        keine Fenstergeometrie hinterlassen, schon gar keine, die auf einem
        Bildschirm ohne Ausgabe entstanden ist.
        """
        if not self.persist_state:
            self.resize(1440, 820)
            QTimer.singleShot(0, lambda: self.splitter.setSizes([1090, 330]))
            return
        settings = self._settings()
        geometry = settings.value("fenster/geometrie")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1440, 820)
        state = settings.value("fenster/zustand")
        if state is not None:
            self.restoreState(state)
        sizes = settings.value("fenster/aufteilung")
        if sizes:
            self.splitter.setSizes([int(size) for size in sizes])
        else:
            QTimer.singleShot(0, lambda: self.splitter.setSizes([1090, 330]))

    def closeEvent(self, event) -> None:
        if not self.persist_state:
            super().closeEvent(event)
            return
        settings = self._settings()
        settings.setValue("fenster/geometrie", self.saveGeometry())
        settings.setValue("fenster/zustand", self.saveState())
        settings.setValue("fenster/aufteilung", self.splitter.sizes())
        super().closeEvent(event)
