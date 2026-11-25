import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from devloop import merge, slices, store

HEAD = "a" * 40
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hooks import headless_merge_guard  # noqa: E402


@pytest.fixture
def gate(proj, mk_slice, give, tmp_path, cfg_edit, fakebin, monkeypatch):
    board = tmp_path / "board"
    board.write_text(
        '#!/bin/sh\nif [ "$1" = show ]; then cat "$BOARD_LOG" 2>/dev/null; exit 0; fi\n'
        'echo "$@" >> "$BOARD_LOG"\n'
    )
    board.chmod(0o755)
    monkeypatch.setenv("BOARD_LOG", str(tmp_path / "board.log"))
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.log"))
    monkeypatch.setenv("FAKE_GH_JSON", str(tmp_path / "gh.json"))
    monkeypatch.setenv("FAKE_NOTIFY_LOG", str(tmp_path / "notify.log"))
    gh = {
        "statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"}],
        "files": [{"path": "CHANGELOG.md"}],
        "on_merge": {"state": "MERGED"},
    }
    (tmp_path / "gh.json").write_text(json.dumps(gh))

    def edit(c):
        c["board"] = {
            "cmd": [str(board)],
            "phase_map": {p: p.lower() for p in slices.PHASES},
        }
        c["notify"] = {"cmd": [str(fakebin / "notify")]}
        c["merge"] = {
            "allowed": True,
            "repos": {"main": {"required_checks": ["ci"]}},
        }

    cfg_edit(edit)
    plan = tmp_path / "plan.md"
    plan.write_text("Rules consulted: x\n")
    sha = __import__("hashlib").sha256(plan.read_bytes()).hexdigest()

    ids = iter(range(1, 100))

    def make(risk="R1", **fields):
        k = mk_slice(
            proj,
            phase="GATE",
            risk=risk,
            head=HEAD,
            pr=7,
            plan_path=str(plan),
            approved_plan_sha=sha,
            **fields,
        )
        slices.record_tick_evidence(proj, k, "selfcheck", "GO", head=HEAD)
        n = next(ids)
        give(proj, k, "t1", f"t1-g{n}")
        give(proj, k, "code:codex", f"c-g{n}")
        return k

    def set_gh(**kw):
        d = json.loads((tmp_path / "gh.json").read_text())
        d.update(kw)
        (tmp_path / "gh.json").write_text(json.dumps(d))

    make.set_gh = set_gh
    make.gh_calls = lambda: (
        [json.loads(x) for x in (tmp_path / "gh.log").read_text().splitlines()]
        if (tmp_path / "gh.log").exists()
        else []
    )
    make.board = lambda: (
        (tmp_path / "board.log").read_text()
        if (tmp_path / "board.log").exists()
        else ""
    )
    return make


def test_merge_refuses_head_mismatch(proj, gate):
    k = gate()
    gate.set_gh(headRefOid="b" * 40)
    r = merge.merge(proj, k)
    assert r.outcome == "refused" and "reviewed head" in r.reason
    assert not [c for c in gate.gh_calls() if c[:2] == ["pr", "merge"]]


def test_merge_refuses_missing_full_suite(proj, gate):
    k = gate(full_suite_required=True)
    r = merge.merge(proj, k)
    assert r.outcome == "refused" and "full suite" in r.reason


def test_merge_success_moves_to_document(proj, gate):
    k = gate()
    r = merge.merge(proj, k)
    assert r.outcome == "merged"
    assert slices.load(proj, k)["phase"] == "DOCUMENT"
    merges = [c for c in gate.gh_calls() if c[:2] == ["pr", "merge"]]
    assert merges[0][-3:] == ["--squash", "--match-head-commit", HEAD]
    row = k.split("--")[0]
    assert f"merged {row} S1 7 {row} S1" in gate.board()
    assert slices.load(proj, k)["merge_attempt"] is None


def test_merge_unknown_state_no_retry(proj, gate):
    k = gate()
    gate.set_gh(on_merge={"fail_view": True})
    assert merge.merge(proj, k).outcome == "unknown"
    s = slices.load(proj, k)
    assert s["phase"] == "GATE" and s["merge_unknown"] is True
    assert merge.merge(proj, k).outcome == "unknown"
    merges = [c for c in gate.gh_calls() if c[:2] == ["pr", "merge"]]
    assert len(merges) == 1


