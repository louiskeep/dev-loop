"""The dashboard read model: what it holds, what it leaves out, and how it degrades."""

import datetime
import html
import json
import os

import pytest
from dash_fixture import AGENT_NAMES, ago, dash, put_run, put_slice  # noqa: F401

from devloop import notes, slices, store
from devloop.dash import render, view

PLANTED = ("password hunter2", "user:pw@host", "/etc/shadow", "ghp_PLANTEDSECRET1")


def build(proj, **kw):
    return view.build_view(proj, proc="/nonexistent-proc", **kw)


def card(v, key):
    for row in v["rows"]:
        for c in row["cards"]:
            if c["key"] == key:
                return c
    for c in v["parked"]:
        if c["key"] == key:
            return c
    raise AssertionError(f"no card {key}")


def test_view_keys_match_schema(proj, dash):
    v = build(proj)
    assert set(v) == set(view.VIEW_SCHEMA) and v["schema"] == 2
    bad = dict(v, purpose="free text")
    with pytest.raises(view.ViewError):
        view._seal(bad, view.VIEW_SCHEMA, set())
    nested = json.loads(json.dumps(v))
    nested["rows"][0]["cards"][0]["next_action"] = "x"
    with pytest.raises(view.ViewError):
        view._seal(nested, view.VIEW_SCHEMA, set())


def test_word_outside_the_table_is_refused(proj, dash):
    v = json.loads(json.dumps(build(proj)))
    v["rows"][0]["cards"][0]["phase"] = "purpose of the slice"
    labels = view._labels(
        __import__("devloop.project", fromlist=["x"]).load_project(proj)
    )
    with pytest.raises(view.ViewError):
        view._seal(v, view.VIEW_SCHEMA, labels)


def test_version_is_stable_and_changes_with_state(proj, dash):
    a, b = build(proj), build(proj)
    assert a["version"] == b["version"] and len(a["version"]) == 16
    s = slices.load(proj, dash["frame"])
    s["phase"] = "PLAN"
    slices._save(proj, s)
    assert build(proj)["version"] != a["version"]


def test_needs_you_present_with_empty_state(proj):
    v = build(proj)
    assert v["needs_you"] == [] and v["rows"] == [] and v["parked"] == []
    assert v["counts"]["needs_you"] == 0 and v["memory"] is None


def test_needs_you_lists_only_the_owners_questions_and_holds(proj, dash):
    v = build(proj)
    kinds = {(n["kind"], n["key"]) for n in v["needs_you"]}
    assert ("question", "q-owner") in kinds
    assert ("hold", dash["held"]) in kinds
    assert ("hold", dash["parked"]) in kinds  # an owner park
    assert {k for k, _ in kinds} == {"question", "hold"}
    assert {n["owner"] for n in v["needs_you"]} == {"Product owner (you)"}
    assert v["counts"]["needs_you"] == len(v["needs_you"]) == 3


def test_a_question_item_carries_its_text_options_and_answer_file(proj, dash):
    q = next(n for n in build(proj)["needs_you"] if n["kind"] == "question")
    assert q["question"] == "Pick a thing" and q["options"] == ["a", "b"]
    assert q["recommended"] == "a"
    assert q["answer_at"] == notes.questions_path(proj).name
    assert "/" not in q["answer_at"]


def test_known_problems_have_their_own_tech_lead_panel(proj, dash):
    v = build(proj)
    kinds = {(n["kind"], n["key"]) for n in v["problems"]}
    assert ("problem", "disk-low") in kinds and ("stuck", dash["plan"]) in kinds
    assert {n["owner"] for n in v["problems"]} == {"Tech lead"}
    assert v["counts"]["problems"] == len(v["problems"])


def test_one_question_and_three_problems(proj):
    notes.ask(
        proj, "q1", question="Which?", options=["x", "y"], recommended="x", wait=True
    )
    for key in ("p1", "p2", "p3"):
        notes.problem(proj, key, impact="i", fix="f")
    v = build(proj)
    assert v["counts"]["needs_you"] == 1 and len(v["needs_you"]) == 1
    assert v["counts"]["problems"] == 3 and len(v["problems"]) == 3
    page = render.render_page(v)
    assert 'id="problems"' in page and "Known problems" in page
    assert "Which?" in page and ">x<" in page and "Decide" in page
    assert 'data-open-key="q1"' in page and "No fix recorded" not in page


