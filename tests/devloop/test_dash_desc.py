"""Drawer descriptions: what a row and a slice are, and why, in plain words."""

import re
from pathlib import Path

from dash_fixture import put_slice

from devloop.dash import render, roadmap, view

GROUP = (
    """
| # | Item | Notes |
|---|------|-------|
| 24zj | **THING-ONE:** Make the [report](docs/r.md) faster with `fast mode`. It cuts the wait. Users asked for it. The fourth sentence stays out. | x |
| LONG-ROW | A very long first sentence """
    + "word " * 120
    + """ends here. Short. | x |
| ONE-LINE | Only one sentence here | x |
"""
)


def test_row_description_takes_up_to_three_sentences_and_cleans_markup():
    d = roadmap.parse_descriptions(GROUP)
    assert d["24zj"] == (
        "Make the report faster with fast mode. It cuts the wait. Users asked for it."
    )
    assert d["THING-ONE"] == d["24zj"]
    assert d["ONE-LINE"] == "Only one sentence here"


def test_row_description_is_capped_at_a_boundary():
    d = roadmap.parse_descriptions(GROUP)["LONG-ROW"]
    assert len(d) <= roadmap.DESC_LIMIT + 3
    assert d.endswith("...") and not d.rstrip(".").endswith(" ")
    assert " ".join(d[:-3].split()) == d[:-3]  # cut between words


def test_sentences_fill_up_to_the_cap_only():
    text = "A" * 200 + ". " + "B" * 150 + ". " + "C" * 150 + ". Tail."
    out = roadmap.describe(text, 400)
    assert out == "A" * 200 + ". " + "B" * 150 + "."


def test_unknown_row_has_no_description(proj, cfg_edit, tmp_path):
    (tmp_path / "ROADMAP.md").write_text("# roadmap\n")
    g = tmp_path / "roadmap" / "groups"
    g.mkdir(parents=True)
    (g / "a.md").write_text(GROUP)
    cfg_edit(lambda c: c["roadmap"].update(path=str(tmp_path / "ROADMAP.md")))
    put_slice(proj, "NOPE", "S1", "PLAN")
    put_slice(proj, "24zj", "S2", "PLAN")
    v = view.build_view(proj, proc="/nonexistent-proc")
    rows = {r["row"]: r for r in v["rows"]}
    assert rows["NOPE"]["desc"] is None
    assert rows["24zj"]["desc"].startswith("Make the report faster")
    page = render.render_page(v)
    assert "?" not in re.search(r'<div id="ds-NOPE--S1".*?</div>', page, re.S).group(0)


PLAN = """# 9z Title of the plan

Status: plan (revision 2)
Owner: Cam

**Goal:** Stop the dashboard from hiding stuck slices.

**Why:** The owner missed a stuck slice for a day.

## Steps
Do things.
"""


def _plan(tmp_path, text):
    p = tmp_path / "plan.md"
    p.write_text(text)
    return str(p)


def test_plan_goal_and_why(tmp_path):
    out = roadmap.plan_summary(_plan(tmp_path, PLAN), 300)
    assert out == (
        "Stop the dashboard from hiding stuck slices. "
        "The owner missed a stuck slice for a day."
    )


def test_plan_first_paragraph_skips_metadata(tmp_path):
    text = "# T\n\nStatus: plan\nRisk: R1\n\nThis slice moves the thing. Because.\n\n## B\n"
    assert roadmap.plan_summary(_plan(tmp_path, text), 300) == (
        "This slice moves the thing. Because."
    )


def test_plan_goal_heading_and_cap(tmp_path):
    text = "# T\n\n## Goal\n\n" + "word " * 200 + "\n"
    out = roadmap.plan_summary(_plan(tmp_path, text), 300)
    assert len(out) <= 303 and out.endswith("...")


def test_plan_missing_or_empty_gives_none(tmp_path):
    assert roadmap.plan_summary(str(tmp_path / "gone.md"), 300) is None
    assert roadmap.plan_summary(None, 300) is None
    assert roadmap.plan_summary(str(tmp_path), 300) is None  # a directory
    assert roadmap.plan_summary(_plan(tmp_path, "# T\n\nStatus: x\n"), 300) is None


def test_slice_description_from_plan_then_purpose(proj, tmp_path):
    plan = _plan(tmp_path, PLAN)
    a = put_slice(proj, "R1", "A", "PLAN", plan_path=plan, purpose="from purpose")
    b = put_slice(proj, "R1", "B", "PLAN", purpose="from purpose")
    c = put_slice(proj, "R1", "C", "PLAN")
    v = view.build_view(proj, proc="/nonexistent-proc")
    cards = {x["key"]: x for r in v["rows"] for x in r["cards"]}
    assert cards[a]["slice_desc"].startswith("Stop the dashboard")
    assert cards[b]["slice_desc"] == "from purpose"
    assert cards[c]["slice_desc"] is None


def test_plan_text_is_scrubbed(proj, tmp_path):
    plan = _plan(
        tmp_path, "# T\n\n**Goal:** Read /etc/shadow with ghp_PLANTEDSECRET1 now.\n"
    )
    k = put_slice(proj, "R1", "A", "PLAN", plan_path=plan)
    v = view.build_view(proj, proc="/nonexistent-proc")
    text = str(v)
    assert "/etc/shadow" not in text and "ghp_PLANTEDSECRET1" not in text
    assert k in text


def test_drawer_leads_with_descriptions_above_the_gate_list(proj, tmp_path):
    plan = _plan(tmp_path, PLAN)
    put_slice(proj, "R1", "A", "PLAN", plan_path=plan)
    v = view.build_view(proj, proc="/nonexistent-proc")
    v["rows"][0]["desc"] = "Row text here."
    v["rows"][0]["title"] = "Row name"
    page = render.render_page(v)
    d = re.search(r'<div id="ds-R1--A".*?Work history', page, re.S).group(0)
    order = [
        d.index("Row: Row name"),
        d.index("Row text here."),
        d.index("Slice: A"),
        d.index("Stop the dashboard"),
        d.index("Before it can move on"),
    ]
    assert order == sorted(order)


def test_work_history_entries_are_separate_items(proj):
    k = put_slice(proj, "R1", "A", "PLAN")
    v = view.build_view(proj, proc="/nonexistent-proc")
    run = {
        "ref": "w000001",
        "role": "Developer",
        "icon": "build",
        "harness": "claude",
        "started_at": v["tick"]["last_pass_at"],
        "ended_at": v["tick"]["last_pass_at"],
        "summary": "built it",
        "status": "DONE",
        "verdict": "GO",
    }
    v["drawers"] = [{"key": k, "runs": [run, run, run], "calls": [], "files": []}]
    page = render.render_page(v)
    d = re.search(r'<div id="ds-R1--A".*?</div>', page, re.S).group(0)
    hist = re.search(r'<ul class="list history">(.*?)</ul>', d, re.S).group(1)
    assert hist.count("<li>") == 3
    css = (Path(view.__file__).parent / "web" / "dash.css").read_text()
    assert ".history li" in css
