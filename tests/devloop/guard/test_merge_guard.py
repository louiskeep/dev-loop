"""The headless merge guard hook: what a worker or subagent may and may not run."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from hooks import headless_merge_guard

DENIED = [
    "gh pr merge 5 --squash",
    "gh -R o/r pr merge 5",
    "gh pr --repo o/r merge 5",
    "gh pr merge --auto 5",
    "env gh pr merge 5",
    "command gh pr merge 5",
    "sudo -u bob gh pr merge 5",
    "timeout 60 gh pr merge 5",
    "GH_TOKEN=x gh pr merge 5",
    "/usr/bin/gh pr merge 5",
    "GH pr MERGE 5",
    "cd x && gh pr merge 5",
    "true; gh pr merge 5",
    "echo hi | gh pr merge 5",
    "bash -c 'gh pr merge 5'",
    "bash -lc 'gh pr merge 5'",
    'sh -ec "gh pr merge 5"',
    "eval 'gh pr merge 5'",
    "echo 5 | xargs gh pr merge",
    "find . -maxdepth 0 -exec gh pr merge 5 \\;",
    "echo $(gh pr merge 5)",
    "gh api repos/o/r/pulls/5/merge -X PUT",
    "gh api -X PUT /repos/o/r/pulls/12/merge",
    "gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {}) { x } }'",
    "curl -X POST https://api.github.com/repos/o/r/merges",
    "curl https://api.github.com/repos/o/r/git/refs/heads/main",
    "git push origin main",
    "git push origin HEAD:main",
    "git push origin :main",
    "git push origin +main",
    "git push -u origin main",
    "git push --all",
    "git push --mirror origin",
    "git -C /x push origin main",
    "env git push origin main",
    "git status && git push origin refs/heads/main",
    "bash -c 'git push origin main'",
    "devloop-merge x; gh pr merge 1",
    "devloop-merge x && git push origin main",
    "devloop-merge $(gh pr merge 1)",
]
ALLOWED = [
    "gh pr view 5",
    "gh -R o/r pr view 5 --json state,mergeStateStatus,mergeable",
    "gh pr list",
    "gh run list",
    "gh api repos/x/y/pulls/5",
    "gh api repos/x/y/pulls/5/files",
    "gh api -X GET repos/x/y/pulls/5",
    "gh alias list",
    "git status",
    "git log --oneline --merges",
    "git push -u origin feat/x",
    "git push --force-with-lease origin feat/x",
    "git push origin HEAD:feat/x",
    "git push origin HEAD",
    "git push",
    "git push origin",
    'gh pr create --title "add guard" --body "please merge when green"',
    "echo merge the thing",
    'git commit -m "merge main into the branch"',
    "env FOO=1 ls",
    "devloop-merge proj ROW--S1",
    "/home/someone/bin/devloop-merge proj ROW--S1",
]


@pytest.mark.parametrize("command", DENIED)
def test_merge_guard_denies_in_headless_mode(command):
    assert headless_merge_guard.blocked(command) is True


@pytest.mark.parametrize("command", ALLOWED)
def test_merge_guard_allows_reads_feature_pushes_and_devloop_merge(command):
    assert headless_merge_guard.blocked(command) is False


def _repo_on(tmp_path, branch):
    subprocess.run(["git", "init", "-q", "-b", branch, str(tmp_path)], check=True)
    return tmp_path


def test_bare_push_is_denied_only_on_the_base_branch(tmp_path):
    main = _repo_on(tmp_path / "a", "main")
    feat = _repo_on(tmp_path / "b", "feat/x")
    assert headless_merge_guard.blocked("git push", main) is True
    assert headless_merge_guard.blocked("git push origin", main) is True
    assert headless_merge_guard.blocked("git push", feat) is False
    assert headless_merge_guard.blocked("git push origin feat/x", main) is False


def run_guard(command, headless=False, run=False, agent_id=None, raw=None, cwd=None):
    env = {**os.environ}
    env.pop("DEVLOOP_HEADLESS", None)
    env.pop("DEVLOOP_RUN", None)
    env.pop("DEVLOOP_BASE_BRANCHES", None)
    if headless:
        env["DEVLOOP_HEADLESS"] = "1"
    if run:
        env["DEVLOOP_RUN"] = "r1"
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_id:
        event["agent_id"] = agent_id
    if cwd:
        event["cwd"] = str(cwd)
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "headless_merge_guard.py")],
        input=raw if raw is not None else json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


WORKER_MSG = "only the orchestrator pushes to main or merges"


def test_loop_worker_pushes_are_left_to_the_git_policy(tmp_path):
    # The Bash text of a loop worker's push is not inspected: pre-push sees the real refs
    # (test_ref_policy.py, AT8). A subagent without DEVLOOP_RUN is still checked here.
    for command, cwd in (
        ("git push origin main", None),
        ("git push origin HEAD:feat/x", None),
        ("git push", _repo_on(tmp_path / "m", "main")),
    ):
        assert run_guard(command, headless=True, run=True, cwd=cwd).returncode == 0
    r = run_guard("git push origin main", agent_id="a1")
    assert r.returncode == 2 and WORKER_MSG in r.stderr
    r = run_guard("git push origin main", run=True, agent_id="a1")
    assert r.returncode == 0


def test_a_loop_worker_still_cannot_merge():
    for command in ("gh pr merge 5", "devloop-merge p K"):
        assert run_guard(command, run=True).returncode == 2
        assert run_guard(command, run=True, agent_id="a1").returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "env | grep -E 'DEVLOOP|GIT_CONFIG'",
        "env | grep x",
        "env | sort | head",
    ],
)
def test_a_prefix_word_that_feeds_a_pipe_is_a_plain_command(command):
    assert headless_merge_guard.blocked(command, worker=True) is False
    assert run_guard(command, run=True).returncode == 0


@pytest.mark.parametrize(
    "command", ["env", "ls; sudo", "ls | sudo", "echo 'x", "env -S 'ls'"]
)
def test_text_that_cannot_be_parsed_is_still_denied_for_a_worker(command):
    assert headless_merge_guard.blocked(command, worker=True) is True
    assert run_guard(command, run=True).returncode == 2


def test_worker_cannot_run_devloop_merge_or_gh_merge():
    assert (
        run_guard("devloop-merge proj ROW--S1", headless=True, run=True).returncode == 2
    )
    assert run_guard("gh pr merge 5", headless=True, run=True).returncode == 2


def test_subagent_is_not_the_orchestrator():
    assert run_guard("gh pr merge 5", agent_id="a1").returncode == 2
    assert run_guard("devloop-merge p K", agent_id="a1").returncode == 2
    assert run_guard("git push origin main", agent_id="a1").returncode == 2
    assert run_guard("git push origin feat/x", agent_id="a1").returncode == 0
    assert run_guard("git push origin feat/x", agent_id="").returncode == 0


def test_interactive_top_level_is_not_restricted():
    assert run_guard("git push origin main").returncode == 0
    assert run_guard("gh pr merge 5").returncode == 0


def test_headless_tick_merges_only_through_devloop_merge_and_may_push_base():
    assert run_guard("gh pr merge 5", headless=True).returncode == 2
    assert run_guard("devloop-merge proj ROW--S1", headless=True).returncode == 0
    assert run_guard("git push origin main", headless=True).returncode == 0


def test_worker_fails_closed_on_an_unreadable_event_but_the_orchestrator_does_not():
    assert run_guard("", run=True, raw="not json").returncode == 2
    assert run_guard("", headless=True, raw="not json").returncode == 0
    assert run_guard("", raw="not json").returncode == 0


def test_merge_guard_only_acts_in_headless_sessions():
    assert run_guard("gh pr merge 5", True).returncode == 2
    assert run_guard("gh pr merge 5", False).returncode == 0
    assert run_guard("gh pr view 5", True).returncode == 0


def test_merge_guard_is_registered():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    cmds = [h["command"] for g in hooks["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert any("headless_merge_guard" in c for c in cmds)


def test_push_of_head_or_at_is_the_current_branch(tmp_path):
    main = _repo_on(tmp_path / "a", "main")
    feat = _repo_on(tmp_path / "b", "feat/x")
    assert headless_merge_guard.blocked("git push origin HEAD", main) is True
    assert headless_merge_guard.blocked("git push origin HEAD:main", feat) is True
    assert headless_merge_guard.blocked("git push origin @", main) is True
    assert headless_merge_guard.blocked("git push origin HEAD", feat) is False


def test_push_reads_the_branch_from_cd_or_dash_c(tmp_path):
    main = _repo_on(tmp_path / "a", "main")
    feat = _repo_on(tmp_path / "b", "feat/x")
    assert headless_merge_guard.blocked(f"cd {main} && git push", feat) is True
    assert headless_merge_guard.blocked(f"git -C {main} push", feat) is True
    assert headless_merge_guard.blocked(f"cd {feat} && git push", main) is False
    assert (
        headless_merge_guard.blocked(f"git -C {feat} push origin HEAD", main) is False
    )


def test_push_to_a_configured_base_is_denied(tmp_path, monkeypatch):
    feat = _repo_on(tmp_path / "b", "feat/x")
    monkeypatch.setenv("DEVLOOP_BASE_BRANCHES", "develop,main")
    assert headless_merge_guard.blocked("git push origin HEAD:develop", feat) is True
    assert headless_merge_guard.blocked("git push origin develop", feat) is True
    assert headless_merge_guard.blocked("git push origin HEAD:refs/heads/develop", feat)
    assert headless_merge_guard.blocked("git push origin HEAD", feat) is False
    dev = _repo_on(tmp_path / "c", "develop")
    assert headless_merge_guard.blocked("git push origin HEAD", dev) is True


def test_main_and_master_are_always_base_branches(monkeypatch):
    monkeypatch.setenv("DEVLOOP_BASE_BRANCHES", "develop")
    assert headless_merge_guard._base_branches() == ["develop", "main", "master"]
