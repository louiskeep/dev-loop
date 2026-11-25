"""The live read model: worker cards, feed, KPIs and slice drawers."""

import os
import time

import pytest
import transcript_fixture as tf
from dash_fixture import ago, put_run, put_slice

from devloop import runs, slices, store
from devloop.dash import live, transcript, view


def build(proj, cache=None, **kw):
    return view.build_view(proj, proc="/nonexistent-proc", cache=cache, **kw)


def write_log(proj, run_id, items, name="log"):
    rd = store.run_dir(proj, run_id)
    path = store.out_dir(rd) / name
    path.write_bytes(items if isinstance(items, bytes) else tf.claude_events(items))
    return path


def bash(call_id, cmd="git status"):
    return tf.assistant("m" + call_id, tf.tool_use(call_id, "Bash", command=cmd), out=7)


def card_for(v, key):
    return next(c for c in v["live"] if c["key"] == key)


def finish(proj, run_id):
    """The way apply and the wrapper leave a worker: exit file, then applied."""
    rd = store.run_dir(proj, run_id)
    store.write_json(
        rd / "exit", {"code": 0, "reason": "exit", "ended_at": ago(minutes=1)}
    )
    (rd / "applied").write_text("")
    runs.update_request(
        proj, run_id, pid=999999999, pid_start="0", payload_pgid=999999999
    )


# --- registry ------------------------------------------------------------------


def test_every_run_kind_has_an_icon():
    assert set(live.ICONS) == set(runs.KINDS)
    assert set(live.ICONS.values()) <= set(live.ICON_IDS)


# --- cards -----------------------------------------------------------------------


def test_missing_transcript_card_says_starting(proj, dash):
    c = card_for(build(proj), dash["review"])
    assert c["state"] == "starting" and c["doing"] is None and c["tools"] == 0


def test_codex_fix_card_says_outside(proj, dash):
    put_run(proj, "codex-fix.a", kind="codex-fix", key=dash["plan"], alive=True)
    c = card_for(build(proj), dash["plan"])
    assert c["state"] == "outside" and c["harness"] == "codex"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_unreadable_transcript_marks_card_only(proj, dash):
    put_run(proj, "builder.b", kind="builder", key=dash["plan"], alive=True)
    path = write_log(proj, "builder.b", [bash("a")])
    write_log(proj, "builder.live", [bash("z")])
    path.chmod(0)
    try:
        v = build(proj)
    finally:
        path.chmod(0o600)
    assert card_for(v, dash["plan"])["state"] == "unreadable"
    assert card_for(v, dash["review"])["state"] == "live"


def test_live_cards_one_per_executing_run(proj, dash):
    put_run(proj, "builder.b", kind="builder", key=dash["plan"], alive=True)
    put_run(proj, "dennis.c", kind="dennis", key=dash["gate"], alive=True)
    v = build(proj)
    assert len(v["live"]) == 3 == v["kpi"]["running"]
    assert len({c["ref"] for c in v["live"]}) == 3
    # Exited and lost runs are waiting, not live.
    assert {w["state"] for w in v["waiting_runs"]} == {
        "awaiting-apply",
        "awaiting-release",
    }


def test_live_card_fields(proj, dash):
    put_run(
        proj,
        "builder.m",
        kind="builder",
        key=dash["plan"],
        alive=True,
        argv=["claude", "--model", "claude-sonnet-5-5", "-p"],
    )
    write_log(
        proj,
        "builder.m",
        [
            tf.init("claude-opus-5-5"),
            tf.assistant(
                "m1", tf.tool_use("a", "Bash", command="git status"), out=40, inp=5
            ),
            tf.user(tf.tool_result("a", True)),
            tf.assistant(
                "m2", tf.tool_use("b", "Read", file_path="/x/view.py"), out=60, inp=9
            ),
        ],
    )
    c = card_for(build(proj), dash["plan"])
    assert (
        c["role"] == "Developer" and c["icon"] == "build" and c["harness"] == "claude"
    )
    assert c["slice_active"] is True and c["phase"] == "PLAN"
    assert c["model"] == "claude-sonnet-5-5"
    assert c["state"] == "live" and c["tools"] == 2 and c["errors"] == 1
    assert c["doing"]["tag"] == "READ" and c["doing"]["target"] == "view.py"
    assert c["doing"]["running"] is True
    assert c["tokens_out"] == 100 and c["context"] == 9
    assert len(c["spark"]) == transcript.SPARK_MINUTES and sum(c["spark"]) == 2
    assert set(c) == set(view.VIEW_SCHEMA["live"][0])


