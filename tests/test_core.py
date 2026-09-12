"""Tests for the shared domain core — no toolkit, no window, no display."""

from datetime import date, timedelta

import pytest

from core.data import TODAY, generate
from core.model import Sample, Status, status_of
from core.query import apply_query, summarise


def _sample(**overrides) -> Sample:
    base = dict(
        sample_id="P-2026-000001", matrix="Trinkwasser", analyte="Blei",
        value=0.010, target=0.010, tolerance=0.002, unit="mg/l",
        analyst="M. Krinninger", received=TODAY - timedelta(days=3),
        due=TODAY + timedelta(days=10),
    )
    base.update(overrides)
    return Sample(**base)


@pytest.mark.parametrize(
    "value, expected",
    [(0.010, Status.OK), (0.0116, Status.WARN), (0.0125, Status.FAIL)],
)
def test_ampel_follows_the_tolerance_band(value, expected):
    assert status_of(_sample(value=value), TODAY) is expected


def test_overdue_blocks_even_when_the_value_is_fine():
    overdue = _sample(due=TODAY - timedelta(days=1))
    assert status_of(overdue, TODAY) is Status.FAIL


def test_due_soon_warns():
    assert status_of(_sample(due=TODAY + timedelta(days=2)), TODAY) is Status.WARN


def test_generator_is_deterministic():
    assert generate(500) == generate(500)


def test_generator_covers_all_three_states():
    counts = summarise(generate(2_000))
    assert all(count > 0 for count in counts.values()), counts


def test_free_text_search_hits_every_identifying_field():
    rows = generate(2_000)
    for needle in ("blei", "boden", "krinninger", "p-2026-000007"):
        hits = apply_query(rows, text=needle)
        assert hits, needle
        assert len(hits) < len(rows), needle


def test_sort_direction_reverses_without_losing_rows():
    rows = generate(1_000)
    up = apply_query(rows, sort_key="value")
    down = apply_query(rows, sort_key="value", descending=True)
    assert [s.value for s in up] == sorted(s.value for s in rows)
    assert [s.sample_id for s in down] == [s.sample_id for s in reversed(up)] or \
        [s.value for s in down] == list(reversed([s.value for s in up]))


def test_status_filter_returns_only_that_state():
    rows = generate(2_000)
    failed = apply_query(rows, status=Status.FAIL)
    assert failed
    assert all(status_of(s, TODAY) is Status.FAIL for s in failed)


def test_query_never_mutates_the_source_list():
    rows = generate(200)
    before = list(rows)
    apply_query(rows, text="blei", sort_key="value", descending=True)
    assert rows == before


def test_date_arithmetic_uses_the_fixed_today():
    assert isinstance(TODAY, date)
    assert all(s.received <= s.due for s in generate(500))
