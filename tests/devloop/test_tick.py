import datetime
import json
import os
import subprocess
from pathlib import Path

import pytest

from devloop import caps, slices, status, store, tick

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def env(proj, cfg_edit, fakebin, tmp_path, monkeypatch, home):
    monkeypatch.setenv("DEVLOOP_FAKE_MEMAVAIL_KB", str(64 * 1024 * 1024))
    monkeypatch.setenv("FAKE_NOTIFY_LOG", str(tmp_path / "notify.log"))
    monkeypatch.setenv("FAKE_ARGV_LOG", str(tmp_path / "argv.log"))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "final")
    monkeypatch.setenv("FAKE_CLAUDE_FINAL", "ok\nTICKLINE: moved things along")
    board = tmp_path / "board"
    board.write_text('#!/bin/sh\necho "$@" >> "$BOARD_LOG"\n')
    board.chmod(0o755)
    monkeypatch.setenv("BOARD_LOG", str(tmp_path / "board.log"))

    def edit(c):
        c["notify"] = {"cmd": [str(fakebin / "notify")]}
        c["status_copy"] = str(tmp_path / "vault" / "status.md")
        c["board"] = {
            "cmd": [str(board)],
            "phase_map": {p: p.lower() for p in slices.PHASES},
        }

    cfg_edit(edit)


def posts(tmp_path):
    p = tmp_path / "notify.log"
    return p.read_text().splitlines() if p.exists() else []


def claude_calls(tmp_path):
    p = tmp_path / "argv.log"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.startswith("{")]


def test_every_tick_posts_one_slack_line(proj, tmp_path, home):
    tick.tick(proj)  # model
    assert len(posts(tmp_path)) == 1 and "Dev loop" in posts(tmp_path)[0]
    tick.tick(proj)  # unchanged snapshot: skipped
    assert len(posts(tmp_path)) == 2 and "no change since" in posts(tmp_path)[1]
    (home / "p" / "PAUSE").write_text("")
    tick.tick(proj)  # paused
    assert len(posts(tmp_path)) == 3 and "paused" in posts(tmp_path)[2]
    assert len(claude_calls(tmp_path)) == 1


def test_lock_collision_sets_requested_and_reruns(proj, home, monkeypatch):
    import fcntl

    fd = os.open(home / "p" / "tick.lock", os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert tick.tick(proj) == 0
        assert (home / "p" / "tick.requested").exists()
    finally:
        os.close(fd)
    calls = []

    def fake_pass(project):
        calls.append(1)
        if len(calls) == 1:
            (home / "p" / "tick.requested").touch()
        return "skipped"

    monkeypatch.setattr(tick, "_pass", fake_pass)
    tick.tick(proj)
    assert len(calls) == 2 and not (home / "p" / "tick.requested").exists()


def test_back_to_back_runs_are_capped_at_three(proj, home, monkeypatch):
    calls = []

    def always(project):
        calls.append(1)
        (home / "p" / "tick.requested").touch()
        return "skipped"

    monkeypatch.setattr(tick, "_pass", always)
    tick.tick(proj)
    assert len(calls) == 3


def test_skip_unchanged_snapshot_until_two_hours(proj, tmp_path, home):
    tick.tick(proj)
    tick.tick(proj)
    assert len(claude_calls(tmp_path)) == 1
    state = json.loads((home / "p" / "tick-state.json").read_text())
    old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)
    state["last_model_at"] = old.isoformat()
    (home / "p" / "tick-state.json").write_text(json.dumps(state))
    tick.tick(proj)
    assert len(claude_calls(tmp_path)) == 2


def test_changed_snapshot_calls_the_model_again(proj, mk_slice, tmp_path):
    tick.tick(proj)
    mk_slice(proj, phase="DEVELOP", risk="R1")
    tick.tick(proj)
    assert len(claude_calls(tmp_path)) == 2


def test_model_prompt_carries_intent_and_snapshot(proj, tmp_path):
    tick.tick(proj)
    call = claude_calls(tmp_path)[0]
    assert "ship the thing" in call["stdin"] and "# Snapshot" in call["stdin"]
    assert call["env"]["DEVLOOP_HEADLESS"] == "1"
    assert "--permission-mode" in call["argv"] and "opus" in call["argv"]


def test_tick_model_starts_without_mcp_and_with_a_tick_id(proj, tmp_path, cfg_edit):
    tick.tick(proj)
    call = claude_calls(tmp_path)[0]
    i = call["argv"].index("--mcp-config")
    assert "--strict-mcp-config" in call["argv"]
    assert call["argv"][i + 1] == '{"mcpServers":{}}'
    assert call["env"]["DEVLOOP_TICK_ID"]


def test_tick_mcp_allow_list_and_fresh_tick_ids(proj, tmp_path, cfg_edit, home):
    cfg_edit(lambda c: c.update(mcp={"tick": "/etc/tick-mcp.json"}))
    tick.tick(proj)
    mk = claude_calls(tmp_path)[0]
    assert mk["argv"][mk["argv"].index("--mcp-config") + 1] == "/etc/tick-mcp.json"
    state = json.loads((home / "p" / "tick-state.json").read_text())
    state["last_model_at"] = "2020-01-01T00:00:00+00:00"
    (home / "p" / "tick-state.json").write_text(json.dumps(state))
    tick.tick(proj)
    ids = [c["env"]["DEVLOOP_TICK_ID"] for c in claude_calls(tmp_path)]
    assert len(set(ids)) == 2


