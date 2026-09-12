#!/usr/bin/env bash
# Runs everything this comparison claims: tests, benchmarks, screenshots.
#
#   tools/run_all.sh            # full run, takes a few minutes
#   SIZES="2000" tools/run_all.sh   # quick pass
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
SIZES="${SIZES:-2000 20000 100000}"
export PYTHONPATH="$PWD"

echo "== Tests =="
"$PYTHON" -m pytest tests variant_web -q

echo
echo "== Benchmarks ($SIZES) =="
"$PYTHON" bench/run_bench.py --sizes $SIZES

echo
echo "== Screenshots =="
if [ -n "${DISPLAY:-}" ]; then
  "$PYTHON" tools/shoot.py
else
  xvfb-run -a -s "-screen 0 1440x900x24" "$PYTHON" tools/shoot.py
fi

echo
echo "Done. Numbers in bench/results.md, pictures in docs/screenshots/."