def test_stuck_flag_after_stale_secs(proj, dash):
    v = build(proj)
    assert card(v, dash["plan"])["stuck"] is True  # 5 h, no executing run
    assert card(v, dash["review"])["stuck"] is False  # recent
    s = slices.load(proj, dash["review"])
    s["last_moved_at"] = ago(hours=9)
    slices._save(proj, s)
    # A live run on the slice means it is working, not stuck.
    assert card(build(proj), dash["review"])["stuck"] is False


def test_cards_sorted_stuck_then_oldest(proj, dash):
    v = build(proj)
    row1 = next(r for r in v["rows"] if r["row"] == "ROW1")
    assert [c["slice"] for c in row1["cards"]][0] == "PLANNED"  # stuck first
    assert v["rows"][0]["row"] == "ROW1" and v["rows"][0]["stuck"] is True
    rest = [c["since"] for c in row1["cards"][1:]]
    assert rest == sorted(rest)


def test_checklist_lists_missing_first(proj, dash):
    c = card(build(proj), dash["gate"])
    items = c["checklist"]["items"]
    assert c["checklist"]["available"] and c["checklist"]["total"] == len(items)
    oks = [i["ok"] for i in items]
    assert oks == sorted(oks)  # False before True
    assert c["checklist"]["met"] == sum(oks)
    reviews = [i for i in items if i["code"] == "code-review"]
    assert {i["role"] for i in reviews} == {"Senior reviewer", "Outside reviewer"}


def test_fresh_frame_card_renders_nulls(proj, dash):
    c = card(build(proj), dash["frame"])
    assert c["risk"] is None and c["pr"] is None and c["url"] is None
    assert c["phase"] == "FRAME" and c["phase_index"] == 0 and c["held"] is False


def test_held_and_parked_cards_show_their_parked_from_phase(proj, dash):
    v = build(proj)
    held, parked = card(v, dash["held"]), card(v, dash["parked"])
    assert (held["phase"], held["held"]) == ("PLAN", True)
    assert (parked["phase"], parked["held"]) == ("DEVELOP", True)
    assert held["owner"] == "Product owner (you)"
    assert held["checklist"]["available"] is False


def test_card_owner_is_role_label(proj, dash):
    v = build(proj)
    assert card(v, dash["gate"])["owner"] == "Tech lead"  # no executing run
    # An executing builder run owns its card only when this process counts as alive.
    c = card(v, dash["review"])
    assert c["owner"] == "Developer"


def test_pr_url_comes_from_config_slug(proj, dash):
    c = card(build(proj), dash["review"])
    assert c["url"] == "https://github.com/own/main/pull/5" and c["pr"] == 5


def test_exited_builder_and_lost_test_are_listed_by_state(proj, dash):
    v = build(proj)
    states = {(w["role"], "executing") for w in v["live"]} | {
        (w["role"], w["state"]) for w in v["waiting_runs"]
    }
    assert states == {
        ("Developer", "executing"),
        ("Developer", "awaiting-apply"),
        ("QA", "awaiting-release"),
    }
    # Neither unfinished run owns a card.
    assert card(v, dash["develop"])["owner"] == "Tech lead"
    assert card(v, dash["gate"])["owner"] == "Tech lead"
    used = {s["pool"]: s["used"] for s in v["slots"]}
    assert used == {"builder": 2, "tester": 1, "reviewer": 0, "planner": 0}
    assert v["counts"]["executing"] == 1


def test_stuck_shows_while_a_finished_run_awaits_apply(proj, dash):
    s = slices.load(proj, dash["develop"])
    s["last_moved_at"] = ago(hours=4)
    slices._save(proj, s)
    assert card(build(proj), dash["develop"])["stuck"] is True


def test_slots_read_per_role_pool(proj, dash):
    slots = {s["pool"]: s for s in build(proj)["slots"]}
    assert slots["builder"]["label"] == "Developers" and slots["builder"]["cap"] == 8
    assert (
        slots["tester"]["label"] == "QA" and slots["reviewer"]["label"] == "Reviewers"
    )
    assert slots["planner"]["label"] == "Architects"


def test_roles_config_changes_the_labels(proj, dash, cfg_edit):
    cfg_edit(lambda c: c.update(roles={"builder": "Engineer", "tick": "Lead"}))
    v = build(proj)
    assert card(v, dash["review"])["owner"] == "Engineer"
    assert card(v, dash["gate"])["owner"] == "Lead"