def test_cap_detection_sets_cap_until_and_blocks_model(
    proj, tmp_path, home, monkeypatch
):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "usage-cap")
    tick.tick(proj)
    assert (home / "p" / "cap-until").is_file()
    assert "usage cap" in "\n".join(posts(tmp_path)).lower()
    n = len(claude_calls(tmp_path))
    tick.tick(proj)
    assert len(claude_calls(tmp_path)) == n  # blocked while the cap stands
    assert "usage cap" in posts(tmp_path)[-1].lower()


def test_timeout_is_not_a_cap_and_alerts_once(proj, tmp_path, home, monkeypatch):
    monkeypatch.setattr(tick, "MODEL_TIMEOUT_S", 1)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "sleep")
    monkeypatch.setenv("FAKE_SLEEP", "30")
    tick.tick(proj)
    assert not (home / "p" / "cap-until").exists()
    alerts = [p for p in posts(tmp_path) if "timed out" in p]
    assert len(alerts) == 1
    tick.tick(proj)
    assert len([p for p in posts(tmp_path) if "timed out" in p]) == 1  # six-hour rule


def test_detect_cap_shapes():
    assert caps.detect_cap(124, '{"type":"rate_limit"}') is None
    when = caps.detect_cap(1, '{"type":"rate_limit","resets_at":4102444800}')
    assert when.year == 2100
    soon = caps.detect_cap(
        1,
        json.dumps(
            {"type": "result", "is_error": True, "result": "You hit your usage limit"}
        ),
    )
    assert soon > datetime.datetime.now(datetime.UTC)
    assert (
        caps.detect_cap(0, json.dumps({"type": "result", "result": "usage limit docs"}))
        is None
    )
    assert caps.detect_cap(1, "plain text") is None


def test_pause_collects_only(proj, home, tmp_path):
    rd = store.run_dir(proj, "builder.x")
    rd.mkdir(parents=True)
    store.write_json(
        rd / "request.json",
        {
            "id": "builder.x",
            "kind": "builder",
            "row": "R",
            "slice": "S",
            "key": "R--S",
            "pid": 2**22 + 3,
            "pid_start": "1",
            "boot_id": "other",
            "reader": "claude",
        },
    )
    (home / "p" / "PAUSE").write_text("")
    tick.tick(proj)
    assert (rd / "exit").is_file()
    assert claude_calls(tmp_path) == []


def test_failed_tick_still_posts(proj, tmp_path, monkeypatch):
    def boom(project):
        raise RuntimeError("snapshot broke")

    monkeypatch.setattr(tick.snapshot, "build_snapshot", boom)
    with pytest.raises(RuntimeError):
        tick.tick(proj)
    assert len(posts(tmp_path)) == 1 and "failed" in posts(tmp_path)[0]


def test_ticklog_gets_the_model_line_privately(proj, tmp_path, home):
    tick.tick(proj)
    assert "moved things along" in (home / "p" / "tick-log.md").read_text()
    assert "moved things along" not in "\n".join(posts(tmp_path))


def test_tick_slack_line_is_scrubbed(proj):
    line = status.tick_line(
        proj, "model", "used ghp_abcdefghijkl1234 at /home/a/b/c fix 3f2a9c1de4b7"
    )
    assert "ghp_" not in line and "/home/a/b" not in line and "3f2a9c1de4b7" not in line
    assert len(line) <= 300


def test_notify_gets_env_file_vars(proj, cfg_edit, tmp_path, monkeypatch, fakebin):
    envf = tmp_path / "notify.env"
    envf.write_text("# comment\nHOOK_TOKEN=abc123\n")
    cfg_edit(lambda c: c["notify"].update(env_file=str(envf)))
    monkeypatch.setenv("FAKE_NOTIFY_ENV", "HOOK_TOKEN")
    tick.tick(proj)
    assert posts(tmp_path)[0].startswith("abc123\t")


def test_notify_failure_is_logged_not_fatal(proj, tmp_path, monkeypatch, capsys, home):
    monkeypatch.setenv("FAKE_NOTIFY_EXIT", "1")
    assert tick.tick(proj) == 0
    assert "notify failed" in capsys.readouterr().err
    assert "notify-failed" in (home / "p" / "events.jsonl").read_text()


def test_public_outputs_use_event_fields(proj, mk_slice, tmp_path, home, monkeypatch):
    secret = "hunter2"
    cred = "https://user:pw@host/x"
    k = mk_slice(proj, phase="PLAN", risk="R1", rulebooks=["a.md"])
    slices.set_fields(
        proj, k, next_action=f"password {secret} {cred}", purpose=f"{secret} {cred}"
    )
    store.append_event(proj, "slice-moved", key=k, row=f"{secret}", slice="s", to=cred)
    monkeypatch.setenv("FAKE_CLAUDE_FINAL", f"done\nTICKLINE: leaked {secret} {cred}")
    tick.tick(proj)
    surfaces = [
        "\n".join(posts(tmp_path)),
        (home / "p" / "STATUS.md").read_text(),
        (tmp_path / "vault" / "status.md").read_text(),
        (tmp_path / "board.log").read_text()
        if (tmp_path / "board.log").exists()
        else "",
    ]
    for text in surfaces:
        assert secret not in text and "pw@" not in text