def test_model_falls_back_to_the_file(proj, dash):
    write_log(proj, "builder.live", [tf.init("claude-opus-5-5"), bash("a")])
    assert card_for(build(proj), dash["review"])["model"] == "claude-opus-5-5"


def test_max_live_cap(proj, dash):
    for i in range(20):
        put_run(proj, f"builder.n{i}", kind="builder", key=dash["plan"], alive=True)
    v = build(proj)
    assert len(v["live"]) == live.MAX_LIVE == 16
    assert v["kpi"]["running"] == 21


def test_kpi_counts(proj, dash):
    write_log(proj, "builder.live", [bash("a"), bash("b"), bash("c")])
    v = build(proj)
    k = v["kpi"]
    assert k["running"] == 1 and k["needs_you"] == 3
    assert k["slots"] == sum(s["cap"] for s in v["slots"])
    assert k["merged_today"] == v["counts"]["merged_today"]
    assert k["tools_per_min"] == 3
    assert k["free_mb"] is None and k["memory_state"] is None
    assert k["last_pass_at"] == v["tick"]["last_pass_at"]


# --- feed ------------------------------------------------------------------------


def test_feed_merges_calls_and_events_newest_first(proj, dash):
    write_log(proj, "builder.live", [bash("a", "git x"), bash("b", "pytest")])
    v = build(proj)
    tags = {f["tag"] for f in v["feed"]}
    assert {"SHELL", "MERGED", "DISPATCHED", "MOVED"} <= tags
    ats = [f["at"] for f in v["feed"]]
    parsed = [view._when(a) for a in ats]
    assert parsed == sorted(parsed, reverse=True)
    call = next(f for f in v["feed"] if f["tag"] == "SHELL")
    assert call["target"] in ("git", "pytest") and call["role"] == "Developer"
    assert call["words"] is None
    event = next(f for f in v["feed"] if f["tag"] == "MERGED")
    assert event["words"] == "merged ROW0 M1" and event["role"] is None


def test_feed_limit(proj, dash):
    write_log(proj, "builder.live", [bash(f"c{i}") for i in range(80)])
    assert len(build(proj)["feed"]) == transcript.FEED_LIMIT


def test_worker_across_executing_applied_evicted(proj, dash):
    key = dash["review"]
    cache = transcript.Cache()
    path = write_log(proj, "builder.live", [bash("a", "git x"), bash("b", "pytest")])
    v = build(proj, cache=cache)
    assert card_for(v, key)["tools"] == 2 and path in cache.entries
    finish(proj, "builder.live")
    v = build(proj, cache=cache)
    assert not any(c["key"] == key for c in v["live"])
    drawer = next(d for d in v["drawers"] if d["key"] == key)
    assert [r["role"] for r in drawer["runs"]] == ["Developer"]
    assert len(drawer["calls"]) == 2
    # The slice leaves the active set: no drawer keeps the file, but the feed keeps facts.
    s = slices.load(proj, key)
    s["phase"] = "DONE"
    slices._save(proj, s)
    v = build(proj, cache=cache)
    assert path not in cache.entries
    assert not any(d["key"] == key for d in v["drawers"])
    assert sum(1 for f in v["feed"] if f["tag"] == "SHELL") == 2


# --- drawers ---------------------------------------------------------------------


def test_drawer_runs_belong_to_their_slice(proj, dash):
    put_run(proj, "builder.other", kind="builder", key=dash["plan"], alive=True)
    v = build(proj)
    by_key = {d["key"]: d for d in v["drawers"]}
    refs_review = {r["ref"] for r in by_key[dash["review"]]["runs"]}
    refs_plan = {r["ref"] for r in by_key[dash["plan"]]["runs"]}
    assert len(refs_review) == 1 and len(refs_plan) == 1 and not refs_review & refs_plan


def test_orphan_worker_has_no_drawer_link(proj, dash):
    put_run(proj, "builder.orphan", kind="builder", key="GONE--S", alive=True)
    v = build(proj)
    c = card_for(v, "GONE--S")
    assert c["slice_active"] is False and c["phase"] is None
    assert not any(d["key"] == "GONE--S" for d in v["drawers"])


