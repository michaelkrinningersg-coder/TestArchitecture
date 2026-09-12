"""Ansicht des Prüfpfads — lesend, weil er nur wächst."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableView,
    QVBoxLayout,
)

from labcontrol.storage import AuditEntry, Storage

HEADERS = ("Zeitpunkt (UTC)", "Benutzer", "Vorgang", "Probe-ID", "Detail", "Begründung")
WIDTHS = (140, 120, 110, 120, 300, 320)


class AuditModel(QAbstractTableModel):
    def __init__(self, entries: list[AuditEntry] | None = None) -> None:
        super().__init__()
        self._entries = entries or []

    def set_entries(self, entries: list[AuditEntry]) -> None:
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        entry = self._entries[index.row()]
        return (
            entry.at.strftime("%d.%m.%Y %H:%M:%S"),
            entry.actor,
            entry.action,
            entry.sample_id,
            entry.detail,
            entry.note,
        )[index.column()]

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return HEADERS[section]


class AuditDialog(QDialog):
    """Der vollständige Prüfpfad, nach Probe-ID durchsuchbar."""

    def __init__(self, storage: Storage, parent=None, sample_id: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Prüfpfad")
        self.resize(1080, 560)
        self._storage = storage

        self.search = QLineEdit(sample_id)
        self.search.setPlaceholderText("nach Probe-ID einschränken …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.reload)

        self.count = QLabel()
        self.model = AuditModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)  # eine Zeile je Eintrag, sonst springt die Höhe
        header = self.table.horizontalHeader()
        for section, width in enumerate(WIDTHS):
            self.table.setColumnWidth(section, width)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.Interactive)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Schließen")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        top = QHBoxLayout()
        top.addWidget(QLabel("Filter:"))
        top.addWidget(self.search, 1)
        top.addWidget(self.count)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)
        self.reload()

    def reload(self) -> None:
        entries = self._storage.audit(self.search.text().strip() or None)
        self.model.set_entries(entries)
        self.count.setText(f"{len(entries)} Einträge")
