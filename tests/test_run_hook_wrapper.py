"""Exercise hooks/run-hook.sh the way hooks.json invokes it (wrapper -> module)."""

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WRAPPER = str(REPO / "hooks" / "run-hook.sh")


def _run_via_wrapper(hook_name, event, cwd):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO)}
    return subprocess.run(
        ["bash", WRAPPER, hook_name],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _git(t, *a):
    subprocess.run(["git", *a], cwd=t, check=True, capture_output=True, text=True)


def test_wrapper_dispatches_gate_guard_allow_on_non_bash(tmp_path):
    out = _run_via_wrapper(
        "gate_guard",
        {"tool_name": "Read", "tool_input": {}, "cwd": str(tmp_path)},
        tmp_path,
    )
    assert out.returncode == 0 and out.stdout.strip() == ""


def test_wrapper_dispatches_gate_guard_deny_on_ungated_push(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c0")
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "git push origin main"},
        "cwd": str(tmp_path),
    }
    out = _run_via_wrapper("gate_guard", event, tmp_path)
    assert out.returncode == 2
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_wrapper_dispatches_session_start(tmp_path):
    out = _run_via_wrapper("session_start", {}, tmp_path)
    assert out.returncode == 0
    assert (
        "conductor"
        in json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"].lower()
    )
