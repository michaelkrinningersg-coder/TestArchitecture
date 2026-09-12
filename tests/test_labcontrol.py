"""Tests der Anwendung: Ablage, Prüfpfad, Freigabelauf, Oberfläche.

Alles läuft ohne Bildschirm — die Qt-Tests auf der Offscreen-Plattform.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.model import Status
from labcontrol.domain import (
    ReleaseState,
    SampleRecord,
    ValidationError,
    ampel_reason,
    describe_changes,
    validate,
)
from labcontrol.export import HEADER, write_csv
from labcontrol.storage import Storage

TODAY = date.today()


def _record(**overrides) -> SampleRecord:
    base = dict(
        sample_id="P-TEST-000001", matrix="Trinkwasser", analyte="Blei",
        value=0.010, target=0.010, tolerance=0.002, unit="mg/l",
        analyst="M. Krinninger", received=TODAY - timedelta(days=3),
        due=TODAY + timedelta(days=10),
    )
    base.update(overrides)
    return SampleRecord(**base)


@pytest.fixture()
def storage() -> Storage:
    store = Storage()
    store.seed(300)
    yield store
    store.close()


# -- Ablage -----------------------------------------------------------------
def test_seeding_is_idempotent(storage):
    assert storage.count() == 300
    assert storage.seed(300) == 0
    assert storage.count() == 300


def test_text_filter_covers_every_identifying_field(storage):
    for needle in ("Blei", "Boden", "Krinninger", "P-2026-000007"):
        hits = storage.query(text=needle)
        assert hits, needle
        assert len(hits) < storage.count(), needle


def test_ampel_filter_returns_only_that_evaluation(storage):
    blocked = storage.query(ampel=Status.FAIL)
    assert blocked
    assert all(record.ampel() is Status.FAIL for record in blocked)


def test_state_filter_and_sorting(storage):
    open_samples = storage.query(state=ReleaseState.OPEN, sort_key="value")
    assert open_samples
    assert all(record.state is ReleaseState.OPEN for record in open_samples)
    assert [r.value for r in open_samples] == sorted(r.value for r in open_samples)

    descending = storage.query(state=ReleaseState.OPEN, sort_key="value",
                               descending=True)
    assert [r.value for r in descending] == list(reversed(
        [r.value for r in open_samples]))


def test_sorting_by_ampel_puts_the_blocked_ones_first(storage):
    records = storage.query(sort_key="ampel")
    assert records[0].ampel() is Status.FAIL
    assert records[-1].ampel() is Status.OK


# -- Schreiben und Prüfpfad -------------------------------------------------
def test_creating_writes_an_audit_entry(storage):
    created = storage.create(_record(), actor="pruefer1")
    assert storage.get(created.sample_id) is not None
    entries = storage.audit(created.sample_id)
    assert [entry.action for entry in entries] == ["angelegt"]
    assert entries[0].actor == "pruefer1"


def test_duplicate_sample_id_is_refused(storage):
    storage.create(_record(), actor="pruefer1")
    with pytest.raises(ValueError, match="gibt es schon"):
        storage.create(_record(), actor="pruefer1")


def test_update_records_what_changed(storage):
    before = storage.create(_record(), actor="pruefer1")
    after = storage.update(before, _record(value=0.0115, analyst="S. Bauer"),
                           actor="pruefer2")
    assert after.value == 0.0115
    detail = storage.audit(before.sample_id)[0].detail
    assert "Messwert" in detail and "Prüfer" in detail
    assert storage.get(before.sample_id).analyst == "S. Bauer"


def test_update_without_a_change_writes_nothing(storage):
    before = storage.create(_record(), actor="pruefer1")
    storage.update(before, _record(), actor="pruefer1")
    assert len(storage.audit(before.sample_id)) == 1


def test_decision_needs_a_reason(storage):
    record = storage.create(_record(), actor="pruefer1")
    with pytest.raises(ValueError, match="Begründung"):
        storage.decide(record, ReleaseState.RELEASED, "   ", actor="pruefer1")
    assert storage.get(record.sample_id).state is ReleaseState.OPEN


def test_decision_is_stored_with_who_and_why(storage):
    record = storage.create(_record(), actor="pruefer1")
    decided = storage.decide(record, ReleaseState.BLOCKED,
                             "Wiederholung angefordert.", actor="pruefer2")
    assert decided.state is ReleaseState.BLOCKED
    stored = storage.get(record.sample_id)
    assert stored.decided_by == "pruefer2"
    assert stored.decision_note == "Wiederholung angefordert."
    assert stored.decided_at is not None
    assert storage.audit(record.sample_id)[0].action == "gesperrt"


def test_audit_only_grows(storage):
    record = storage.create(_record(), actor="pruefer1")
    storage.decide(record, ReleaseState.RELEASED, "in Ordnung", actor="pruefer1")
    storage.decide(record, ReleaseState.OPEN, "Rückfrage Kunde", actor="pruefer2")
    actions = [entry.action for entry in storage.audit(record.sample_id)]
    assert actions == ["zurückgesetzt", "freigegeben", "angelegt"]


# -- Fachliche Regeln -------------------------------------------------------
@pytest.mark.parametrize("value, expected", [
    (0.010, Status.OK), (0.0116, Status.WARN), (0.0125, Status.FAIL)])
def test_ampel_follows_the_tolerance_band(value, expected):
    assert _record(value=value).ampel(TODAY) is expected


def test_overdue_blocks_even_with_a_good_value():
    assert _record(due=TODAY - timedelta(days=1)).ampel(TODAY) is Status.FAIL


def test_ampel_reason_names_the_actual_cause():
    assert "außerhalb" in ampel_reason(_record(value=0.02), TODAY)
    assert "Frist" in ampel_reason(_record(due=TODAY - timedelta(days=3)), TODAY)
    assert "ausgeschöpft" in ampel_reason(_record(value=0.0118), TODAY)
    assert "fällig in 1 Tag" == ampel_reason(_record(due=TODAY + timedelta(days=1)),
                                             TODAY)


def test_validation_rejects_impossible_input():
    with pytest.raises(ValidationError, match="Toleranz"):
        validate(_record(tolerance=0))
    with pytest.raises(ValidationError, match="Frist"):
        validate(_record(due=TODAY - timedelta(days=30),
                         received=TODAY - timedelta(days=1)))
    with pytest.raises(ValidationError, match="Probe-ID"):
        validate(_record(sample_id="  "))


def test_change_description_is_readable():
    text = describe_changes(_record(), _record(value=0.011, matrix="Boden"))
    assert text == "Matrix: Trinkwasser → Boden; Messwert: 0.01 → 0.011"


# -- Export -----------------------------------------------------------------
def test_csv_export_carries_the_decision(tmp_path, storage):
    record = storage.create(_record(), actor="pruefer1")
    storage.decide(record, ReleaseState.BLOCKED, "gesperrt wegen Frist",
                   actor="pruefer2")
    target = tmp_path / "export.csv"
    written = write_csv(target, storage.query(text="P-TEST"))
    assert written == 1
    lines = target.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].split(";") == list(HEADER)
    assert "gesperrt wegen Frist" in lines[1]
    assert "pruefer2" in lines[1]


# -- Oberfläche, ohne Fenster ----------------------------------------------
@pytest.fixture(scope="module")
def qt_app():
    from labcontrol.app import build_application

    return build_application([])


def test_table_model_renders_every_column(qt_app, storage):
    from PySide6.QtCore import Qt

    from labcontrol.table_model import COLUMNS, PILL_ROLE, SampleTableModel

    model = SampleTableModel(storage.query())
    assert model.rowCount() == 300
    assert model.columnCount() == len(COLUMNS)
    first = [model.data(model.index(0, column), Qt.DisplayRole)
             for column in range(model.columnCount())]
    assert all(cell for cell in first)
    background, foreground, _bold = model.data(model.index(0, 8), PILL_ROLE)
    assert background.startswith("#") and foreground.startswith("#")


def test_main_window_filters_and_counts(qt_app, storage):
    from labcontrol.main_window import MainWindow

    window = MainWindow(storage, persist_state=False)
    assert window.model.rowCount() == 300
    assert window.selected_record() is not None  # startet mit Auswahl

    window.search.setText("Cadmium")
    assert window.model.rowCount() == 30
    window.search.setText("Plutonium")
    assert window.model.rowCount() == 0
    assert "Keine Probe ausgewählt" in window.details.toHtml()

    window.reset_filters()
    assert window.model.rowCount() == 300
    window.close()


def test_decision_dialog_requires_a_reason_before_it_can_be_confirmed(qt_app,
                                                                      storage):
    from PySide6.QtWidgets import QDialogButtonBox

    from labcontrol.dialogs import DecisionDialog

    record = storage.query(ampel=Status.FAIL)[0]
    dialog = DecisionDialog(None, record=record)
    confirm = dialog.buttons.button(QDialogButtonBox.Ok)
    assert not confirm.isEnabled()
    dialog.reason.setPlainText("Charge gesperrt, Ursachenanalyse läuft.")
    assert confirm.isEnabled()
    assert dialog.decision() == (ReleaseState.BLOCKED,
                                 "Charge gesperrt, Ursachenanalyse läuft.")
    dialog.close()


def test_selftest_passes(capsys):
    from labcontrol.app import selftest

    assert selftest(rows=50) == 0
    assert "bestanden" in capsys.readouterr().out
