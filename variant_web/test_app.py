"""The whole variant under test — no window, no display, no toolkit driver.

Every assertion below is about behaviour a user sees: what the filter keeps,
which way the sort points, what the Ampel says, what an empty result looks
like and how much of a large result set actually reaches the browser.
"""

import pytest

from core.data import TODAY, generate
from core.model import Status, status_of
from variant_web.app import create_app


@pytest.fixture()
def client():
    """A page small enough that the 200-row cap does not interfere."""
    return create_app(generate(150)).test_client()


def _sample_ids(body: str) -> list[str]:
    return [chunk.split("</td>")[0]
            for chunk in body.split("<td>P-")[1:]]


def test_filter_narrows_the_result_set(client):
    everything = client.get("/").get_data(as_text=True)
    filtered = client.get("/?q=Cadmium").get_data(as_text=True)
    assert "Cadmium" in filtered
    assert "Nitrat" not in filtered
    assert filtered.count("<tr class=") == 15
    assert everything.count("<tr class=") == 150


def test_sort_direction_reverses_the_order(client):
    ascending = _sample_ids(client.get("/?sort=sample_id&dir=asc").get_data(as_text=True))
    descending = _sample_ids(client.get("/?sort=sample_id&dir=desc").get_data(as_text=True))
    assert ascending == sorted(ascending)
    assert descending == list(reversed(ascending))


def test_ampel_marks_every_row_with_its_state(client):
    body = client.get("/?status=fail").get_data(as_text=True)
    assert 'class="fail"' in body
    assert 'class="ok"' not in body
    assert "gesperrt" in body


def test_empty_state_is_rendered_when_nothing_matches(client):
    body = client.get("/?q=Plutonium").get_data(as_text=True)
    assert 'id="empty-state"' in body
    assert "Keine Probe entspricht dem Filter." in body
    assert "<tr class=" not in body


def test_large_result_set_is_capped_and_says_so():
    body = create_app(generate(20_000)).test_client().get("/").get_data(as_text=True)
    assert body.count("<tr class=") == 200
    assert "Anzeige auf 200 Zeilen begrenzt" in body
    assert "(20 000 Proben gesamt)" in body


def test_status_counts_match_the_domain_core():
    rows = generate(2_000)
    body = create_app(rows).test_client().get("/").get_data(as_text=True)
    blocked = sum(1 for s in rows if status_of(s, TODAY) is Status.FAIL)
    assert f"gesperrt {blocked}" in body
