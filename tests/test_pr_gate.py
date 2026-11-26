"""The PR gate: one narrow PreToolUse check on the raw command text, no tokenizer."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "pr_gate.py"
GATE_PASS = ROOT / "bin" / "devloop-gate-pass"

# The user-scope entry the Tech lead adds to ~/.claude/settings.json, kept here verbatim
# so the load-failure behavior of the real command is tested.
SETTINGS_COMMAND = (
    'f="$HOME/.local/share/devloop/lib/hooks/pr_gate.py"; '
    'if [ -f "$f" ] && [ -r "$f" ]; then python3 -I "$f"; '
    "case $? in 0) exit 0;; 2) exit 2;; esac; fi; "
    "echo 'pr gate not installed; run devloop-install' >&2; exit 2"
)

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def _git(cwd, *args):
    return subprocess.run(
        [*GIT, "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _config(name, repo_path):
    return {
        "name": name,
        "intent": "ship",
        "roadmap": {"path": "R.md", "section": "5", "row_id": "ROW"},
        "repos": [{"name": "r", "path": str(repo_path), "slug": "o/r"}],
        "rules_dir": "/tmp/x",
        "capacity": {"builder": 1, "tester": 1, "reviewer": 1, "planner": 1},
        "models": {"tick": "m", "planner": "m", "builder": "m", "reviewer": "m"},
        "reviews": {t: {"plan": [], "code": []} for t in ("R0", "R1", "R2")},
        "reviewers": {},
    }


@pytest.fixture
def world(tmp_path):
    """A configured repo with one commit, a project config naming it, and an env."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a").write_text("1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c1")
    state = tmp_path / "state"
    (state / "p").mkdir(parents=True)
    (state / "p" / "project.json").write_text(json.dumps(_config("p", repo)))
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("DEVLOOP_")},
        "HOME": str(home),
        "DEVLOOP_HOME": str(state),
    }
    return repo, env, tmp_path


def run_hook(command, cwd, env, hook=HOOK, raw=None):
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return subprocess.run(
        ["python3", "-I", str(hook)],
        input=raw if raw is not None else json.dumps(event),
        env=env,
        capture_output=True,
        text=True,
    )


def denied(r):
    if r.returncode != 2:
        return False
    out = json.loads(r.stdout)["hookSpecificOutput"]
    return out["permissionDecision"] == "deny" and bool(out["permissionDecisionReason"])


def allowed(r):
    return r.returncode == 0 and r.stdout == ""


def stamp(repo):
    (repo / ".git" / "decoy-quality-gate").write_text(
        _git(repo, "rev-parse", "HEAD") + "\n"
    )


def commit(repo, name="b"):
    (repo / name).write_text(name)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", name)


# ---- AT20, AT21: the receipt ----


def test_pr_create_needs_a_receipt_for_head(world):
    repo, env, _ = world
    assert denied(run_hook("gh pr create --fill", repo, env))
    stamp(repo)
    assert allowed(run_hook("gh pr create --fill", repo, env))
    commit(repo)
    assert denied(run_hook("gh pr create --fill", repo, env))


@pytest.mark.parametrize(
    "command",
    [
        "bash -lc 'gh pr create --fill'",
        "timeout 60 gh pr create",
        "gh pr new",
        "gh   pr\tcreate",
        "# it's a note\ngh pr create --fill",
    ],
)
def test_pr_create_is_found_in_the_text_whatever_wraps_it(world, command):
    repo, env, _ = world
    assert denied(run_hook(command, repo, env))


def test_the_denial_text_names_the_steps_and_echoes_nothing_of_the_command(world):
    repo, env, _ = world
    r = run_hook("gh pr create --title SECRET-TITLE", repo, env)
    reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    for word in ("dennis", "barry", "devloop-gate-pass"):
        assert word in reason
    assert "SECRET-TITLE" not in r.stdout + r.stderr
    assert "not installed" not in r.stderr


# ---- AT22: the target must be the cwd ----


