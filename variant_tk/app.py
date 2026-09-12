"""LabControl — Tkinter variant: ttk.Treeview.

Run it:  python -m variant_tk.app --rows 20000
Headless: xvfb-run -a python -m variant_tk.app --screenshot out.png
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import tkinter as tk
from tkinter import ttk

if __package__ in (None, ""):  # allow `python variant_tk/app.py` as well
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data import TODAY, generate
from core.model import AMPEL_BG, AMPEL_FG, Sample, Status, status_of
from core.query import COLUMNS, STATUS_TITLE, STATUS_WIDTH, apply_query, summarise

#: The Treeview is rebuilt item by item, so a keystroke must not trigger a
#: rebuild on its own — this is the delay the Qt variant does not need.
DEBOUNCE_MS = 150

STATUS_CHOICES = (("Alle Status", None), ("frei", Status.OK),
                  ("prüfen", Status.WARN), ("gesperrt", Status.FAIL))
STATUS_BY_LABEL = dict(STATUS_CHOICES)


class LabControlTk:
    """Search field, status filter, tree, footer — the whole variant."""

    def __init__(self, root: tk.Misc, rows: list[Sample]) -> None:
        self.root = root
        self._all_rows = rows
        self._sort_key = "sample_id"
        self._descending = False
        self._pending_rebuild: str | None = None
        self.last_rebuild_ms = 0.0

        root.title("LabControl — Tkinter (ttk.Treeview)")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=24, font=("DejaVu Sans", 9),
                        borderwidth=0)
        style.configure("Treeview.Heading", font=("DejaVu Sans", 9, "bold"))
        style.map("Treeview", background=[("selected", "#2f6fb0")])

        controls = ttk.Frame(root, padding=(8, 8, 8, 4))
        controls.pack(fill="x")
        ttk.Label(controls, text="Filter:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_typed)
        self.search = ttk.Entry(controls, textvariable=self.search_var)
        self.search.pack(side="left", fill="x", expand=True, padx=6)
        self.status_var = tk.StringVar(value=STATUS_CHOICES[0][0])
        self.status_box = ttk.Combobox(
            controls, textvariable=self.status_var, state="readonly", width=12,
            values=[label for label, _ in STATUS_CHOICES],
        )
        self.status_box.bind("<<ComboboxSelected>>", lambda _e: self.rebuild())
        self.status_box.pack(side="left")

        body = ttk.Frame(root, padding=(8, 0))
        body.pack(fill="both", expand=True)
        keys = [c.key for c in COLUMNS] + ["status"]
        self.tree = ttk.Treeview(body, columns=keys, show="headings",
                                 selectmode="browse")
        for column in COLUMNS:
            self.tree.heading(column.key, text=column.title,
                              command=lambda k=column.key: self._sort_by(k))
            self.tree.column(column.key, width=column.width, stretch=False,
                             anchor="e" if column.numeric else "w")
        self.tree.heading("status", text=STATUS_TITLE,
                          command=lambda: self._sort_by("status"))
        self.tree.column("status", width=STATUS_WIDTH, anchor="center",
                         stretch=True)
        # A Treeview colours whole rows only: the Ampel has to be a row tag,
        # a per-cell background is not available in this widget.
        for status in Status:
            self.tree.tag_configure(status.value, background=AMPEL_BG[status],
                                    foreground=AMPEL_FG[status])
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.footer = ttk.Label(root, padding=(10, 4))
        self.footer.pack(fill="x")
        self.rebuild()

    # -- filtering ----------------------------------------------------------
    def _on_search_typed(self, *_args: object) -> None:
        """Collapse a burst of keystrokes into a single rebuild."""
        if self._pending_rebuild is not None:
            self.root.after_cancel(self._pending_rebuild)
        self._pending_rebuild = self.root.after(DEBOUNCE_MS, self._flush_search)

    def _flush_search(self) -> None:
        self._pending_rebuild = None
        self.rebuild()

    def _sort_by(self, key: str) -> None:
        self.set_sort(key, not self._descending if key == self._sort_key else False)

    def set_sort(self, key: str, descending: bool = False) -> None:
        """Sort by ``key`` and mark the active column in its heading."""
        self._sort_key, self._descending = key, descending
        arrow = " ▼" if descending else " ▲"
        for column in COLUMNS:
            self.tree.heading(column.key,
                              text=column.title + (arrow if column.key == key else ""))
        self.tree.heading("status",
                          text=STATUS_TITLE + (arrow if key == "status" else ""))
        self.rebuild()

    def rebuild(self) -> None:
        """Re-apply filter and sort, then refill the tree row by row."""
        started = time.perf_counter()
        rows = apply_query(
            self._all_rows, text=self.search_var.get(),
            status=STATUS_BY_LABEL[self.status_var.get()],
            sort_key=self._sort_key, descending=self._descending,
        )
        self.tree.delete(*self.tree.get_children())
        self._insert_all(rows)
        self.last_rebuild_ms = (time.perf_counter() - started) * 1000
        counts = summarise(rows)
        self.footer.configure(text=(
            f"{len(rows):,} von {len(self._all_rows):,} Proben   ·   "
            f"frei {counts[Status.OK]:,}   prüfen {counts[Status.WARN]:,}   "
            f"gesperrt {counts[Status.FAIL]:,}   ·   "
            f"Aufbau {self.last_rebuild_ms:.1f} ms").replace(",", " "))

    def _insert_all(self, rows: list[Sample]) -> None:
        """One insert call per row — the cost the Qt model does not pay."""
        insert = self.tree.insert
        renderers = [column.render for column in COLUMNS]
        for sample in rows:
            status = status_of(sample, TODAY)
            insert("", "end",
                   values=[render(sample) for render in renderers] + [status.label],
                   tags=(status.value,))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LabControl, Tkinter variant")
    parser.add_argument("--rows", type=int, default=2_000)
    parser.add_argument("--filter", default="")
    parser.add_argument("--sort", default="sample_id")
    parser.add_argument("--desc", action="store_true")
    parser.add_argument("--status", choices=("ok", "warn", "fail"))
    parser.add_argument("--screenshot", help="save a PNG of the window and exit")
    parser.add_argument("--size", default="1400x780")
    args = parser.parse_args(argv)

    root = tk.Tk()
    root.geometry(args.size)
    app = LabControlTk(root, generate(args.rows))
    if args.filter:
        app.search_var.set(args.filter)
    if args.status:
        app.status_var.set({"ok": "frei", "warn": "prüfen",
                            "fail": "gesperrt"}[args.status])
    app.set_sort(args.sort, args.desc)

    if args.screenshot:
        from PIL import ImageGrab

        root.update()
        root.update_idletasks()
        ImageGrab.grab(xdisplay=None).crop(
            (0, 0, root.winfo_width(), root.winfo_height())
        ).save(args.screenshot)
        print(f"{args.screenshot} · {len(app.tree.get_children())} rows · "
              f"rebuild {app.last_rebuild_ms:.1f} ms")
        root.destroy()
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
