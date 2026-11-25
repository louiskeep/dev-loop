"""The tick: lock, collect, snapshot, skip or call the model, render, post."""

import datetime
import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from . import notes, runs, slices, snapshot, status, store
from . import project as projmod
from .caps import detect_cap, utc_now

MAX_BACK_TO_BACK = 3
SKIP_WINDOW_S = 7200
MODEL_TIMEOUT_S = 1800
ALERT_EVERY_S = 6 * 3600
TICKLINE = re.compile(r"^TICKLINE:\s*(.+)$", re.MULTILINE)


def _state_file(project):
    return store.state_dir(project) / "tick-state.json"


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def build_prompt(project, snapshot_md) -> str:
    """Generic tick prompt, the playbook, the project's docs, intent, roadmap, snapshot."""
    cfg = projmod.load_project(project)
    skill = store.skill_dir()
    parts = [_read_text(skill / "TICK.md"), _read_text(skill / "PLAYBOOK.md")]
    for key in ("tick_doc", "playbook_doc"):
        if cfg.get(key):
            parts.append(_read_text(cfg[key]))
    parts.append(f"## Project intent\n\n{cfg['intent']}")
    parts.append(
        f"## Roadmap (section {cfg['roadmap']['section']}, row prefix "
        f"{cfg['roadmap']['row_id']})\n\n{_read_text(cfg['roadmap']['path'])[:30000]}"
    )
    parts.append(f"# Snapshot\n\n{snapshot_md}")
    return "\n\n".join(p for p in parts if p)


def _alert(project, reason, text):
    """One alert per distinct reason per six hours."""
    path = store.state_dir(project) / "alerts.json"
    with store.locked(path):
        sent = store.read_json(path)
        last = sent.get(reason)
        if (
            last
            and (utc_now() - datetime.datetime.fromisoformat(last)).total_seconds()
            < ALERT_EVERY_S
        ):
            return
        sent[reason] = store.now_iso()
        store.write_json(path, sent)
    notes.notify(project, text)


def _model_call(project, cfg, prompt):
    argv = [
        "timeout",
        str(MODEL_TIMEOUT_S),
        "claude",
        "-p",
        *projmod.mcp_args(cfg, "tick"),
        "--model",
        cfg["models"]["tick"],
        "--permission-mode",
        "auto",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    env = dict(
        os.environ,
        DEVLOOP_HEADLESS="1",
        DEVLOOP_PROJECT=project,
        DEVLOOP_TICK_ID=secrets.token_hex(8),
        DEVLOOP_BASE_BRANCHES=projmod.base_branches(projmod.load_project(project)),
    )
    try:
        r = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            timeout=MODEL_TIMEOUT_S + 60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _final_text(output) -> str:
    final = ""
    for line in output.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            final = str(ev.get("result", ""))
    return final or output


def _run_model(project, cfg, md) -> str:
    rc, output = _model_call(project, cfg, build_prompt(project, md))
    until = detect_cap(rc, output)
    if until is not None:
        store.write_text(store.state_dir(project) / "cap-until", until.isoformat())
        _alert(
            project,
            "cap",
            f"Usage cap reached. The dev loop resumes after {status.clock(until)}.",
        )
        return "capped"
    if rc != 0:
        _alert(
            project,
            "model-failed" if rc != 124 else "model-timeout",
            "The dev loop tick model call timed out."
            if rc == 124
            else "The dev loop tick model call failed.",
        )
        return "failed"
    line = TICKLINE.findall(_final_text(output))
    if line:
        notes.ticklog(project, line[-1])  # private page; never posted
    return "model"


def _pass(project) -> str:
    """One tick pass. Returns the outcome word for the post."""
    cfg = projmod.load_project(project)
    state = store.read_json(_state_file(project))
    runs.collect(project)
    notes.reconcile_hold_questions(project)
    slices.drain_pending_board(project)
    d = store.state_dir(project)
    if (d / "PAUSE").exists():
        status.write_status(project)
        return "paused"
    if store.cap_active(project):
        status.write_status(project)
        return "capped"
    md, digest = snapshot.build_snapshot(project)
    last = state.get("last_model_at")
    fresh = (
        last is not None
        and (utc_now() - datetime.datetime.fromisoformat(last)).total_seconds()
        < SKIP_WINDOW_S
    )
    # A finished run nobody applied yet holds its slot, so it always earns a model pass:
    # a worker can end during the model call and be saved in last_hash unseen.
    if (
        digest == state.get("last_hash")
        and fresh
        and not snapshot.finished_unapplied(project)
    ):
        status.write_status(project)
        return "skipped"
    outcome = _run_model(project, cfg, md)
    status.write_status(project)
    if outcome == "model":
        state.update(
            last_hash=snapshot.build_snapshot(project)[1], last_model_at=store.now_iso()
        )
        store.write_json(_state_file(project), state)
    return outcome


def post_tick_line(project, outcome, started) -> None:
    state = store.read_json(_state_file(project))
    last = state.get("last_model_at")
    summary = status.summarize(project, started) if outcome == "model" else ""
    last_dt = datetime.datetime.fromisoformat(last) if last else None
    notes.notify(
        project,
        status.tick_line(project, outcome, summary, last_model_at=last_dt),
    )


def tick(project) -> int:
    d = store.state_dir(project)
    fd = os.open(d / "tick.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            (d / "tick.requested").touch()
            return 0
        for _ in range(MAX_BACK_TO_BACK):
            (d / "tick.requested").unlink(missing_ok=True)
            started, outcome = store.now_iso(), "failed"
            try:
                outcome = _pass(project)
            finally:
                # A crashed or timed-out pass still posts, as `failed`.
                post_tick_line(project, outcome, started)
            if not (d / "tick.requested").exists():
                break
        return 0
    finally:
        os.close(fd)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: devloop-tick PROJECT", file=sys.stderr)
        return 2
    return tick(args[0])