@pytest.mark.parametrize(
    "command",
    [
        "cd ../x && gh pr create",
        "gh pr create -R a/b",
        "gh pr create --head y",
        "git -C ../x status && gh pr create",
        "pushd ../x; gh pr create",
        "GIT_DIR=../x/.git gh pr create",
        "gh pr create --repo a/b",
        "GH_REPO=a/b gh pr create",
        "gh pr create -Ra/b",
        "git -C../x status; gh pr create",
        "git --work-tree=../x status; gh pr create",
    ],
)
def test_a_pr_create_that_changes_its_target_is_denied_even_with_a_receipt(
    world, command
):
    repo, env, _ = world
    stamp(repo)
    r = run_hook(command, repo, env)
    assert denied(r)
    assert "by itself from the worktree" in r.stderr


def test_cd_into_the_repo_from_a_non_repo_cwd_is_denied(world):
    repo, env, tmp = world
    stamp(repo)
    elsewhere = tmp / "plain"
    elsewhere.mkdir()
    assert denied(run_hook(f"cd {repo} && gh pr create", elsewhere, env))


# ---- AT23: pushes are not this hook's business ----


@pytest.mark.parametrize(
    "command",
    [
        "git push origin feat",
        "git push --force origin feat",
        "git diff -C HEAD~1",
        "grep -rn push docs",
    ],
)
def test_push_commands_pass_the_pr_gate(world, command):
    repo, env, _ = world
    assert allowed(run_hook(command, repo, env))


# ---- AT24: hooks run ----


@pytest.mark.parametrize(
    "command",
    [
        "git push --no-verify",
        "git commit --no-verify -m x",
        "git -c core.hooksPath=/dev/null push",
        "git -c CORE.HOOKSPATH=/dev/null push",
    ],
)
def test_skipping_hooks_is_denied_in_a_configured_repo_only(world, command):
    repo, env, tmp = world
    r = run_hook(command, repo, env)
    assert denied(r) and "git hooks must run here; do not skip them" in r.stderr
    plain = tmp / "plain"
    plain.mkdir()
    assert allowed(run_hook(command, plain, env))


def test_an_unreadable_project_config_denies_pr_create_everywhere(world):
    """Plan B2 rule 5: a config that cannot be read leaves the repo list unknown."""
    repo, env, tmp = world
    state = Path(env["DEVLOOP_HOME"])
    (state / "broken").mkdir()
    (state / "broken" / "project.json").write_text("{not json")
    stamp(repo)
    assert denied(run_hook("gh pr create --fill", repo, env))
    other = tmp / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    assert denied(run_hook("gh pr create --fill", other, env))
    assert allowed(run_hook("git push origin feat", repo, env))


# ---- AT25: other repos ----


def test_a_repo_outside_every_project_config_is_not_gated(world):
    _, env, tmp = world
    other = tmp / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    (other / "a").write_text("1")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "c")
    assert allowed(run_hook("gh pr create --fill", other, env))
    assert allowed(run_hook("git push --no-verify", other, env))
    plain = tmp / "plain"
    plain.mkdir()
    assert allowed(run_hook("gh pr create --fill", plain, env))


def test_a_linked_worktree_of_a_configured_repo_is_gated(world):
    repo, env, tmp = world
    _git(repo, "worktree", "add", "-q", str(tmp / "wt"), "-b", "wtb")
    assert denied(run_hook("gh pr create --fill", tmp / "wt", env))
    (repo / ".git" / "worktrees" / "wt" / "decoy-quality-gate").write_text(
        _git(tmp / "wt", "rev-parse", "HEAD") + "\n"
    )
    assert allowed(run_hook("gh pr create --fill", tmp / "wt", env))


# ---- AT26, AT26b: fail closed ----


def test_a_receipt_that_cannot_be_read_denies(world):
    repo, env, _ = world
    (repo / ".git" / "decoy-quality-gate").mkdir()
    assert denied(run_hook("gh pr create --fill", repo, env))


def _lib(tmp, with_package=True, with_hook=True):
    lib = tmp / "home" / ".local" / "share" / "devloop" / "lib"
    (lib / "hooks").mkdir(parents=True)
    if with_hook:
        shutil.copy(HOOK, lib / "hooks" / "pr_gate.py")
    if with_package:
        (lib / "devloop").symlink_to(ROOT / "devloop")
    return lib


