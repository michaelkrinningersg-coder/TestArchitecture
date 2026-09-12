"""Takes the screenshots used for the visual comparison.

All three variants are photographed with the same data set, the same window
size and the same filter state, so the pictures can be laid side by side.

    xvfb-run -a -s "-screen 0 1440x900x24" python tools/shoot.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # the web shot renders in this process
OUT = ROOT / "docs" / "screenshots"
SIZE = "1400x780"
ROWS = 2_000

#: name → command line arguments shared by the Qt and Tkinter variants
SHOTS = {
    "overview": [],
    "filtered": ["--filter", "Cadmium"],
    "blocked": ["--status", "fail", "--sort", "due"],
}


def run(command: list[str], env: dict | None = None) -> None:
    finished = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                              env={**os.environ, "PYTHONPATH": str(ROOT),
                                   **(env or {})})
    if finished.returncode != 0:
        raise SystemExit(f"failed: {' '.join(command)}\n{finished.stderr}")
    print("  " + (finished.stdout.strip().splitlines() or ["done"])[-1])


def shoot_desktop(variant: str, module: str, env: dict | None = None) -> None:
    for name, extra in SHOTS.items():
        target = OUT / f"{variant}_{name}.png"
        print(f"{variant} · {name}")
        run([sys.executable, "-m", module, "--rows", str(ROWS), "--size", SIZE,
             "--screenshot", str(target)] + extra, env)


def shoot_web() -> None:
    """Chromium against the real server, same window size as the desktop shots."""
    import glob
    import threading

    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    from core.data import generate
    from variant_web.app import create_app

    server = make_server("127.0.0.1", 0, create_app(generate(ROWS)), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/"
    width, _, height = SIZE.partition("x")

    executables = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    queries = {"overview": "", "filtered": "?q=Cadmium",
               "blocked": "?status=fail&sort=due"}
    with sync_playwright() as play:
        browser = play.chromium.launch(
            executable_path=executables[0] if executables else None)
        page = browser.new_page(viewport={"width": int(width), "height": int(height)},
                                device_scale_factor=1)
        for name, query in queries.items():
            page.goto(base + query, wait_until="load")
            target = OUT / f"web_{name}.png"
            page.screenshot(path=str(target))
            rows = page.evaluate("document.querySelectorAll('tbody tr').length")
            print(f"web · {name}\n  {target} · {rows} rows")
        browser.close()
    server.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="screenshots of all variants")
    parser.add_argument("--only", choices=("qt", "tk", "web"), nargs="*")
    args = parser.parse_args(argv)
    wanted = args.only or ["qt", "tk", "web"]

    OUT.mkdir(parents=True, exist_ok=True)
    if "qt" in wanted:
        shoot_desktop("qt", "variant_qt.app", {"QT_QPA_PLATFORM": "xcb"})
    if "tk" in wanted:
        shoot_desktop("tk", "variant_tk.app")
    if "web" in wanted:
        shoot_web()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