def test_shipped_merges_and_closed_rows_only(proj, dash):
    shipped = build(proj)["shipped"]
    kinds = [e["kind"] for e in shipped]
    assert kinds.count("merge") == 3 and kinds.count("row-close") == 1
    assert all(e["row"] != "ROW9" for e in shipped)  # attempted, not closed
    assert [e["at"] for e in shipped] == sorted(
        (e["at"] for e in shipped), reverse=True
    )
    close = next(e for e in shipped if e["kind"] == "row-close")
    assert close["slice"] is None and close["pr"] is None


def test_merge_with_unknown_slug_has_no_url(proj, dash):
    by_pr = {e["pr"]: e for e in build(proj)["shipped"] if e["kind"] == "merge"}
    assert by_pr[5]["url"] == "https://github.com/own/main/pull/5"
    assert by_pr[9]["url"] is None


def test_non_integer_pr_in_the_ledger_is_dropped(proj, dash):
    path = store.state_dir(proj) / "merges.json"
    data = json.loads(path.read_text())
    data["merges"]["own/main#x@ddd4"] = {
        "row": "R",
        "slice": "S",
        "pr": "7/../../x",
        "at": ago(minutes=1),
    }
    path.write_text(json.dumps(data))
    assert all(e["pr"] != "7/../../x" for e in build(proj)["shipped"])


def test_orphan_run_listed_without_card(proj, dash):
    put_run(proj, "builder.orphan", kind="builder", key="GONE--S", alive=True)
    v = build(proj)
    assert any(w["key"] == "GONE--S" for w in v["live"])
    assert not any(w["slice_active"] for w in v["live"] if w["key"] == "GONE--S")
    assert all(c["key"] != "GONE--S" for r in v["rows"] for c in r["cards"])


def test_bad_merge_ledger_keeps_cards(proj, dash):
    (store.state_dir(proj) / "merges.json").write_text("{not json")
    v = build(proj)
    assert card(v, dash["gate"]) and v["shipped"][0]["kind"] == "row-close"
    assert ("unreadable", "merges") in {(n["kind"], n["key"]) for n in v["problems"]}
    assert v["counts"]["merged_today"] is None and v["counts"]["rows_closed"] == 1


def test_bad_sibling_beside_gate_card(proj, dash):
    (store.state_dir(proj) / "slices" / "ROW2--BROKEN.json").write_text("{nope")
    v = build(proj)
    # The gate check reads every sibling and fails closed, so this checklist reads
    # unavailable; the card, the other cards and the rest of the page still render.
    assert card(v, dash["gate"])["checklist"]["available"] is False
    assert card(v, dash["frame"])["checklist"]["available"] is True
    assert ("unreadable", "ROW2--BROKEN") in {
        (n["kind"], n["key"]) for n in v["problems"]
    }


def test_a_checklist_that_raises_reads_unavailable(proj, dash, monkeypatch):
    def boom(project, key, to=None):
        raise KeyError("x")

    monkeypatch.setattr(slices, "exit_items", boom)
    c = card(build(proj), dash["gate"])
    assert c["checklist"] == {"available": False, "met": 0, "total": 0, "items": []}


def test_build_view_writes_nothing(proj, home, cfg_edit, monkeypatch):
    put_slice(proj, "ROW1", "ONE", "PLAN", risk="R1")
    assert not (home / "p" / "runs").exists()

    def refuse(*a, **k):
        raise AssertionError("the view wrote or locked")

    monkeypatch.setattr(store, "locked", refuse)
    monkeypatch.setattr(store, "write_text", refuse)
    monkeypatch.setattr(store, "write_json", refuse)

    def listing():
        return sorted(
            (str(p.relative_to(home)), p.stat().st_mtime_ns) for p in home.rglob("*")
        )

    before = listing()
    assert build(proj)["rows"]
    assert listing() == before
    assert not (home / "p" / "runs").exists()


def test_activity_uses_fixed_wording_and_role_labels(proj, dash):
    words = [a["words"] for a in build(proj)["activity"]]
    assert "merged ROW0 M1" in words
    assert any(w.startswith("started Developer on") for w in words)


