import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOOP = str(REPO / "hooks" / "loop_state.py")


def _git(t, *a):
    subprocess.run(["git", *a], cwd=t, check=True, capture_output=True, text=True)


def _init_repo(tmp):
    _git(tmp, "init")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / "f").write_text("x")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-m", "c0")
    # empty-docs repo config so docs-current does not block these tests
    (tmp / ".loop-config.json").write_text(
        '{"roadmap_paths": [], "shipped_log_paths": []}'
    )


def _run(tmp, *args):
    return subprocess.run(
        [sys.executable, LOOP, "--repo", str(tmp), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_init_writes_state_and_gitignores(tmp_path):
    _init_repo(tmp_path)
    assert (
        _run(
            tmp_path, "init", "--slice", "s", "--risk", "R1", "--rationale", "x"
        ).returncode
        == 0
    )
    assert (tmp_path / ".loop-state.json").exists()
    assert ".loop-state.json" in (tmp_path / ".gitignore").read_text()


def test_record_gate_requires_reviewer(tmp_path):
    _init_repo(tmp_path)
    _run(tmp_path, "init", "--slice", "s", "--risk", "R1", "--rationale", "x")
    r = _run(tmp_path, "record-gate", "dennis", "--status", "green")
    assert r.returncode != 0  # argparse: missing --reviewer


def test_check_merge_blocks_then_allows(tmp_path):
    _init_repo(tmp_path)
    _run(tmp_path, "init", "--slice", "s", "--risk", "R1", "--rationale", "x")
    assert _run(tmp_path, "check-merge").returncode == 1
    _run(
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
    assert _run(tmp_path, "check-merge").returncode == 0


def test_new_commit_makes_gate_stale(tmp_path):
    _init_repo(tmp_path)
    _run(tmp_path, "init", "--slice", "s", "--risk", "R1", "--rationale", "x")
    _run(
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
    assert _run(tmp_path, "check-merge").returncode == 0
    (tmp_path / "f2").write_text("y")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c1")
    r = _run(tmp_path, "check-merge")
    assert r.returncode == 1 and "stale" in r.stderr


def test_r2_needs_codex(tmp_path):
    _init_repo(tmp_path)
    _run(tmp_path, "init", "--slice", "s", "--risk", "R2", "--rationale", "x")
    _run(
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
    assert _run(tmp_path, "check-merge").returncode == 1
    _run(
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
    assert _run(tmp_path, "check-merge").returncode == 0


def test_set_findings_rejects_negative(tmp_path):
    _init_repo(tmp_path)
    _run(tmp_path, "init", "--slice", "s", "--risk", "R1", "--rationale", "x")
    assert _run(tmp_path, "set-findings", "-1").returncode == 1
