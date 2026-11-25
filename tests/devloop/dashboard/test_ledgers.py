"""A damaged ledger is marked unreadable once; the rest of the dashboard stays."""

import json

import pytest
from dash_fixture import dash  # noqa: F401
from test_dash_view import build, card

from devloop import store


def write(proj, name, doc):
    (store.state_dir(proj) / name).write_text(json.dumps(doc))


def unreadable(v):
    return {n["key"] for n in v["problems"] if n["kind"] == "unreadable"}


def row_closes(v):
    return [e for e in v["shipped"] if e["kind"] == "row-close"]


def merges(v):
    return [e for e in v["shipped"] if e["kind"] == "merge"]


@pytest.mark.parametrize("bad", [[1], ["x"], "text", 5])
def test_wrong_merge_container_marks_merges_unreadable(proj, dash, bad):
    write(proj, "merges.json", {"merges": bad})
    v = build(proj)
    assert "merges" in unreadable(v) and "closures" not in unreadable(v)
    assert merges(v) == [] and row_closes(v)
    assert v["counts"]["merged_today"] is None and v["counts"]["rows_closed"] == 1
    assert card(v, dash["gate"])


@pytest.mark.parametrize("closed", ["false", "yes", 1, [], {}])
def test_wrong_typed_closed_mark_makes_the_closures_unreadable(proj, dash, closed):
    write(proj, "closures.json", {"closures": {"ROW1": {"closed": closed}}})
    v = build(proj)
    assert "closures" in unreadable(v) and "merges" not in unreadable(v)
    assert row_closes(v) == [] and merges(v)
    assert v["counts"]["rows_closed"] is None
    assert v["counts"]["merged_today"] is not None
    assert card(v, dash["gate"])


def test_wrong_closures_container_marks_closures_unreadable(proj, dash):
    write(proj, "closures.json", {"closures": ["ROW1"]})
    v = build(proj)
    assert "closures" in unreadable(v) and row_closes(v) == []
    assert v["counts"]["rows_closed"] is None


def test_healthy_ledgers_are_not_marked(proj, dash):
    v = build(proj)
    assert not (unreadable(v) & {"merges", "closures"})
    assert merges(v) and row_closes(v)
