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


PLAN = """# 9z KEY-ONE: stop the dashboard from hiding stuck slices

Status: plan (revision 2)
Revision: 2
Code head this revision is checked against: abc1234. Line numbers are from that head.

## 0. Changes in this revision

Revision 2 folds a review: eight should-fix items and a Problem list rewrite.

## 1. Frame

### 1.1 Problem

The owner missed a stuck slice for a day. It stayed hidden in a long list. A third
sentence stays out.

## 2. Guarantee and non-goals
Do things.
"""


def _plan(tmp_path, text):
    p = tmp_path / "plan.md"
    p.write_text(text)
    return str(p)


def test_plan_what_from_title_and_why_from_problem(tmp_path):
    out = roadmap.plan_summary(_plan(tmp_path, PLAN), 300)
    assert out == (
        "Stop the dashboard from hiding stuck slices. Why: The owner missed a "
        "stuck slice for a day. It stayed hidden in a long list."
    )


def test_problem_heading_any_level_and_numbering(tmp_path):
    text = "# T: do it\n\n## 1. Problem\n\nRevision 3 note.\n\nThe real reason.\n"
    assert roadmap.plan_summary(_plan(tmp_path, text), 300) == (
        "Do it. Why: The real reason."
    )


def test_skipped_sections_and_lines_never_give_the_why(tmp_path):
    text = (
        "# T: do it\n\n## Revision 2 changes to the Problem\n\nNot this.\n\n"
        "## Problem\n\nStatus: plan\n\nCode head this is.\n\nRevision 2 folds.\n"
    )
    assert roadmap.plan_summary(_plan(tmp_path, text), 300) == "Do it."


def test_title_without_colon_drops_key_tokens(tmp_path):
    text = "# 24zk PROP-SHIMS D6 manifest half refuses a bad entry\n"
    out = roadmap.plan_summary(_plan(tmp_path, text), 300, ("ROW", "D6"))
    assert out == "Manifest half refuses a bad entry."


def test_imported_title_with_only_keys_gives_nothing(tmp_path):
    text = "# F31-AUDIT AE1a1 (imported from /loop)\n"
    assert (
        roadmap.plan_summary(_plan(tmp_path, text), 300, ("F31-AUDIT", "AE1a1")) is None
    )


def test_long_title_is_capped_at_a_word(tmp_path):
    text = "# T: " + "word " * 200 + "\n"
    out = roadmap.plan_summary(_plan(tmp_path, text), 300)
    assert len(out) <= 303 and out.endswith("...")


def test_plan_missing_or_empty_gives_none(tmp_path):
    assert roadmap.plan_summary(str(tmp_path / "gone.md"), 300) is None
    assert roadmap.plan_summary(None, 300) is None
    assert roadmap.plan_summary(str(tmp_path), 300) is None  # a directory
    assert roadmap.plan_summary(_plan(tmp_path, "Status: x\n"), 300) is None


def test_row_description_drops_the_name_and_audit_note():
    text = (
        "| 24zk | PROP-SHIMS (audit 2026-11-15; owner call): re-exports go. Done. | x |"
    )
    assert roadmap.parse_descriptions(text)["24zk"] == "Re-exports go. Done."


def test_duty_rows_get_a_fixed_line(proj):
    put_slice(proj, "DUTY-RELEASE", "2026-11-25", "DEVELOP")
    put_slice(proj, "DUTY-ROADMAP", "2026-11-25", "DEVELOP")
    v = view.build_view(proj, proc="/nonexistent-proc")
    rows = {r["row"]: r for r in v["rows"]}
    assert rows["DUTY-RELEASE"]["desc"] == "Evening engine release"
    assert rows["DUTY-RELEASE"]["title"] == "Evening engine release"
    assert rows["DUTY-ROADMAP"]["desc"] == "Daily roadmap update"


def test_slice_description_from_plan_then_purpose(proj, tmp_path):
    plan = _plan(tmp_path, PLAN)
    a = put_slice(proj, "R1", "A", "PLAN", plan_path=plan, purpose="from purpose")
    b = put_slice(proj, "R1", "B", "PLAN", purpose="from purpose")
    c = put_slice(proj, "R1", "C", "PLAN")
    d = put_slice(proj, "R1", "D", "PLAN", purpose="R1 D")  # only the names again
    v = view.build_view(proj, proc="/nonexistent-proc")
    cards = {x["key"]: x for r in v["rows"] for x in r["cards"]}
    assert cards[a]["slice_desc"].startswith("Stop the dashboard")
    assert cards[b]["slice_desc"] == "from purpose"
    assert cards[c]["slice_desc"] is None
    assert cards[d]["slice_desc"] is None


def test_plan_text_is_scrubbed(proj, tmp_path):
    plan = _plan(tmp_path, "# T: read /etc/shadow with ghp_PLANTEDSECRET1 now\n")
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


def test_why_that_leads_into_a_list_ends_with_a_period(tmp_path):
    text = "# T: do it\n\n## 1. Problem\n\nThe engine reads cells in many ways today:\n\n- a\n"
    assert roadmap.plan_summary(_plan(tmp_path, text), 300) == (
        "Do it. Why: The engine reads cells in many ways today."
    )
