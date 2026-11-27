import subprocess

import pytest
from test_wrap import mk, result

from devloop import push, runs, store, wrap

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(cwd, *args):
    return subprocess.run(
        [*GIT, "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True
    )
    wt = tmp_path / "wt"
    wt.mkdir()
    git(wt, "init", "-q", "-b", "main")
    (wt / "a").write_text("1")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "c1")
    git(wt, "remote", "add", "origin", str(remote))
    git(wt, "push", "-q", "origin", "main")
    git(wt, "checkout", "-q", "-b", "feat/s")
    return wt, remote


def commit(wt, name="b"):
    (wt / name).write_text(name)
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", name)
    return git(wt, "rev-parse", "HEAD")


def remote_head(remote, branch="feat/s"):
    r = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def run_writer(proj, tmp_path, wt, kind="builder", branch="feat/s", head=""):
    rd = mk(
        proj,
        tmp_path,
        [
            "sh",
            "-c",
            f"printf 'status: DONE\\nhead: {head}\\n---\\nok\\n' > $DEVLOOP_RUN_DIR/result.md",
        ],
        kind=kind,
        worktree=str(wt),
        branch=branch,
        bases="main,master",
    )
    wrap.run(proj, "r1")
    return rd


def test_unpushed_commit_is_pushed_and_applied(proj, tmp_path, repo):
    wt, remote = repo
    sha = commit(wt)
    rd = run_writer(proj, tmp_path, wt, head=sha)
    assert remote_head(remote) == sha
    res = runs.parse_result(rd, "builder")
    assert (res.status, res.head) == ("DONE", sha)


def test_codex_builder_commit_is_pushed(proj, tmp_path, repo):
    wt, remote = repo
    sha = commit(wt)
    rd = mk(
        proj,
        tmp_path,
        ["true"],
        kind="codex-builder",
        reader="exit",
        worktree=str(wt),
        branch="feat/s",
        bases="main",
    )
    wrap.run(proj, "r1")
    assert remote_head(remote) == sha
    assert result(rd)["status"] == "DONE" and result(rd)["head"] == sha


def test_already_pushed_head_makes_no_push_call(proj, tmp_path, repo, monkeypatch):
    wt, _remote = repo
    sha = commit(wt)
    git(wt, "push", "-q", "origin", "HEAD:refs/heads/feat/s")
    calls = []
    real = push._git

    def spy(w, *args):
        calls.append(args[0])
        return real(w, *args)

    monkeypatch.setattr(push, "_git", spy)
    rd = run_writer(proj, tmp_path, wt, head=sha)
    assert "push" not in calls
    assert runs.parse_result(rd, "builder").status == "DONE"


def test_non_fast_forward_is_refused_and_not_applied(proj, tmp_path, repo):
    wt, remote = repo
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    git(other, "checkout", "-q", "-b", "feat/s")
    theirs = commit(other, "theirs")
    git(other, "push", "-q", "origin", "feat/s")
    sha = commit(wt, "mine")
    rd = run_writer(proj, tmp_path, wt, head=sha)
    assert remote_head(remote) == theirs
    res = runs.parse_result(rd, "builder")
    assert res.status == "FAILED" and res.head is None
    assert (
        "not in the worktree"
        in store.read_header(store.out_dir(rd) / "result.md")["push"]
    )


@pytest.mark.parametrize("name", ["main", "master"])
def test_base_branch_is_refused(proj, tmp_path, repo, name):
    wt, remote = repo
    git(wt, "checkout", "-q", "-B", name)
    sha = commit(wt)
    before = remote_head(remote, name)
    rd = run_writer(proj, tmp_path, wt, branch=name, head=sha)
    assert remote_head(remote, name) == before
    res = runs.parse_result(rd, "builder")
    assert res.status == "FAILED" and res.head is None
    assert "base branch" in store.read_header(store.out_dir(rd) / "result.md")["push"]


def test_push_failure_is_recorded_not_applied(proj, tmp_path, repo):
    wt, remote = repo
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    sha = commit(wt)
    rd = run_writer(proj, tmp_path, wt, head=sha)
    assert remote_head(remote) is None
    res = runs.parse_result(rd, "builder")
    assert res.status == "FAILED" and res.head is None
    assert "push" in store.read_header(store.out_dir(rd) / "result.md")["push"]
    assert store.read_json(rd / "exit")["reason"] == "exit"


def test_amended_head_is_pushed_and_reported(proj, tmp_path, repo):
    wt, remote = repo
    old = commit(wt)
    (wt / "b").write_text("amended")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "--amend", "-m", "b2")
    new = git(wt, "rev-parse", "HEAD")
    assert new != old
    rd = run_writer(proj, tmp_path, wt, head=old)
    assert remote_head(remote) == new
    res = runs.parse_result(rd, "builder")
    assert (res.status, res.head) == ("DONE", new)


def test_confirm_failure_makes_the_result_failed(proj, tmp_path, repo, monkeypatch):
    wt, _remote = repo
    sha = commit(wt)

    def boom(req):
        raise OSError("confirm blew up")

    monkeypatch.setattr(push, "push_slice_branch", boom)
    rd = run_writer(proj, tmp_path, wt, head=sha)
    assert runs.parse_result(rd, "builder").status == "FAILED"
