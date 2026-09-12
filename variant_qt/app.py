"""LabControl — Qt variant: QTableView over a QAbstractTableModel.

Run it:  python -m variant_qt.app --rows 20000
Headless: QT_QPA_PLATFORM=offscreen python -m variant_qt.app --screenshot out.png
"""

from __future__ import annotations

import argparse
import os
import sys
import time

if __package__ in (None, ""):  # allow `python variant_qt/app.py` as well
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.data import TODAY, generate
from core.model import AMPEL_BG, AMPEL_FG, Sample, Status, status_of
from core.query import COLUMNS, STATUS_TITLE, STATUS_WIDTH, apply_query, summarise

STATUS_CHOICES = (("Alle Status", None), ("frei", Status.OK),
                  ("prüfen", Status.WARN), ("gesperrt", Status.FAIL))


class SampleTableModel(QAbstractTableModel):
    """Adapts a list of samples to the table view.

    The view asks for the cells it paints and nothing else, which is why the
    cost of a filter change does not grow with the number of rows.
    """

    def __init__(self, rows: list[Sample] | None = None) -> None:
        super().__init__()
        self._rows: list[Sample] = rows or []

    # -- population ---------------------------------------------------------
    def set_rows(self, rows: list[Sample]) -> None:
        """Replace the whole result set.

        One reset instead of N insertions, and deliberately no per-row work:
        the Ampel state is evaluated in ``data()`` for the cells the view
        actually asks about, which keeps this call independent of ``len(rows)``.
        """
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    # -- read-only model interface -----------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS) + 1

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        sample = self._rows[index.row()]
        status = status_of(sample, TODAY)
        is_status_column = index.column() == len(COLUMNS)

        if role == Qt.DisplayRole:
            if is_status_column:
                return status.label
            return COLUMNS[index.column()].render(sample)
        if role == Qt.BackgroundRole:
            return QColor(AMPEL_BG[status])
        if role == Qt.ForegroundRole:
            return QColor(AMPEL_FG[status])
        if role == Qt.TextAlignmentRole:
            if is_status_column:
                return int(Qt.AlignCenter)
            if COLUMNS[index.column()].numeric:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.FontRole and is_status_column:
            font = QFont()
            font.setBold(status is not Status.OK)
            return font
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return STATUS_TITLE if section == len(COLUMNS) else COLUMNS[section].title

    def sort_key_for_column(self, section: int) -> str:
        return "status" if section == len(COLUMNS) else COLUMNS[section].key


class MainWindow(QMainWindow):
    """Search field, status filter, table, footer — the whole variant."""

    def __init__(self, rows: list[Sample]) -> None:
        super().__init__()
        self.setWindowTitle("LabControl — Qt (QTableView)")
        self._all_rows = rows
        self._sort_key = "sample_id"
        self._descending = False
        self.last_rebuild_ms = 0.0

        self.search = QLineEdit(placeholderText="Suche Probe-ID, Analyt, Matrix, Prüfer …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.rebuild)

        self.status_box = QComboBox()
        for label, _ in STATUS_CHOICES:
            self.status_box.addItem(label)
        self.status_box.currentIndexChanged.connect(self.rebuild)

        self.model = SampleTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(True)
        header.sectionClicked.connect(self._on_header_clicked)
        for section, column in enumerate(COLUMNS):
            self.table.setColumnWidth(section, column.width)
        self.table.setColumnWidth(len(COLUMNS), STATUS_WIDTH)

        self.footer = QLabel()
        self.footer.setContentsMargins(4, 2, 4, 2)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Filter:"))
        controls.addWidget(self.search, 1)
        controls.addWidget(self.status_box)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 4)
        layout.addLayout(controls)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.footer)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.rebuild()

    def rebuild(self) -> None:
        """Re-apply filter and sort, then hand the result to the model."""
        started = time.perf_counter()
        status = STATUS_CHOICES[self.status_box.currentIndex()][1]
        rows = apply_query(
            self._all_rows, text=self.search.text(), status=status,
            sort_key=self._sort_key, descending=self._descending,
        )
        self.model.set_rows(rows)
        self.last_rebuild_ms = (time.perf_counter() - started) * 1000
        counts = summarise(rows)
        self.footer.setText(
            f"{len(rows):,} von {len(self._all_rows):,} Proben   ·   "
            f"frei {counts[Status.OK]:,}   prüfen {counts[Status.WARN]:,}   "
            f"gesperrt {counts[Status.FAIL]:,}   ·   "
            f"Aufbau {self.last_rebuild_ms:.1f} ms".replace(",", " ")
        )

    def _on_header_clicked(self, section: int) -> None:
        key = self.model.sort_key_for_column(section)
        self.set_sort(key, not self._descending if key == self._sort_key else False)

    def set_sort(self, key: str, descending: bool = False) -> None:
        """Sort by ``key`` and move the header's sort indicator along."""
        self._sort_key, self._descending = key, descending
        section = next((i for i, column in enumerate(COLUMNS) if column.key == key),
                       len(COLUMNS))
        self.table.horizontalHeader().setSortIndicator(
            section, Qt.DescendingOrder if descending else Qt.AscendingOrder)
        self.rebuild()


def build(rows: list[Sample]) -> MainWindow:
    """Create the window without entering the event loop (used by the bench)."""
    return MainWindow(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LabControl, Qt variant")
    parser.add_argument("--rows", type=int, default=2_000)
    parser.add_argument("--filter", default="")
    parser.add_argument("--sort", default="sample_id")
    parser.add_argument("--desc", action="store_true")
    parser.add_argument("--status", choices=("ok", "warn", "fail"))
    parser.add_argument("--screenshot", help="save a PNG of the window and exit")
    parser.add_argument("--size", default="1400x780")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    window = build(generate(args.rows))
    width, _, height = args.size.partition("x")
    window.resize(int(width), int(height))
    window.search.setText(args.filter)
    if args.status:
        wanted = {"ok": Status.OK, "warn": Status.WARN,
                  "fail": Status.FAIL}[args.status]
        window.status_box.setCurrentIndex(
            [state for _, state in STATUS_CHOICES].index(wanted))
    window.set_sort(args.sort, args.desc)
    window.show()

    if args.screenshot:
        app.processEvents()
        window.grab().save(args.screenshot)
        print(f"{args.screenshot} · {window.model.rowCount()} rows · "
              f"rebuild {window.last_rebuild_ms:.1f} ms")
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
