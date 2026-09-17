import json
import shlex
import subprocess
import sys
from pathlib import Path

from hooks import gate_guard as gg

REPO = Path(__file__).resolve().parent.parent
GUARD = str(REPO / "hooks" / "gate_guard.py")
LOOP = str(REPO / "hooks" / "loop_state.py")


def c(cmd, branch="feat/x", protected=("main",)):
    return gg.classify(shlex.split(cmd), list(protected), branch)


# ---- classify() partition ----


def test_allow_cases():
    # push to a non-protected destination -> allow outright
    assert c("git push origin feat/x", branch="feat/x")[0] == "allow"
    assert c("git push origin main:feat/x")[0] == "allow"  # dst feat/x
    # non-family subcommands come back as maybe_alias; main() resolves them to allow
    # when no matching git alias exists (covered by the main() integration tests).
    assert c("git status")[0] == "maybe_alias"
    assert c("git commit -m x")[0] == "maybe_alias"
    assert c("git rebase main")[0] == "maybe_alias"


def test_check_cases_on_main():
    assert c("git push origin main", branch="main") == ("check", "main")
    assert c("git push origin HEAD", branch="main") == ("check", "HEAD")
    assert c("git push origin HEAD:main", branch="main") == ("check", "HEAD")
    assert c("git push origin HEAD:refs/heads/main", branch="main") == ("check", "HEAD")
    assert c("git push origin feat/x:main", branch="main") == ("check", "feat/x")
    assert c("git push --force origin main", branch="main") == ("check", "main")
    assert c("git push --force-with-lease origin main", branch="main") == (
        "check",
        "main",
    )
    assert c("git push origin --force-with-lease main", branch="main") == (
        "check",
        "main",
    )
    assert c("git merge --ff-only feat/x", branch="main") == ("check", "feat/x")


def test_bare_push():
    assert c("git push", branch="main")[0] == "check_bare_push"
    assert c("git push origin", branch="main")[0] == "check_bare_push"


def test_flagged_feature_pushes_allowed():
    # common no-arg flags on a feature-branch push must NOT be denied (P1-1)
    for cmd in (
        "git push -u origin feat/x",
        "git push --set-upstream origin feat/x",
        "git push -v origin feat/x",
        "git push --no-verify origin feat/x",
        "git push -q origin feat/x",
    ):
        assert c(cmd, branch="feat/x")[0] == "allow", cmd


def test_arg_taking_or_unknown_push_option_denied():
    # options that take a separate argument would shift refspec parsing -> deny
    assert c("git push -o ci.skip origin main", branch="main")[0] == "deny"
    assert c("git push --repo=x origin main", branch="main")[0] == "deny"


def test_deny_cases():
    assert c("git merge feat/x", branch="main")[0] == "deny"  # no --ff-only
    assert c("git pull", branch="main")[0] == "deny"
    assert c("git push --all origin")[0] == "deny"
    assert c("git push --mirror")[0] == "deny"
    assert c("git push origin :main", branch="main")[0] == "deny"  # delete
    assert c("git push origin main:", branch="main")[0] == "deny"  # empty dst
    assert (
        c("git push origin main:HEAD", branch="main")[0] == "deny"
    )  # unresolvable remote HEAD
    assert (
        c("git push origin main feat/x", branch="main")[0] == "deny"
    )  # multiple refspecs
    assert c("gh pr merge 12")[0] == "deny"
    assert c("gh --repo o/r pr merge 12")[0] == "deny"  # global opt before pr merge


def test_branch_lookup_failure_fails_closed():
    assert c("git merge --ff-only x", branch=None)[0] == "deny"
    assert c("git pull", branch=None)[0] == "deny"
    assert c("git push origin HEAD", branch=None)[0] == "deny"


# ---- main() integration ----


def _git(t, *a):
    subprocess.run(["git", *a], cwd=t, check=True, capture_output=True, text=True)


def _repo(tmp, branch="main"):
    _git(tmp, "init", "-b", branch)
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


def _invoke(command, cwd, env=None):
    event = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    )
    return subprocess.run(
        [sys.executable, GUARD],
        input=event,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_gated_push_denied_without_state(tmp_path):
    _repo(tmp_path)
    out = _invoke("git push origin main", tmp_path)
    assert out.returncode == 2
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_gated_push_allowed_when_green(tmp_path):
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
    assert _invoke("git push origin main", tmp_path).returncode == 0


def test_new_commit_makes_it_stale(tmp_path):
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
    (tmp_path / "f2").write_text("y")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c1")
    assert _invoke("git push origin main", tmp_path).returncode == 2


def test_metachar_denied(tmp_path):
    _repo(tmp_path)
    assert _invoke("git push origin main;", tmp_path).returncode == 2
    assert _invoke("cd /other && git push origin main", tmp_path).returncode == 2


def test_feature_push_allowed(tmp_path):
    _repo(tmp_path, branch="feat/x")
    assert _invoke("git push origin feat/x", tmp_path).returncode == 0


def test_flagged_first_push_allowed(tmp_path):
    # the most common op: first push of a feature branch with -u
    _repo(tmp_path, branch="feat/x")
    assert _invoke("git push -u origin feat/x", tmp_path).returncode == 0


def test_default_config_allows_merge_without_doc_paths(tmp_path):
    # No .loop-config.json: the shipped config.json defaults (empty doc paths) must
    # not block a green-gated merge (P1-2).
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c0")
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
    assert _invoke("git push origin main", tmp_path).returncode == 0


def test_env_prefix_push_gated(tmp_path):
    _repo(tmp_path)
    # env-prefix stripped, then gated: no state -> deny
    assert _invoke("GIT_SSH=x git push origin main", tmp_path).returncode == 2


def test_retarget_denied_outside_repo(tmp_path):
    outside = tmp_path / "notarepo"
    outside.mkdir()
    assert _invoke("git -C /target push origin main", outside).returncode == 2
    assert _invoke("GIT_SSH=x git -C /target push origin main", outside).returncode == 2


def test_plain_push_outside_repo_allowed(tmp_path):
    outside = tmp_path / "notarepo"
    outside.mkdir()
    assert _invoke("git push origin main", outside).returncode == 0


def test_escape_hatch_inline(tmp_path):
    _repo(tmp_path)
    (tmp_path / ".loop-config.json").write_text(
        '{"roadmap_paths": [], "shipped_log_paths": [], "escape_hatch": true}'
    )
    out = _invoke("DEVLOOP_OVERRIDE=hotfix git push origin main", tmp_path)
    assert out.returncode == 0
    assert "hotfix" in (tmp_path / ".loop-audit.log").read_text()


def test_escape_hatch_disabled_by_default(tmp_path):
    _repo(tmp_path)
    assert (
        _invoke("DEVLOOP_OVERRIDE=hotfix git push origin main", tmp_path).returncode
        == 2
    )