def test_merge_open_after_error_clears_flag(proj, gate):
    k = gate()
    s = slices.load(proj, k)
    s["merge_unknown"] = True
    slices._save(proj, s)
    r = merge.merge(proj, k)
    assert r.outcome == "refused" and slices.load(proj, k)["merge_unknown"] is False
    assert not [c for c in gate.gh_calls() if c[:2] == ["pr", "merge"]]


def test_merge_crash_after_success_finishes_once(proj, gate, monkeypatch):
    k = gate()
    real = merge._finish
    calls = []

    def boom(*a):
        calls.append(1)
        raise RuntimeError("crash")

    monkeypatch.setattr(merge, "_finish", boom)
    with pytest.raises(RuntimeError):
        merge.merge(proj, k)
    s = slices.load(proj, k)
    assert s["phase"] == "GATE" and s["merge_attempt"]["head"] == HEAD
    monkeypatch.setattr(merge, "_finish", real)
    assert merge.merge(proj, k).outcome == "merged"
    assert merge.merge(proj, k).outcome == "refused"  # now in DOCUMENT
    assert len([c for c in gate.gh_calls() if c[:2] == ["pr", "merge"]]) == 1
    assert gate.board().count("merged ") == 1


def test_merge_failed_call_with_open_pr_refuses_and_scrubs(proj, gate, monkeypatch):
    k = gate()
    gate.set_gh(on_merge={})
    monkeypatch.setenv("FAKE_GH_MERGE_EXIT", "1")
    r = merge.merge(proj, k)
    assert r.outcome == "refused"
    assert slices.load(proj, k)["merge_unknown"] is False
    assert slices.load(proj, k)["merge_attempt"] is None


@pytest.mark.parametrize(
    "rollup",
    [
        [{"name": "ci", "conclusion": "FAILURE"}],
        [
            {"name": "ci", "conclusion": "SUCCESS"},
            {"name": "lint", "status": "IN_PROGRESS"},
        ],
        [
            {"name": "ci", "conclusion": "SUCCESS"},
            {"name": "x", "conclusion": "FAILURE"},
        ],
        [{"name": "ci", "conclusion": "SKIPPED"}],
    ],
)
def test_merge_refuses_red_or_pending_check(proj, gate, rollup):
    k = gate()
    gate.set_gh(statusCheckRollup=rollup)
    assert merge.merge(proj, k).outcome == "refused"


def test_merge_refuses_missing_required_check(proj, gate):
    k = gate()
    gate.set_gh(statusCheckRollup=[])
    r = merge.merge(proj, k)
    assert r.outcome == "refused" and "required check ci" in r.reason


def test_merge_refuses_evidence_without_run_dir(proj, gate):
    import shutil

    k = gate()
    shutil.rmtree(store.run_dir(proj, "c-g1"))
    r = merge.merge(proj, k)
    assert r.outcome == "refused" and "code review codex" in r.reason


def test_merge_refuses_wrong_base(proj, gate):
    k = gate()
    gate.set_gh(baseRefName="feature-parent")
    assert "does not target main" in merge.merge(proj, k).reason


def test_merge_refuses_unknown_merge_state(proj, gate, cfg_edit, monkeypatch):
    cfg_edit(lambda c: c["merge"].update(require_up_to_date=True))
    monkeypatch.setattr(merge, "_up_to_date", lambda *a: None)
    k = gate()
    gate.set_gh(mergeStateStatus="UNKNOWN")
    assert merge.merge(proj, k).outcome == "refused"
    gate.set_gh(mergeStateStatus="CLEAN")
    assert merge.merge(proj, k).outcome == "merged"


