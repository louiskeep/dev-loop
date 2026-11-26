"""UI revision 1: the owner's tables, plain words, shipped record and top bar."""

import re
from pathlib import Path

from dash_fixture import ago, put_run, put_slice

from devloop import notes, slices, store
from devloop.dash import render, roadmap, view, words

WEB = Path(view.__file__).parent / "web"
GONE = (
    "SHELL",
    "Tools / min",
    "Product owner (you)",
    "Checklist",
    "Runs",
    "Recent tool calls",
    ">?<",
)


def build(proj):
    return view.build_view(proj, proc="/nonexistent-proc")


def page_of(proj, token="t"):
    return render.render_page(build(proj), token=token)


def test_removed_words_are_absent_from_the_page(proj, dash):
    page = page_of(proj)
    for text in GONE:
        assert text not in page, text
    # The drawer sources too: a drawer never shows tool calls or "Runs".
    assert "Work history" in page and "Before it can move on" in page


def test_every_phase_has_stage_words():
    for phase in slices.PHASES:
        assert words.stage(phase) and "?" not in words.stage(phase)
    assert words.stage("REMEDIATE", 2) == "Fixing review findings (round 2)"
    assert words.stage("DEVELOP", 2) == "Building"
    assert set(words.RISK) == set(slices.RISKS)


def test_hold_and_its_question_are_one_row(proj, dash):
    held = dash["held"]
    notes.ensure_hold_question(proj, held)
    v = build(proj)
    keys = [n["key"] for n in v["needs_you"]]
    assert keys.count(held) == 1 and f"hold:{held}" not in keys
    hold = next(n for n in v["needs_you"] if n["key"] == held)
    assert hold["answer_key"] == f"hold:{held}" and hold["slice"] == "HELD1"
    page = page_of(proj)
    assert page.count(f'data-open-key="hold:{held}"') == 1  # one Decide button


def test_decide_opens_the_question_and_stuck_opens_the_slice(proj, dash):
    page = page_of(proj)
    assert 'data-open-kind="q" data-open-key="q-owner"' in page
    v = build(proj)
    assert all(n["kind"] != "stuck" for n in v["needs_you"])  # stuck on the loop
    key = put_slice(
        proj,
        "ROWQ",
        "ASKS",
        "DEVELOP",
        last_moved_at=ago(hours=5),
        waiting_on="question q-unrelated",
    )
    v = build(proj)
    item = next(n for n in v["needs_you"] if n["key"] == key)
    assert item["kind"] == "stuck"
    page = render.render_page(v)
    assert f'data-open-kind="slice" data-open-key="{key}">Decide' in page


def test_stuck_on_the_loop_goes_to_the_work_table(proj, dash):
    v = build(proj)
    plan = next(c for r in v["rows"] for c in r["cards"] if c["key"] == dash["plan"])
    assert plan["stuck"] and plan["wait"] == "stuck"
    page = page_of(proj)
    assert "Nobody (stuck" in page and ">Waiting<" in page


def test_work_table_who_is_on_it(proj, dash):
    page = page_of(proj)
    assert re.search(r"Developer \(Claude[^)]*\) for <time", page)
    assert "Who is on it now" in page and ">Row<" in page and ">Next step<" in page
    assert "slices done" not in page  # owner: one plain row per slice, no group rows


def test_row_titles_come_from_the_roadmap(proj, dash, cfg_edit, tmp_path):
    doc = tmp_path / "ROADMAP.md"
    doc.write_text("# x\n")
    groups = tmp_path / "roadmap" / "groups"
    groups.mkdir(parents=True)
    (groups / "a.md").write_text(
        "| # | Item | Repo |\n|---|---|---|\n"
        "| ROW1 | Plain name of row one. More detail follows. | cli |\n"
    )
    cfg_edit(lambda c: c["roadmap"].update(path=str(doc)))
    v = build(proj)
    titles = {r["row"]: r["title"] for r in v["rows"]}
    assert titles["ROW1"] == "Plain name of row one"
    assert titles["ROW2"] is None  # falls back to the key on the page
    page = render.render_page(v)
    assert "Plain name of row one" in page and "<code>ROW2</code>" in page


def test_roadmap_titles_parser():
    text = (
        "| 24zj | PROP-SURFACE (audit, package B). Trim the surface. | engine |\n"
        "|---|---|---|\n| junk |\n"
    )
    out = roadmap.parse_titles(text)
    assert out["24zj"] == "PROP-SURFACE" and out["PROP-SURFACE"] == "PROP-SURFACE"


def test_problems_table_fields(proj, dash):
    notes.problem(proj, "p-open", impact="things break", fix="patch it", detail="more")
    notes.problem(proj, "p-open", impact="things break", fix="patch it", detail="more")
    notes.problem(proj, "p-fixed", impact="old pain", fix="done already")
    notes.problem_close(proj, "p-fixed") if hasattr(notes, "problem_close") else None
    v = build(proj)
    rows = {p["key"]: p for p in v["known_problems"]}
    assert rows["p-open"]["count"] == 2 and rows["p-open"]["open"]
    page = render.render_page(v)
    for text in ("things break", "patch it", "more", "Open", "2 times, last"):
        assert text in page
    assert "Known problems" in page and "Fix" in page and "Seen" in page


def _closed(proj, key, days_ago):
    path = store.state_path(proj) / "problems.json"
    data = store.read_json(path)
    e = data["problems"][key]
    e["open"], e["closed_at"] = False, ago(days=days_ago)
    store.write_json(path, data)