def test_drawer_keeps_finished_run_history(proj, dash):
    key = dash["review"]
    write_log(proj, "builder.live", [bash("a", "git x")])
    finish(proj, "builder.live")
    put_run(proj, "builder.second", kind="builder", key=key, alive=True)
    v = build(proj)
    drawer = next(d for d in v["drawers"] if d["key"] == key)
    assert len(drawer["runs"]) == 2
    assert [c["tag"] for c in drawer["calls"]] == ["SHELL"]


def test_drawer_files_from_claude_edit_and_write(proj, dash):
    write_log(
        proj,
        "builder.live",
        [
            tf.assistant(
                "m1",
                tf.tool_use("a", "Edit", file_path="/w/view.py"),
                tf.tool_use("b", "Edit", file_path="/w/view.py"),
                tf.tool_use("c", "Write", file_path="/w/new.md"),
                tf.tool_use("d", "Edit", file_path="/w/.env"),
            )
        ],
    )
    put_run(proj, "codex-review.r", kind="codex-review", key=dash["review"], alive=True)
    write_log(
        proj,
        "codex-review.r",
        tf.codex_header().encode() + tf.codex_patch("A cx.py", "M cy.py").encode(),
        name="codex.out",
    )
    v = build(proj)
    drawer = next(d for d in v["drawers"] if d["key"] == dash["review"])
    assert drawer["files"] == [
        {"name": "view.py", "edits": 2},
        {"name": "new.md", "edits": 1},
    ]
    assert {c["tag"] for c in drawer["calls"]} >= {"EDIT", "WRITE"}


def test_drawer_sources_capped(proj, dash):
    for i in range(31):
        put_slice(proj, "ROWX", f"S{i:02d}", "DEVELOP")
    v = build(proj)
    assert len(v["drawers"]) == live.DRAWER_SLICES == 30


def test_drawer_run_status_and_verdict_from_result_header(proj, dash):
    rd = store.run_dir(proj, "builder.live")
    (store.out_dir(rd) / "result.md").write_text(
        "status: DONE\nverdict: GO\n---\nbody\n"
    )
    drawer = next(d for d in build(proj)["drawers"] if d["key"] == dash["review"])
    assert (
        drawer["runs"][0]["status"] == "DONE" and drawer["runs"][0]["verdict"] == "GO"
    )
    (store.out_dir(rd) / "result.md").write_text("status: weird\nverdict: ghp_x\n---\n")
    drawer = next(d for d in build(proj)["drawers"] if d["key"] == dash["review"])
    assert drawer["runs"][0]["status"] is None and drawer["runs"][0]["verdict"] is None


def test_finished_transcripts_parsed_per_build_are_capped(proj, dash, monkeypatch):
    monkeypatch.setattr(live, "MAX_DRAWER_FILES", 2)
    key = dash["review"]
    for i in range(5):
        put_run(
            proj, f"builder.old{i}", kind="builder", key=key, started=ago(hours=5 + i)
        )
        write_log(proj, f"builder.old{i}", [bash("a")])
        finish(proj, f"builder.old{i}")
    cache = transcript.Cache()
    build(proj, cache=cache)
    assert len(cache.entries) <= 1 + 2 + 1  # live card, two finished, spare


# --- labels ----------------------------------------------------------------------


def test_no_persona_names_in_labels(proj, dash):
    put_run(proj, "dennis.c", kind="dennis", key=dash["gate"], alive=True)
    put_run(proj, "codex-review.d", kind="codex-review", key=dash["plan"], alive=True)
    put_run(proj, "codex-fix.e", kind="codex-fix", key=dash["plan"], alive=True)
    v = build(proj)
    labels = [c["role"] for c in v["live"]] + [w["role"] for w in v["waiting_runs"]]
    labels += [f["role"] for f in v["feed"] if f["role"]]
    labels += [r["role"] for d in v["drawers"] for r in d["runs"]]
    words = [f["words"] for f in v["feed"] if f["words"]]
    for name in (
        "dennis",
        "codex-review",
        "codex-fix",
        "codex-builder",
        "zed-agent",
        "qux-agent",
    ):
        assert not any(name in x.lower() for x in labels + words), name
    assert "Senior reviewer" in labels and "Outside reviewer" in labels


def test_reader_is_not_slowed_by_a_huge_file(proj, dash, monkeypatch):
    write_log(proj, "builder.live", tf.claude_events([bash("a")]) * 30000)
    cache = transcript.Cache()
    start = time.monotonic()
    build(proj, cache=cache)
    assert time.monotonic() - start < 5