def test_no_agent_names_or_run_ids_in_the_view(proj, dash):
    text = json.dumps(build(proj))
    for name in (*AGENT_NAMES, "builder.live", "builder.done", "test.lost", "dennis"):
        assert name not in text
    roles = {
        i["role"]
        for r in build(proj)["rows"]
        for c in r["cards"]
        for i in c["checklist"]["items"]
    } - {None}
    assert roles <= {"Senior reviewer", "Outside reviewer"}


def test_token_in_id_is_redacted_in_the_view(proj, home, dash):
    put_slice(proj, "ghp_DEMOTOKEN123", "S1", "PLAN")
    notes_key = "ghp_DEMOTOKEN123"
    from devloop import notes

    notes.problem(proj, notes_key, impact="x", fix="y")
    text = json.dumps(build(proj))
    assert "ghp_DEMOTOKEN" not in text and '"?"' in text


def test_free_text_never_reaches_the_view(proj, dash):
    for key in (dash["review"], dash["gate"]):
        slices.set_fields(
            proj,
            key,
            purpose=PLANTED[2],
            next_action=PLANTED[2] + PLANTED[3],
            waiting_on=PLANTED[0],
        )
    from devloop import notes

    notes.ticklog(proj, "TICKLINE " + " ".join(PLANTED))
    notes.problem(proj, "leaky", impact=PLANTED[2], fix=PLANTED[3], detail=PLANTED[2])
    text = json.dumps(build(proj))
    # Purpose, next action and problem text are shown scrubbed: paths and tokens go.
    for planted in (PLANTED[2], PLANTED[3], str(store.state_dir(proj))):
        assert planted not in text
    # Fields no page reads stay out entirely, words included.
    assert "password hunter2" not in text and "TICKLINE" not in text


def test_node_status_comes_from_the_file_only(proj, dash):
    srcs = {s["label"]: s["ok"] for s in build(proj)["sources"]}
    assert srcs == {"Node-a": True, "Node-b": False}


def test_tick_facts(proj, dash):
    t = build(proj)["tick"]
    assert t["last_pass_at"] and t["paused"] is False and t["capped"] is False
    (store.state_dir(proj) / "PAUSE").write_text("")
    assert build(proj)["tick"]["paused"] is True


# --- memory --------------------------------------------------------------------


def make_proc(root, avail_kb=6_000_000, total_kb=12_000_000):
    root.mkdir(exist_ok=True)
    (root / "meminfo").write_text(
        f"MemTotal:       {total_kb} kB\nMemFree: 1 kB\nMemAvailable:   {avail_kb} kB\n"
    )

    def proc(pid, comm, pgrp, rss_pages):
        d = root / str(pid)
        d.mkdir(exist_ok=True)
        (d / "stat").write_text(f"{pid} ({comm}) S 1 {pgrp} {pgrp} 0 -1 4194560")
        (d / "statm").write_text(f"{rss_pages * 2} {rss_pages} 0 0 0 0 0")
        (d / "comm").write_text(comm + "\n")

    return proc


def test_memory_from_a_fixture_proc(proj, dash, tmp_path):
    root = tmp_path / "proc"
    proc = make_proc(root, avail_kb=6_000_000)
    pg = os.getpgid(0)
    # Two processes in the live builder's payload group, one of them with a path-like comm.
    proc(900, "claude", pg, 25600)  # 100 MB
    proc(901, "node", pg, 12800)  # 50 MB
    proc(500, "postgres", 500, 51200)  # 200 MB
    proc(501, "sshd", 501, 2560)
    for n in range(502, 508):
        proc(n, f"tiny{n}", n, 256)
    v = view.build_view(proj, proc=str(root))
    m = v["memory"]
    assert (m["total_mb"], m["available_mb"]) == (11718, 5859)
    assert m["floor_mb"] == 2560
    assert m["reserved_mb"] == 600  # the live builder, started just now
    assert m["headroom_mb"] == 5859 - 600 and m["state"] == "ok"
    assert m["workers"] == [
        {"role": "Developer", "slice": dash["review"], "rss_mb": 150}
    ]
    assert [t["name"] for t in m["top"]][:2] == ["postgres", "sshd"]
    assert len(m["top"]) == 5 and m["hosts"] == []
    assert "claude" not in {t["name"] for t in m["top"]}  # workers are not repeated


