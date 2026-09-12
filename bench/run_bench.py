"""Runs the whole benchmark matrix and writes bench/results.json + results.md.

Each cell is a separate process: Qt once on the offscreen platform and once on
a real X server, Tkinter on that same X server, the web variant server side,
and Chromium for the end-to-end page load.

    python bench/run_bench.py                 # full matrix
    python bench/run_bench.py --sizes 2000    # quick pass
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
XVFB = ["xvfb-run", "-a", "-s", "-screen 0 1440x900x24"]

#: label → (worker variant, extra environment, needs an X server)
JOBS = {
    "shared-core": ("shared", {}, False),
    "qt-offscreen": ("qt", {"QT_QPA_PLATFORM": "offscreen"}, False),
    "qt-x11": ("qt", {"QT_QPA_PLATFORM": "xcb"}, True),
    "tk-x11": ("tk", {}, True),
    "web-server": ("web", {}, False),
    "web-browser": ("browser", {}, False),
}


def run_cell(label: str, rows: int, repeat: int) -> dict | None:
    variant, extra_env, needs_display = JOBS[label]
    command = [sys.executable, "-m", "bench.bench_rebuild",
               "--variant", variant, "--rows", str(rows), "--repeat", str(repeat)]
    if needs_display and not os.environ.get("DISPLAY"):
        if not shutil.which("xvfb-run"):
            print(f"  {label:<13} skipped (no display, no xvfb-run)")
            return None
        command = XVFB + command

    env = {**os.environ, "PYTHONPATH": str(ROOT), **extra_env}
    started = time.perf_counter()
    finished = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                              text=True)
    if finished.returncode != 0:
        tail = finished.stderr.strip().splitlines()[-1:] or ["(no output)"]
        print(f"  {label:<13} FAILED: {tail[0]}")
        return None

    payload = json.loads(finished.stdout.strip().splitlines()[-1])
    payload["label"] = label
    payload["wall_s"] = round(time.perf_counter() - started, 1)
    print(f"  {label:<13} ok ({payload['wall_s']} s)")
    return payload


def environment() -> dict:
    cpu = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    info = {"python": platform.python_version(), "system": platform.platform(),
            "cpu": cpu, "cpu_count": os.cpu_count(),
            "measured_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        import PySide6
        info["pyside6"] = PySide6.__version__
    except ImportError:
        pass
    try:
        import tkinter
        info["tk"] = str(tkinter.TkVersion)
    except ImportError:
        pass
    return info


def markdown(results: list[dict], sizes: list[int]) -> str:
    """Two tables: the painted round trip, and the toolkit-only update cost."""
    lines = ["# Messergebnisse", "", "Median aus mehreren Durchläufen, "
             "Millisekunden.", ""]
    for mode, caption in (("painted", "Vollaufbau bis gezeichnet"),
                          ("update", "Vollaufbau ohne erzwungenes Neuzeichnen")):
        lines += [f"## {caption}", "",
                  "| Variante | " + " | ".join(f"{n:,} Zeilen".replace(",", " ")
                                               for n in sizes) + " |",
                  "|---|" + "---|" * len(sizes)]
        for label in JOBS:
            cells = []
            for size in sizes:
                hit = next((r for r in results
                            if r["label"] == label and r["rows"] == size), None)
                if hit is None or "full" not in hit:
                    cells.append("–")
                else:
                    cells.append(f"{hit['full'][mode]['median_ms']:.1f}")
            if set(cells) != {"–"}:
                lines.append(f"| {label} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LabControl benchmark matrix")
    parser.add_argument("--sizes", type=int, nargs="+",
                        default=[2_000, 20_000, 100_000])
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--jobs", nargs="+", default=list(JOBS),
                        choices=list(JOBS))
    parser.add_argument("--out", default=str(ROOT / "bench" / "results.json"))
    args = parser.parse_args(argv)

    results: list[dict] = []
    for size in args.sizes:
        print(f"{size:,} rows".replace(",", " "))
        repeat = args.repeat if size <= 20_000 else max(3, args.repeat - 2)
        for label in args.jobs:
            # Chromium against a fully rendered 100 000-row page is a memory
            # experiment, not a UI measurement — the capped page is the point.
            if label == "web-browser" and size > 20_000:
                continue
            cell = run_cell(label, size, repeat)
            if cell is not None:
                results.append(cell)

    payload = {"environment": environment(), "results": results}
    Path(args.out).write_text(json.dumps(payload, indent=2))
    report = markdown(results, args.sizes)
    Path(args.out).with_suffix(".md").write_text(report)
    print()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