def test_merge_changelog_glob_per_repo(proj, gate, cfg_edit):
    def edit(c):
        c["repos"].append(
            {"name": "eng", "path": c["repos"][0]["path"], "slug": "own/eng"}
        )
        c["merge"]["repos"]["main"]["changelog_glob"] = "CHANGELOG.md"
        c["merge"]["repos"]["eng"] = {
            "required_checks": ["ci"],
            "changelog_glob": "changelog.d/*",
        }

    cfg_edit(edit)
    eng = gate(repo="eng")
    assert "changelog" in merge.merge(proj, eng).reason  # eng wants changelog.d/*
    main = gate()
    assert merge.merge(proj, main).outcome == "merged"  # main wants CHANGELOG.md


def test_merge_changelog_glob_missing_refuses(proj, gate, cfg_edit):
    cfg_edit(
        lambda c: c["merge"]["repos"]["main"].update(changelog_glob="changelog.d/*")
    )
    assert "changelog" in merge.merge(proj, gate()).reason


def test_merge_refuses_gate_dispatch_before_codex_go(proj, gate, cfg_edit):
    def edit(c):
        c["reviews"]["R0"]["code"] = ["codex"]
        c["merge"]["repos"]["main"].update(
            dispatch_workflow="gate.yml", gate_status="gate-run"
        )

    cfg_edit(edit)
    k = gate()
    roll = [
        {"name": "ci", "conclusion": "SUCCESS"},
        {
            "name": "gate-run",
            "conclusion": "SUCCESS",
            "targetUrl": "https://x/actions/runs/99",
        },
    ]
    run = {
        "workflowName": "Gate",
        "path": ".github/workflows/gate.yml",
        "event": "workflow_dispatch",
        "headSha": HEAD,
        "conclusion": "success",
        "jobs": [
            {"conclusion": "success"},
            {"conclusion": "skipped"},
            {"conclusion": "skipped"},
        ],
        "createdAt": "2000-01-01T00:00:00Z",
    }
    gate.set_gh(statusCheckRollup=roll, run=run)
    assert "gate workflow" in merge.merge(proj, k).reason
    gate.set_gh(run={**run, "createdAt": "2999-01-01T00:00:00Z", "event": "push"})
    assert merge.merge(proj, k).outcome == "refused"
    gate.set_gh(run={**run, "createdAt": "2999-01-01T00:00:00Z"})
    assert merge.merge(proj, k).outcome == "merged"


def _origin(repo, tmp_path):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def g(*a, cwd=repo):
        subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env=env)

    bare = tmp_path / "origin.git"
    g("init", "-q", "--bare", str(bare), cwd=tmp_path)
    g("init", "-q", "-b", "main")
    (repo / "f").write_text("x")
    g("add", "-A")
    g("commit", "-qm", "c")
    g("remote", "add", "origin", str(bare))
    g("push", "-q", "origin", "main")


@pytest.mark.parametrize("code,outcome", [(0, "merged"), (1, "refused")])
def test_merge_runs_verify_cmd(proj, gate, cfg_edit, tmp_path, code, outcome):
    _origin(tmp_path / "repo", tmp_path)
    cfg_edit(
        lambda c: c["merge"]["repos"]["main"].update(
            verify_cmd=["sh", "-c", f"test -f f && exit {code}"]
        )
    )
    r = merge.merge(proj, gate())
    assert r.outcome == outcome, r.reason


def test_merge_with_public_selfcheck_succeeds(proj, gate):
    k = gate()
    s = slices.load(proj, k)
    s["phase"] = "SELF_CHECK"
    s["evidence"][HEAD].pop("selfcheck", None)
    slices._save(proj, s)
    slices.selfcheck(proj, k, "GO", head=HEAD)
    s = slices.load(proj, k)
    s["phase"] = "GATE"
    slices._save(proj, s)
    assert merge.merge(proj, k).outcome == "merged"


def test_merge_not_allowed_refuses(proj, gate, cfg_edit):
    cfg_edit(lambda c: c["merge"].update(allowed=False))
    assert "not allowed" in merge.merge(proj, gate()).reason


def test_notify_says_merging_in_fixed_words(proj, gate, tmp_path):
    k = gate()
    merge.merge(proj, k)
    text = (tmp_path / "notify.log").read_text()
    assert "Merging" in text and "PR 7" in text


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


