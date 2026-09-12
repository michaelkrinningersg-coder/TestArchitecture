"""What each variant needs before a test can touch it.

The point of this file is not coverage, it is the precondition: the web
variant answers in a plain process, the Qt table model answers without a
window, the Qt window itself needs a platform plugin, and the Tkinter variant
needs a display server or it cannot even be constructed.
"""

from __future__ import annotations

import os

import pytest

# Must be set before PySide6 builds its platform integration.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.data import generate
from core.model import Status

pyside = pytest.importorskip("PySide6", reason="Qt-Variante nicht installiert")


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_qt_model_answers_without_any_application(qt_app):
    """The table model is plain Python state — no window involved."""
    from PySide6.QtCore import Qt

    from variant_qt.app import SampleTableModel

    model = SampleTableModel(generate(50))
    assert model.rowCount() == 50
    assert model.columnCount() == 9
    assert model.headerData(0, Qt.Horizontal, Qt.DisplayRole) == "Probe-ID"
    assert model.data(model.index(0, 0), Qt.DisplayRole) == "P-2026-000001"
    assert model.data(model.index(0, 8), Qt.DisplayRole) in {s.label for s in Status}


def test_qt_filter_reaches_the_model(qt_app):
    """Driving the widget needs a platform plugin — offscreen is enough."""
    from variant_qt.app import build

    window = build(generate(2_000))
    before = window.model.rowCount()
    window.search.setText("Cadmium")
    assert window.model.rowCount() == 200 < before
    window.search.setText("Plutonium")
    assert window.model.rowCount() == 0


@pytest.mark.skipif(not os.environ.get("DISPLAY"),
                    reason="Tkinter braucht einen X-Server (z. B. xvfb-run)")
def test_tk_variant_needs_a_display():
    import tkinter as tk

    from variant_tk.app import LabControlTk

    root = tk.Tk()
    try:
        window = LabControlTk(root, generate(2_000))
        assert len(window.tree.get_children()) == 2_000
        window.search_var.set("Cadmium")
        window.rebuild()
        assert len(window.tree.get_children()) == 200
    finally:
        root.destroy()