def test_tick_launcher_runs(proj, home, tmp_path, fakebin):
    out = subprocess.run(
        [str(ROOT / "bin" / "devloop-tick"), proj],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DEVLOOP_HOME": str(home),
            "DEVLOOP_BIN_DIR": str(ROOT / "bin"),
        },
        check=False,
    )
    assert out.returncode == 0, out.stderr


def test_tick_session_gets_the_configured_base_branches(proj, cfg_edit, tmp_path):
    cfg_edit(
        lambda c: c.update(
            merge={"allowed": True, "repos": {"main": {"base": "develop"}}}
        )
    )
    tick.tick(proj)
    env = claude_calls(tmp_path)[0]["env"]
    assert env["DEVLOOP_BASE_BRANCHES"] == "develop,main,master"


def test_finished_unapplied_run_forces_a_model_pass_whatever_the_hash(
    proj, mk_slice, mk_run, tmp_path, home
):
    from devloop import snapshot

    k = mk_slice(proj, phase="DEVELOP", risk="R1")
    mk_run(proj, "w1", kind="builder", key=k, applied=False)
    # The state a worker leaves when it ends during the model call: the post-call
    # snapshot already contains the finished run, so the hash looks unchanged.
    _, digest = snapshot.build_snapshot(proj)
    store.write_json(
        home / "p" / "tick-state.json",
        {"last_hash": digest, "last_model_at": store.now_iso()},
    )
    tick.tick(proj)
    assert len(claude_calls(tmp_path)) == 1


# --- owner decisions reach the model ------------------------------------------------


def stub_model(monkeypatch, prompts, on_first_call=None, rc=0):
    def fake(project, cfg, prompt):
        prompts.append(prompt)
        if on_first_call and len(prompts) == 1:
            on_first_call()
        final = json.dumps({"type": "result", "result": "ok\nTICKLINE: moved"})
        return rc, final

    monkeypatch.setattr(tick, "_model_call", fake)


def override(proj, text):
    from devloop import notes

    q = notes.load_questions(notes.questions_path(proj))[1]["q1"]
    notes.answer(
        proj, "q1", text, expect_version=notes.question_version(q), via="dashboard"
    )


def tech_question(proj):
    from devloop import notes

    notes.ask(
        proj, "q1", question="Pick", options=["A", "B"], recommended="A", klass="tech"
    )


def test_override_wakes_skippable_tick(proj, monkeypatch):
    prompts = []
    stub_model(monkeypatch, prompts)
    tick.tick(proj)
    tech_question(proj)
    tick.tick(proj)
    assert len(prompts) == 1  # a tech decision alone changes nothing
    override(proj, "other: use the slower path")
    tick.tick(proj)
    assert len(prompts) == 2 and "other: use the slower path" in prompts[-1]
    tick.tick(proj)
    assert len(prompts) == 2  # acknowledged: the window skips again


def test_override_during_model_call_reaches_next_prompt(proj, monkeypatch):
    prompts = []
    tech_question(proj)
    stub_model(
        monkeypatch, prompts, on_first_call=lambda: override(proj, "other: new plan")
    )
    tick.tick(proj)
    assert len(prompts) == 1 and "other: new plan" not in prompts[0]
    tick.tick(proj)  # inside the skip window, but the answer was never seen
    assert len(prompts) == 2 and "other: new plan" in prompts[1]
    tick.tick(proj)
    assert len(prompts) == 2


def test_failed_model_call_leaves_the_decision_outstanding(proj, monkeypatch):
    prompts = []
    tech_question(proj)
    tick.tick(proj)
    override(proj, "other: do it")
    stub_model(monkeypatch, prompts, rc=1)
    tick.tick(proj)
    stub_model(monkeypatch, prompts)
    tick.tick(proj)
    assert len(prompts) == 2 and all("other: do it" in p for p in prompts)
    tick.tick(proj)
    assert len(prompts) == 2


def test_a_live_project_never_starts_a_tick_session(proj, tmp_path, cfg_edit, home):
    cfg_edit(lambda c: c.update(mode="live"))
    tick.tick(proj)
    assert claude_calls(tmp_path) == []
    assert posts(tmp_path) == []  # the live session posts its own updates
    assert (home / "p" / "STATUS.md").is_file()  # the dashboard still refreshes


def test_no_test_can_reach_a_real_model():
    import shutil

    for name in ("claude", "codex"):
        r = subprocess.run([shutil.which(name)], capture_output=True, check=False)
        assert r.returncode in (0, 97)  # a fake from fakebin, or the guard
    # This module's autouse env puts fakebin first; the guard sits right behind it.
    assert "no-real-models" in os.environ["PATH"]
