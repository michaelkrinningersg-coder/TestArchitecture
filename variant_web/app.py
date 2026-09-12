"""LabControl — web variant: Flask with server-rendered HTML.

Run it:  python -m variant_web.app --rows 20000   → http://127.0.0.1:5000
Test it: pytest variant_web/test_app.py
"""

from __future__ import annotations

import argparse
import os
import sys

if __package__ in (None, ""):  # allow `python variant_web/app.py` as well
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request

from core.data import TODAY, generate
from core.model import AMPEL_BG, AMPEL_FG, Sample, Status, status_of
from core.query import COLUMNS, STATUS_TITLE, apply_query, summarise

#: Server-rendered HTML puts the whole result set on the wire, so the page is
#: capped. Raising this is what the payload benchmark measures.
ROW_LIMIT = 200

STATUS_CHOICES = (("Alle Status", ""), ("frei", "ok"), ("prüfen", "warn"),
                  ("gesperrt", "fail"))


def create_app(rows: list[Sample] | None = None,
               row_limit: int | None = ROW_LIMIT) -> Flask:
    """Build the application; tests inject their own small ``rows``."""
    app = Flask(__name__)
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True
    app.config["ROWS"] = generate(2_000) if rows is None else rows
    app.config["ROW_LIMIT"] = row_limit

    @app.get("/")
    def index() -> str:
        text = request.args.get("q", "")
        status_key = request.args.get("status", "")
        sort_key = request.args.get("sort", "sample_id")
        descending = request.args.get("dir", "asc") == "desc"
        status = {s.value: s for s in Status}.get(status_key)

        all_rows = app.config["ROWS"]
        found = apply_query(all_rows, text=text, status=status,
                            sort_key=sort_key, descending=descending)
        limit = app.config["ROW_LIMIT"]
        shown = found if limit is None else found[:limit]

        return render_template(
            "index.html",
            columns=COLUMNS,
            status_title=STATUS_TITLE,
            status_choices=STATUS_CHOICES,
            rows=[(sample, status_of(sample, TODAY)) for sample in shown],
            total=len(all_rows),
            found=len(found),
            shown=len(shown),
            counts=[(state.label, count)
                    for state, count in summarise(found).items()],
            query=text,
            status_key=status_key,
            sort_key=sort_key,
            direction="desc" if descending else "asc",
            bg=AMPEL_BG,
            fg=AMPEL_FG,
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LabControl, web variant")
    parser.add_argument("--rows", type=int, default=2_000)
    parser.add_argument("--limit", type=int, default=ROW_LIMIT,
                        help="rows per page; 0 renders everything")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args(argv)

    app = create_app(generate(args.rows), row_limit=args.limit or None)
    app.run(port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