def test_memory_leaks_no_arguments_or_paths(proj, dash, tmp_path):
    root = tmp_path / "proc"
    proc = make_proc(root)
    proc(700, "weird/../name", 700, 100)
    proc(701, "ghp_AAAAAAAAAAAA", 701, 90)
    d = root / "702"
    d.mkdir()
    (d / "stat").write_text("702 (curl) S 1 702 702 0 -1")
    (d / "statm").write_text("1 1 0 0 0 0 0")
    (d / "comm").write_text("curl\n")
    (d / "cmdline").write_text("curl\x00--password\x00hunter2\x00/etc/shadow\x00")
    text = json.dumps(view.build_view(proj, proc=str(root))["memory"])
    for leak in ("hunter2", "--password", "/etc", "weird", "ghp_", "cmdline"):
        assert leak not in text
    assert "curl" in text


@pytest.mark.parametrize(
    "avail_kb, state",
    [
        (2560 * 1024 + 600 * 1024 + 1024 * 1024, "ok"),  # at floor + 1 GB: ok
        (2560 * 1024 + 600 * 1024 + 1024 * 1024 - 1024, "tight"),
        (2560 * 1024 + 600 * 1024, "tight"),  # exactly at the floor
        (2560 * 1024 + 600 * 1024 - 1024, "low"),
    ],
)
def test_memory_thresholds(proj, dash, tmp_path, avail_kb, state):
    proc = tmp_path / "proc"
    make_proc(proc, avail_kb=avail_kb)
    got = view.build_view(proj, proc=str(proc))["memory"]
    assert got["state"] == state


def test_a_question_cut_on_a_space_still_seals(proj):
    long_q = "word " * 80  # a long question is shown whole, trailing space dropped
    notes.ask(
        proj, "q1", question=long_q, options=["a", "b"], recommended="a", wait=True
    )
    q = build(proj)["needs_you"][0]
    assert q["question"] == q["question"].strip() and len(q["question"]) <= 1000


@pytest.mark.parametrize("n", [118, 119, 120, 121, 122])
def test_option_length_boundaries_seal(proj, n):
    opt = "x" * (n - 1) + " y"
    notes.ask(proj, "q1", question="Which?", options=[opt], recommended=opt, wait=True)
    assert build(proj)["needs_you"][0]["options"]


def test_a_null_ledger_entry_marks_only_that_ledger_unreadable(proj):
    from devloop import status

    now = datetime.datetime.now(datetime.timezone.utc)
    got = status.counts_from({"a": {"at": now.isoformat()}}, {"x": None}, now)
    assert got["rows_closed"] is None and got["merged_total"] == 1


def test_a_naive_merge_timestamp_counts_as_utc(proj):
    from devloop import status

    now = datetime.datetime.now(datetime.timezone.utc)
    naive = now.replace(tzinfo=None).isoformat()
    got = status.counts_from({"a": {"at": naive}}, {}, now)
    assert got["merged_week"] == 1


def test_displayed_pools_equal_the_configured_slots(proj):
    from devloop import project as projmod

    assert [s["pool"] for s in build(proj)["slots"]] == list(projmod.SLOTS)


def test_a_dashboard_read_never_creates_the_project_dir(proj, home):
    import shutil

    shutil.move(str(home / proj), str(home / "moved"))
    with pytest.raises(Exception, match="project.json"):
        build(proj)
    assert not (home / proj).exists()


def test_tick_line_shows_every_configured_slot(proj):
    from devloop import project as projmod
    from devloop import status

    cfg = projmod.load_project(proj)
    line = status.tick_line(proj, "model", "x")
    assert all(f"{projmod.pool_label(cfg, s)} 0/" in line for s in projmod.SLOTS)


# --- schema 2, owner text and the hint ----------------------------------------------


def test_schema_is_two(proj, dash):
    assert build(proj)["schema"] == 2 == view.SCHEMA


def test_view_build_creates_no_directories(proj, home, dash):
    def tree():
        return sorted(str(p.relative_to(home)) for p in home.rglob("*"))

    before = tree()
    build(proj)
    assert tree() == before
    with pytest.raises(Exception):
        build("no-such-project")
    assert tree() == before
    assert not (home / "no-such-project").exists()


