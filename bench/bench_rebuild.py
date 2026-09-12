"""Measures one variant at one data set size and prints a JSON result.

Two numbers are taken per transition, because they answer different questions:

``update_ms``  filter, sort and hand the result to the widget — the cost that
               belongs to the toolkit's data structure.
``painted_ms`` the same, plus a forced synchronous repaint — what the user
               waits for before the new table is on screen.

Every repetition starts from the opposite state, set up outside the timer, so
each measurement is a real transition and never a no-op.

The orchestrator in ``bench/run_bench.py`` calls this worker once per variant
and size, each in its own process, so no toolkit ever sees another one.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import statistics
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data import generate

#: Matches roughly a tenth of the rows — one analyte out of ten.
FILTER_TEXT = "blei"


def _timed(action, setup, repeat: int) -> dict:
    """Time ``action`` ``repeat`` times, running ``setup`` outside the clock."""
    samples = []
    for _ in range(repeat):
        setup()
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000)
    return {"median_ms": round(statistics.median(samples), 1),
            "best_ms": round(min(samples), 1),
            "runs": [round(s, 1) for s in samples]}


def bench_qt(rows: int, repeat: int) -> dict:
    from PySide6.QtWidgets import QApplication

    from variant_qt.app import build

    app = QApplication(sys.argv[:1])
    window = build(generate(rows))
    window.resize(1400, 780)
    window.show()
    app.processEvents()

    def set_text(text: str, paint: bool):
        def run():
            window.search.setText(text)            # triggers rebuild()
            if paint:
                window.table.viewport().repaint()  # force the paint, don't defer
                app.processEvents()
        return run

    def measure(text: str, other: str) -> dict:
        return {
            "update": _timed(set_text(text, False), set_text(other, True), repeat),
            "painted": _timed(set_text(text, True), set_text(other, True), repeat),
        }

    set_text("", True)()  # warm up
    result = {"full": measure("", FILTER_TEXT), "filtered": measure(FILTER_TEXT, "")}
    result["filtered_rows"] = window.model.rowCount()
    result["platform"] = app.platformName()
    return result


def bench_tk(rows: int, repeat: int) -> dict:
    import tkinter as tk

    from variant_tk.app import LabControlTk

    root = tk.Tk()
    root.geometry("1400x780")
    window = LabControlTk(root, generate(rows))
    root.update()

    def set_text(text: str, paint: bool):
        def run():
            window.search_var.set(text)
            window.rebuild()   # called directly: the debounce is not the cost
            if paint:
                root.update()  # force layout and paint
        return run

    def measure(text: str, other: str) -> dict:
        return {
            "update": _timed(set_text(text, False), set_text(other, True), repeat),
            "painted": _timed(set_text(text, True), set_text(other, True), repeat),
        }

    set_text("", True)()  # warm up
    result = {"full": measure("", FILTER_TEXT), "filtered": measure(FILTER_TEXT, "")}
    result["filtered_rows"] = len(window.tree.get_children())
    result["platform"] = "x11"
    root.destroy()
    return result


def bench_web(rows: int, repeat: int) -> dict:
    """Server side only: request in, finished HTML out (browser paint excluded)."""
    from variant_web.app import ROW_LIMIT, create_app

    data = generate(rows)
    capped = create_app(data, row_limit=ROW_LIMIT).test_client()
    uncapped = create_app(data, row_limit=None).test_client()

    def get(client, url: str):
        return lambda: client.get(url).get_data()

    def measure(client, url: str) -> dict:
        timing = _timed(get(client, url), lambda: None, repeat)
        return {"update": timing, "painted": timing}

    get(capped, "/")()  # warm up
    body_capped = capped.get("/").get_data()
    body_uncapped = uncapped.get("/").get_data()
    return {
        "full": measure(capped, "/"),
        "filtered": measure(capped, f"/?q={FILTER_TEXT}"),
        "uncapped_full": measure(uncapped, "/"),
        "filtered_rows": capped.get(f"/?q={FILTER_TEXT}")
                               .get_data(as_text=True).count("<tr class="),
        "bytes_capped": len(body_capped),
        "bytes_uncapped": len(body_uncapped),
        "bytes_capped_gzip": len(gzip.compress(body_capped, 6)),
        "bytes_uncapped_gzip": len(gzip.compress(body_uncapped, 6)),
        "platform": "flask-testclient",
    }


def bench_browser(rows: int, repeat: int) -> dict:
    """End to end in Chromium: navigation until the table is actually laid out."""
    import glob
    import threading

    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    from variant_web.app import ROW_LIMIT, create_app

    data = generate(rows)
    servers = {}
    for name, limit in (("capped", ROW_LIMIT), ("uncapped", None)):
        server = make_server("127.0.0.1", 0, create_app(data, row_limit=limit),
                             threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers[name] = server

    executables = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    result: dict = {}
    with sync_playwright() as play:
        browser = play.chromium.launch(
            executable_path=executables[0] if executables else None)
        page = browser.new_page(viewport={"width": 1400, "height": 780})
        for name, server in servers.items():
            url = f"http://127.0.0.1:{server.server_port}/"

            def load():
                page.goto(url, wait_until="load")
                # touching the layout forces the render to actually happen
                page.evaluate("document.body.getBoundingClientRect().height")

            load()  # warm up
            result[name] = _timed(load, lambda: page.goto("about:blank"), repeat)
            page.goto(url, wait_until="load")
            result[f"{name}_rows"] = page.evaluate(
                "document.querySelectorAll('tbody tr').length")
        browser.close()
    for server in servers.values():
        server.shutdown()
    result["platform"] = "chromium"
    return result


def bench_shared(rows: int, repeat: int) -> dict:
    """The cost every variant pays: filter, sort and count, in plain Python.

    Whatever this costs, no toolkit can undercut it — it is the floor under all
    three variants and it is the same code in all three.
    """
    from core.query import apply_query, summarise

    data = generate(rows)

    def query(text: str):
        def run():
            summarise(apply_query(data, text=text, sort_key="sample_id"))
        return run

    query("")()  # warm up
    full = _timed(query(""), lambda: None, repeat)
    filtered = _timed(query(FILTER_TEXT), lambda: None, repeat)
    return {"full": {"update": full, "painted": full},
            "filtered": {"update": filtered, "painted": filtered},
            "filtered_rows": len(apply_query(data, text=FILTER_TEXT)),
            "platform": "python"}


BENCHES = {"shared": bench_shared, "qt": bench_qt, "tk": bench_tk, "web": bench_web, "browser": bench_browser}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="one variant, one size")
    parser.add_argument("--variant", choices=sorted(BENCHES), required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args(argv)

    result = BENCHES[args.variant](args.rows, args.repeat)
    result.update(variant=args.variant, rows=args.rows)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
