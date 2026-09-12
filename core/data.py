"""Deterministic synthetic data set, so every run measures the same rows."""

from __future__ import annotations

import random
from datetime import date, timedelta

from .model import Sample

SEED = 17025
TODAY = date(2026, 9, 12)

MATRICES = ("Trinkwasser", "Abwasser", "Boden", "Klärschlamm", "Luft", "Feststoff")
ANALYTES = (
    ("Blei", "mg/l", 0.010, 0.002),
    ("Cadmium", "mg/l", 0.003, 0.0008),
    ("Nitrat", "mg/l", 50.0, 5.0),
    ("Chrom VI", "mg/l", 0.050, 0.010),
    ("pH-Wert", "-", 7.4, 0.3),
    ("Leitfähigkeit", "µS/cm", 780.0, 60.0),
    ("TOC", "mg/l", 4.2, 0.6),
    ("Benzo(a)pyren", "µg/l", 0.010, 0.003),
    ("Kupfer", "mg/l", 2.0, 0.25),
    ("Ammonium", "mg/l", 0.50, 0.08),
)
ANALYSTS = (
    "M. Krinninger", "S. Bauer", "T. Vogel", "A. Reindl",
    "K. Sommer", "J. Haas", "L. Brandt", "D. Weiß",
)


def generate(count: int, seed: int = SEED, today: date = TODAY) -> list[Sample]:
    """Return ``count`` samples; identical for identical ``count`` and ``seed``.

    The mix is tuned to look like a real release backlog: most samples are
    clean, a fifth sits close to a limit, and a small share is out of tolerance
    or past its due date.
    """
    rng = random.Random(seed)
    samples: list[Sample] = []
    for i in range(count):
        analyte, unit, target, tolerance = ANALYTES[i % len(ANALYTES)]
        roll = rng.random()
        if roll < 0.08:                       # outside tolerance
            factor = rng.uniform(1.05, 2.4)
        elif roll < 0.26:                     # inside, but close to the limit
            factor = rng.uniform(0.75, 1.0)
        else:
            factor = rng.uniform(0.0, 0.6)
        value = target + rng.choice((-1, 1)) * factor * tolerance
        received = today - timedelta(days=rng.randint(0, 12))
        samples.append(
            Sample(
                sample_id=f"P-{2026}-{i + 1:06d}",
                matrix=MATRICES[rng.randrange(len(MATRICES))],
                analyte=analyte,
                value=round(value, 4),
                target=target,
                tolerance=tolerance,
                unit=unit,
                analyst=ANALYSTS[rng.randrange(len(ANALYSTS))],
                received=received,
                due=received + timedelta(days=rng.choice((7, 10, 14, 21))),
            )
        )
    return samples
