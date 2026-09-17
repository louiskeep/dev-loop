import json
import subprocess
import sys
from pathlib import Path

from hooks import done_claim_check as dc

REPO = Path(__file__).resolve().parent.parent
LOOP = str(REPO / "hooks" / "loop_state.py")


def _git(t, *a):
    subprocess.run(["git", *a], cwd=t, check=True, capture_output=True, text=True)


def _repo(tmp):
    _git(tmp, "init", "-b", "main")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / "f").write_text("x")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-m", "c0")
    (tmp / ".loop-config.json").write_text(
        '{"roadmap_paths": [], "shipped_log_paths": []}'
    )


def _cli(tmp, *args):
    subprocess.run(
        [sys.executable, LOOP, "--repo", str(tmp), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_detects_done_language():
    assert dc.claims_done("This is done and merge-ready.")
    assert dc.claims_done("Slice complete, ready to ship.")
    assert not dc.claims_done("Working on the next task now.")


def test_stop_hook_active_short_circuits(capsys):
    assert (
        dc.main_from_event({"stop_hook_active": True, "last_assistant_message": "done"})
        == 0
    )
    assert capsys.readouterr().out.strip() == ""


def test_warns_on_done_claim_without_state(tmp_path, capsys):
    _repo(tmp_path)
    rc = dc.main_from_event(
        {"last_assistant_message": "All done, merge-ready.", "cwd": str(tmp_path)}
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert "no .loop-state.json" in out["hookSpecificOutput"]["additionalContext"]


def test_silent_when_no_done_claim(tmp_path, capsys):
    _repo(tmp_path)
    assert (
        dc.main_from_event(
            {"last_assistant_message": "next up: task 3", "cwd": str(tmp_path)}
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == ""


def test_silent_when_gate_clean(tmp_path, capsys):
    _repo(tmp_path)
    _cli(tmp_path, "init", "--slice", "s", "--risk", "R1", "--rationale", "x")
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
    rc = dc.main_from_event(
        {"last_assistant_message": "done and merge-ready", "cwd": str(tmp_path)}
    )
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""