def test_fixed_problems_show_for_seven_days_only(proj, dash):
    notes.problem(proj, "fresh", impact="a", fix="b")
    notes.problem(proj, "stale", impact="c", fix="d")
    _closed(proj, "fresh", 2)
    _closed(proj, "stale", 9)
    v = build(proj)
    keys = [p["key"] for p in v["known_problems"]]
    assert "fresh" in keys and "stale" not in keys
    assert keys.index("disk-low") < keys.index("fresh")  # open first
    page = render.render_page(v)
    assert "Fixed on " in page and 'class="done"' in page


def test_shipped_parser_three_sections_and_a_malformed_one():
    text = (
        "# Recently shipped\n\nintro\n\n"
        "## 2026-11-23: rows closed\n\n- NESTED-FREEZE: engine #772 shipped.\n"
        "- **Engine #765:** a thing <b>x</b>\n\n"
        "## not a date: broken\n- skipped bullet\n\n"
        "## 2026-11-22: catch-up\n- one\n\n"
        "## 2026-13-45: bad date\n- skipped too\n\n"
        "## 2026-11-24: newest\n"
    )
    out = roadmap.parse_shipped(text)
    assert [s["date"] for s in out] == ["2026-11-24", "2026-11-23", "2026-11-22"]
    assert out[1]["bullets"][0].startswith("NESTED-FREEZE: engine #772")
    assert "**" not in out[1]["bullets"][1]
    assert out[0]["bullets"] == []


def test_shipped_reads_the_configured_doc_with_a_cap_and_cache(
    proj, dash, cfg_edit, tmp_path
):
    doc = tmp_path / "RECENTLY-SHIPPED.md"
    doc.write_text("## 2026-11-23: big win\n- done <script>alert(1)</script> #12\n")
    cfg_edit(lambda c: c.update(shipped_doc=str(doc)))
    page = page_of(proj)
    assert "big win" in page and "2026-11-23" in page
    assert "&lt;script&gt;" in page and "<script>alert" not in page and "#12" in page
    doc.write_text("## 2026-11-25: second\n- x\n")
    import os

    os.utime(doc, (1, 1))
    assert "second" in page_of(proj)
    doc.write_text("## 2026-11-26: huge\n" + "- b\n" * 2_000_000)
    assert len(roadmap._read(doc)) <= roadmap.MAX_BYTES


def test_without_shipped_doc_only_loop_merges_show(proj, dash):
    v = build(proj)
    assert v["recent"] == []
    assert "Merged" in render.render_page(v)


def test_every_source_has_a_plain_label(proj, dash):
    v = build(proj)
    assert all(s["label"] and s["label"] != "?" for s in v["sources"])
    for name in words.COMMANDS:
        assert words.command_label(name) == words.COMMANDS[name]
    assert words.command_label("odd one") == "Odd one"
    for bad in view.SOURCE_FILES:
        assert bad in words.FILES


def test_sources_line_says_ok_or_names_what_failed(proj, dash):
    page = page_of(proj)
    assert "1 data source could not be read: Node-b (command failed)" in page
    store.write_json(
        store.state_path(proj) / "node-status.json",
        {"board": {"ok": True, "at": ago()}},
    )
    (store.state_path(proj) / "merges.json").write_text("{not json")
    page = page_of(proj)
    assert "Merge log (damaged)" in page
    store.write_json(
        store.state_path(proj) / "node-status.json",
        {"board": {"ok": True, "at": ago()}},
    )
    (store.state_path(proj) / "merges.json").write_text("{}")
    assert "All 1 data source read OK." in page_of(proj)
    assert 'id="sources"' in page_of(proj)  # drawer sources only, no health section


def test_top_bar_chips_and_expand_state(proj, dash):
    page = page_of(proj)
    assert re.search(r"Developers <b>\d+/8</b>", page)
    assert "Memory <b>" in page and "Needs you" in page and "Merged today" in page
    assert 'data-toggle="topdetail"' in page and 'id="topdetail"' in page
    assert re.search(r'aria-expanded="false"[^>]*aria-controls="topdetail"', page)
    assert '<div id="topdetail" class="panel" hidden>' in page
    js = (WEB / "dash.js").read_text()
    assert 'store("dash-topdetail"' in js and "applyPanel" in js
    assert js.count("applyPanel()") >= 3  # start, after each swap, after a click


def test_drawer_source_wording(proj, dash):
    run = put_run(
        proj,
        "test.done",
        kind="test",
        key=dash["gate"],
        level="t1",
        started=ago(minutes=30),
        exit_={"code": 0, "reason": "exit", "ended_at": ago(minutes=20)},
    )
    (store.out_dir(run) / "result.md").write_text(
        "status: FAILED\ntests: 2 failing tests\n---\nbody\n"
    )
    page = page_of(proj)
    m = re.search(
        rf'<div id="ds-{dash["gate"]}".*?</div>(?=<div id="ds-|</section>)', page, re.S
    )
    drawer = m.group(0)
    assert "QA ran the tests, failed (2 failing tests), 10m" in drawer
    assert "medium risk" in drawer and "Tests pass on the latest code: " in drawer
    assert "What this step needs before the slice moves to the next stage." in drawer


def test_no_em_dash_in_new_modules():
    for name in ("roadmap.py", "words.py", "render_tables.py"):
        assert "\u2014" not in (WEB.parent / name).read_text()