def run_guard(command, headless):
    env = {**os.environ}
    env.pop("DEVLOOP_HEADLESS", None)
    if headless:
        env["DEVLOOP_HEADLESS"] = "1"
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "headless_merge_guard.py")],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        env=env,
    )


def test_merge_guard_only_acts_in_headless_sessions():
    assert run_guard("gh pr merge 5", True).returncode == 2
    assert run_guard("git push origin main", True).returncode == 2
    assert run_guard("gh pr merge 5", False).returncode == 0
    assert run_guard("gh pr view 5", True).returncode == 0


def test_merge_guard_is_registered():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    cmds = [h["command"] for g in hooks["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert any("headless_merge_guard" in c for c in cmds)


def test_merge_refuses_duplicate_name_with_one_failure(proj, gate):
    k = gate()
    gate.set_gh(
        statusCheckRollup=[
            {"name": "ci", "conclusion": "SUCCESS"},
            {"name": "ci", "conclusion": "FAILURE"},
        ]
    )
    assert "required check ci" in merge.merge(proj, k).reason


def test_merge_refuses_duplicate_non_required_failure(proj, gate):
    k = gate()
    gate.set_gh(
        statusCheckRollup=[
            {"name": "ci", "conclusion": "SUCCESS"},
            {"name": "lint", "conclusion": "SUCCESS"},
            {"name": "lint", "status": "IN_PROGRESS"},
        ]
    )
    assert "check lint" in merge.merge(proj, k).reason


def test_merge_pinned_source_must_match(proj, gate, cfg_edit):
    cfg_edit(
        lambda c: c["merge"]["repos"]["main"].update(
            required_check_sources={"ci": "ci-workflow"}
        )
    )
    k = gate()
    gate.set_gh(
        statusCheckRollup=[
            {"name": "ci", "conclusion": "SUCCESS", "workflowName": "someone-else"}
        ]
    )
    assert "from ci-workflow" in merge.merge(proj, k).reason
    # The pin picks the entry that satisfies `ci`; a red entry from another workflow
    # still blocks the merge.
    gate.set_gh(
        statusCheckRollup=[
            {"name": "ci", "conclusion": "FAILURE", "workflowName": "someone-else"},
            {"name": "ci", "conclusion": "SUCCESS", "workflowName": "ci-workflow"},
        ]
    )
    assert "check ci is FAILURE" in merge.merge(proj, k).reason
    gate.set_gh(
        statusCheckRollup=[
            {"name": "ci", "conclusion": "SKIPPED", "workflowName": "someone-else"},
            {"name": "ci", "conclusion": "SUCCESS", "workflowName": "ci-workflow"},
        ]
    )
    assert merge.merge(proj, k).outcome == "merged"


def test_closed_pr_clears_the_flags_and_refuses_with_a_fixed_reason(proj, gate):
    k = gate()
    gate.set_gh(on_merge={"state": "CLOSED"})
    r = merge.merge(proj, k)
    assert r.outcome == "refused" and "closed, not merged" in r.reason
    s = slices.load(proj, k)
    assert (
        s["phase"] == "GATE"
        and s["merge_unknown"] is False
        and s["merge_attempt"] is None
    )


def test_closed_pr_found_while_reconciling_an_unknown_state(proj, gate):
    k = gate()
    gate.set_gh(on_merge={"fail_view": True})
    assert merge.merge(proj, k).outcome == "unknown"
    gate.set_gh(fail_view=False, state="CLOSED")
    r = merge.merge(proj, k)
    assert r.outcome == "refused" and "closed, not merged" in r.reason
    s = slices.load(proj, k)
    assert s["merge_unknown"] is False and s["merge_attempt"] is None


def test_merged_into_the_wrong_base_refuses_and_clears_the_flags(proj, gate):
    k = gate()
    gate.set_gh(on_merge={"state": "MERGED", "baseRefName": "release"})
    r = merge.merge(proj, k)
    assert r.outcome == "refused" and "another base" in r.reason
    s = slices.load(proj, k)
    assert (
        s["phase"] == "GATE"
        and s["merge_unknown"] is False
        and s["merge_attempt"] is None
    )


def test_plan_edited_after_the_merge_still_finishes_the_move(
    proj, gate, tmp_path, monkeypatch
):
    k = gate()
    real = merge.confirm_pr

    def confirm_then_edit(*a, **kw):
        state = real(*a, **kw)
        (tmp_path / "plan.md").write_text("Rules consulted: edited after the merge\n")
        return state

    monkeypatch.setattr(merge, "confirm_pr", confirm_then_edit)
    assert merge.merge(proj, k).outcome == "merged"
    assert slices.load(proj, k)["phase"] == "DOCUMENT"


def test_merge_reset_is_interactive_and_clears_the_flags(proj, gate, monkeypatch):
    k = gate()
    s = slices.load(proj, k)
    s["merge_unknown"], s["merge_attempt"] = True, {"pr": 7}
    slices._save(proj, s)
    monkeypatch.setenv("DEVLOOP_HEADLESS", "1")
    with pytest.raises(store.HeadlessRefused):
        slices.merge_reset(proj, k)
    monkeypatch.delenv("DEVLOOP_HEADLESS")
    slices.merge_reset(proj, k)
    s = slices.load(proj, k)
    assert s["merge_unknown"] is False and s["merge_attempt"] is None


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


def test_base_branches_come_from_the_project_config(proj):
    from devloop import project as projmod

    cfg = projmod.load_project(proj)
    cfg["merge"] = {"repos": {"main": {"base": "develop"}}}
    assert projmod.base_branches(cfg) == "develop,main,master"


def test_gate_run_failure_conclusion_is_still_refused(proj, gate, cfg_edit):
    def edit(c):
        c["reviews"]["R0"]["code"] = ["codex"]
        c["merge"]["repos"]["main"].update(
            dispatch_workflow="gate.yml", gate_status="gate-run"
        )

    cfg_edit(edit)
    k = gate()
    roll = [
        {"name": "ci", "conclusion": "SUCCESS"},
        {
            "name": "gate-run",
            "conclusion": "SUCCESS",
            "targetUrl": "https://x/actions/runs/9",
        },
    ]
    run = {
        "workflowName": "Gate",
        "path": ".github/workflows/gate.yml",
        "event": "workflow_dispatch",
        "headSha": HEAD,
        "conclusion": "failure",
        "jobs": [{"conclusion": "success"}],
        "createdAt": "2999-01-01T00:00:00Z",
    }
    gate.set_gh(statusCheckRollup=roll, run=run)
    assert merge.merge(proj, k).outcome == "refused"


def test_every_gh_call_uses_the_repo_slug(proj, gate):
    k = gate()
    assert merge.merge(proj, k).outcome == "merged"
    calls = [c for c in gate.gh_calls() if c[:1] in (["pr"], ["run"])]
    assert calls and all(c[c.index("-R") + 1] == "own/main" for c in calls)


def test_a_repo_without_a_valid_slug_is_refused_at_load(proj, cfg_edit):
    from devloop import project as projmod

    for bad in (None, "main", "a/b/c", "a b/c", ""):

        def edit(c, bad=bad):
            if bad is None:
                c["repos"][0].pop("slug")
            else:
                c["repos"][0]["slug"] = bad

        cfg_edit(edit)
        with pytest.raises(projmod.ConfigError, match="slug"):
            projmod.load_project(proj)


def test_dispatch_workflow_with_an_empty_code_roster_is_refused_at_load(
    proj, gate, cfg_edit
):
    from devloop import project as projmod

    def edit(c):
        c["reviews"]["R1"]["code"] = []
        c["merge"]["repos"]["main"] = {"dispatch_workflow": "gate", "gate_status": "g"}

    cfg_edit(edit)
    with pytest.raises(projmod.ConfigError, match="code reviewer in every tier"):
        projmod.load_project(proj)


def test_main_and_master_are_always_base_branches(monkeypatch):
    monkeypatch.setenv("DEVLOOP_BASE_BRANCHES", "develop")
    assert headless_merge_guard._base_branches() == ["develop", "main", "master"]


def test_crash_after_the_move_still_counts_and_delivers_the_board_once(
    proj, gate, monkeypatch
):
    k = gate()
    real = slices.deliver_board
    monkeypatch.setattr(
        slices, "deliver_board", lambda *a: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    with pytest.raises(RuntimeError):
        merge.merge(proj, k)
    s = slices.load(proj, k)
    assert s["phase"] == "DOCUMENT" and s["merge_attempt"] is None
    recs = slices.merge_records(proj)
    assert len(recs) == 1 and next(iter(recs.values()))["board_done"] is False
    monkeypatch.setattr(slices, "deliver_board", real)
    assert merge.merge(proj, k).outcome == "refused"  # already in DOCUMENT
    assert gate.board().count("merged ") == 1
    assert next(iter(slices.merge_records(proj).values()))["board_done"] is True
    ident = {"repo": "main", "slug": "own/main", "pr": 7, "head": HEAD}
    slices.deliver_board(proj, ident)
    slices.deliver_board(proj, ident)
    assert gate.board().count("merged ") == 1  # idempotent per repo, PR and head
    from devloop import status

    assert status.counts(proj)["merged_total"] == 1


def test_crash_before_the_move_finishes_once_and_counts_once(proj, gate, monkeypatch):
    k = gate()
    real = slices.finish_merge
    monkeypatch.setattr(
        slices,
        "finish_merge",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash")),
    )
    with pytest.raises(RuntimeError):
        merge.merge(proj, k)
    monkeypatch.setattr(slices, "finish_merge", real)
    assert merge.merge(proj, k).outcome == "merged"
    assert len(slices.merge_records(proj)) == 1
    assert gate.board().count("merged ") == 1


def test_unknown_response_keeps_the_attempt(proj, gate):
    k = gate()
    gate.set_gh(on_merge={"fail_view": True})
    assert merge.merge(proj, k).outcome == "unknown"
    s = slices.load(proj, k)
    assert s["merge_unknown"] and s["merge_attempt"]["head"] == HEAD


def test_head_and_pr_cannot_change_while_a_merge_is_pending(proj, gate):
    k = gate()
    gate.set_gh(on_merge={"fail_view": True})
    merge.merge(proj, k)
    for field in ({"head": "b" * 40}, {"pr": 99}):
        with pytest.raises(slices.SliceError, match="merge is pending"):
            slices.set_fields(proj, k, **field)
    slices.set_fields(proj, k, next_action="wait")  # other fields stay editable


def test_finish_refuses_when_the_slice_no_longer_matches_the_confirmed_merge(
    proj, gate
):
    k = gate()
    ident = {"repo": "main", "slug": "own/main", "pr": 7, "head": "c" * 40}
    with pytest.raises(slices.TransitionError, match="current PR and head"):
        slices.finish_merge(proj, k, ident=ident)
    assert slices.load(proj, k)["phase"] == "GATE" and not slices.merge_records(proj)


def _dispatch_cfg(cfg_edit, wf):
    def edit(c):
        c["reviews"]["R0"]["code"] = ["codex"]
        c["merge"]["repos"]["main"].update(dispatch_workflow=wf, gate_status="gate-run")

    cfg_edit(edit)


def _green_run(**over):
    run = {
        "workflowName": "Gate",
        "path": ".github/workflows/gate.yml",
        "event": "workflow_dispatch",
        "headSha": HEAD,
        "conclusion": "success",
        "jobs": [{"conclusion": "success"}, {"conclusion": "skipped"}],
        "createdAt": "2999-01-01T00:00:00Z",
    }
    run.update(over)
    roll = [
        {"name": "ci", "conclusion": "SUCCESS"},
        {
            "name": "gate-run",
            "conclusion": "SUCCESS",
            "targetUrl": "https://x/actions/runs/9",
        },
    ]
    return roll, run


@pytest.mark.parametrize(
    "wf,ok", [("gate.yml", True), ("Gate", True), ("other.yml", False)]
)
def test_gate_workflow_matches_file_name_or_display_name(proj, gate, cfg_edit, wf, ok):
    _dispatch_cfg(cfg_edit, wf)
    k = gate()
    roll, run = _green_run()
    gate.set_gh(statusCheckRollup=roll, run=run)
    assert (merge.merge(proj, k).outcome == "merged") is ok


def test_gate_run_with_only_skipped_jobs_or_a_failed_job_is_refused(
    proj, gate, cfg_edit
):
    _dispatch_cfg(cfg_edit, "gate.yml")
    k = gate()
    for jobs in (
        [{"conclusion": "skipped"}],
        [{"conclusion": "success"}, {"conclusion": "failure"}],
    ):
        roll, run = _green_run(jobs=jobs)
        gate.set_gh(statusCheckRollup=roll, run=run)
        assert merge.merge(proj, k).outcome == "refused"


def test_up_to_date_needs_the_base_tip_in_the_head(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _origin(repo, tmp_path)
    cfg = {"path": str(repo)}
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def g(*a):
        return subprocess.run(
            ["git", "-C", str(repo), *a],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()

    old = g("rev-parse", "HEAD")
    g("checkout", "-q", "-b", "feat")
    g("commit", "-q", "--allow-empty", "-m", "feature")
    head = g("rev-parse", "HEAD")
    assert merge._up_to_date(cfg, "main", head) is None
    g("checkout", "-q", "main")
    g("commit", "-q", "--allow-empty", "-m", "base moved")
    g("push", "-q", "origin", "main")
    assert "latest base" in merge._up_to_date(cfg, "main", head)
    assert merge._up_to_date(cfg, "main", g("rev-parse", "HEAD")) is None
    assert "could not fetch" in merge._up_to_date({"path": str(tmp_path)}, "main", old)


def test_merge_preflight_runs_the_ancestor_check_when_required(
    proj, gate, cfg_edit, monkeypatch
):
    cfg_edit(lambda c: c["merge"].update(require_up_to_date=True))
    monkeypatch.setattr(
        merge,
        "_up_to_date",
        lambda *a: "the PR head does not contain the latest base branch",
    )
    k = gate()
    gate.set_gh(mergeStateStatus="CLEAN")
    r = merge.merge(proj, k)
    assert r.outcome == "refused" and "latest base" in r.reason


def test_snapshot_sends_a_ready_gate_slice_to_devloop_merge(proj, gate):
    from devloop import snapshot

    k = gate()
    md, _ = snapshot.build_snapshot(proj)
    assert f"  next command: devloop-merge {proj} {k}\n" in md
    assert "slice move" not in md.split("next command:")[1].split("\n")[0]


def test_fake_gh_rejects_fields_the_real_cli_does_not_have(fakebin):
    for argv in (
        ["run", "view", "1", "--json", "event,path"],
        ["pr", "view", "1", "--json", "state,nope"],
    ):
        r = subprocess.run([str(fakebin / "gh"), *argv], capture_output=True, text=True)
        assert r.returncode == 1 and "Unknown JSON field" in r.stderr
    ok = subprocess.run(
        [str(fakebin / "gh"), "run", "view", "1", "--json", "event,workflowName,jobs"],
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0


def test_workflow_file_name_is_resolved_through_the_actions_api(proj, gate, cfg_edit):
    _dispatch_cfg(cfg_edit, "gate.yml")
    k = gate()
    roll, run = _green_run(workflowName="Some other display name")
    gate.set_gh(statusCheckRollup=roll, run=run)
    assert merge.merge(proj, k).outcome == "merged"
    calls = gate.gh_calls()
    assert any(c[:1] == ["api"] and ".path" in c for c in calls)
    assert all("path" not in c[c.index("--json") + 1] for c in calls if "--json" in c)


@pytest.mark.parametrize(
    "kind,extra",
    [
        ("test", {"level": "t1", "purpose": "code"}),
        ("codex-review", {"reviewer": "codex", "purpose": "code"}),
    ],
)
def test_merge_refuses_while_a_rerun_for_the_head_is_unapplied(
    proj, gate, mk_run, kind, extra
):
    k = gate()
    mk_run(
        proj,
        "rerun",
        kind=kind,
        key=k,
        head=HEAD,
        applied=False,
        verdict="NO-GO",
        **extra,
    )
    r = merge.merge(proj, k)
    assert r.outcome == "refused" and "outstanding" in r.reason
    assert not [c for c in gate.gh_calls() if c[:2] == ["pr", "merge"]]
    d = store.run_dir(proj, "rerun")
    (d / "applied").write_text("")
    assert merge.merge(proj, k).outcome == "merged"


def test_merge_refuses_while_a_covering_full_run_is_unapplied(proj, gate, mk_run):
    k = gate()
    mk_run(
        proj,
        "topfull",
        kind="test",
        key="TOP--S1",
        head="b" * 40,
        level="full",
        covers={k: HEAD},
        applied=False,
    )
    assert "outstanding" in merge.merge(proj, k).reason


def test_red_rerun_after_green_blocks_merge_through_apply(proj, gate, mk_run):
    k = gate()
    assert not slices.validate_gate(proj, k)
    slices.record_evidence(proj, k, "t1", "NO-GO", "t1-red", head=HEAD)
    assert any("t1" in p for p in slices.validate_gate(proj, k))


def _crash_after_move(proj, gate, monkeypatch):
    k = gate()
    real = slices.deliver_board
    monkeypatch.setattr(
        slices, "deliver_board", lambda *a: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    with pytest.raises(RuntimeError):
        merge.merge(proj, k)
    monkeypatch.setattr(slices, "deliver_board", real)
    assert slices.load(proj, k)["phase"] == "DOCUMENT"
    return k


def test_tick_delivers_a_merge_the_crash_left_undelivered(proj, gate, monkeypatch):
    from devloop import tick

    k = _crash_after_move(proj, gate, monkeypatch)
    assert gate.board().count("merged ") == 0
    tick.tick(proj)
    log = gate.board()
    assert log.count("merged ") == 1 and f"{k.split('--')[0]} S1 [d:" in log
    tick.tick(proj)
    assert gate.board().count("merged ") == 1
    assert next(iter(slices.merge_records(proj).values()))["board_done"] is True


def test_tick_confirms_a_delivery_the_board_already_has_without_retrying(
    proj, gate, monkeypatch, tmp_path
):
    from devloop import board, tick

    _crash_after_move(proj, gate, monkeypatch)
    mid = next(iter(slices.merge_records(proj)))
    # The board call landed, then the process died before board_done was saved.
    with open(tmp_path / "board.log", "a") as fh:
        fh.write(f"merged ROW1 S1 7 ROW1 S1 [d:{board.delivery_id(mid)}]\n")
    doc = store.read_json(board._merges_path(proj))
    doc["merges"][mid]["board_attempted"] = True
    store.write_json(board._merges_path(proj), doc)
    tick.tick(proj)
    assert gate.board().count("merged ") == 1  # no second call
    assert slices.merge_records(proj)[mid]["board_done"] is True


def test_tick_never_retries_an_ambiguous_delivery(proj, gate, monkeypatch, tmp_path):
    from devloop import board, tick

    _crash_after_move(proj, gate, monkeypatch)
    mid = next(iter(slices.merge_records(proj)))
    doc = store.read_json(board._merges_path(proj))
    doc["merges"][mid]["board_attempted"] = True  # unknown whether it reached the board
    store.write_json(board._merges_path(proj), doc)
    tick.tick(proj)
    tick.tick(proj)
    rec = slices.merge_records(proj)[mid]
    assert rec["board_ambiguous"] is True and rec["board_done"] is False
    assert gate.board().count("merged ") == 0


def test_a_crash_between_the_board_call_and_board_done_does_not_post_twice(
    proj, gate, monkeypatch
):
    from devloop import board, tick

    k = _crash_after_move(proj, gate, monkeypatch)
    real = board.board_merged

    def call_then_die(*a, **kw):
        real(*a, **kw)
        raise RuntimeError("crash after the board call")

    monkeypatch.setattr(board, "board_merged", call_then_die)
    with pytest.raises(RuntimeError):
        slices.deliver_board(proj, {"slug": "own/main", "pr": 7, "head": HEAD})
    monkeypatch.setattr(board, "board_merged", real)
    tick.tick(proj)
    assert gate.board().count("merged ") == 1
    assert next(iter(slices.merge_records(proj).values()))["board_done"] is True
    assert k
