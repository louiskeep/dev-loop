"""The worker wrapper: run one payload under a deadline and an idle limit.

The wrapper is the only writer of `exit` on a normal path, and it writes it in a
`finally`, so a killed, hung or crashing payload still leaves a record.
"""

import hashlib
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from . import githooks
from . import project as projmod
from . import push, runs, slices, store
from .caps import TAIL_CHARS, detect_cap, detect_cap_text

VERDICT = re.compile(r"VERDICT:\s*(GO-WITH-FIXES|NO-GO|GO)")
SELF_VERDICT = re.compile(r"SELF-VERDICT:\s*(GO-WITH-FIXES|NO-GO|GO)")
VERDICT_READERS = ("claude-verdict", "verdict", "selfverdict")
CODEX_KINDS = ("codex-review", "codex-fix", "codex-builder")
FAILING_BLOCK = "FAILING TEST IDS"
MAX_FAILING = 200


def _size(path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _watch(proc, req, rd) -> str:
    """Poll until the payload ends. Returns the exit reason."""
    poll = float(os.environ.get("DEVLOOP_POLL_SECS", "15"))
    idle_s = float(os.environ.get("DEVLOOP_IDLE_SECS") or req["idle_s"])
    start = last_growth = time.monotonic()
    seen = -1
    while True:
        try:
            proc.wait(timeout=poll)
            return "exit"
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        if now - start > req["deadline_s"]:
            _kill_group(proc)
            return "deadline"
        # Codex writes to `out` and not to the log, so either file growing is progress.
        size = _size(store.out_dir(rd) / "log") + _size(req.get("out") or "")
        if size != seen:
            seen, last_growth = size, now
        elif now - last_growth > idle_s:
            _kill_group(proc)
            return "hung"


def _final_message(req, rd):
    """The reviewer's last message and nothing else; transcripts never count."""
    if req["reader"] == "claude-verdict":
        final = None
        try:
            lines = (store.out_dir(rd) / "log").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return None
        for line in lines.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                final = None if event.get("is_error") else event.get("result")
        return final if isinstance(final, str) else None
    try:
        return Path(req["last_msg"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


_MARKUP = " \t`*_"


def _plain_line(line):
    """The line without the markdown a model wraps around it: backticks, * and _, one final ."""
    line = line.strip(_MARKUP)
    if line.endswith("."):
        line = line[:-1]
    return line.strip(_MARKUP)


def _last_line_verdict(text, rx):
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    m = rx.fullmatch(_plain_line(lines[-1])) if lines else None
    return m.group(1) if m else None


def _verdict_found(req, rd):
    rx = SELF_VERDICT if req["reader"] == "selfverdict" else VERDICT
    return _last_line_verdict(_final_message(req, rd), rx)


def _failing_ids(rd):
    ids, in_block = [], False
    try:
        text = (store.out_dir(rd) / "log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ids
    for line in text.splitlines():
        if FAILING_BLOCK in line:
            in_block = True
            continue
        if line.startswith("FAILED "):
            ids.append(line[7:].split(" - ")[0].strip())
        elif in_block and line.strip():
            ids.append(line.strip())
        elif in_block:
            in_block = False
    return ids[:MAX_FAILING]


def _worktree_head(req):
    try:
        r = subprocess.run(
            ["git", "-C", req["worktree"], "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _write_result(rd, req, code, reason):
    """Build result.md for kinds whose tool cannot write one. Claude workers do it."""
    reader = req["reader"]
    if reader == "claude":
        return
    status, verdict, body = "FAILED", "", ""
    ok = reason == "exit"
    if reader == "exit":
        status = "DONE" if ok and code == 0 else "FAILED"
    elif reader == "testexit":
        if ok and code == 0:
            status, verdict = "DONE", "GO"
        elif ok:
            status, verdict = "DONE", "NO-GO"
            body = "\n".join(_failing_ids(rd))
    else:
        found = _verdict_found(req, rd) if ok and code == 0 else None
        if found:
            status, verdict = "DONE", found
    head = req.get("head") or ""
    if req["kind"] == "codex-builder":
        head = _worktree_head(req) or head  # the commit Codex finished on
    text = f"status: {status}\nhead: {head}\nverdict: {verdict}\n"
    text += f"tests: exit {code}\n---\n{body}\n"
    store.write_text(store.out_dir(rd) / "result.md", text)


def _push_writer(rd, req):
    """Put the writer's commits on the remote before the run can be applied. A head that
    is not on the remote must never become the slice head, so a failure turns the result
    into FAILED with the reason."""
    path = store.out_dir(rd) / "result.md"
    hdr, body = store.read_result(path, errors="strict")
    if hdr.get("status") != "DONE":
        return
    try:
        pushed, why = push.push_slice_branch(req)
    except Exception as exc:  # any failure to confirm must not leave a DONE result
        pushed, why = None, f"push confirmation failed: {type(exc).__name__}"
    if why is None:
        # Always rewrite: it also drops a stale or duplicate head the worker wrote.
        store.rewrite_header(path, head=pushed)
        return
    text = f"status: FAILED\nhead: \nverdict: \ntests: {hdr.get('tests', '')}\n"
    text += f"push: {why}\n---\npush failed: {why}\n{body.strip()}\n"
    store.write_text(path, text)


def _set_cap_until(project, code, rd, req):
    codex = req["kind"] in CODEX_KINDS
    files = (
        [Path(req.get("out") or ""), Path(req.get("last_msg") or "")]
        if codex
        else [store.out_dir(rd) / "log"]
    )
    text = ""
    for p in files:
        try:
            text += p.read_text(encoding="utf-8", errors="replace")[-TAIL_CHARS:] + "\n"
        except OSError:
            pass
    until = (detect_cap_text if codex else detect_cap)(code, text)
    if until is not None:
        store.write_text(store.state_dir(project) / "cap-until", until.isoformat())
    return until is not None


def write_repair(rd, req):
    """After the payload has ended, keep a control copy of the helper's output.

    The hashes come from the bytes copied here, and repair.json is created
    exclusively after removing anything found there, so nothing a payload left
    in the control directory is ever trusted.
    """
    out = Path(req.get("repair_out") or "")
    if not out.is_file():
        return
    data = out.read_bytes()
    copy = rd / "repaired"
    copy.unlink(missing_ok=True)
    copy.write_bytes(data)
    record = rd / "repair.json"
    record.unlink(missing_ok=True)
    fd = os.open(record, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(
            {
                "input_sha": req["plan_sha"],
                "output_sha": hashlib.sha256(data).hexdigest(),
            },
            fh,
        )


def _wake_tick(project):
    tick = store.bin_dir() / "devloop-tick"
    env = dict(os.environ, DEVLOOP_HEADLESS="1", DEVLOOP_LIB=str(store.package_root()))
    try:
        subprocess.Popen(
            [str(tick), project],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=env,
        )
    except OSError:
        pass  # the next cron tick collects the run anyway


def run(project, run_id) -> int:
    rd = store.run_dir(project, run_id)
    req = runs.read_request(project, run_id)
    env = dict(
        os.environ,
        DEVLOOP_HEADLESS="1",
        DEVLOOP_PROJECT=project,
        DEVLOOP_RUN=run_id,
        DEVLOOP_RUN_DIR=str(store.out_dir(rd)),
        DEVLOOP_BASE_BRANCHES=projmod.base_branches(projmod.load_project(project)),
    )
    if req["kind"] in CODEX_KINDS:
        env["CODEX_LAST_MSG"] = req["last_msg"]
    if req.get("cwd"):  # every worker, Claude or not, pushes through the guard hooks
        env = githooks.protect_env(env, rd / "githooks")
    store.out_dir(rd).mkdir(exist_ok=True)
    me = os.getpid()
    runs.update_request(
        project, run_id, pid=me, pid_start=runs.proc_start(me), boot_id=runs.boot_id()
    )
    proc, code, reason = None, -1, "exit"
    try:
        try:
            with (
                open(rd / "brief.md", "rb") as stdin,
                open(store.out_dir(rd) / "log", "ab") as log,
            ):
                proc = subprocess.Popen(
                    req["argv"],
                    cwd=req["cwd"],
                    stdin=stdin,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    start_new_session=True,
                )
        except OSError:
            code, reason = 127, "spawn"
        else:
            runs.update_request(
                project,
                run_id,
                payload_pgid=proc.pid,
                payload_start=runs.proc_start(proc.pid),
            )
            reason = _watch(proc, req, rd)
            code = proc.returncode
    finally:
        if proc is not None and proc.poll() is None:
            _kill_group(proc)
            code = proc.returncode
        finish(project, rd, req, code, reason)
    return 0


def finish(project, rd, req, code, reason):
    """Result, exit, cleanup, wake-up: each step guarded so the exit file always lands."""
    try:
        if reason == "exit" and code != 0 and _set_cap_until(project, code, rd, req):
            reason = "capped"
        elif (
            reason == "exit"
            and req["kind"] == "test"
            and code in req.get("infra_codes", [])
        ):
            reason = "infra"
        if (
            reason == "exit"
            and code == 0
            and req["reader"] in VERDICT_READERS
            and _verdict_found(req, rd) is None
        ):
            reason = (
                "no-verdict"  # a clean exit that named no verdict: retry, not a failure
            )
        _write_result(rd, req, code, reason)
        if reason == "exit" and code == 0 and req["kind"] in slices.WRITER_KINDS:
            _push_writer(rd, req)
        if req["kind"] == "codex-fix":
            write_repair(rd, req)
    except Exception as exc:  # noqa: BLE001 - the exit file must still be written
        with open(store.out_dir(rd) / "log", "a") as log:
            log.write(f"wrapper error: {type(exc).__name__}\n")
        if code == 0:  # a result that could not be published is not a success
            code, reason = 1, "wrapper-error"
    finally:
        runs.create_exit(rd, code=code, reason=reason)
        runs.remove_tree(project, rd.name, req)
        _wake_tick(project)