def run_settings(command, cwd, env):
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return subprocess.run(
        ["sh", "-c", SETTINGS_COMMAND],
        input=json.dumps(event),
        env=env,
        capture_output=True,
        text=True,
    )


def test_a_missing_package_denies_pr_create_and_skip_words_but_allows_the_rest(world):
    repo, env, tmp = world
    _lib(tmp, with_package=False)
    assert denied(run_settings("gh pr create --fill", repo, env))
    assert denied(run_settings("git push --no-verify", repo, env))
    assert allowed(run_settings("ls", repo, env))


def test_a_healthy_install_denies_with_the_receipt_text_not_the_install_text(world):
    repo, env, tmp = world
    _lib(tmp)
    r = run_settings("gh pr create --fill", repo, env)
    assert denied(r) and "dennis" in r.stderr
    assert "not installed" not in r.stderr
    stamp(repo)
    assert allowed(run_settings("gh pr create --fill", repo, env))


@pytest.mark.parametrize("fault", ["missing", "syntax"])
def test_a_broken_or_missing_script_blocks_every_command(world, fault):
    repo, env, tmp = world
    lib = _lib(tmp, with_hook=(fault != "missing"))
    if fault == "syntax":
        (lib / "hooks" / "pr_gate.py").write_text("def broken(:\n")
    r = run_settings("ls", repo, env)
    assert r.returncode == 2
    assert "pr gate not installed; run devloop-install" in r.stderr


# ---- AT26c: cd is a word ----


def test_cd_inside_a_longer_word_is_not_a_directory_change(world):
    repo, env, _ = world
    stamp(repo)
    for title in ("abcd", "cdc"):
        assert allowed(run_hook(f"gh pr create --title {title}", repo, env))
    assert denied(run_hook("(cd x; gh pr create)", repo, env))


# ---- AT27: a known false positive, written down ----


def test_characterization_a_search_for_the_command_text_is_denied(world):
    repo, env, _ = world
    assert denied(run_hook("git grep 'gh pr create'", repo, env))


# ---- AT28, receipt round trip ----


def _gate_pass(repo, env):
    return subprocess.run(
        ["python3", str(GATE_PASS)], cwd=repo, env=env, capture_output=True, text=True
    )


def test_receipt_round_trip_with_the_writer(world):
    repo, env, _ = world
    r = _gate_pass(repo, env)
    assert r.returncode == 0, r.stderr
    receipt = repo / ".git" / "decoy-quality-gate"
    assert receipt.read_text() == _git(repo, "rev-parse", "HEAD") + "\n"
    assert not [p for p in (repo / ".git").iterdir() if "tmp" in p.name]
    assert allowed(run_hook("gh pr create --fill", repo, env))
    receipt.write_text(receipt.read_text().strip() + "x\n")
    assert denied(run_hook("gh pr create --fill", repo, env))
    receipt.write_text(_git(repo, "rev-parse", "HEAD"))  # no newline: still the oid
    assert allowed(run_hook("gh pr create --fill", repo, env))


def test_the_writer_in_a_linked_worktree_writes_that_trees_git_dir(world):
    repo, env, tmp = world
    _git(repo, "worktree", "add", "-q", str(tmp / "wt"), "-b", "wtb")
    assert _gate_pass(tmp / "wt", env).returncode == 0
    assert (repo / ".git" / "worktrees" / "wt" / "decoy-quality-gate").is_file()
    assert not (repo / ".git" / "decoy-quality-gate").exists()


def test_the_writer_outside_a_repo_fails_and_writes_nothing(world):
    _, env, tmp = world
    plain = tmp / "plain"
    plain.mkdir()
    r = _gate_pass(plain, env)
    assert r.returncode != 0 and list(plain.iterdir()) == []


# ---- the hook never breaks an unrelated session ----


def test_unrelated_commands_and_bad_events_pass(world):
    repo, env, _ = world
    assert allowed(run_hook("ls -la", repo, env))
    assert allowed(run_hook("", repo, env, raw="not json"))
    assert allowed(run_hook("", repo, env, raw=json.dumps({"tool_name": "Read"})))
