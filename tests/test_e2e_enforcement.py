"""End-to-end: the full gate lifecycle for an R2 slice, driven through the real hooks."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUARD = str(REPO / "hooks" / "gate_guard.py")
LOOP = str(REPO / "hooks" / "loop_state.py")


def _git(t, *a):
    subprocess.run(["git", *a], cwd=t, check=True, capture_output=True, text=True)


def _cli(tmp, *args):
    subprocess.run(
        [sys.executable, LOOP, "--repo", str(tmp), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _push(tmp):
    event = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "cwd": str(tmp),
        }
    )
    return subprocess.run(
        [sys.executable, GUARD],
        input=event,
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def _commit(tmp, name):
    (tmp / name).write_text("x")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-m", name)


def test_full_r2_lifecycle(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    _commit(tmp_path, "f0")
    # empty-docs config so this test exercises the gate lifecycle, not docs-current
    (tmp_path / ".loop-config.json").write_text(
        '{"roadmap_paths": [], "shipped_log_paths": []}'
    )

    # 1. no state -> deny
    _cli(tmp_path, "init", "--slice", "s", "--risk", "R2", "--rationale", "x")
    assert _push(tmp_path) == 2

    # 2. dennis green but R2 needs codex -> still deny
    _cli(
        tmp_path,
        "record-gate",
        "dennis",
        "--status",
        "green",
        "--reviewer",
        "dennis",
        "--commit",
        "HEAD",
    )
    assert _push(tmp_path) == 2

    # 3. codex green -> allow
    _cli(
        tmp_path,
        "record-gate",
        "codex",
        "--status",
        "green",
        "--reviewer",
        "codex",
        "--commit",
        "HEAD",
    )
    assert _push(tmp_path) == 0

    # 4. a new commit invalidates the gates -> deny (remediation not re-reviewed)
    _commit(tmp_path, "f1")
    assert _push(tmp_path) == 2

    # 5. re-gate on the new commit -> allow
    _cli(
        tmp_path,
        "record-gate",
        "dennis",
        "--status",
        "green",
        "--reviewer",
        "dennis",
        "--commit",
        "HEAD",
    )
    _cli(
        tmp_path,
        "record-gate",
        "codex",
        "--status",
        "green",
        "--reviewer",
        "codex",
        "--commit",
        "HEAD",
    )
    assert _push(tmp_path) == 0

    # 6. a compound merge+push is denied regardless of state
    event = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "git merge x && git push origin main"},
            "cwd": str(tmp_path),
        }
    )
    rc = subprocess.run(
        [sys.executable, GUARD],
        input=event,
        capture_output=True,
        text=True,
        check=False,
    ).returncode
    assert rc == 2
