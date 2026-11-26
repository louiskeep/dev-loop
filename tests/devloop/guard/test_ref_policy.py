"""The ref policy in git's pre-push: which ref updates a push to the project's remotes may make.

Every route here is real git against a bare remote, so the policy sees git's own stdin,
never a hand-made line (the unit cells of check_refs are the only exception).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from devloop import githooks  # noqa: E402
from test_push import commit, git, remote_head, repo  # noqa: E402,F401

ZERO = "0" * 40
OID = "a" * 40
BASE_WORKER = "a worker may not update the base branch main"


def _env(home, worker=False, **extra):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEVLOOP_", "GIT_CONFIG_", "DECOY_"))
    }
    env.update(HOME=str(home), GIT_TERMINAL_PROMPT="0")
    if worker:
        env["DEVLOOP_RUN"] = "r1"
    env.update(extra)
    return env


def push(wt, *args, env):
    return subprocess.run(
        ["git", "-C", str(wt), "push", *args],
        env=env,
        capture_output=True,
        text=True,
    )


def refusal(ref, reason):
    return f"push refused: {ref}: {reason}"


@pytest.fixture
def lib_home(tmp_path):
    """A HOME whose installed lib is this checkout, as the clone shim expects."""
    lib = tmp_path / "libhome" / ".local" / "share" / "devloop"
    lib.mkdir(parents=True)
    (lib / "lib").symlink_to(ROOT)
    return tmp_path / "libhome"


@pytest.fixture
def guarded(repo, lib_home):
    wt, remote = repo
    assert githooks.install_clone_hook(wt) == "installed"
    return wt, remote


def _clone(tmp_path, remote, name):
    dest = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    return dest


def _pushed_x(wt, lib_home):
    """A branch x on the remote at its own commit, pushed by wt."""
    git(wt, "checkout", "-q", "-b", "x")
    first = commit(wt, "x1")
    assert push(wt, "origin", "x", env=_env(lib_home)).returncode == 0
    return first


# ---- AT1, AT2: ordinary pushes pass ----


def test_new_branch_and_fast_forward_are_allowed(guarded, lib_home):
    wt, remote = guarded
    sha = commit(wt)
    assert push(wt, "origin", "feat/s", env=_env(lib_home)).returncode == 0
    assert remote_head(remote) == sha
    sha = commit(wt, "c")
    r = push(wt, "origin", "feat/s", env=_env(lib_home))
    assert r.returncode == 0 and "push refused" not in r.stderr
    assert remote_head(remote) == sha


# ---- AT3, AT9, AT16: deletes ----


@pytest.mark.parametrize("args", [("--delete", "x"), (":x",)])
def test_a_delete_is_refused(guarded, lib_home, args):
    wt, remote = guarded
    sha = _pushed_x(wt, lib_home)
    r = push(wt, "origin", *args, env=_env(lib_home))
    assert r.returncode != 0
    assert refusal("refs/heads/x", "deletes a remote ref") in r.stderr
    assert remote_head(remote, "x") == sha


@pytest.mark.parametrize(
    "args, reason",
    [
        (("--prune", "origin", "refs/heads/*:refs/heads/*"), "deletes a remote ref"),
        # git does not list --mirror removals on pre-push stdin. A mirror push is refused
        # anyway because it also sends the remote-tracking refs, which are not branches.
        (("--mirror", "origin"), "not a branch"),
    ],
)
def test_mirror_and_prune_that_would_delete_are_refused(
    guarded, lib_home, tmp_path, args, reason
):
    wt, remote = guarded
    other = _clone(tmp_path, remote, "other")
    git(other, "checkout", "-q", "-b", "old")
    old = commit(other, "o")
    git(other, "push", "-q", "origin", "old")
    r = push(wt, *args, env=_env(lib_home))
    assert r.returncode != 0
    assert f": {reason}" in r.stderr
    assert remote_head(remote, "old") == old


def test_a_sha256_all_zero_local_oid_is_a_delete():
    out = githooks.check_refs(
        [f"(delete) {'0' * 64} refs/heads/x {'b' * 64}"], env={}, cwd=None
    )
    assert out == [refusal("refs/heads/x", "deletes a remote ref")]


# ---- AT4, AT30: tags ----


@pytest.mark.parametrize(
    "args",
    [("origin", "--tags"), ("origin", "tag", "v1"), ("origin", "HEAD:refs/tags/v1")],
)
def test_a_worker_cannot_push_a_tag(guarded, lib_home, args):
    wt, remote = guarded
    git(wt, "tag", "v1")
    r = push(wt, *args, env=_env(lib_home, worker=True))
    assert r.returncode != 0
    assert refusal("refs/tags/v1", "not a branch") in r.stderr
    tags = subprocess.run(
        ["git", "-C", str(remote), "tag", "-l"], capture_output=True, text=True
    )
    assert tags.stdout.strip() == ""


def test_a_new_tag_is_allowed_for_an_interactive_session_only(guarded, lib_home):
    wt, remote = guarded
    env = _env(lib_home)
    git(wt, "tag", "-a", "v9", "-m", "x")
    assert push(wt, "origin", "refs/tags/v9:refs/tags/v9", env=env).returncode == 0
    # moving the tag is refused, and so is deleting it
    commit(wt, "next")
    git(wt, "tag", "-f", "-a", "v9", "-m", "y")
    moved = push(wt, "-f", "origin", "refs/tags/v9", env=env)
    assert moved.returncode != 0
    assert refusal("refs/tags/v9", "not a branch") in moved.stderr
    gone = push(wt, "origin", "--delete", "v9", env=env)
    assert gone.returncode != 0
    assert refusal("refs/tags/v9", "deletes a remote ref") in gone.stderr
    # a worker may not create one
    git(wt, "tag", "-a", "v10", "-m", "x")
    w = push(
        wt, "origin", "refs/tags/v10:refs/tags/v10", env=_env(lib_home, worker=True)
    )
    assert w.returncode != 0
    assert refusal("refs/tags/v10", "not a branch") in w.stderr


# ---- AT5, AT6, AT7: rewrites ----


def _x_on_two_clones(wt, remote, lib_home, tmp_path):
    _pushed_x(wt, lib_home)
    other = _clone(tmp_path, remote, "other")
    git(other, "checkout", "-q", "x")
    theirs = commit(other, "theirs")
    git(other, "push", "-q", "origin", "x")
    commit(wt, "mine")
    return theirs


def test_force_over_commits_never_fetched_is_refused(guarded, lib_home, tmp_path):
    wt, remote = guarded
    theirs = _x_on_two_clones(wt, remote, lib_home, tmp_path)
    r = push(wt, "--force", "origin", "x", env=_env(lib_home))
    assert r.returncode != 0
    why = "remote has commits this clone has not fetched; fetch first"
    assert refusal("refs/heads/x", why) in r.stderr
    assert remote_head(remote, "x") == theirs


def test_force_over_commits_fetched_but_never_integrated_is_refused(
    guarded, lib_home, tmp_path
):
    wt, remote = guarded
    theirs = _x_on_two_clones(wt, remote, lib_home, tmp_path)
    git(wt, "fetch", "-q", "origin", "x")
    r = push(wt, "--force", "origin", "x", env=_env(lib_home))
    assert r.returncode != 0
    assert refusal("refs/heads/x", "rewrites commits this branch never had") in r.stderr
    assert remote_head(remote, "x") == theirs


def test_force_after_rewriting_its_own_commits_is_allowed(guarded, lib_home):
    wt, remote = guarded
    _pushed_x(wt, lib_home)
    git(wt, "commit", "-q", "--amend", "-m", "x1 again")
    mine = git(wt, "rev-parse", "HEAD")
    r = push(wt, "--force", "origin", "x", env=_env(lib_home))
    assert r.returncode == 0, r.stderr
    assert remote_head(remote, "x") == mine


# ---- AT8: the base branch ----


def test_base_branch_rules_for_a_worker_and_an_interactive_session(guarded, lib_home):
    wt, remote = guarded
    commit(wt, "b")
    before = remote_head(remote, "main")
    w = push(wt, "origin", "HEAD:refs/heads/main", env=_env(lib_home, worker=True))
    assert w.returncode != 0
    assert refusal("refs/heads/main", BASE_WORKER) in w.stderr
    assert remote_head(remote, "main") == before
    # the same fast-forward is fine interactively
    ok = push(wt, "origin", "HEAD:refs/heads/main", env=_env(lib_home))
    assert ok.returncode == 0
    assert remote_head(remote, "main") == git(wt, "rev-parse", "HEAD")
    # a rewrite of main is not
    git(wt, "checkout", "-q", "-b", "alt", "main")
    commit(wt, "d")
    tip = remote_head(remote, "main")
    r = push(wt, "-f", "origin", "alt:main", env=_env(lib_home))
    assert r.returncode != 0
    assert refusal("refs/heads/main", "rewrites a base branch") in r.stderr
    assert remote_head(remote, "main") == tip


def test_configured_base_branches_bind_a_worker(guarded, lib_home):
    wt, _ = guarded
    commit(wt, "b")
    env = _env(lib_home, worker=True, DEVLOOP_BASE_BRANCHES="develop")
    r = push(wt, "origin", "HEAD:refs/heads/develop", env=env)
    assert r.returncode != 0
    why = "a worker may not update the base branch develop"
    assert refusal("refs/heads/develop", why) in r.stderr


# ---- AT10: P0, anchored ----


@pytest.mark.parametrize(
    "line",
    [
        f"refs/heads/x {OID}a refs/heads/x {ZERO}",
        f"refs/heads/x {OID} refs/heads/x {ZERO}a",
        f"refs/heads/x {OID} refs/heads/x",
        f"refs/heads/x {OID}\n refs/heads/x {ZERO}",
        f"refs/heads/x {OID} refs/heads/x {ZERO}\n",
        f"refs/heads/x {OID} refs/heads/x {ZERO} extra",
        f"refs/heads/x {OID.upper()} refs/heads/x {ZERO}",
        "",
    ],
)
def test_an_unreadable_line_is_refused_whole(line):
    out = githooks.check_refs([line], env={}, cwd=None)
    assert len(out) == 1 and out[0].startswith("push refused: ")
    assert out[0].endswith(": unreadable push line")
    assert OID not in out[0]


def test_a_readable_new_branch_line_is_allowed():
    line = f"refs/heads/x {OID} refs/heads/x {ZERO}"
    assert githooks.check_refs([line], env={}) == []


# ---- AT11: override ----


def test_the_owner_override_works_for_a_person_and_never_for_a_worker(
    guarded, lib_home
):
    wt, remote = guarded
    sha = _pushed_x(wt, lib_home)
    worker = _env(lib_home, worker=True, DECOY_PUSH_OVERRIDE="1")
    w = push(wt, "origin", "--delete", "x", env=worker)
    assert w.returncode != 0
    assert "push policy override" not in w.stderr
    assert remote_head(remote, "x") == sha
    r = push(wt, "origin", "--delete", "x", env=_env(lib_home, DECOY_PUSH_OVERRIDE="1"))
    assert r.returncode == 0, r.stderr
    assert "push policy override" in r.stderr
    assert remote_head(remote, "x") is None


# ---- AT12, AT14, AT15: the clone shim ----


def test_a_new_linked_worktree_inherits_the_policy(repo, lib_home, tmp_path):
    wt, remote = repo
    git(wt, "worktree", "add", "-q", str(tmp_path / "w1"), "-b", "w1b")
    # the installer is pointed at a linked tree; the hook still lands in the common dir
    assert githooks.install_clone_hook(tmp_path / "w1") == "installed"
    assert push(wt, "origin", "feat/s", env=_env(lib_home)).returncode == 0
    git(wt, "worktree", "add", "-q", str(tmp_path / "w2"), "-b", "w2b")
    r = push(tmp_path / "w2", "origin", "--delete", "feat/s", env=_env(lib_home))
    assert r.returncode != 0
    assert refusal("refs/heads/feat/s", "deletes a remote ref") in r.stderr
    assert remote_head(remote, "feat/s") is not None


def test_installer_never_overwrites_a_foreign_hook_or_a_hooks_path(repo):
    wt, _ = repo
    hook = wt / ".git" / "hooks" / "pre-push"
    for foreign in (
        "#!/bin/sh\nexit 0\n",
        "#!/bin/sh\n# devloop-ref-policy v2\nexit 0\n",
    ):
        hook.write_text(foreign)
        assert githooks.install_clone_hook(wt).startswith("refused")
        assert hook.read_text() == foreign
    hook.unlink()
    git(wt, "config", "--local", "core.hooksPath", "/elsewhere")
    assert githooks.install_clone_hook(wt).startswith("refused")
    assert not hook.exists()
    git(wt, "config", "--local", "--unset", "core.hooksPath")
    assert githooks.install_clone_hook(wt) == "installed"
    before = hook.stat().st_mtime_ns
    assert githooks.install_clone_hook(wt) == "unchanged"
    assert hook.stat().st_mtime_ns == before
    assert os.access(hook, os.X_OK)
    hook.write_text(hook.read_text() + "# drift\n")
    assert githooks.install_clone_hook(wt) == "updated"


def test_a_shim_whose_target_is_missing_refuses_the_push(guarded, tmp_path):
    wt, remote = guarded
    commit(wt)
    r = push(wt, "origin", "feat/s", env=_env(tmp_path / "empty-home"))
    assert r.returncode != 0
    assert "push policy not installed; run devloop-install" in r.stderr
    assert remote_head(remote) is None


# ---- AT13: workers (protect_env) with the shim present ----


def test_protect_env_hook_runs_the_policy_and_leaves_no_temp_file(
    guarded, lib_home, tmp_path
):
    wt, remote = guarded
    tmpd = tmp_path / "tmpd"
    tmpd.mkdir()
    base = _env(lib_home, worker=True, TMPDIR=str(tmpd))
    env = githooks.protect_env(base, tmp_path / "hooks")
    sha = commit(wt)
    assert push(wt, "origin", "feat/s", env=env).returncode == 0
    assert remote_head(remote) == sha
    assert list(tmpd.iterdir()) == []
    r = push(wt, "origin", "--delete", "feat/s", env=env)
    assert r.returncode != 0
    assert refusal("refs/heads/feat/s", "deletes a remote ref") in r.stderr
    assert list(tmpd.iterdir()) == []
    assert remote_head(remote) == sha


def test_protect_env_alone_enforces_the_policy_with_no_shim(repo, lib_home, tmp_path):
    wt, _ = repo
    commit(wt)
    env = githooks.protect_env(_env(lib_home, worker=True), tmp_path / "hooks")
    r = push(wt, "origin", "HEAD:refs/heads/main", env=env)
    assert r.returncode != 0
    assert BASE_WORKER in r.stderr


# ---- AT29: node mirrors are not the project's remote ----


def _mirror(tmp_path, wt):
    mirror = tmp_path / "mirror.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(mirror)], check=True
    )
    other = tmp_path / "unrelated"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    (other / "u").write_text("u")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "u")
    git(other, "tag", "v1")
    git(
        other,
        "push",
        "-q",
        str(mirror),
        "HEAD:refs/heads/main",
        "HEAD:refs/heads/lbl",
        "v1",
    )
    # a local main that is an ancestor of nothing either remote holds
    empty = git(wt, "mktree")
    root = git(wt, "commit-tree", "-m", "root", empty)
    git(wt, "branch", "-f", "main", root)
    git(wt, "tag", "v1")
    return mirror, git(wt, "rev-parse", "feat/s")


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("route", ["shim", "protect_env"])
def test_a_vm_run_shaped_force_push_to_a_mirror_passes_but_not_to_origin(
    guarded, lib_home, tmp_path, worker, route
):
    wt, remote = guarded
    mirror, raw = _mirror(tmp_path, wt)
    env = _env(lib_home, worker=worker)
    if route == "protect_env":
        env = githooks.protect_env(env, tmp_path / "hooks")
    spec = [
        f"{raw}:refs/heads/lbl",
        "main:refs/heads/main",
        "refs/tags/v*:refs/tags/v*",
    ]
    ok = push(wt, "-q", "-f", str(mirror), *spec, env=env)
    assert ok.returncode == 0, ok.stderr
    assert ok.stderr == ""
    assert remote_head(mirror, "lbl") == raw
    assert remote_head(mirror, "main") == git(wt, "rev-parse", "main")
    no = push(wt, "-q", "-f", "origin", *spec, env=env)
    assert no.returncode != 0
    ours = [x for x in no.stderr.splitlines() if x.startswith("push refused")]
    assert ours and not any(str(remote) in x for x in ours)
    assert remote_head(remote, "lbl") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/a/b.git",
        "ssh://git@github.com/a/b.git",
        "git@github.com:a/b.git",
        "https://user:tok@github.com/a/b.git",
        "HTTPS://GitHub.com/a/b",
        "ssh://git@ssh.github.com:443/a/b.git",
        "https://www.github.com/a/b",
    ],
)
def test_github_urls_are_protected_in_any_spelling(url):
    assert githooks.is_protected_url(url, cwd=None) is True


@pytest.mark.parametrize(
    "url",
    [
        "ssh://mac/Users/x/decoy/engine.git",
        "/srv/mirror/decoy.git",
        "https://example.com/github.com/a/b",
        "https://github.com.evil.example/a/b",
        "git@gitlab.com:a/b.git",
    ],
)
def test_other_hosts_are_not_protected_without_being_the_origin(url, repo):
    wt, _ = repo
    assert githooks.is_protected_url(url, cwd=wt) is False


def test_the_origin_url_is_protected_whatever_its_host(repo):
    wt, remote = repo
    assert githooks.is_protected_url(str(remote), cwd=wt) is True
    git(wt, "remote", "set-url", "--push", "origin", "ssh://mac/elsewhere.git")
    assert githooks.is_protected_url("ssh://mac/elsewhere.git", cwd=wt) is True


# ---- AT31: standard library only ----


def test_the_policy_file_decides_alone(repo, tmp_path):
    wt, remote = repo
    lone = tmp_path / "lone"
    lone.mkdir()
    shutil.copy(ROOT / "devloop" / "githooks.py", lone / "githooks.py")
    sha = git(wt, "rev-parse", "HEAD")

    def run(line):
        return subprocess.run(
            ["python3", "-I", str(lone / "githooks.py"), "pre-push", "origin"]
            + [str(remote)],
            input=line,
            cwd=wt,
            env=_env(tmp_path),
            capture_output=True,
            text=True,
        )

    ok = run(f"refs/heads/x {sha} refs/heads/x {ZERO}\n")
    assert ok.returncode == 0 and ok.stderr == ""
    bad = run(f"(delete) {ZERO} refs/heads/x {sha}\n")
    assert bad.returncode == 1
    assert refusal("refs/heads/x", "deletes a remote ref") in bad.stderr


# ---- AT34: detached worktrees ----


def test_detached_worktree_rewrites_follow_its_own_head_reflog(
    guarded, lib_home, tmp_path
):
    wt, remote = guarded
    env = _env(lib_home)
    a, b = tmp_path / "a", tmp_path / "b"
    git(wt, "worktree", "add", "-q", "--detach", str(a), "feat/s")
    git(wt, "worktree", "add", "-q", "--detach", str(b), "feat/s")
    commit(a, "c")
    assert push(a, "origin", "HEAD:refs/heads/x", env=env).returncode == 0
    git(a, "commit", "-q", "--amend", "-m", "c again")
    mine = git(a, "rev-parse", "HEAD")
    assert push(a, "-f", "origin", "HEAD:refs/heads/x", env=env).returncode == 0
    assert remote_head(remote, "x") == mine
    commit(b, "d")
    r = push(b, "-f", "origin", "HEAD:refs/heads/x", env=env)
    assert r.returncode != 0
    assert refusal("refs/heads/x", "rewrites commits this branch never had") in r.stderr
    assert remote_head(remote, "x") == mine


# ---- the installer reaches every configured repo ----


def test_devloop_install_writes_shims_and_the_pr_gate(
    tmp_path, home, minimal_project, write_project
):
    repo_dir = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo_dir)], check=True)
    cfg = minimal_project()
    cfg["repos"][0]["path"] = str(repo_dir)
    write_project(home, "p", cfg)
    h = tmp_path / "h"
    env = {**_env(h), "DEVLOOP_HOME": str(home)}
    cmd = [sys.executable, str(ROOT / "bin" / "devloop-install"), str(tmp_path / "bin")]
    run = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert "installed" in run.stdout
    shim = repo_dir / ".git" / "hooks" / "pre-push"
    assert "# devloop-ref-policy v1" in shim.read_text()
    lib = h / ".local" / "share" / "devloop" / "lib"
    assert (lib / "hooks" / "pr_gate.py").is_file()
    assert (tmp_path / "bin" / "devloop-gate-pass").is_file()
    # a foreign hook is refused and reported, with a nonzero exit and no path in the line
    shim.write_text("#!/bin/sh\nexit 0\n")
    again = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert again.returncode != 0
    out = again.stdout + again.stderr
    assert "refused" in out and str(repo_dir) not in out
