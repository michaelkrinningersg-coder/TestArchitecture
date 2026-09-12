"""Counts real code lines per variant — blanks, comments and docstrings out.

The interesting figure is not the total but the split: how much of each variant
is toolkit binding, and how much is the domain logic all three share.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GROUPS = {
    "gemeinsamer Kern": ["core/model.py", "core/data.py", "core/query.py"],
    "Qt-Variante": ["variant_qt/app.py"],
    "Tkinter-Variante": ["variant_tk/app.py"],
    "Web-Variante": ["variant_web/app.py", "variant_web/templates/index.html"],
}


def python_code_lines(source: str) -> int:
    """Lines that are neither blank, nor a comment, nor part of a docstring."""
    documentation: set[int] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        if ast.get_docstring(node, clean=False) is None:
            continue
        first = node.body[0]
        documentation.update(range(first.lineno, (first.end_lineno or 0) + 1))

    return sum(
        1 for number, line in enumerate(source.splitlines(), 1)
        if line.strip() and not line.strip().startswith("#")
        and number not in documentation
    )


def cli_lines(source: str) -> int:
    """Lines in ``main()`` and the ``__main__`` guard — measurement scaffolding.

    Every variant carries an argparse entry point so the benchmark and the
    screenshot tool can drive it; that is harness, not application code.
    """
    documentation: set[int] = set()
    tree = ast.parse(source)
    counted: set[int] = set()
    for node in tree.body:
        is_main = isinstance(node, ast.FunctionDef) and node.name == "main"
        is_guard = isinstance(node, ast.If) and ast.unparse(node.test).startswith(
            "__name__")
        if is_main or is_guard:
            counted.update(range(node.lineno, (node.end_lineno or 0) + 1))
        if is_main and ast.get_docstring(node, clean=False) is not None:
            first = node.body[0]
            documentation.update(range(first.lineno, (first.end_lineno or 0) + 1))

    return sum(
        1 for number, line in enumerate(source.splitlines(), 1)
        if number in counted and line.strip()
        and not line.strip().startswith("#") and number not in documentation
    )


def markup_lines(source: str) -> tuple[int, int]:
    """(markup, css) — the styling a desktop toolkit supplies on its own."""
    in_style = False
    markup = css = 0
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<style"):
            in_style = True
        if in_style:
            css += 1
        else:
            markup += 1
        if stripped.startswith("</style"):
            in_style = False
    return markup, css


def main() -> int:
    print(f"{'':<20}{'gesamt':>8}{'CLI':>6}{'App':>6}  Datei")
    totals: dict[str, tuple[int, int]] = {}
    for group, files in GROUPS.items():
        group_total = group_cli = 0
        for relative in files:
            path = ROOT / relative
            source = path.read_text(encoding="utf-8")
            if path.suffix == ".py":
                count, cli = python_code_lines(source), cli_lines(source)
                note = ""
            else:
                markup, css = markup_lines(source)
                count, cli = markup + css, 0
                note = f"  (davon {css} Zeilen CSS)"
            group_total += count
            group_cli += cli
            print(f"{'':<20}{count:>8}{cli:>6}{count - cli:>6}  {relative}{note}")
        totals[group] = (group_total, group_cli)
        print(f"{group:<20}{group_total:>8}{group_cli:>6}"
              f"{group_total - group_cli:>6}  ── Summe")
        print()

    print("UI-Code je Variante, ohne gemeinsamen Kern und ohne CLI-Gerüst:")
    ui_only = {k: v for k, v in totals.items() if k != "gemeinsamer Kern"}
    for group, (total, cli) in sorted(ui_only.items(), key=lambda i: i[1][0] - i[1][1]):
        print(f"  {group:<20}{total - cli:>6}")
    core_total, core_cli = totals["gemeinsamer Kern"]
    print(f"\ngemeinsamer Kern: {core_total - core_cli} Zeilen, "
          f"von allen drei Varianten unverändert benutzt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
