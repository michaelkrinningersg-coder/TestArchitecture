"""Dialoge: Probe anlegen und bearbeiten, Freigabe entscheiden, Über."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
)

from core.model import AMPEL_BG, AMPEL_FG, Status
from labcontrol import __version__
from labcontrol.domain import (
    ReleaseState,
    SampleRecord,
    ValidationError,
    ampel_reason,
    validate,
)


def _to_qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _from_qdate(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


class SampleDialog(QDialog):
    """Legt eine Probe an oder ändert eine bestehende."""

    def __init__(self, parent=None, record: SampleRecord | None = None,
                 suggestions: dict[str, list[str]] | None = None,
                 next_id: str = "") -> None:
        super().__init__(parent)
        self._editing = record is not None
        self.setWindowTitle("Probe bearbeiten" if self._editing else "Neue Probe")
        self.setModal(True)
        self.setMinimumWidth(440)
        suggestions = suggestions or {}

        self.sample_id = QLineEdit(record.sample_id if record else next_id)
        self.sample_id.setReadOnly(self._editing)
        if self._editing:
            self.sample_id.setToolTip("Die Probe-ID bleibt, damit der Prüfpfad "
                                      "zusammenhängend bleibt.")

        self.matrix = self._combo(suggestions.get("matrix", []),
                                  record.matrix if record else "")
        self.analyte = self._combo(suggestions.get("analyte", []),
                                   record.analyte if record else "")
        self.analyst = self._combo(suggestions.get("analyst", []),
                                   record.analyst if record else "")
        self.unit = self._combo(suggestions.get("unit", []),
                                record.unit if record else "mg/l")

        self.value = self._spin(record.value if record else 0.0)
        self.target = self._spin(record.target if record else 0.0)
        self.tolerance = self._spin(record.tolerance if record else 0.001,
                                    minimum=0.0001)

        today = date.today()
        self.received = QDateEdit(_to_qdate(record.received if record else today))
        self.due = QDateEdit(_to_qdate(record.due if record else today))
        for widget in (self.received, self.due):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("dd.MM.yyyy")

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.addRow("Probe-ID", self.sample_id)
        form.addRow("Matrix", self.matrix)
        form.addRow("Analyt", self.analyte)
        form.addRow("Messwert", self.value)
        form.addRow("Einheit", self.unit)
        form.addRow("Sollwert", self.target)
        form.addRow("Toleranz (±)", self.tolerance)
        form.addRow("Eingang", self.received)
        form.addRow("Fällig", self.due)
        form.addRow("Prüfer", self.analyst)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Speichern")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._source = record

    def _combo(self, values: list[str], current: str) -> QComboBox:
        box = QComboBox()
        box.setEditable(True)
        box.addItems(values)
        box.setCurrentText(current)
        return box

    def _spin(self, value: float, minimum: float = -1e9) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(4)
        spin.setRange(minimum, 1e9)
        spin.setValue(value)
        spin.setSingleStep(0.001)
        return spin

    def record(self) -> SampleRecord:
        """Baut den Datensatz aus den Eingabefeldern."""
        base = self._source
        return SampleRecord(
            sample_id=self.sample_id.text().strip(),
            matrix=self.matrix.currentText().strip(),
            analyte=self.analyte.currentText().strip(),
            value=self.value.value(),
            target=self.target.value(),
            tolerance=self.tolerance.value(),
            unit=self.unit.currentText().strip(),
            analyst=self.analyst.currentText().strip(),
            received=_from_qdate(self.received.date()),
            due=_from_qdate(self.due.date()),
            state=base.state if base else ReleaseState.OPEN,
            decided_at=base.decided_at if base else None,
            decided_by=base.decided_by if base else "",
            decision_note=base.decision_note if base else "",
            row_id=base.row_id if base else None,
        )

    def _on_accept(self) -> None:
        try:
            validate(self.record())
        except ValidationError as error:
            QMessageBox.warning(self, "Eingabe unvollständig", str(error))
            return
        self.accept()


class DecisionDialog(QDialog):
    """Freigeben, sperren oder zurücksetzen — immer mit Begründung."""

    def __init__(self, parent=None, record: SampleRecord | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Freigabe entscheiden")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._record = record
        status = record.ampel() if record else Status.OK

        headline = QLabel(f"<b>{record.sample_id}</b> · {record.analyte} in "
                          f"{record.matrix}<br>Messwert {record.value:g} "
                          f"{record.unit} · Soll {record.target:g} ± "
                          f"{record.tolerance:g}")
        headline.setTextFormat(Qt.RichText)

        badge = QLabel(f"  Bewertung: {status.label} — {ampel_reason(record)}  ")
        badge.setStyleSheet(
            f"background: {AMPEL_BG[status]}; color: {AMPEL_FG[status]};"
            "padding: 6px 10px; border-radius: 3px; font-weight: 600;")

        self.release = QRadioButton("freigeben")
        self.block = QRadioButton("sperren")
        self.reopen = QRadioButton("zurück auf offen")
        self.release.setChecked(status is not Status.FAIL)
        self.block.setChecked(status is Status.FAIL)

        self.reason = QPlainTextEdit()
        self.reason.setPlaceholderText(
            "Begründung — geht unveränderlich in den Prüfpfad.")
        self.reason.setFixedHeight(84)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {AMPEL_FG[Status.FAIL]};")

        choices = QHBoxLayout()
        choices.addWidget(self.release)
        choices.addWidget(self.block)
        choices.addWidget(self.reopen)
        choices.addStretch(1)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Entscheidung eintragen")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(headline)
        layout.addWidget(badge)
        layout.addWidget(line)
        layout.addLayout(choices)
        layout.addWidget(QLabel("Begründung"))
        layout.addWidget(self.reason)
        layout.addWidget(self.hint)
        layout.addWidget(self.buttons)

        for button in (self.release, self.block, self.reopen):
            button.toggled.connect(self._refresh)
        self.reason.textChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        has_reason = bool(self.reason.toPlainText().strip())
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(has_reason)
        if self.release.isChecked() and self._record \
                and self._record.ampel() is Status.FAIL:
            self.hint.setText("Diese Probe ist außerhalb der Toleranz oder "
                              "überfällig. Eine Freigabe braucht eine "
                              "belastbare Begründung.")
        elif not has_reason:
            self.hint.setText("Ohne Begründung wird nichts gespeichert.")
        else:
            self.hint.setText("")

    def decision(self) -> tuple[ReleaseState, str]:
        if self.block.isChecked():
            state = ReleaseState.BLOCKED
        elif self.reopen.isChecked():
            state = ReleaseState.OPEN
        else:
            state = ReleaseState.RELEASED
        return state, self.reason.toPlainText().strip()


def show_about(parent, database: str) -> None:
    """Kurzer Steckbrief der Anwendung."""
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion

    QMessageBox.about(
        parent, "Über LabControl",
        f"<h3>LabControl {__version__}</h3>"
        "<p>Freigabe von Laborproben mit fortschreibendem Prüfpfad.</p>"
        f"<p><b>Qt</b> {qVersion()} · <b>PySide6</b> {pyside_version}</p>"
        f"<p><b>Datenbank</b><br><code>{database}</code></p>")