def test_owner_question_shown_in_full_with_real_hint(proj, home):
    long_q = "Which path? " + "details " * 40 + "password hunter2 /etc/shadow"
    notes.ask(
        proj,
        "q-full",
        question=long_q,
        options=["hunter2", "use the slower path", "/etc/shadow"],
        recommended="hunter2",
        wait=True,
    )
    v = build(proj)
    q = next(n for n in v["needs_you"] if n["key"] == "q-full")
    path = notes.questions_path(proj)
    rec = notes.load_questions(path)[1]["q-full"]
    assert q["question"] == " ".join(long_q.split())
    assert q["options"] == ["hunter2", "use the slower path", "/etc/shadow"]
    assert q["recommended"] == "hunter2"
    assert q["answer_key"] == "q-full" and q["detail"] is None
    assert q["klass"] == "product" and q["version"] == notes.question_version(rec)
    page = render.render_page(v)
    text = html.unescape(page)
    assert "hunter2" in text
    assert "devloop-state p answer q-full <option>" in text and "Decide" in text


def test_hold_answer_uses_hold_key(proj, dash):
    held = dash["held"]
    notes.ensure_hold_question(proj, held)
    v = build(proj)
    item = next(n for n in v["needs_you"] if n["key"] == held)
    assert item["kind"] == "hold" and item["answer_key"] == f"hold:{held}"
    assert item["options"] and item["recommended"] == "keep-held"
    text = html.unescape(render.render_page(v))
    assert f"devloop-state p answer hold:{held} <option>" in text


def test_hold_without_a_question_record_has_no_answer_key(proj, dash):
    item = next(n for n in build(proj)["needs_you"] if n["kind"] == "hold")
    assert item["answer_key"] is None and item["options"] == []


def test_activity_and_feed_read_events_once(proj, dash, monkeypatch):
    from devloop import status

    real, calls = status.events, []

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(status, "events", counting)
    build(proj)
    assert len(calls) == 1


def test_every_event_kind_has_a_tag():
    from devloop import status

    assert set(status.EVENTS) == set(status.EVENT_KINDS)
    for kind in status.EVENT_KINDS:
        assert status.event_tag(kind) in status.EVENT_TAGS, kind
    assert status.event_tag("mystery") is None
    assert status.EVENT_TAGS == (
        "DISPATCHED",
        "APPLIED",
        "MOVED",
        "MERGED",
        "CLOSED",
        "QUESTION",
        "DECIDED",
        "ANSWERED",
        "DROPPED",
        "PAUSED",
        "RESUMED",
        "TICK",
    )


def test_version_digest_fixtures(proj, dash):
    assert store.canonical_digest({"b": [1, 2], "a": "x"}) == "721ef82f2d6c0997"
    body = {"a": 1, "z": None}
    assert view.version_of({**body, "version": "ignored"}) == store.canonical_digest(
        body
    )
    v = build(proj)
    assert v["version"] == store.canonical_digest(
        {k: x for k, x in v.items() if k != "version"}
    )


def test_secret_in_owner_text_is_redacted_and_option_not_submittable(proj):
    token = "ghp_PLANTEDSECRET1234567"
    notes.ask(
        proj,
        "q-secret",
        question=f"Use {token} for the call? Key at https://u:pw@host/x",
        options=["keep it", f"use {token}", "/etc/shadow"],
        recommended="keep it",
        wait=True,
    )
    v = build(proj)
    q = next(n for n in v["needs_you"] if n["key"] == "q-secret")
    blob = json.dumps(v)
    assert token not in blob and "u:pw@host" not in blob
    assert "[redacted]" in q["question"]
    assert q["options"][0] == "keep it" and q["options"][2] == "/etc/shadow"
    assert q["option_ok"] == [True, False, True]
    assert token not in render.render_page(v)


def test_each_held_or_parked_slice_shows_once(proj, dash):
    """The system's hold question is the hold item's own record, never a second row."""
    for key in (dash["held"], dash["parked"]):
        notes.ensure_hold_question(proj, key)
    items = build(proj)["needs_you"]
    assert [n for n in items if n["kind"] == "question" and "hold:" in n["key"]] == []
    for key in (dash["held"], dash["parked"]):
        hits = [n for n in items if key in (n["key"] or "")]
        assert len(hits) == 1 and hits[0]["kind"] == "hold"
        assert hits[0]["answer_key"] == f"hold:{key}" and hits[0]["options"]


def test_a_released_park_leaves_needs_you_at_once(proj, dash):
    """After the owner approves, the slice waits only for the tick to resume it."""
    key = dash["parked"]
    notes.ensure_hold_question(proj, key)
    notes.answer(proj, f"hold:{key}", "approve")
    items = build(proj)["needs_you"]
    assert [n for n in items if key in (n["key"] or "")] == []
