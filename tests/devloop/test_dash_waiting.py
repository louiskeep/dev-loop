"""QA waits stay in work in progress; the bottom section is only waits on other work."""

import re

from dash_fixture import dash, put_slice  # noqa: F401

from devloop import slices
from devloop.dash import render, view


def build(proj):
    return view.build_view(proj, proc="/nonexistent-proc")


def section(page, name):
    return re.search(rf'<section id="{name}".*?</section>', page, re.S).group(0)


def test_is_qa_wait_checks_phase_and_slot_wording():
    s = {"phase": "PARKED", "waiting_on": "tester slot"}
    assert view.is_qa_wait(s)
    assert not view.is_qa_wait({**s, "phase": "HELD"})
    assert not view.is_qa_wait({**s, "waiting_on": "builder slot"})
    assert not view.is_qa_wait({**s, "waiting_on": None})


def test_qa_wait_shows_in_work_in_progress_not_the_bottom(proj):
    k = put_slice(
        proj,
        "R1",
        "QA1",
        "PARKED",
        parked_from="VERIFY",
        park_reason="stuck",
        waiting_on="tester slot",
    )
    v = build(proj)
    assert k not in [c["key"] for c in v["parked"]]
    cards = {c["key"]: c for r in v["rows"] for c in r["cards"]}
    assert cards[k]["phase"] == "VERIFY" and cards[k]["wait"] == "slot"
    page = render.render_page(v)
    row = re.search(rf'<tr class="slice"[^>]*data-card-key="{k}".*?</tr>', page, re.S)
    assert "Waiting for a QA slot" in row.group(0)
    assert "QA1" not in section(page, "parked")


def test_bottom_section_is_renamed_and_lists_waits(proj):
    a = put_slice(proj, "LOC", "E-S1", "DEVELOP")
    b = put_slice(
        proj,
        "R2",
        "B",
        "PARKED",
        parked_from="PLAN",
        park_reason="stuck",
        waiting_on="LOC E-S1 to merge",
    )
    page = render.render_page(build(proj))
    sec = section(page, "parked")
    assert "Waiting on other work" in sec and "Parked and held" not in page
    for head in ("Row", "Slice", "Waiting for", "Since"):
        assert f"<th>{head}</th>" in sec
    assert f'data-open-kind="slice" data-open-key="{a}"' in sec
    assert "LOC E-S1 to merge" in sec and b not in sec


def test_double_dash_key_links_too(proj):
    a = put_slice(proj, "LOC", "E-S1", "DEVELOP")
    put_slice(
        proj,
        "R2",
        "B",
        "PARKED",
        parked_from="PLAN",
        park_reason="stuck",
        waiting_on=f"{a} first",
    )
    sec = section(render.render_page(build(proj)), "parked")
    assert f'data-open-key="{a}"' in sec


def test_unknown_wait_is_plain_text(proj):
    put_slice(
        proj,
        "R2",
        "B",
        "PARKED",
        parked_from="PLAN",
        park_reason="stuck",
        waiting_on="some outside thing",
    )
    sec = section(render.render_page(build(proj)), "parked")
    assert "some outside thing" in sec and "data-open-key" not in sec


def test_owner_holds_stay_out_of_the_bottom_section(proj, dash):
    v = build(proj)
    page = render.render_page(v)
    sec = section(page, "parked")
    for key in (dash["held"], dash["parked"]):  # a hold and an unanswered owner park
        assert any(n["key"] == key for n in v["needs_you"])
        assert key.split("--")[1] not in sec
    assert "Nothing is waiting on other work." in sec


def test_released_owner_park_shows_as_released(proj, dash):
    s = slices.load(proj, dash["parked"])
    s["owner_answered"] = True
    slices._save(proj, s)
    sec = section(render.render_page(build(proj)), "parked")
    assert "Released by you, resumes on the next tick" in sec
    assert "PARKED1" in sec


def test_parked_row_without_active_slice_shows_the_roadmap_title(
    proj, cfg_edit, tmp_path
):
    (tmp_path / "ROADMAP.md").write_text("# r\n")
    g = tmp_path / "roadmap" / "groups"
    g.mkdir(parents=True)
    (g / "a.md").write_text("| PARKROW | Plain row name. More. | x |\n")
    cfg_edit(lambda c: c["roadmap"].update(path=str(tmp_path / "ROADMAP.md")))
    put_slice(
        proj,
        "PARKROW",
        "S1",
        "PARKED",
        parked_from="PLAN",
        park_reason="stuck",
        waiting_on="something outside",
    )
    sec = section(render.render_page(build(proj)), "parked")
    assert "Plain row name" in sec


def _old_verify(proj):
    from dash_fixture import ago

    return put_slice(proj, "R9", "Q", "VERIFY", last_moved_at=ago(hours=3))


def test_a_slice_queued_for_a_full_slot_is_waiting_not_stuck(proj, monkeypatch):
    k = _old_verify(proj)
    monkeypatch.setattr(view.runs, "slot_usage", lambda project: {"tester": 4})
    cards = {c["key"]: c for r in build(proj)["rows"] for c in r["cards"]}
    assert cards[k]["stuck"] is False
    assert (cards[k]["wait"], cards[k]["wait_for"]) == ("slot", "QA")


def test_a_slice_with_a_free_slot_and_no_move_is_stuck(proj, monkeypatch):
    k = _old_verify(proj)
    monkeypatch.setattr(view.runs, "slot_usage", lambda project: {"tester": 1})
    cards = {c["key"]: c for r in build(proj)["rows"] for c in r["cards"]}
    assert cards[k]["stuck"] is True and cards[k]["wait"] == "stuck"


def test_a_slice_whose_test_just_ran_is_not_stuck(proj, monkeypatch):
    from dash_fixture import ago, put_run

    k = _old_verify(proj)
    put_run(proj, "test.recent", kind="test", key=k, started=ago(minutes=40), exit_=1)
    monkeypatch.setattr(view.runs, "slot_usage", lambda project: {"tester": 1})
    cards = {c["key"]: c for r in build(proj)["rows"] for c in r["cards"]}
    assert cards[k]["stuck"] is False
