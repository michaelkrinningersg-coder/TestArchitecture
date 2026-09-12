"""Shows that Qt's repaint cost does not depend on the number of rows.

    QT_QPA_PLATFORM=offscreen python bench/paint_probe.py

Prints, per data set size, how long a model reset and a full viewport repaint
take. The repaint figure is the constant this environment adds to every Qt
measurement in bench/results.json; the model reset is what Qt actually pays for
more data.
"""

from __future__ import annotations

import os
import statistics
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from core.data import generate
from variant_qt.app import build


def median_ms(action, repeat: int = 5) -> float:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 1)


def main() -> int:
    app = QApplication(sys.argv[:1])
    print(f"{'Zeilen':>8} {'set_rows':>10} {'repaint':>10} {'sichtbar':>10}")
    for rows in (2_000, 20_000, 100_000):
        window = build(generate(rows))
        window.resize(1400, 780)
        window.show()
        app.processEvents()
        reset = median_ms(lambda: window.model.set_rows(window.model._rows))
        paint = median_ms(lambda: window.table.viewport().repaint())
        visible = window.table.viewport().height() // 24
        print(f"{rows:>8} {reset:>9.1f} ms {paint:>7.1f} ms {visible:>7} Zeilen")
        window.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
